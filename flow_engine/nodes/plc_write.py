from services.plc_write_service import (
    ensure_write_table,
    create_write_command,
)


class PLCWrite:
    """Queue a value for an Edge-side Modbus holding-register write.

    The actual Modbus connection is handled by SCADA_FLOW_EDGE. This node only
    creates a server-side write command, so the VPS never needs direct access
    to the PLC network.
    """

    def __init__(self, config=None):
        self.config = config or {}
        self._last_value = object()

    def _int_value(self, key, default=None):
        value = self.config.get(key, default)
        if value in (None, ""):
            return default
        try:
            return int(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"PLCWrite {key} must be an integer.") from exc

    def execute(self, data=None):
        data = data if isinstance(data, dict) else {}

        plc_id = self._int_value("plc_id")
        register = self._int_value("register")

        if plc_id is None or plc_id <= 0:
            raise ValueError("PLCWrite PLC ID must be greater than zero.")
        if register is None or not 0 <= register <= 65535:
            raise ValueError("PLCWrite register must be between 0 and 65535.")

        value = data.get("Value")
        if value is None:
            value = data.get("PulseValue")
        if value is None:
            value = data.get("PLCWriteValue")

        if value is None:
            raise ValueError("PLCWrite requires an incoming Value.")

        try:
            value = int(value)
        except (TypeError, ValueError) as exc:
            raise ValueError("PLCWrite incoming Value must be an integer.") from exc

        if not 0 <= value <= 65535:
            raise ValueError("PLCWrite value must be between 0 and 65535.")

        # Only queue a new command when the value changes. This is important
        # for periodic Pulse nodes: a 5-second pulse should create only the
        # 1 -> 0 transitions, not a new database command on every scan.
        if value != self._last_value:
            ensure_write_table()
            command_id = create_write_command(
                self.config.get("company_id"),
                plc_id,
                register,
                value,
            )
            self._last_value = value
            data["PLCWriteCommandID"] = command_id
            print(
                "PLC WRITE COMMAND QUEUED:",
                "CommandID:", command_id,
                "PLC_ID:", plc_id,
                "REGISTER:", register,
                "VALUE:", value,
            )

        data["PLCWriteValue"] = value
        return data
