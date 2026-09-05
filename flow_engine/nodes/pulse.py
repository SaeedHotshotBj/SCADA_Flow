import json
import os
import time

from services.plc_write_service import (
    ensure_write_table,
    create_write_command,
)
from database import get_company_flow


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
    """Generate a periodic pulse using the current Flow node configuration."""

    _CONFIG_REFRESH_INTERVAL = 1.0

    def __init__(self, config=None):
        self.config = config or {}
        self._started_at = time.monotonic()
        self._last_write_value = object()
        self._last_config_refresh = 0.0
        _debug_log(
            "Pulse INIT | company_id={} | node_id={} | plc_id={} | register={} | "
            "interval={} | pulse_width={}".format(
                self.config.get("company_id"),
                self.config.get("_node_id"),
                self.config.get("plc_id"),
                self.config.get("register"),
                self.config.get("interval"),
                self.config.get("pulse_width"),
            )
        )

    def _refresh_flow_config(self):
        """Reload this exact Pulse node from the latest saved company Flow.

        This keeps the running FlowRunner instance aligned with edits made in
        the Drawflow editor without introducing hardcoded Pulse settings.
        """
        now = time.monotonic()
        if now - self._last_config_refresh < self._CONFIG_REFRESH_INTERVAL:
            return

        self._last_config_refresh = now

        company_id = self.config.get("company_id")
        node_id = self.config.get("_node_id")
        if company_id is None or node_id is None:
            return

        try:
            flow_json = get_company_flow(int(company_id))
            if not flow_json:
                return

            flow = json.loads(flow_json) if isinstance(flow_json, str) else flow_json
            nodes = (
                flow.get("drawflow", {})
                .get("Home", {})
                .get("data", {})
            )
            node = nodes.get(str(node_id))
            if not isinstance(node, dict) or node.get("name") != "Pulse":
                return

            fresh_data = node.get("data", {})
            if not isinstance(fresh_data, dict):
                return

            previous = {
                key: self.config.get(key)
                for key in ("interval", "pulse_width", "plc_id", "register")
            }

            for key in ("interval", "pulse_width", "plc_id", "register"):
                if key in fresh_data:
                    self.config[key] = fresh_data[key]

            current = {
                key: self.config.get(key)
                for key in ("interval", "pulse_width", "plc_id", "register")
            }

            if current != previous:
                # Restart the timing phase when Flow configuration changes so
                # the new settings take effect immediately and deterministically.
                self._started_at = time.monotonic()
                self._last_write_value = object()
                _debug_log(
                    "Pulse CONFIG RELOADED | company_id={} | node_id={} | "
                    "old={} | new={}".format(
                        company_id,
                        node_id,
                        previous,
                        current,
                    )
                )
                print(
                    "PULSE CONFIG RELOADED:",
                    "CompanyID:", company_id,
                    "NodeID:", node_id,
                    current,
                )

        except Exception as exc:
            _debug_log(
                "Pulse CONFIG RELOAD ERROR | company_id={} | node_id={} | error={}".format(
                    company_id,
                    node_id,
                    repr(exc),
                )
            )

    def _number(self, key):
        value = self.config.get(key)
        if value in (None, ""):
            raise ValueError(f"Pulse {key} must be configured in the Flow.")
        try:
            return float(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"Pulse {key} must be a number.") from exc

    def _int_value(self, key):
        value = self.config.get(key)
        if value in (None, ""):
            raise ValueError(f"Pulse {key} must be configured in the Flow.")
        try:
            return int(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"Pulse {key} must be an integer.") from exc

    def _queue_plc_write(self, pulse):
        plc_id = self._int_value("plc_id")
        register = self._int_value("register")

        if plc_id <= 0:
            raise ValueError("Pulse PLC ID must be greater than zero.")
        if not 0 <= register <= 65535:
            raise ValueError("Pulse register must be between 0 and 65535.")

        _debug_log(
            "Pulse PLC WRITE | company_id={} | node_id={} | plc_id={} | register={} | "
            "value={} | previous_value={}".format(
                self.config.get("company_id"),
                self.config.get("_node_id"),
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
                "Pulse QUEUE ERROR | company_id={} | node_id={} | plc_id={} | "
                "register={} | value={} | error={}".format(
                    self.config.get("company_id"),
                    self.config.get("_node_id"),
                    plc_id,
                    register,
                    pulse,
                    repr(exc),
                )
            )
            raise

        self._last_write_value = pulse
        _debug_log(
            "Pulse COMMAND QUEUED | command_id={} | company_id={} | node_id={} | "
            "plc_id={} | register={} | value={}".format(
                command_id,
                self.config.get("company_id"),
                self.config.get("_node_id"),
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

        self._refresh_flow_config()

        # Every Pulse behavior value comes from the current Flow node config.
        interval = self._number("interval")
        pulse_width = self._number("pulse_width")

        if interval <= 0:
            raise ValueError("Pulse interval must be greater than zero.")
        if pulse_width <= 0:
            raise ValueError("Pulse width must be greater than zero.")
        if pulse_width > interval:
            raise ValueError("Pulse width cannot be greater than the interval.")

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
