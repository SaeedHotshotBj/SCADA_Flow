# =====================================================
# SCADA_FLOW REPORT OUTPUT NODE
# DYNAMIC REPORT QUERY + TRIGGER SNAPSHOT OUTPUT
# =====================================================

import json
from datetime import datetime

import jdatetime

from database import get_company_flow, get_connection, get_latest_tag_values
from services.report_service import (
    get_report_data,
    ensure_report_tables,
    save_report_snapshot,
)


class ReportOutput:

    def __init__(self, config=None):
        self.config = config or {}
        self.company_id = self.config.get("company_id")
        self.date_picker = self.config.get("DatePicker", "JalaliPicker")
        self.products = self.config.get("products", [])
        self._flow_definition_signature = None
        self._flow_definitions = {}
        self._last_report_event_key = None

        try:
            ensure_report_tables()
        except Exception as exc:
            print("REPORT DATABASE INIT ERROR:", exc)

    def _load_flow(self):
        if self.company_id is None:
            return {}
        try:
            flow = get_company_flow(self.company_id)
            if isinstance(flow, str):
                flow = json.loads(flow)
            return flow or {}
        except Exception as exc:
            print("REPORT FLOW LOAD ERROR:", exc)
            return {}

    def _load_flow_definitions(self):
        if self.company_id is None:
            return {}

        try:
            flow = self._load_flow()
            nodes = flow.get("drawflow", {}).get("Home", {}).get("data", {})
            definitions = {}

            for node in nodes.values():
                if not isinstance(node, dict) or node.get("name") != "TagMapper":
                    continue
                mappings = node.get("data", {}).get("mappings", [])
                if not isinstance(mappings, list):
                    continue
                for mapping in mappings:
                    if not isinstance(mapping, dict):
                        continue
                    name = str(mapping.get("name", "")).strip()
                    if name:
                        definitions[name.lower()] = mapping
                break

            signature = json.dumps(
                definitions,
                sort_keys=True,
                ensure_ascii=False,
                separators=(",", ":"),
                default=str,
            )
            if signature != self._flow_definition_signature:
                self._flow_definition_signature = signature
                self._flow_definitions = definitions

            return self._flow_definitions
        except Exception as exc:
            print("REPORT FLOW DEFINITION LOAD ERROR:", exc)
            return self._flow_definitions

    def _management_context_tags(self, data):
        result = {}
        registers = data.get("Registers", {}) or {}
        if not isinstance(registers, dict):
            registers = {}

        try:
            flow = self._load_flow()
            nodes = flow.get("drawflow", {}).get("Home", {}).get("data", {})
            config = None

            for node in nodes.values():
                if not isinstance(node, dict) or node.get("name") != "ManagementPanel":
                    continue
                raw = node.get("data", {}) or {}
                config = raw.get("config", raw) or {}
                break

            if not isinstance(config, dict):
                config = {}

            def read_register(*fields):
                address = None
                for field in fields:
                    value = config.get(field)
                    if value not in (None, ""):
                        address = value
                        break
                if address in (None, ""):
                    return None

                candidates = [str(address).strip()]
                try:
                    candidates.append(str(int(float(address))))
                except (TypeError, ValueError):
                    pass

                for key in candidates:
                    if key in registers and registers[key] not in (None, ""):
                        return registers[key]
                return None

            contract = read_register(
                "contract_code_register",
                "contractCodeRegister",
                "contract_code_plc_register",
                "contractCodePLCRegister",
            )
            product = read_register(
                "product_code_register",
                "productCodeRegister",
                "product_code_plc_register",
                "productCodePLCRegister",
            )

            if contract is not None:
                result["ContractCode"] = contract
            if product is not None:
                result["ProductCode"] = product

        except Exception as exc:
            print("REPORT MANAGEMENT REGISTER ERROR:", exc)

        return result

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
                    continue
            return None

        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
            try:
                return datetime.strptime(text, fmt)
            except Exception:
                continue
        return None

    def _report_tag_definitions(self):
        definitions = self._load_flow_definitions()
        result = []
        for item in self.products:
            if not isinstance(item, dict):
                continue
            tag = str(item.get("tag", "")).strip()
            if not tag:
                continue
            definition = definitions.get(tag.lower())
            if definition:
                result.append((item, definition))
        return result

    def _latest_report_timestamp(self):
        if self.company_id is None:
            return None
        conn = get_connection()
        try:
            row = conn.execute(
                """
                SELECT Timestamp
                FROM ReportHistory
                WHERE CompanyID = ?
                ORDER BY ReportID DESC
                LIMIT 1
                """,
                (int(self.company_id),),
            ).fetchone()
            return None if row is None else row["Timestamp"]
        finally:
            conn.close()

    def _latest_trigger_event_from_tag_history(self):
        if self.company_id is None:
            return None

        trigger_items = []
        for product, definition in self._report_tag_definitions():
            if str(definition.get("storage", "TIME")).strip().upper() != "TRIGGER":
                continue
            tag = str(product.get("tag", "")).strip()
            if tag:
                trigger_items.append((tag, definition))

        if not trigger_items:
            return None

        conn = get_connection()
        try:
            latest_rows = []
            for tag, definition in trigger_items:
                row = conn.execute(
                    """
                    SELECT ID, TagName, Value, Timestamp
                    FROM TagHistory
                    WHERE CompanyID = ?
                      AND LOWER(TagName) = LOWER(?)
                    ORDER BY ID DESC
                    LIMIT 1
                    """,
                    (int(self.company_id), tag),
                ).fetchone()
                if row is not None:
                    latest_rows.append((row, definition))

            if not latest_rows:
                return None

            newest_row, newest_definition = max(
                latest_rows,
                key=lambda item: int(item[0]["ID"] or 0),
            )

            event_timestamp = str(newest_row["Timestamp"] or "")
            latest_report_timestamp = self._latest_report_timestamp()

            try:
                event_dt = datetime.fromisoformat(event_timestamp.replace("T", " ").replace("Z", ""))
            except Exception:
                event_dt = None

            try:
                report_dt = (
                    datetime.fromisoformat(
                        str(latest_report_timestamp).replace("T", " ").replace("Z", "")
                    )
                    if latest_report_timestamp
                    else None
                )
            except Exception:
                report_dt = None

            if report_dt is not None and event_dt is not None and event_dt <= report_dt:
                return None

            event_key = (int(newest_row["ID"] or 0), event_timestamp)
            if self._last_report_event_key == event_key:
                return None

            self._last_report_event_key = event_key

            return {
                "register": newest_definition.get("trigger_register"),
                "timestamp": event_timestamp,
                "tag": str(newest_row["TagName"]).strip(),
                "value": newest_row["Value"],
            }
        finally:
            conn.close()

    def _runtime_tags_for_products(self, data):
        live_tags = data.get("Tags", {}) or {}
        if not isinstance(live_tags, dict):
            live_tags = {}

        requested = [
            str(item.get("tag", "")).strip()
            for item in self.products
            if isinstance(item, dict) and str(item.get("tag", "")).strip()
        ]

        resolved = {}
        lookup = {
            str(name).strip().lower(): (name, value)
            for name, value in live_tags.items()
        }
        missing = []

        for tag in requested:
            item = lookup.get(tag.lower())
            if item is None or item[1] is None:
                missing.append(tag)
            else:
                resolved[item[0]] = item[1]

        if missing and self.company_id is not None:
            try:
                latest = get_latest_tag_values(self.company_id, missing)
                for tag in missing:
                    item = latest.get(tag)
                    if item and item.get("value") is not None:
                        resolved[tag] = item["value"]
            except Exception as exc:
                print("REPORT LATEST VALUE LOAD ERROR:", exc)

        return resolved

    def _save_realtime_snapshot(self, data):
        event = None
        events = data.get("EdgeTriggerEvents", []) if isinstance(data, dict) else []
        if isinstance(events, list) and events:
            report_tags = {
                str(item.get("tag", "")).strip().lower()
                for item in self.products
                if isinstance(item, dict) and str(item.get("tag", "")).strip()
            }
            for candidate in events:
                if not isinstance(candidate, dict):
                    continue
                event_tags = candidate.get("tags", {}) or {}
                if not isinstance(event_tags, dict):
                    continue
                matched = [
                    (str(name).strip(), value)
                    for name, value in event_tags.items()
                    if str(name).strip().lower() in report_tags and value is not None
                ]
                if matched:
                    event = dict(candidate)
                    event["tags"] = dict(event_tags)
                    event["report_tags"] = [name for name, _ in matched]
                    break

        if event is None:
            event = self._latest_trigger_event_from_tag_history()

        if event is None:
            return 0

        tags = self._runtime_tags_for_products(data)
        tags.update(self._management_context_tags(data))

        if isinstance(data.get("Tags"), dict):
            for key in ("ContractCode", "ProductCode"):
                value = data["Tags"].get(key)
                if value is not None:
                    tags[key] = value

        event_tags = event.get("tags", {}) or {}
        for name, value in event_tags.items():
            if value is not None:
                tags[str(name).strip()] = value

        if not tags:
            return 0

        report_products = list(self.products)
        if "ContractCode" in tags:
            report_products.append({
                "tag": "ContractCode",
                "name": "ContractCode",
                "context_role": "contract_code",
            })
        if "ProductCode" in tags:
            report_products.append({
                "tag": "ProductCode",
                "name": "ProductCode",
                "context_role": "product_code",
            })

        timestamp = (
            event.get("timestamp")
            or data.get("Timestamp")
            or datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        )

        trigger_tag = event.get("tag")
        if not trigger_tag:
            matching_names = event.get("report_tags", []) or []
            if matching_names:
                trigger_tag = matching_names[0]

        trigger_value = event.get("value")
        if trigger_value is None and trigger_tag:
            trigger_value = event_tags.get(trigger_tag)

        report_id = save_report_snapshot(
            self.company_id,
            tags,
            report_products,
            timestamp=str(timestamp).replace("T", " "),
            trigger_tag=trigger_tag,
            trigger_register=event.get("register"),
            trigger_value=trigger_value,
        )

        if report_id is None:
            return 0

        print(
            "REPORT SNAPSHOT SAVED:",
            "Company=", self.company_id,
            "ReportID=", report_id,
            "TriggerTag=", trigger_tag,
            "TriggerRegister=", event.get("register"),
            "TriggerValue=", trigger_value,
            "ContractCode=", tags.get("ContractCode"),
            "ProductCode=", tags.get("ProductCode"),
            "Columns=", [
                item.get("tag")
                for item in self.products
                if isinstance(item, dict)
            ],
        )
        return 1

    def execute(self, data=None):
        if data is None:
            data = {}

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

        calendar = request.get("Calendar")
        if calendar not in ("Jalali", "Gregorian"):
            calendar = "Jalali" if self.date_picker == "JalaliPicker" else "Gregorian"

        start = self.normalize_date(request.get("Start"), calendar)
        end = self.normalize_date(request.get("End"), calendar)

        report = {
            "columns": self.products,
            "rows": [],
            "totals": [0.0 for _ in self.products],
            "grand_total": 0,
        }

        if company_id is not None and start is not None and end is not None and end >= start:
            report = get_report_data(company_id, start, end)

        data["ReportData"] = report
        data["ChartData"] = {
            "type": "bar",
            "calendar": calendar,
            "date_picker": self.date_picker,
            "report": report,
            "labels": [item.get("name", item.get("tag", "")) for item in report.get("columns", [])],
            "datasets": [{
                "label": "مجموع گزارش",
                "data": report.get("totals", []),
            }],
        }

        print(
            "REPORT OUTPUT:",
            "Company:", company_id,
            "Columns:", len(report.get("columns", [])),
            "Rows:", len(report.get("rows", [])),
            "Totals:", report.get("totals", []),
        )
        return data
