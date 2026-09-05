import os
import time

from services.plc_write_service import (
    ensure_write_table,
    create_write_command,
)


_DEBUG_LOG = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
    "static",
    "plc_write_debug.log",
)


def _debug_log(message):
    try:
        os.makedirs(os.path.dirname(_DEBUG_LOG), exist_ok=True)
        with open(_DEBUG_LOG, "a", encoding="utf-8") as file:
            file.write(
                f"{time.strftime('%Y-%m-%d %H:%M:%S')} | {message}\n"
            )
    except Exception as exc:
        print("PLC WRITE DEBUG LOG ERROR:", repr(exc))


class PLCWrite:
    """Queue a value for an Edge-side Modbus holding-register write.

    The actual Modbus connection is handled by SCADA_FLOW_EDGE. This node only
    creates a server-side write command, so the VPS never needs direct access
    to the PLC network.
    """

    def __init__(self, config=None):
        self.config = config or {}
        self._last_value = object()
        _debug_log(
            "PLCWrite INIT | company_id={} | plc_id={} | register={}".format(
                self.config.get("company_id"),
                self.config.get("plc_id"),
                self.config.get("register"),
            )
        )

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
            _debug_log(
                "PLCWrite ERROR | invalid PLC ID | value={} | data={}".format(
                    plc_id, repr(data)
                )
            )
            raise ValueError("PLCWrite PLC ID must be greater than zero.")
        if register is None or not 0 <= register <= 65535:
            _debug_log(
                "PLCWrite ERROR | invalid register | register={} | data={}".format(
                    register, repr(data)
                )
            )
            raise ValueError("PLCWrite register must be between 0 and 65535.")

        value = data.get("Value")
        if value is None:
            value = data.get("PulseValue")
        if value is None:
            value = data.get("PLCWriteValue")

        _debug_log(
            "PLCWrite EXECUTE | company_id={} | plc_id={} | register={} | "
            "incoming_value={} | previous_value={} | data_keys={}".format(
                self.config.get("company_id"),
                plc_id,
                register,
                repr(value),
                repr(self._last_value),
                sorted(str(key) for key in data.keys()),
            )
        )

        if value is None:
            _debug_log("PLCWrite ERROR | incoming Value is missing")
            raise ValueError("PLCWrite requires an incoming Value.")

        try:
            value = int(value)
        except (TypeError, ValueError) as exc:
            _debug_log(
                "PLCWrite ERROR | incoming Value is not integer | value={}".format(
                    repr(value)
                )
            )
            raise ValueError("PLCWrite incoming Value must be an integer.") from exc

        if not 0 <= value <= 65535:
            _debug_log(
                "PLCWrite ERROR | value out of range | value={}".format(value)
            )
            raise ValueError("PLCWrite value must be between 0 and 65535.")

        # Only queue a new command when the value changes. This is important
        # for periodic Pulse nodes: a 5-second pulse should create only the
        # 1 -> 0 transitions, not a new database command on every scan.
        if value != self._last_value:
            try:
                ensure_write_table()
                command_id = create_write_command(
                    self.config.get("company_id"),
                    plc_id,
                    register,
                    value,
                )
            except Exception as exc:
                _debug_log(
                    "PLCWrite QUEUE ERROR | company_id={} | plc_id={} | "
                    "register={} | value={} | error={}".format(
                        self.config.get("company_id"),
                        plc_id,
                        register,
                        value,
                        repr(exc),
                    )
                )
                raise

            self._last_value = value
            data["PLCWriteCommandID"] = command_id
            _debug_log(
                "PLCWrite COMMAND QUEUED | command_id={} | company_id={} | "
                "plc_id={} | register={} | value={}".format(
                    command_id,
                    self.config.get("company_id"),
                    plc_id,
                    register,
                    value,
                )
            )
            print(
                "PLC WRITE COMMAND QUEUED:",
                "CommandID:", command_id,
                "PLC_ID:", plc_id,
                "REGISTER:", register,
                "VALUE:", value,
            )
        else:
            _debug_log(
                "PLCWrite NO CHANGE | plc_id={} | register={} | value={}".format(
                    plc_id, register, value
                )
            )

        data["PLCWriteValue"] = value
        return data
