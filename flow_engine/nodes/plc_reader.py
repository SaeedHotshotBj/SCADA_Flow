# =====================================================
# SCADA_FLOW PLC READER NODE
# MODBUS REGISTER READER
# =====================================================


from plc import read_registers






class PLCReader:



    def __init__(self, config):


        self.config = config or {}



        self.ip = self.config.get(

            "ip",

            "127.0.0.1"

        )


        self.port = self.config.get(

            "port",

            502

        )


        self.slave = self.config.get(

            "slave",

            1

        )


        self.register = self.config.get(

            "register",

            0

        )


        self.count = self.config.get(

            "count",

            20

        )






    # =====================================================
    # EXECUTE
    # =====================================================


    def execute(self, data=None):


        if data is None:


            data = {}






        try:



            values = read_registers(

                self.ip,

                self.port,

                self.slave,

                self.register,

                self.count

            )





            registers = {}





            for index, value in enumerate(values):



                address = self.register + index



                registers[str(address)] = value






            data["Registers"] = registers



            data["PLC_Online"] = True






            print()

            print(
                "PLC READER:"
            )

            print(
                registers
            )

            print()



        except Exception as e:



            print(

                "PLC READ ERROR:",

                e

            )



            data["Registers"] = {}


            data["PLC_Online"] = False






        return data