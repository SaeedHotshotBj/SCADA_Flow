# ============================================================
# SCADA_FLOW
# PLC READER NODE
#
# PLC configuration comes ONLY from Drawflow.
#
# The SCADA_FLOW server normally receives live PLC values from
# SCADA_FLOW_EDGE. When fresh edge historian data exists, use it
# first so the VPS does not try to reach a PLC on the customer's
# private LAN. Direct Modbus remains available as a fallback.
#
# Expected configuration:
#
# {
#     "ip": "...",
#     "port": ...,
#     "slave": ...,
#     "register": ...,
#     "count": ...
# }
#
# Output:
#
# data["Registers"] = {
#     "100": value,
#     "101": value,
#     ...
# }
#
# TagMapper uses these absolute register addresses.
# ============================================================

from plc import read_registers
from database import get_connection


class PLCReader:

    # ========================================================
    # INIT
    # ========================================================

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

        Register addresses are taken from the company's Tags table,
        not hard-coded here. This keeps the Drawflow configuration
        as the source of the PLC read range while the database maps
        incoming edge tag names back to absolute register addresses.
        """

        company_id = self._get_config("company_id")

        if company_id is None:
            return {}

        try:
            company_id = int(company_id)
        except (TypeError, ValueError):
            return {}

        start = int(register)
        end = start + int(count) - 1

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
    # EXECUTE
    # ========================================================

    def execute(self, data=None):

        if data is None:
            data = {}

        # ----------------------------------------------------
        # Read configuration from Drawflow
        # ----------------------------------------------------

        ip = self._get_config("ip")
        port = self._get_config("port")
        slave = self._get_config("slave")
        register = self._get_config("register")
        count = self._get_config("count")

        # ----------------------------------------------------
        # Validate configuration
        # ----------------------------------------------------

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

        # ----------------------------------------------------
        # Convert numeric configuration
        # ----------------------------------------------------

        try:
            port = int(port)
            slave = int(slave)
            register = int(register)
            count = int(count)

        except (TypeError, ValueError) as exc:
            raise ValueError(
                "PLCReader configuration contains invalid "
                "numeric values."
            ) from exc

        # ----------------------------------------------------
        # Validate numeric values
        # ----------------------------------------------------

        if port <= 0:
            raise ValueError(
                "PLC port must be greater than zero."
            )

        if slave < 0:
            raise ValueError(
                "PLC slave ID cannot be negative."
            )

        if register < 0:
            raise ValueError(
                "PLC start register cannot be negative."
            )

        if count <= 0:
            raise ValueError(
                "PLC register count must be greater than zero."
            )

        # ----------------------------------------------------
        # Prefer data received from SCADA_FLOW_EDGE.
        #
        # This is important on the VPS: the customer's PLC is on
        # a private LAN and is not directly reachable from the VPS.
        # ----------------------------------------------------

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

        else:

            # ------------------------------------------------
            # No edge historian values are available.
            # Fall back to direct Modbus using Drawflow config.
            # ------------------------------------------------

            raw_registers = read_registers(
                ip=ip,
                port=port,
                slave=slave,
                register=register,
                count=count
            )

            for index, value in enumerate(raw_registers):

                address = register + index

                registers[str(address)] = value

            print()
            print("PLC READER: DIRECT MODBUS")
            print(
                f"PLC: {ip}:{port}"
            )
            print(
                f"Slave: {slave}"
            )
            print(
                f"Start Register: {register}"
            )
            print(
                f"Count: {count}"
            )
            print(
                f"Registers Read: {len(raw_registers)}"
            )
            print()

        # ----------------------------------------------------
        # Output
        # ----------------------------------------------------

        result = dict(data)

        result["PLC"] = {
            "ip": ip,
            "port": port,
            "slave": slave,
            "register": register,
            "count": count
        }

        result["Registers"] = registers

        # Keep compatibility with any code using lowercase key.
        result["registers"] = registers

        return result
