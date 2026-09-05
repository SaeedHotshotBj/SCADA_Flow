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


class Pulse:
    """Generate a periodic pulse and optionally queue it for PLC writing.

    interval: time in seconds between the start of consecutive pulses.
    pulse_width: time in seconds that the pulse remains ON.
    plc_id/register: optional target for the Edge-side Modbus write.
    """

    def __init__(self, config=None):
        self.config = config or {}
        self._started_at = time.monotonic()
        self._last_write_value = object()
        _debug_log(
            "Pulse INIT | company_id={} | plc_id={} | register={} | "
            "interval={} | pulse_width={}".format(
                self.config.get("company_id"),
                self.config.get("plc_id"),
                self.config.get("register"),
                self.config.get("interval"),
                self.config.get("pulse_width"),
            )
        )

    def _number(self, key, default=None):
        value = self.config.get(key, default)
        if value in (None, ""):
            return default
        try:
            return float(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"Pulse {key} must be a number.") from exc

    def _int_value(self, key, default=None):
        value = self.config.get(key, default)
        if value in (None, ""):
            return default
        try:
            return int(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"Pulse {key} must be an integer.") from exc

    def _queue_plc_write(self, pulse):
        plc_id = self._int_value("plc_id")
        register = self._int_value("register")

        # Pulse remains usable as a pure pulse generator when no PLC target
        # is configured.
        if plc_id is None and register is None:
            return None

        if plc_id is None or plc_id <= 0:
            raise ValueError("Pulse PLC ID must be greater than zero.")
        if register is None or not 0 <= register <= 65535:
            raise ValueError("Pulse register must be between 0 and 65535.")

        _debug_log(
            "Pulse PLC WRITE | company_id={} | plc_id={} | register={} | "
            "value={} | previous_value={}".format(
                self.config.get("company_id"),
                plc_id,
                register,
                pulse,
                repr(self._last_write_value),
            )
        )

        if pulse == self._last_write_value:
            return None

        try:
            ensure_write_table()
            command_id = create_write_command(
                self.config.get("company_id"),
                plc_id,
                register,
                pulse,
            )
        except Exception as exc:
            _debug_log(
                "Pulse QUEUE ERROR | company_id={} | plc_id={} | "
                "register={} | value={} | error={}".format(
                    self.config.get("company_id"),
                    plc_id,
                    register,
                    pulse,
                    repr(exc),
                )
            )
            raise

        self._last_write_value = pulse
        _debug_log(
            "Pulse COMMAND QUEUED | command_id={} | company_id={} | "
            "plc_id={} | register={} | value={}".format(
                command_id,
                self.config.get("company_id"),
                plc_id,
                register,
                pulse,
            )
        )
        print(
            "PLC WRITE COMMAND QUEUED:",
            "CommandID:", command_id,
            "PLC_ID:", plc_id,
            "REGISTER:", register,
            "VALUE:", pulse,
        )

        return command_id

    def execute(self, data=None):
        if data is None:
            data = {}

        interval = self._number("interval", 1)
        pulse_width = self._number("pulse_width", 0.1)

        if interval is None or interval <= 0:
            raise ValueError("Pulse interval must be greater than zero.")
        if pulse_width is None or pulse_width <= 0:
            raise ValueError("Pulse width must be greater than zero.")
        if pulse_width > interval:
            raise ValueError("Pulse width cannot be greater than the interval.")

        # The interval is the actual period: each new pulse starts exactly
        # once per interval. No other node controls or overrides this timing.
        elapsed = time.monotonic() - self._started_at
        phase = elapsed % interval
        pulse = 1 if phase < pulse_width else 0

        command_id = self._queue_plc_write(pulse)
        if command_id is not None:
            data["PLCWriteCommandID"] = command_id

        data["Pulse"] = pulse
        data["PulseValue"] = pulse
        data["Value"] = pulse
        data["PLCWriteValue"] = pulse

        tags = data.get("Tags")
        if not isinstance(tags, dict):
            tags = {}
        tags["Pulse"] = pulse
        data["Tags"] = tags

        return data
