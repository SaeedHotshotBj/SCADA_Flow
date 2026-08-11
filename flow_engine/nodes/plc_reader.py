# ============================================================
# SCADA_FLOW
# PLC READER NODE
#
# PLC configuration comes ONLY from Drawflow.
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
        # Read PLC
        # ----------------------------------------------------

        raw_registers = read_registers(
            ip=ip,
            port=port,
            slave=slave,
            register=register,
            count=count
        )

        # ----------------------------------------------------
        # Convert returned list into absolute register map
        #
        # Example:
        #
        # start = 100
        #
        # raw_registers[0] -> register 100
        # raw_registers[1] -> register 101
        # raw_registers[35] -> register 135
        # raw_registers[41] -> register 141
        # ----------------------------------------------------

        registers = {}

        for index, value in enumerate(raw_registers):

            address = register + index

            registers[str(address)] = value

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

        # ----------------------------------------------------
        # Diagnostic information
        # ----------------------------------------------------

        print()
        print("PLC READER:")
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

        return result