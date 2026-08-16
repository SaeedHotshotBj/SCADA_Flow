# ============================================================
# SCADA_FLOW
# PLC READER NODE
#
# The SCADA_FLOW server normally receives live PLC values from
# SCADA_FLOW_EDGE. The VPS must not attempt to connect directly
# to a customer's private PLC network.
#
# Edge historian data is therefore the normal server-side source.
# Direct Modbus can be explicitly enabled with the environment
# variable SCADA_DIRECT_MODBUS=1 for installations where it is
# intentionally required.
#
# Register/tag definitions are read from the company's Drawflow
# TagMapper configuration. No tag names or register values are
# hard-coded in this node.
# ============================================================

import json
import os
from datetime import datetime

from plc import read_registers
from database import get_connection


# Edge is considered offline when no fresh historian sample has
# arrived for this many seconds.
EDGE_OFFLINE_TIMEOUT = 2


class PLCReader:

    def __init__(self, config=None, *args, **kwargs):

        self.config = config or {}

    # ========================================================
    # CONFIG
    # ========================================================

    def _get_config(self, key, default=None):

        value = self.config.get(key)

        if value is None:
            return default

        return value

    # ========================================================
    # DRAWFLOW TAG MAPPINGS
    # ========================================================

    def _get_edge_mappings(self):
        """
        Load the register -> tag definitions from the company's
        saved Drawflow. This keeps the editor as the source of
        configuration and avoids depending on old/demo Tags rows.
        """

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
                flow
                .get("drawflow", {})
                .get("Home", {})
                .get("data", {})
            )

            mappings = []

            for node in nodes.values():

                if node.get("name") != "TagMapper":
                    continue

                node_data = node.get(
                    "data",
                    {}
                )

                mapper_config = node_data.get(
                    "config",
                    node_data
                )

                node_mappings = mapper_config.get(
                    "mappings",
                    []
                )

                if not isinstance(node_mappings, list):
                    continue

                for mapping in node_mappings:

                    if not isinstance(mapping, dict):
                        continue

                    register = mapping.get("register")
                    name = str(
                        mapping.get("name", "")
                    ).strip()

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

            # Remove duplicate register/name definitions.
            result = []
            seen = set()

            for mapping in mappings:

                key = (
                    mapping["register"],
                    mapping["name"]
                )

                if key in seen:
                    continue

                seen.add(key)
                result.append(mapping)

            return result

        except Exception as exc:

            print(
                "EDGE MAPPING LOAD ERROR:",
                exc
            )

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
    # EDGE HISTORIAN
    # ========================================================

    def _is_fresh_edge_timestamp(self, timestamp):
        """Return True only when the latest Edge sample is <= 2 seconds old."""

        if timestamp is None:
            return False

        if isinstance(timestamp, datetime):
            sample_time = timestamp
        else:
            text = str(timestamp).strip().replace("T", " ")
            sample_time = None

            for fmt in (
                "%Y-%m-%d %H:%M:%S.%f",
                "%Y-%m-%d %H:%M:%S",
            ):
                try:
                    sample_time = datetime.strptime(text, fmt)
                    break
                except ValueError:
                    continue

            if sample_time is None:
                return False

        age = (datetime.now() - sample_time).total_seconds()

        return age <= EDGE_OFFLINE_TIMEOUT

    def _read_edge_registers(self, register, count):
        """
        Read the latest values received from SCADA_FLOW_EDGE.

        The Drawflow TagMapper supplies the register -> tag mapping.
        The latest value for each configured tag is then returned
        using the absolute register address expected by TagMapper.

        If the latest Edge historian sample is older than the
        configured offline timeout, return zero for that tag. This
        prevents the server from repeatedly reusing the last PLC
        value while the Edge is disconnected.
        """

        company_id = self._get_config("company_id")

        try:
            company_id = int(company_id)
            start = int(register)
            end = start + int(count) - 1
        except (TypeError, ValueError):
            return {}

        mappings = self._get_edge_mappings()

        if not mappings:
            return {}

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
                    (
                        company_id,
                        tag_name,
                    )
                )

                row = cursor.fetchone()

                if not row:
                    continue

                # The Edge has stopped sending. Do not reuse the
                # previous PLC value; expose zero to the existing
                # flow so SQLWriter can store it normally.
                if not self._is_fresh_edge_timestamp(row["Timestamp"]):
                    registers[str(register_address)] = 0
                else:
                    registers[str(register_address)] = row["Value"]

            return registers

        except Exception as exc:

            print(
                "EDGE HISTORIAN READ ERROR:",
                exc
            )

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

    def _read_direct_modbus(
        self,
        ip,
        port,
        slave,
        register,
        count
    ):

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
        print(f"Count: {count}")
        print(f"Registers Read: {len(raw_registers)}")
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
            name
            for name, value in required.items()
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
            raise ValueError(
                "PLC register count must be greater than zero."
            )

        # ----------------------------------------------------
        # SERVER DEFAULT: EDGE DATA
        # ----------------------------------------------------
        # The VPS should not generate connection errors by trying
        # to reach customer PLCs. Direct Modbus is explicit opt-in.

        registers = self._read_edge_registers(
            register,
            count
        )

        if registers:

            print()
            print("PLC READER: EDGE DATA")
            print(
                f"Company: {self._get_config('company_id')}"
            )
            print(
                f"Registers Available: {len(registers)}"
            )
            print()

        elif os.environ.get(
            "SCADA_DIRECT_MODBUS",
            "0"
        ).strip().lower() in {
            "1",
            "true",
            "yes",
            "on"
        }:

            registers = self._read_direct_modbus(
                ip,
                port,
                slave,
                register,
                count
            )

        else:

            # No edge value is currently available. This is a
            # normal transient state, not a PLC connection error.
            print()
            print("PLC READER: WAITING FOR EDGE DATA")
            print(
                f"Company: {self._get_config('company_id')}"
            )
            print(
                f"Register Range: {register}-{register + count - 1}"
            )
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
