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
# variable SCADA_DIRECT_MODBUS=1 for installations where the PLC
# is intentionally reachable from this server.
#
# Expected Drawflow configuration:
# {
#     "ip": "...",
#     "port": ...,
#     "slave": ...,
#     "register": ...,
#     "count": ...
# }
#
# Output:
# data["Registers"] = {
#     "100": value,
#     "101": value,
#     ...
# }
# ============================================================

import os

from plc import read_registers
from database import get_connection


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
    # EDGE HISTORIAN
    # ========================================================

    def _read_edge_registers(self, register, count):
        """
        Read the latest values received from SCADA_FLOW_EDGE.

        The register mapping remains database/flow driven. This
        method never contains fixed tag names or register values.
        """

        company_id = self._get_config("company_id")

        if company_id is None:
            return {}

        try:
            company_id = int(company_id)
            start = int(register)
            end = start + int(count) - 1
        except (TypeError, ValueError):
            return {}

        conn = None
        cursor = None

        try:
            conn = get_connection()
            cursor = conn.cursor()

            cursor.execute(
                """
                SELECT
                    t.RegisterAddress,
                    h.Value
                FROM Tags t
                INNER JOIN TagHistory h
                    ON h.CompanyID = t.CompanyID
                   AND h.TagName = t.TagName
                WHERE t.CompanyID = ?
                  AND t.RegisterAddress BETWEEN ? AND ?
                  AND h.ID = (
                      SELECT h2.ID
                      FROM TagHistory h2
                      WHERE h2.CompanyID = h.CompanyID
                        AND h2.TagName = h.TagName
                      ORDER BY h2.Timestamp DESC, h2.ID DESC
                      LIMIT 1
                  )
                ORDER BY t.RegisterAddress
                """,
                (
                    company_id,
                    start,
                    end,
                )
            )

            rows = cursor.fetchall()
            registers = {}

            for row in rows:

                address = row[0]
                value = row[1]

                if address is None:
                    continue

                registers[str(int(address))] = value

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
        # to reach 192.168.x.x customer PLCs. Direct Modbus is an
        # explicit opt-in for installations where it is required.

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
