# =====================================================
# SCADA_FLOW REPORT OUTPUT NODE
# TRIGGERED PRODUCTION REPORT
# =====================================================

from datetime import datetime
import jdatetime

from database import get_connection, get_company_flow


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

    def _tag_mapper_definitions(self):
        """
        Load the available report tags from the company's TagMapper node.

        ReportOutput never contains a hardcoded register/tag list. The
        TagMapper in the saved Flow is the source of truth.
        """
        definitions = {}

        if self.company_id is None:
            return definitions

        try:
            flow = get_company_flow(self.company_id)
            if not flow:
                return definitions

            nodes = (
                flow.get("drawflow", {})
                    .get("Home", {})
                    .get("data", {})
            )

            for node in nodes.values():
                if node.get("name") != "TagMapper":
                    continue

                data = node.get("data", {})
                config = data.get("config", data)
                mappings = config.get("mappings", [])

                if not isinstance(mappings, list):
                    continue

                for mapping in mappings:
                    if not isinstance(mapping, dict):
                        continue

                    tag = str(mapping.get("name", "")).strip()
                    if not tag:
                        continue

                    register = mapping.get("register")
                    try:
                        register = int(register)
                    except (TypeError, ValueError):
                        register = None

                    definitions[tag.lower()] = {
                        "tag": tag,
                        "register": register,
                        "unit": str(mapping.get("unit", "")).strip(),
                    }

                # A company's flow normally has one TagMapper. Once found,
                # all its mappings have been collected.
                if definitions:
                    break

        except Exception as e:
            print("REPORT TAGMAPPER LOAD ERROR:", e)

        return definitions

    def _selected_products(self):
        """
        Resolve ReportOutput selections against TagMapper definitions.

        A report selection is accepted only when its tag/name exists in
        TagMapper. This prevents hardcoded backend report columns and keeps
        the report tied to the Flow Designer configuration.
        """
        result = []
        seen = set()
        definitions = self._tag_mapper_definitions()

        for product in self.products:
            if not isinstance(product, dict):
                continue

            requested_tag = str(
                product.get("tag", product.get("name", ""))
            ).strip()

            if not requested_tag:
                continue

            definition = definitions.get(requested_tag.lower())

            # Also allow a numeric register in ReportOutput. The register is
            # resolved through TagMapper; it is never used as a hardcoded DB
            # column name.
            if definition is None:
                try:
                    requested_register = int(requested_tag)
                except (TypeError, ValueError):
                    requested_register = None

                if requested_register is not None:
                    for item in definitions.values():
                        if item.get("register") == requested_register:
                            definition = item
                            break

            if definition is None:
                print(
                    "REPORT TAG SKIPPED - NOT IN TAGMAPPER:",
                    requested_tag
                )
                continue

            tag = definition["tag"]
            key = tag.lower()

            if key in seen:
                continue

            seen.add(key)

            name = str(product.get("name", "")).strip()
            if not name or name.lower() == requested_tag.lower():
                name = tag

            unit = str(product.get("unit", "")).strip()
            if not unit:
                unit = definition.get("unit", "")

            result.append({
                "name": name,
                "tag": tag,
                "unit": unit,
                "register": definition.get("register")
            })

        return result

    def build_report(self, conn, products, start, end):
        if not products:
            return {
                "columns": [],
                "rows": [],
                "totals": [],
                "grand_total": 0
            }

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
        totals = {
            item["tag"].lower(): 0.0
            for item in products
        }

        for timestamp, values in by_time.items():
            row_values = []
            row_total = 0.0

            for product in products:
                value = values.get(
                    product["tag"].lower(),
                    0.0
                )

                row_values.append(value)
                totals[product["tag"].lower()] += value
                row_total += value

            result_rows.append({
                "timestamp": timestamp,
                "values": row_values,
                "row_total": row_total
            })

        column_totals = [
            round(
                totals[product["tag"].lower()],
                3
            )
            for product in products
        ]

        grand_total = round(
            sum(column_totals),
            3
        )

        return {
            "columns": [
                {
                    "name": item["name"],
                    "tag": item["tag"],
                    "unit": item["unit"],
                    "register": item.get("register")
                }
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
        company_id = request.get(
            "CompanyID",
            self.company_id
        )
        self.company_id = company_id

        calendar = request.get("Calendar")
        if calendar not in ("Jalali", "Gregorian"):
            calendar = (
                "Jalali"
                if self.date_picker == "JalaliPicker"
                else "Gregorian"
            )

        start = self.normalize_date(
            request.get("Start"),
            calendar
        )

        end = self.normalize_date(
            request.get("End"),
            calendar
        )

        report = {
            "columns": [],
            "rows": [],
            "totals": [],
            "grand_total": 0
        }

        products = self._selected_products()

        if (
            company_id is not None
            and start
            and end
            and end >= start
        ):
            conn = get_connection()
            try:
                report = self.build_report(
                    conn,
                    products,
                    start,
                    end
                )
            finally:
                conn.close()

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
        print("TagMapper definitions:", len(self._tag_mapper_definitions()))
        print("Selected products:", products)
        print("Rows:", len(report["rows"]))
        print("Column totals:", report["totals"])
        print("Grand total:", report["grand_total"])
        print("===================================")
        print()

        return data
