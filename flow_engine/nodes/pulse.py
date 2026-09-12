"""Flow-native Pulse node.

The server runtime validates and carries the Pulse configuration through the
Drawflow graph. Physical Modbus writes are deliberately performed by the Edge
runtime, where the PLC is reachable.
"""


class Pulse:
    def __init__(self, config=None):
        self.config = config or {}

    @staticmethod
    def _u16(value, field):
        try:
            value = int(value)
        except (TypeError, ValueError):
            raise ValueError(f"{field} must be an integer")
        if not 0 <= value <= 65535:
            raise ValueError(f"{field} must be between 0 and 65535")
        return value

    @staticmethod
    def _positive(value, field):
        try:
            value = float(value)
        except (TypeError, ValueError):
            raise ValueError(f"{field} must be a number")
        if value <= 0:
            raise ValueError(f"{field} must be greater than zero")
        return value

    def execute(self, data=None):
        payload = data if isinstance(data, dict) else {}
        enabled = self.config.get("enabled", True)
        if isinstance(enabled, str):
            enabled = enabled.strip().lower() not in {"0", "false", "no", "off"}
        if not enabled:
            return payload

        plc_id = self._u16(self.config.get("plc_id"), "PLC ID")
        register = self._u16(self.config.get("register"), "Register")
        bandwidth = self._u16(self.config.get("bandwidth", 1), "Pulse Value")
        width = self._positive(self.config.get("pulse_width"), "Pulse Width (ms)")
        interval = self._positive(self.config.get("interval"), "Interval (ms)")
        if interval <= width:
            raise ValueError("Interval must be greater than Pulse Width")

        payload["PulseCommand"] = {
            "PLC_ID": plc_id,
            "Register": register,
            "Value": bandwidth,
            "PulseWidthMs": width,
            "IntervalMs": interval,
            "Enabled": True,
        }
        return payload
