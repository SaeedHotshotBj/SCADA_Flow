# =====================================================
# SCADA_FLOW REPORT OUTPUT NODE
# Flow-defined report query and field-level role filtering
# =====================================================

from datetime import datetime

import jdatetime

from services.report_service import get_report_data, ensure_report_tables

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

    def execute(self, data=None):
        data = data or {}
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
