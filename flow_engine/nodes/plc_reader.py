# =====================================================
# SCADA_FLOW PLC READER NODE
# MODBUS REGISTER READER
# =====================================================

from plc import read_registers


class PLCReader:

    def __init__(self, config):

        self.config = config or {}

    # =================================================
    # EXECUTE
    # =================================================

    def execute(self, data=None):

        if data is None:
            data = {}

        try:

            ip = self.config["ip"]
            port = self.config["port"]
            slave = self.config["slave"]
            register = self.config["register"]
            count = self.config["count"]

            values = read_registers(
                ip,
                port,
                slave,
                register,
                count
            )

            if values is None:

                data["Registers"] = {}
                data["PLC_Online"] = False

                return data

            registers = {}

            for index, value in enumerate(values):

                address = register + index

                registers[str(address)] = value

            data["Registers"] = registers
            data["PLC_Online"] = True

            return data

        except KeyError as e:

            print(
                "PLC READER CONFIG ERROR:",
                e
            )

            data["Registers"] = {}
            data["PLC_Online"] = False

            return data

        except Exception as e:

            print(
                "PLC READ ERROR:",
                e
            )

            data["Registers"] = {}
            data["PLC_Online"] = False

            return data