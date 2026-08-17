# =====================================================
# SCADA_FLOW REPORT OUTPUT NODE
# DYNAMIC REPORT QUERY OUTPUT
# =====================================================

from datetime import datetime

import jdatetime

from services.report_service import get_report_data, ensure_report_tables


class ReportOutput:

    def __init__(self, config=None):
        self.config = config or {}
        self.company_id = self.config.get("company_id")
        self.date_picker = self.config.get("DatePicker", "JalaliPicker")
        self.products = self.config.get("products", [])

        # Make the report database available as soon as a
        # ReportOutput node exists in the flow.
        try:
            ensure_report_tables()
        except Exception as exc:
            print("REPORT DATABASE INIT ERROR:", exc)

    @staticmethod
    def normalize_date(value, calendar):
        if not value:
            return None

        text = str(value).strip().replace("T", " ")
        text = text.translate(str.maketrans("۰۱۲۳۴۵۶۷۸۹", "0123456789"))

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
        if data is None:
            data = {}

        request = data.get("ReportRequest", {}) or {}
        company_id = request.get("CompanyID", self.company_id)
        self.company_id = company_id

        calendar = request.get("Calendar")
        if calendar not in ("Jalali", "Gregorian"):
            calendar = (
                "Jalali"
                if self.date_picker == "JalaliPicker"
                else "Gregorian"
            )

        start = self.normalize_date(request.get("Start"), calendar)
        end = self.normalize_date(request.get("End"), calendar)

        report = {
            "columns": [],
            "rows": [],
            "totals": [],
            "grand_total": 0,
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
            )

        data["ReportData"] = report

        data["ChartData"] = {
            "type": "bar",
            "calendar": calendar,
            "date_picker": self.date_picker,
            "report": report,
            "labels": [
                item["name"]
                for item in report["columns"]
            ],
            "datasets": [{
                "label": "مجموع گزارش",
                "data": report["totals"],
            }],
        }

        print()
        print("========== REPORT OUTPUT ==========")
        print("Company:", company_id)
        print("Calendar:", calendar)
        print("Start:", start)
        print("End:", end)
        print("Columns:", len(report["columns"]))
        print("Rows:", len(report["rows"]))
        print("Column totals:", report["totals"])
        print("Grand total:", report["grand_total"])
        print("===================================")
        print()

        return data
