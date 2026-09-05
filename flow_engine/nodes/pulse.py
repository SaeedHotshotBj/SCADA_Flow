import time


class Pulse:
    """Generate a periodic digital pulse.

    interval: time in seconds between the start of consecutive pulses.
    pulse_width: time in seconds that the pulse remains ON.
    """

    def __init__(self, config=None):
        self.config = config or {}
        self._started_at = time.monotonic()

    def _number(self, key, default=None):
        value = self.config.get(key, default)
        if value in (None, ""):
            return default
        try:
            return float(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"Pulse {key} must be a number.") from exc

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

        elapsed = time.monotonic() - self._started_at
        phase = elapsed % interval
        pulse = 1 if phase < pulse_width else 0

        data["Pulse"] = pulse
        data["PulseValue"] = pulse
        data["Value"] = pulse

        tags = data.get("Tags")
        if not isinstance(tags, dict):
            tags = {}
        tags["Pulse"] = pulse
        data["Tags"] = tags

        return data
