# =====================================================
# SCADA_FLOW REPORT OUTPUT NODE
# Flow-defined report query and production-event persistence
# =====================================================

from datetime import datetime

import jdatetime

from services.report_service import (
    ensure_report_tables,
    get_report_data,
    save_report_snapshot,
)

try:
    from flask import has_request_context, session
except Exception:
    def has_request_context():
        return False
    session = {}


class ReportOutput:
    def __init__(self, config=None):
        self.config = config or {}
        self.company_id = self.config.get("company_id")
        self.date_picker = self.config.get("DatePicker", "JalaliPicker")
        self.products = self.config.get("products", [])

    @staticmethod
    def _plc_id(value):
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def normalize_date(value, calendar):
        if not value:
            return None
        text = str(value).strip().replace("T", " ").translate(
            str.maketrans("۰۱۲۳۴۵۶۷۸۹", "0123456789")
        )
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

    def _event_products(self, data):
        products = []
        seen = set()

        def append_product(item):
            if not isinstance(item, dict):
                return
            tag = str(item.get("tag", item.get("name", ""))).strip()
            name = str(item.get("name", tag)).strip() or tag
            if not tag:
                return
            plc_id = self._plc_id(item.get("plc_id", item.get("PLC_ID")))
            key = (tag.lower(), plc_id)
            if key in seen:
                return
            seen.add(key)
            products.append(item)

        for item in self.products:
            append_product(item)

        for item in data.get("ReportCalculations", []) or []:
            if not isinstance(item, dict):
                continue
            append_product({
                "name": str(item.get("name", item.get("tag", ""))).strip(),
                "tag": str(item.get("tag", item.get("name", ""))).strip(),
                "unit": str(item.get("unit", "")).strip(),
                "allowed_roles": item.get("allowed_roles", ""),
                "source": "management_calculation",
            })

        return products

    def _execute_production_event(self, data, event):
        company_id = data.get("CompanyID", self.company_id)
        tags = dict(data.get("Tags", {}) or {})
        plc_id = self._plc_id(data.get("PLC_ID", event.get("PLC_ID")))
        products = self._event_products(data)
        if company_id is None or plc_id is None or not products:
            data["Report_Written"] = 0
            return data

        report_id = save_report_snapshot(
            company_id,
            tags,
            products,
            timestamp=event.get("timestamp"),
            trigger_tag=event.get("trigger_tag") or (
                f"__TRIGGER_REGISTER_{event.get('register')}"
                if event.get("register") is not None else None
            ),
            trigger_register=event.get("register"),
            trigger_value=event.get("trigger_value"),
            plc_id=plc_id,
            trigger_edge=event.get("edge"),
            start_timestamp=event.get("start_timestamp"),
            end_timestamp=event.get("end_timestamp"),
            duration_seconds=event.get("duration_seconds", 0),
            start_complete=event.get("start_complete", 1),
            trigger_event_id=event.get("event_id"),
            report_node_id=str(data.get("_CurrentReportNodeID", self.config.get("node_id", ""))) or None,
        )
        data["ReportID"] = report_id
        data["Report_Written"] = 1 if report_id is not None else 0
        return data

    def execute(self, data=None):
        data = data or {}

        production_event = data.get("ProductionEvent")
        if isinstance(production_event, dict):
            return self._execute_production_event(data, production_event)

        request = data.get("ReportRequest", {}) or {}
        if not request:
            data["Report_Written"] = 0
            return data

        company_id = request.get("CompanyID", self.company_id)
        self.company_id = company_id
        calendar = request.get("Calendar") or (
            "Jalali" if self.date_picker == "JalaliPicker" else "Gregorian"
        )
        start = self.normalize_date(request.get("Start"), calendar)
        end = self.normalize_date(request.get("End"), calendar)
        plc_id = self._plc_id(request.get("PLC_ID", request.get("plc_id")))

        role = request.get("Role")
        if role is None and has_request_context():
            role = session.get("role")

        ensure_report_tables()
        report = {
            "columns": [],
            "rows": [],
            "totals": [],
            "grand_total": 0.0,
        }
        if (
            company_id is not None
            and start is not None
            and end is not None
            and end >= start
        ):
            report = get_report_data(
                company_id,
                start,
                end,
                plc_id=plc_id,
                user_role=role,
            )

        data["ReportData"] = report
        data["ChartData"] = {
            "type": "bar",
            "calendar": calendar,
            "date_picker": self.date_picker,
            "PLC_ID": plc_id,
            "report": report,
            "labels": [
                item.get("name", item.get("tag", ""))
                for item in report.get("columns", [])
            ],
            "datasets": [
                {
                    "label": "مجموع گزارش",
                    "data": report.get("totals", []),
                }
            ],
        }
        return data
