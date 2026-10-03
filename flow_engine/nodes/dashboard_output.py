# =====================================================
# SCADA_FLOW DASHBOARD OUTPUT NODE
# PLC-AWARE LIVE DASHBOARD OUTPUT
# =====================================================

from datetime import datetime
from socket_manager import send_dashboard_data


class DashboardOutput:

    DEFAULT_TIMEOUT_SECONDS = 10

    def __init__(self, config):
        self.config = config or {}
        self.widgets = self.config.get("widgets", [])
        try:
            self.timeout = max(0.0, float(self.config.get("timeout", self.DEFAULT_TIMEOUT_SECONDS)))
        except (TypeError, ValueError):
            self.timeout = float(self.DEFAULT_TIMEOUT_SECONDS)

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

    @staticmethod
    def _storage_type(data, widget, plc_id, tag):
        configured = str(
            widget.get("storage", widget.get("StorageType", ""))
            or ""
        ).strip().upper()
        if configured in {"LIVE", "TIME", "TRIGGER", "TRIGGER_SIGNAL"}:
            return configured

        tag_key = str(tag or "").strip().lower()
        definitions = data.get("TagDefinitions", []) or []
        for definition in definitions:
            if not isinstance(definition, dict):
                continue
            try:
                definition_plc = int(
                    definition.get("plc_id", definition.get("PLC_ID"))
                )
            except (TypeError, ValueError):
                definition_plc = None

            definition_tag = str(
                definition.get("name", definition.get("TagName", ""))
            ).strip().lower()
            storage = str(
                definition.get("storage", definition.get("StorageType", ""))
                or ""
            ).strip().upper()

            if (
                definition_plc == plc_id
                and definition_tag == tag_key
                and storage
            ):
                return storage

        return ""

    @staticmethod
    def _allowed_roles(value):
        if isinstance(value, (list, tuple, set)):
            return [str(item).strip() for item in value if str(item).strip()]
        return [item.strip() for item in str(value or "").replace(";", ",").split(",") if item.strip()]

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
            allowed_roles = self._allowed_roles(widget.get("allowed_roles"))
            output["Tags"][tag] = value
            output["TagValues"].append({
                "PLC_ID": plc_id,
                "TagName": tag,
                "Value": value,
                "Timestamp": timestamp,
                "StorageType": self._storage_type(data, widget, plc_id, tag),
                "title": widget.get("title", tag),
                "unit": widget.get("unit", ""),
                "AllowedRoles": allowed_roles,
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
                    "Timestamp": timestamp,
                    "StorageType": "",
                    "AllowedRoles": [],
                })

        try:
            send_dashboard_data(output)
            print("DASHBOARD OUTPUT SENT", output)
        except Exception as exc:
            print("DASHBOARD OUTPUT ERROR:", exc)

        data["DashboardData"] = output
        return data
