# =====================================================
# SCADA_FLOW DASHBOARD OUTPUT NODE
# PLC-AWARE LIVE DASHBOARD OUTPUT
# =====================================================

from datetime import datetime
from socket_manager import send_dashboard_data


class DashboardOutput:

    def __init__(self, config):
        self.config = config or {}
        self.widgets = self.config.get("widgets", [])
        raw_timeout = self.config.get("timeout")
        try:
            self.timeout = None if raw_timeout in (None, "") else max(0.0, float(raw_timeout))
        except (TypeError, ValueError) as exc:
            raise ValueError("DashboardOutput timeout must be a number.") from exc

    @staticmethod
    def _plc_id(value):
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _value(data, plc_id, tag):
        tag = str(tag or "").strip()
        pid = DashboardOutput._plc_id(plc_id)
        plc_tags = data.get("PLC_Tags", {}) or {}
        if pid is not None and tag:
            key = "%s:%s" % (pid, tag.lower())
            if key in plc_tags:
                return plc_tags[key]
        return (data.get("Tags", {}) or {}).get(tag)

    def execute(self, data=None):
        data = data or {}
        engaged_roles = data.get("EngagedRoles", [])
        company_id = data.get("CompanyID", self.config.get("company_id"))
        timestamp = data.get("Timestamp", datetime.now().isoformat())

        output = {
            "Online": True,
            "Tags": {},
            "TagValues": [],
            "Roles": engaged_roles,
            "EdgeTimeout": self.timeout,
            "CompanyID": company_id,
            "Timestamp": timestamp,
        }

        for widget in self.widgets:
            if not isinstance(widget, dict):
                continue
            tag = str(widget.get("tag", "")).strip()
            if not tag:
                continue
            plc_id = self._plc_id(widget.get("plc_id", widget.get("PLC_ID", data.get("PLC_ID"))))
            value = self._value(data, plc_id, tag)
            if value is None:
                continue
            output["Tags"][tag] = value
            output["TagValues"].append({
                "PLC_ID": plc_id,
                "TagName": tag,
                "Value": value,
                "title": widget.get("title", tag),
                "unit": widget.get("unit", ""),
            })

        machine_cards = data.get("MachineCards", [])
        if isinstance(machine_cards, list):
            output["MachineCards"] = machine_cards
            output["MachineIconLibrary"] = data.get("MachineIconLibrary", [])

        if not self.widgets:
            output["Tags"] = data.get("Tags", {}) or {}
            for tag, value in output["Tags"].items():
                output["TagValues"].append({
                    "PLC_ID": data.get("PLC_ID"),
                    "TagName": tag,
                    "Value": value,
                })

        try:
            send_dashboard_data(output)
            print("DASHBOARD OUTPUT SENT", output)
        except Exception as exc:
            print("DASHBOARD OUTPUT ERROR:", exc)

        data["DashboardData"] = output
        return data
