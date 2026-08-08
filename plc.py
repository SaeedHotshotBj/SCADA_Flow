```python
from pymodbus.client.sync import ModbusTcpClient


# =====================================================
# MODBUS TCP READ
# =====================================================

def read_registers(
    ip,
    port,
    slave,
    start,
    count,
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
```
