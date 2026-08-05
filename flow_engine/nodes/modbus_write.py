from pymodbus.client.sync import ModbusTcpClient





class ModbusWrite:



    def __init__(self, config):

        self.config = config






    def execute(

        self,

        data=None

    ):



        print(

            "Modbus Write..."

        )






        ip = self.config.get(

            "ip"

        )



        port = self.config.get(

            "port",

            502

        )



        slave = self.config.get(

            "slave",

            1

        )



        register = self.config.get(

            "register"

        )



        value = self.config.get(

            "value"

        )






        if register is None:


            print(

                "Register not configured"

            )


            return data






        try:



            client = ModbusTcpClient(

                ip,

                port=port

            )





            if client.connect():



                result = client.write_register(

                    register,

                    value,

                    unit=slave

                )




                client.close()





                if result.isError():

                    print(

                        "PLC Write Error"

                    )



                else:


                    print(

                        "PLC Write Successful"

                    )




            else:


                print(

                    "PLC Connection Failed"

                )





        except Exception as e:



            print(

                "Modbus Error:",

                e

            )







        return data