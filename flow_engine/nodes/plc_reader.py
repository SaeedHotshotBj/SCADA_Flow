# ============================================================
# SCADA_FLOW
# PLC READER NODE
#
# Server-side source is SCADA_FLOW_EDGE historian data.
# Temporary zero glitches are ignored for 2 seconds so the
# dashboard keeps the last valid value. A zero that remains
# continuously for 2 seconds is accepted as a real zero.
# ============================================================

import json
import os
import time

from datetime import datetime

from plc import read_registers
from database import get_connection


ZERO_DEBOUNCE_SECONDS = 2.0


class PLCReader:

    def __init__(self, config=None, *args, **kwargs):
        self.config = config or {}
        self._watchdog_zero_memory = set()
        self._zero_memory = {}

    # ========================================================
    # CONFIG
    # ========================================================

    def _get_config(self, key, default=None):
        value = self.config.get(key)
        if value is None:
            return default
        return value

    def _edge_timeout(self):
        """Maximum age of Edge data considered live, in seconds."""
        value = os.environ.get("SCADA_EDGE_TIMEOUT", "2")
        try:
            return max(0.1, float(value))
        except (TypeError, ValueError):
            return 2.0

    # ========================================================
    # DRAWFLOW TAG MAPPINGS
    # ========================================================

    def _get_edge_mappings(self):
        company_id = self._get_config("company_id")

        try:
            company_id = int(company_id)
        except (TypeError, ValueError):
            return []

        conn = None
        cursor = None

        try:
            conn = get_connection()
            cursor = conn.cursor()

            cursor.execute(
                """
                SELECT FlowJson
                FROM Flows
                WHERE CompanyID = ?
                ORDER BY FlowID DESC
                LIMIT 1
                """,
                (company_id,)
            )

            row = cursor.fetchone()
            if not row:
                return []

            flow_json = row[0]
            if isinstance(flow_json, str):
                flow = json.loads(flow_json)
            else:
                flow = flow_json

            nodes = (
                flow.get("drawflow", {})
                .get("Home", {})
                .get("data", {})
            )

            mappings = []

            for node in nodes.values():
                if node.get("name") != "TagMapper":
                    continue

                node_data = node.get("data", {})
                mapper_config = node_data.get("config", node_data)
                node_mappings = mapper_config.get("mappings", [])

                if not isinstance(node_mappings, list):
                    continue

                for mapping in node_mappings:
                    if not isinstance(mapping, dict):
                        continue

                    register = mapping.get("register")
                    name = str(mapping.get("name", "")).strip()

                    if register in (None, "") or not name:
                        continue

                    try:
                        register = int(register)
                    except (TypeError, ValueError):
                        continue

                    mappings.append({
                        "register": register,
                        "name": name
                    })

            result = []
            seen = set()

            for mapping in mappings:
                key = (mapping["register"], mapping["name"])
                if key in seen:
                    continue
                seen.add(key)
                result.append(mapping)

            return result

        except Exception as exc:
            print("EDGE MAPPING LOAD ERROR:", exc)
            return []

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

    # ========================================================
    # WATCHDOG ZERO
    # ========================================================

    def _write_watchdog_zero(self, company_id, tag_name):
        """Disabled: stale Edge data must never overwrite a live value with zero."""
        return False

    def _clear_watchdog_zero(self, company_id, tag_name):
        key = (int(company_id), str(tag_name).strip().lower())
        self._watchdog_zero_memory.discard(key)

    # ========================================================
    # ZERO DEBOUNCE
    # ========================================================

    def _is_zero(self, value):
        try:
            return float(value) == 0.0
        except (TypeError, ValueError):
            return False

    def _zero_key(self, company_id, tag_name):
        return (int(company_id), str(tag_name).strip().lower())

    def _handle_zero(self, company_id, tag_name, value):
        """
        Return the action for an observed zero.

        True  -> accept zero.
        False -> ignore zero temporarily and keep previous value.
        """
        key = self._zero_key(company_id, tag_name)
        now = time.monotonic()

        if not self._is_zero(value):
            self._zero_memory.pop(key, None)
            return True

        first_zero = self._zero_memory.get(key)

        if first_zero is None:
            self._zero_memory[key] = now
            print(
                "PLC READER ZERO DEBOUNCE START:",
                tag_name,
                "WAIT:",
                ZERO_DEBOUNCE_SECONDS,
                "seconds"
            )
            return False

        if now - first_zero < ZERO_DEBOUNCE_SECONDS:
            print(
                "PLC READER ZERO IGNORED:",
                tag_name,
                "AGE:",
                round(now - first_zero, 2),
                "seconds"
            )
            return False

        self._zero_memory.pop(key, None)
        print("PLC READER ZERO ACCEPTED:", tag_name)
        return True

    # ========================================================
    # EDGE HISTORIAN
    # ========================================================

    def _read_edge_registers(self, register, count):
        """
        Read the latest Edge values.

        A zero is treated as a transient communication glitch for the
        first 2 seconds. During that period the previous non-zero value
        is returned. If zero remains continuously for 2 seconds, zero is
        accepted as the real PLC value.
        """
        company_id = self._get_config("company_id")

        try:
            company_id = int(company_id)
            start = int(register)
            end = start + int(count) - 1
        except (TypeError, ValueError):
            return {}

        mappings = self._get_edge_mappings()
        mappings = [
            mapping
            for mapping in mappings
            if start <= mapping["register"] <= end
        ]

        if not mappings:
            return {}

        conn = None
        cursor = None

        try:
            conn = get_connection()
            cursor = conn.cursor()

            registers = {}
            timeout = self._edge_timeout()
            now = time.time()

            for mapping in mappings:
                tag_name = mapping["name"]
                register_address = mapping["register"]

                cursor.execute(
                    """
                    SELECT Value, Timestamp
                    FROM TagHistory
                    WHERE CompanyID = ?
                      AND TagName = ?
                    ORDER BY Timestamp DESC, ID DESC
                    LIMIT 1
                    """,
                    (company_id, tag_name)
                )

                row = cursor.fetchone()

                if not row:
                    continue

                value = row["Value"]
                timestamp = row["Timestamp"]

                try:
                    timestamp_text = str(timestamp).replace("T", " ").strip()
                    if timestamp_text.endswith("Z"):
                        timestamp_text = timestamp_text[:-1]

                    edge_time = datetime.fromisoformat(timestamp_text).timestamp()
                    age = now - edge_time
                except Exception:
                    age = timeout + 1

                # -------------------------------------------------
                # TEMPORARY ZERO
                # -------------------------------------------------
                if self._is_zero(value):
                    if not self._handle_zero(company_id, tag_name, value):
                        cursor.execute(
                            """
                            SELECT Value
                            FROM TagHistory
                            WHERE CompanyID = ?
                              AND TagName = ?
                              AND Value IS NOT NULL
                              AND Value != 0
                            ORDER BY Timestamp DESC, ID DESC
                            LIMIT 1
                            """,
                            (company_id, tag_name)
                        )

                        previous_row = cursor.fetchone()

                        if previous_row:
                            value = previous_row["Value"]
                        else:
                            # No previous real value exists, so do not
                            # manufacture a dashboard value.
                            continue

                else:
                    self._zero_memory.pop(
                        self._zero_key(company_id, tag_name),
                        None
                    )

                registers[str(register_address)] = value

                if age <= timeout and value not in (None, 0, 0.0):
                    self._clear_watchdog_zero(company_id, tag_name)

                if age > timeout:
                    print(
                        "PLC READER: EDGE DATA STALE - KEEPING LAST VALUE:",
                        tag_name,
                        "AGE:",
                        round(age, 2),
                        "VALUE:",
                        value
                    )

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

    # ========================================================
    # DIRECT MODBUS
    # ========================================================

    def _read_direct_modbus(self, ip, port, slave, register, count):
        raw_registers = read_registers(
            ip=ip,
            port=port,
            slave=slave,
            register=register,
            count=count
        )

        registers = {}

        for index, value in enumerate(raw_registers):
            address = register + index
            registers[str(address)] = value

        print()
        print("PLC READER: DIRECT MODBUS")
        print(f"PLC: {ip}:{port}")
        print(f"Slave: {slave}")
        print(f"Start Register: {register}")
        print(f"Count: {len(raw_registers)}")
        print()

        return registers

    # ========================================================
    # EXECUTE
    # ========================================================

    def execute(self, data=None):
        if data is None:
            data = {}

        ip = self._get_config("ip")
        port = self._get_config("port")
        slave = self._get_config("slave")
        register = self._get_config("register")
        count = self._get_config("count")

        required = {
            "ip": ip,
            "port": port,
            "slave": slave,
            "register": register,
            "count": count
        }

        missing = [
            name for name, value in required.items()
            if value is None or value == ""
        ]

        if missing:
            raise ValueError(
                "PLCReader configuration is incomplete. "
                f"Missing: {', '.join(missing)}"
            )

        try:
            port = int(port)
            slave = int(slave)
            register = int(register)
            count = int(count)
        except (TypeError, ValueError) as exc:
            raise ValueError(
                "PLCReader configuration contains invalid numeric values."
            ) from exc

        if port <= 0:
            raise ValueError("PLC port must be greater than zero.")
        if slave < 0:
            raise ValueError("PLC slave ID cannot be negative.")
        if register < 0:
            raise ValueError("PLC start register cannot be negative.")
        if count <= 0:
            raise ValueError("PLC register count must be greater than zero.")

        registers = self._read_edge_registers(register, count)

        if registers:
            print()
            print("PLC READER: EDGE DATA")
            print(f"Company: {self._get_config('company_id')}")
            print(f"Registers Available: {len(registers)}")
            print(f"EDGE TIMEOUT: {self._edge_timeout()} seconds")
            print(f"ZERO DEBOUNCE: {ZERO_DEBOUNCE_SECONDS} seconds")
            print()

        elif os.environ.get(
            "SCADA_DIRECT_MODBUS", "0"
        ).strip().lower() in {"1", "true", "yes", "on"}:
            registers = self._read_direct_modbus(
                ip, port, slave, register, count
            )

        else:
            print()
            print("PLC READER: WAITING FOR EDGE DATA")
            print(f"Company: {self._get_config('company_id')}")
            print(f"Register Range: {register}-{register + count - 1}")
            print()

        result = dict(data)

        result["PLC"] = {
            "ip": ip,
            "port": port,
            "slave": slave,
            "register": register,
            "count": count
        }

        result["Registers"] = registers
        result["registers"] = registers

        return result
