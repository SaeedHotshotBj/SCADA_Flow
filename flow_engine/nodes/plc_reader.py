```python
# =====================================================
# SCADA_FLOW PLC READER NODE
# MODBUS REGISTER READER
# =====================================================

from plc import read_registers


class PLCReader:

    def __init__(self, config):

        self.config = config or {}

        # ALL PLC CONFIGURATION COMES FROM flow.json

        self.ip = self.config.get("ip")
        self.port = self.config.get("port")
        self.slave = self.config.get("slave")
        self.register = self.config.get("register")
        self.count = self.config.get("count")

    # =================================================
    # EXECUTE
    # =================================================

    def execute(self, data=None):

        if data is None:
            data = {}

        # PLC configuration must come from flow.json

        if (
            self.ip is None
            or self.port is None
            or self.slave is None
            or self.register is None
            or self.count is None
        ):

            print("PLC READER: Missing PLC configuration")

            data["Registers"] = {}
            data["PLC_Online"] = False

            return data

        try:

            values = read_registers(
                self.ip,
                self.port,
                self.slave,
                self.register,
                self.count
            )

            if values is None:

                data["Registers"] = {}
                data["PLC_Online"] = False

                return data

            registers = {}

            for index, value in enumerate(values):

                address = self.register + index

                registers[str(address)] = value

            data["Registers"] = registers
            data["PLC_Online"] = True

            return data

        except Exception as e:

            print(
                "PLC READ ERROR:",
                e
            )

            data["Registers"] = {}
            data["PLC_Online"] = False

            return data
```
