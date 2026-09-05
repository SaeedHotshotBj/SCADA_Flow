# =====================================================
# SCADA_FLOW REPORT OUTPUT NODE
# PLC-AWARE REPORT QUERY + TRIGGER SNAPSHOT OUTPUT
# =====================================================

import json
from datetime import datetime
import jdatetime

from database import get_company_flow, get_connection, get_latest_tag_values
from services.report_service import get_report_data, ensure_report_tables, save_report_snapshot


class ReportOutput:
    # Compatibility hook used by the management-context wiring layer.
    # Keep it on the class so older runtime patch code can discover it before
    # an instance is created, while each instance also gets its own mapping.
    _management_context_tags = {}

    def __init__(self, config=None):
        self.config = config or {}
        self.company_id = self.config.get("company_id")
        self.date_picker = self.config.get("DatePicker", "JalaliPicker")
        self.products = self.config.get("products", [])
        self._last_report_event_key = None
        self._management_context_tags = {}

    @staticmethod
    def _plc_id(value):
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    def _load_flow(self):
        flow = get_company_flow(self.company_id) if self.company_id is not None else {}
        if isinstance(flow, str):
            flow = json.loads(flow)
        return flow or {}

    def _load_definitions(self):
        definitions = {}
        for node in self._load_flow().get("drawflow", {}).get("Home", {}).get("data", {}).values():
            if not isinstance(node, dict) or node.get("name") != "TagMapper":
                continue
            mappings = node.get("data", {}).get("mappings", [])
            if isinstance(mappings, list):
                for item in mappings:
                    if isinstance(item, dict) and str(item.get("name", "")).strip():
                        definitions[str(item["name"]).strip().lower()] = item
        return definitions

    @staticmethod
    def normalize_date(value, calendar):
        if not value:
            return None
        text = str(value).strip().replace("T", " ").translate(str.maketrans("۰۱۲۳۴۵۶۷۸۹", "0123456789"))
        if calendar == "Jalali":
            text = text.replace("-", "/")
            for fmt in ("%Y/%m/%d %H:%M:%S", "%Y/%m/%d %H:%M"):
                try:
                    return jdatetime.datetime.strptime(text, fmt).togregorian()
                except Exception:
                    pass
            return None
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
            try:
                return datetime.strptime(text, fmt)
            except Exception:
                pass
        return None

    def _report_tag_definitions(self):
        definitions = self._load_definitions()
        result = []
        for item in self.products:
            if not isinstance(item, dict):
                continue
            tag = str(item.get("tag", "")).strip()
            if tag:
                result.append((item, definitions.get(tag.lower(), {})))
        return result

    def _latest_report_timestamp(self, plc_id=None):
        conn = get_connection()
        try:
            sql = "SELECT Timestamp FROM ReportHistory WHERE CompanyID=?"
            params = [int(self.company_id)]
            if plc_id is not None:
                sql += " AND PLC_ID=?"
                params.append(int(plc_id))
            sql += " ORDER BY ReportID DESC LIMIT 1"
            row = conn.execute(sql, params).fetchone()
            return row["Timestamp"] if row else None
        finally:
            conn.close()

    def _latest_trigger_event(self, data):
        events = data.get("EdgeTriggerEvents", []) if isinstance(data, dict) else []
        if isinstance(events, list):
            for event in events:
                if not isinstance(event, dict):
                    continue
                event_plc = self._plc_id(event.get("PLC_ID", data.get("PLC_ID")))
                for name, value in (event.get("tags", {}) or {}).items():
                    for product, definition in self._report_tag_definitions():
                        if str(product.get("tag", "")).strip().lower() == str(name).strip().lower() and str(definition.get("storage", "TIME")).upper() == "TRIGGER":
                            return {"plc_id": event_plc, "tag": name, "value": value, "timestamp": event.get("timestamp"), "register": event.get("register")}
        return None

    def _runtime_tags(self, data):
        live = data.get("Tags", {}) or {}
        return dict(live) if isinstance(live, dict) else {}

    def _save_realtime_snapshot(self, data):
        event = self._latest_trigger_event(data)
        if event is None:
            return 0
        plc_id = self._plc_id(event.get("plc_id", data.get("PLC_ID")))
        tags = self._runtime_tags(data)
        if not tags:
            return 0
        timestamp = event.get("timestamp") or data.get("Timestamp") or datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        report_id = save_report_snapshot(
            self.company_id,
            tags,
            self.products,
            timestamp=str(timestamp).replace("T", " "),
            trigger_tag=event.get("tag"),
            trigger_register=event.get("register"),
            trigger_value=event.get("value"),
            plc_id=plc_id,
        )
        if report_id:
            print("REPORT SNAPSHOT SAVED:", "Company=", self.company_id, "PLC_ID=", plc_id, "ReportID=", report_id)
            return 1
        return 0

    def execute(self, data=None):
        data = data or {}
        if not data.get("ReportRequest"):
            try:
                data["Report_Written"] = self._save_realtime_snapshot(data)
            except Exception as exc:
                print("REPORT SNAPSHOT ERROR:", exc)
                data["Report_Written"] = 0
            return data

        request = data.get("ReportRequest", {}) or {}
        company_id = request.get("CompanyID", self.company_id)
        self.company_id = company_id
        calendar = request.get("Calendar") or ("Jalali" if self.date_picker == "JalaliPicker" else "Gregorian")
        start = self.normalize_date(request.get("Start"), calendar)
        end = self.normalize_date(request.get("End"), calendar)
        plc_id = self._plc_id(request.get("PLC_ID", request.get("plc_id")))

        report = {"columns": self.products, "rows": [], "totals": [0.0 for _ in self.products], "grand_total": 0.0}
        if company_id is not None and start is not None and end is not None and end >= start:
            report = get_report_data(company_id, start, end, plc_id=plc_id)

        data["ReportData"] = report
        data["ChartData"] = {
            "type": "bar",
            "calendar": calendar,
            "date_picker": self.date_picker,
            "PLC_ID": plc_id,
            "report": report,
            "labels": [item.get("name", item.get("tag", "")) for item in report.get("columns", [])],
            "datasets": [{"label": "مجموع گزارش", "data": report.get("totals", [])}],
        }
        return data
