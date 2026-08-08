```python
# =====================================================
# SCADA_FLOW PLC COMMUNICATION
# MODBUS TCP REGISTER READER
#
# IMPORTANT:
# PLC configuration comes from flow.json / PLCReader.
# This file contains NO hard-coded PLC configuration.
# =====================================================

from pymodbus.client.sync import ModbusTcpClient


# =====================================================
# MODBUS TCP READ
# =====================================================

def read_registers(
    ip,
    port=502,
    slave=1,
    start=0,
    count=1,
    timeout=3
):

    client = ModbusTcpClient(
        host=ip,
        port=port,
        timeout=timeout
    )

    try:

        if not client.connect():

            print(
                "PLC CONNECTION FAILED:",
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
                "MODBUS READ ERROR:",
                ip,
                "START:",
                start,
                "COUNT:",
                count
            )

            return None


        return result.registers


    except Exception as e:

        print(
            "PLC ERROR:",
            e
        )

        return None


    finally:

        client.close()
```
