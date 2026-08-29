import json
import os
import time
from datetime import datetime

from plc import read_registers
from database import get_connection

ZERO_DEBOUNCE_SECONDS = 2.0
MAPPING_CACHE_SECONDS = 30.0
TRIGGER_TAG_PREFIX = "__TRIGGER_REGISTER_"


class PLCReader:
    def __init__(self, config=None, *args, **kwargs):
        self.config = config or {}
        self._watchdog_zero_memory = set()
        self._zero_memory = {}
        self._mapping_cache = None
        self._mapping_cache_time = 0.0
        self._mapping_cache_company_id = None

    def _get_config(self, key, default=None):
        value = self.config.get(key)
        return default if value is None else value

    def _edge_timeout(self):
        try:
            return max(0.1, float(os.environ.get("SCADA_EDGE_TIMEOUT", "2")))
        except (TypeError, ValueError):
            return 2.0

    def _get_edge_mappings(self, force=False):
        try:
            company_id = int(self._get_config("company_id"))
        except (TypeError, ValueError):
            return []

        now = time.monotonic()
        if (not force and self._mapping_cache is not None and self._mapping_cache_company_id == company_id and now - self._mapping_cache_time < MAPPING_CACHE_SECONDS):
            return self._mapping_cache

        conn = None
        cursor = None
        try:
            conn = get_connection()
            cursor = conn.cursor()
            cursor.execute("SELECT FlowJson FROM Flows WHERE CompanyID = ? ORDER BY FlowID DESC LIMIT 1", (company_id,))
            row = cursor.fetchone()
            if not row:
                return self._mapping_cache or []

            flow = json.loads(row[0]) if isinstance(row[0], str) else row[0]
            nodes = flow.get("drawflow", {}).get("Home", {}).get("data", {})
            mappings = []
            trigger_registers = set()

            for node in nodes.values():
                if node.get("name") != "TagMapper":
                    continue
                node_data = node.get("data", {}) or {}
                mapper_config = node_data.get("config", node_data) or {}
                node_mappings = mapper_config.get("mappings", [])
                if not isinstance(node_mappings, list):
                    continue

                for mapping in node_mappings:
                    if not isinstance(mapping, dict):
                        continue
                    name = str(mapping.get("name", "")).strip()
                    register = mapping.get("register")
                    if not name or register in (None, ""):
                        continue
                    try:
                        register = int(register)
                    except (TypeError, ValueError):
                        continue
                    mappings.append({"register": register, "name": name})

                    if str(mapping.get("storage", "TIME")).strip().upper() == "TRIGGER":
                        try:
                            trigger_registers.add(int(mapping.get("trigger_register")))
                        except (TypeError, ValueError):
                            pass
                break

            # The Edge sends every shared trigger register as a reserved
            # historian tag. Add it to the server register map so SQLWriter
            # receives Registers[118] (or any other configured trigger register).
            for trigger_register in sorted(trigger_registers):
                mappings.append({
                    "register": trigger_register,
                    "name": f"{TRIGGER_TAG_PREFIX}{trigger_register}",
                })

            result = []
            seen = set()
            for mapping in mappings:
                key = (mapping["register"], mapping["name"])
                if key in seen:
                    continue
                seen.add(key)
                result.append(mapping)

            self._mapping_cache = result
            self._mapping_cache_time = now
            self._mapping_cache_company_id = company_id
            return result
        except Exception as exc:
            print("EDGE MAPPING LOAD ERROR:", exc)
            return self._mapping_cache or []
        finally:
            if cursor is not None:
                try:
                    cursor.close()
                except Exception:
                    pass
            if conn is not None:
                try:
                    conn.close()
                except Exception:
                    pass

    def _is_zero(self, value):
        try:
            return float(value) == 0.0
        except (TypeError, ValueError):
            return False

    def _zero_key(self, company_id, tag_name):
        return (int(company_id), str(tag_name).strip().lower())

    def _handle_zero(self, company_id, tag_name, value):
        key = self._zero_key(company_id, tag_name)
        now = time.monotonic()
        if not self._is_zero(value):
            self._zero_memory.pop(key, None)
            return True
        first_zero = self._zero_memory.get(key)
        if first_zero is None:
            self._zero_memory[key] = now
            return False
        if now - first_zero < ZERO_DEBOUNCE_SECONDS:
            return False
        self._zero_memory.pop(key, None)
        return True

    def _read_edge_registers(self, register, count):
        try:
            company_id = int(self._get_config("company_id"))
            start = int(register)
            end = start + int(count) - 1
        except (TypeError, ValueError):
            return {}

        mappings = [m for m in self._get_edge_mappings() if start <= m["register"] <= end]
        if not mappings:
            return {}

        tag_names = [m["name"] for m in mappings]
        conn = None
        cursor = None
        try:
            conn = get_connection()
            cursor = conn.cursor()
            placeholders = ",".join("?" for _ in tag_names)
            cursor.execute(
                f"""
                SELECT TagName, Value, Timestamp
                FROM TagHistory AS H
                WHERE H.CompanyID = ?
                  AND H.TagName IN ({placeholders})
                  AND H.ID = (
                      SELECT H2.ID FROM TagHistory AS H2
                      WHERE H2.CompanyID = H.CompanyID
                        AND H2.TagName = H.TagName
                      ORDER BY H2.Timestamp DESC, H2.ID DESC LIMIT 1
                  )
                """,
                [company_id] + tag_names,
            )
            latest_rows = {row["TagName"]: row for row in cursor.fetchall()}
            registers = {}
            timeout = self._edge_timeout()
            now = time.time()

            for mapping in mappings:
                tag_name = mapping["name"]
                row = latest_rows.get(tag_name)
                if not row:
                    continue
                value = row["Value"]
                timestamp = row["Timestamp"]

                try:
                    timestamp_text = str(timestamp).replace("T", " ").strip().rstrip("Z")
                    age = now - datetime.fromisoformat(timestamp_text).timestamp()
                except Exception:
                    age = timeout + 1

                # Do not debounce the reserved trigger register. A real 0 is
                # essential because SQLWriter needs to see 0 before 1.
                is_trigger_register = tag_name.startswith(TRIGGER_TAG_PREFIX)
                if self._is_zero(value) and not is_trigger_register:
                    if not self._handle_zero(company_id, tag_name, value):
                        cursor.execute(
                            """
                            SELECT Value FROM TagHistory
                            WHERE CompanyID = ? AND TagName = ?
                              AND Value IS NOT NULL AND Value != 0
                            ORDER BY Timestamp DESC, ID DESC LIMIT 1
                            """,
                            (company_id, tag_name),
                        )
                        previous = cursor.fetchone()
                        if previous:
                            value = previous["Value"]
                        else:
                            continue
                else:
                    self._zero_memory.pop(self._zero_key(company_id, tag_name), None)

                registers[str(mapping["register"])] = value

                if age <= timeout and (is_trigger_register or value not in (None, 0, 0.0)):
                    self._watchdog_zero_memory.discard(self._zero_key(company_id, tag_name))

            return registers
        except Exception as exc:
            print("EDGE HISTORIAN READ ERROR:", exc)
            return {}
        finally:
            if cursor is not None:
                try:
                    cursor.close()
                except Exception:
                    pass
            if conn is not None:
                try:
                    conn.close()
                except Exception:
                    pass

    def _read_direct_modbus(self, ip, port, slave, register, count):
        raw_registers = read_registers(ip=ip, port=port, slave=slave, register=register, count=count)
        return {str(register + index): value for index, value in enumerate(raw_registers)}

    def execute(self, data=None):
        if data is None:
            data = {}

        ip = self._get_config("ip")
        port = self._get_config("port")
        slave = self._get_config("slave")
        register = self._get_config("register")
        count = self._get_config("count")

        required = {"ip": ip, "port": port, "slave": slave, "register": register, "count": count}
        missing = [name for name, value in required.items() if value in (None, "")]
        if missing:
            raise ValueError("PLCReader configuration is incomplete. Missing: " + ", ".join(missing))

        try:
            port, slave, register, count = int(port), int(slave), int(register), int(count)
        except (TypeError, ValueError) as exc:
            raise ValueError("PLCReader configuration contains invalid numeric values.") from exc

        if port <= 0 or slave < 0 or register < 0 or count <= 0:
            raise ValueError("PLCReader configuration contains invalid numeric values.")

        registers = self._read_edge_registers(register, count)
        if not registers and os.environ.get("SCADA_DIRECT_MODBUS", "0").strip().lower() in {"1", "true", "yes", "on"}:
            registers = self._read_direct_modbus(ip, port, slave, register, count)

        result = dict(data)
        result["PLC"] = {"ip": ip, "port": port, "slave": slave, "register": register, "count": count}
        result["Registers"] = registers
        result["registers"] = registers
        return result
