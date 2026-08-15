# =====================================================
# SCADA_FLOW REPORT OUTPUT NODE
# PRODUCT REPORT FROM HISTORIAN
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

    def calculate_product(self, conn, product, start, end):
        tag = str(product.get("tag", "")).strip()
        aggregation = str(product.get("aggregation", "SUM")).upper()

        if not tag:
            return 0.0

        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT Timestamp, Value
            FROM PLC_Data
            WHERE CompanyID = ?
              AND LOWER(TagName) = LOWER(?)
              AND Timestamp BETWEEN ? AND ?
            ORDER BY Timestamp ASC
            """,
            (
                self.company_id,
                tag,
                start.strftime("%Y-%m-%d %H:%M:%S"),
                end.strftime("%Y-%m-%d %H:%M:%S"),
            )
        )
        rows = cursor.fetchall()
        cursor.close()

        values = []
        for row in rows:
            try:
                values.append(float(row["Value"]))
            except (TypeError, ValueError):
                pass

        if not values:
            return 0.0
        if aggregation == "MAX":
            return max(values)
        if aggregation == "MIN":
            return min(values)
        if aggregation == "AVG":
            return sum(values) / len(values)
        if aggregation == "DELTA":
            return values[-1] - values[0]
        return sum(values)

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

        result = []

        if company_id is not None and start and end and end >= start:
            conn = get_connection()
            try:
                for product in self.products:
                    if not isinstance(product, dict):
                        continue

                    name = str(product.get("name", "")).strip()
                    if not name:
                        continue

                    value = self.calculate_product(conn, product, start, end)
                    result.append({
                        "product": name,
                        "tag": product.get("tag", ""),
                        "unit": product.get("unit", ""),
                        "aggregation": str(product.get("aggregation", "SUM")).upper(),
                        "value": round(value, 3),
                    })
            finally:
                conn.close()

        data["ReportData"] = result
        data["ChartData"] = {
            "type": "bar",
            "calendar": calendar,
            "date_picker": self.date_picker,
            "labels": [item["product"] for item in result],
            "datasets": [{
                "label": "Production",
                "data": [item["value"] for item in result],
            }]
        }

        print()
        print("========== REPORT OUTPUT ==========")
        print("Company:", company_id)
        print("Calendar:", calendar)
        print("Start:", start)
        print("End:", end)
        print("Products:", result)
        print("===================================")
        print()

        return data
