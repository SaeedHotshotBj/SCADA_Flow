# =====================================================
# SCADA_FLOW REPORT OUTPUT NODE
# TRIGGERED PRODUCTION REPORT
# =====================================================

from datetime import datetime
import jdatetime

from database import get_connection


class ReportOutput:

    def __init__(self, config=None):
        self.config = config or {}
        self.company_id = self.config.get("company_id")
        self.date_picker = self.config.get("DatePicker", "GregorianPicker")
        self.products = self.config.get("products", [])

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

    def _selected_products(self):
        result = []
        seen = set()
        for product in self.products:
            if not isinstance(product, dict):
                continue
            tag = str(product.get("tag", "")).strip()
            name = str(product.get("name", "")).strip()
            if not tag or not name:
                continue
            key = tag.lower()
            if key in seen:
                continue
            seen.add(key)
            result.append({
                "name": name,
                "tag": tag,
                "unit": str(product.get("unit", "")).strip()
            })
        return result

    def build_report(self, conn, products, start, end):
        if not products:
            return {"columns": [], "rows": [], "totals": [], "grand_total": 0}

        tags = [item["tag"] for item in products]
        placeholders = ",".join("?" for _ in tags)

        cursor = conn.cursor()
        cursor.execute(
            f"""
            SELECT Timestamp, TagName, Value
            FROM PLC_Data
            WHERE CompanyID = ?
              AND LOWER(TagName) IN ({placeholders})
              AND Timestamp BETWEEN ? AND ?
              AND StorageType = 'REPORT_TRIGGER'
            ORDER BY Timestamp ASC, ID ASC
            """,
            [
                self.company_id,
                *[tag.lower() for tag in tags],
                start.strftime("%Y-%m-%d %H:%M:%S"),
                end.strftime("%Y-%m-%d %H:%M:%S")
            ]
        )
        rows = cursor.fetchall()
        cursor.close()

        # One trigger creates one timestamp shared by all report tags.
        by_time = {}
        for row in rows:
            timestamp = str(row["Timestamp"])
            tag = str(row["TagName"]).lower()
            try:
                value = float(row["Value"])
            except (TypeError, ValueError):
                continue
            by_time.setdefault(timestamp, {})[tag] = value

        result_rows = []
        totals = {item["tag"].lower(): 0.0 for item in products}

        for timestamp, values in by_time.items():
            row_values = []
            row_total = 0.0
            for product in products:
                value = values.get(product["tag"].lower(), 0.0)
                row_values.append(value)
                totals[product["tag"].lower()] += value
                row_total += value

            result_rows.append({
                "timestamp": timestamp,
                "values": row_values,
                "row_total": row_total
            })

        column_totals = [
            round(totals[product["tag"].lower()], 3)
            for product in products
        ]
        grand_total = round(sum(column_totals), 3)

        return {
            "columns": [
                {"name": item["name"], "tag": item["tag"], "unit": item["unit"]}
                for item in products
            ],
            "rows": result_rows,
            "totals": column_totals,
            "grand_total": grand_total
        }

    def execute(self, data=None):
        if data is None:
            data = {}

        request = data.get("ReportRequest", {}) or {}
        company_id = request.get("CompanyID", self.company_id)
        self.company_id = company_id

        calendar = request.get("Calendar")
        if calendar not in ("Jalali", "Gregorian"):
            calendar = "Jalali" if self.date_picker == "JalaliPicker" else "Gregorian"

        start = self.normalize_date(request.get("Start"), calendar)
        end = self.normalize_date(request.get("End"), calendar)

        report = {"columns": [], "rows": [], "totals": [], "grand_total": 0}
        products = self._selected_products()

        if company_id is not None and start and end and end >= start:
            conn = get_connection()
            try:
                report = self.build_report(conn, products, start, end)
            finally:
                conn.close()

        data["ReportData"] = report
        data["ChartData"] = {
            "type": "bar",
            "calendar": calendar,
            "date_picker": self.date_picker,
            "labels": [item["name"] for item in report["columns"]],
            "datasets": [{
                "label": "مجموع تولید",
                "data": report["totals"]
            }]
        }

        print()
        print("========== REPORT OUTPUT ==========")
        print("Company:", company_id)
        print("Calendar:", calendar)
        print("Start:", start)
        print("End:", end)
        print("Products:", report["columns"])
        print("Rows:", len(report["rows"]))
        print("Column totals:", report["totals"])
        print("Grand total:", report["grand_total"])
        print("===================================")
        print()

        return data
