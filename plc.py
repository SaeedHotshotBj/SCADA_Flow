from pymodbus.client.sync import ModbusTcpClient
from config import PLC_CONFIG





# =====================================================
# MODBUS TCP READ
# =====================================================


def read_registers(

        ip,

        port=502,

        slave=1,

        start=135,

        count=24

):


    client = ModbusTcpClient(

        host=ip,

        port=port,

        timeout=PLC_CONFIG["timeout"]

    )



    try:


        connected = client.connect()



        if not connected:

            print(

                "PLC Connection Failed:",

                ip

            )

            return None





        result = client.read_holding_registers(

            address=start,

            count=count,

            unit=slave

        )





        if result.isError():


            print(

                "Modbus Read Error:",

                ip

            )


            return None





        return result.registers





    except Exception as e:


        print(

            "PLC Error:",

            e

        )


        return None





    finally:


        client.close()







# =====================================================
# TEST FUNCTION
# =====================================================


def test_plc(ip):


    print("====================")

    print(

        "TEST PLC:",

        ip

    )

    print("====================")



    data = read_registers(

        ip,

        502,

        1,

        135,

        24

    )



    print(data)



    return data