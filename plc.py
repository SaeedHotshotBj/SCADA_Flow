# ============================================================
# SCADA_FLOW
# PLC / MODBUS TCP COMMUNICATION
#
# No PLC configuration is stored here.
#
# IP, port, slave, register and count are supplied by
# PLCReader.
# ============================================================

try:

    from pymodbus.client import ModbusTcpClient

except ImportError:

    from pymodbus.client.sync import ModbusTcpClient


def read_registers(
    ip,
    port,
    slave,
    register,
    count,
    timeout=3
):

    """
    Read holding registers from PLC.

    All PLC configuration comes from the Flow Editor.
    """

    client = ModbusTcpClient(
        str(ip),
        port=int(port),
        timeout=float(timeout)
    )

    try:

        if not client.connect():

            raise ConnectionError(
                f"Unable to connect to PLC {ip}:{port}"
            )

        # ----------------------------------------------------
        # New PyModbus
        # ----------------------------------------------------

        try:

            result = client.read_holding_registers(
                address=int(register),
                count=int(count),
                slave=int(slave)
            )

        # ----------------------------------------------------
        # Old PyModbus
        # ----------------------------------------------------

        except TypeError:

            result = client.read_holding_registers(
                address=int(register),
                count=int(count),
                unit=int(slave)
            )

        # ----------------------------------------------------
        # Check Modbus response
        # ----------------------------------------------------

        if result.isError():

            raise RuntimeError(
                f"Modbus read error: {result}"
            )

        return list(
            result.registers
        )

    finally:

        client.close()