# =====================================================
# SCADA_FLOW REPORT OUTPUT NODE
# DYNAMIC REPORT QUERY + SNAPSHOT OUTPUT
# =====================================================

import json
import time
from datetime import datetime, timedelta

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
        self._last_snapshot_at = {}
        self._trigger_memory = {}
        self._last_trigger_signatures = {}
        self._trigger_initialized = False

        try:
            ensure_report_tables()
        except Exception as exc:
            print("REPORT DATABASE INIT ERROR:", exc)

    def _load_flow_definitions(self):
        if self.company_id is None:
            return {}
        try:
            flow = get_company_flow(self.company_id)
            if not flow:
                return {}
            if isinstance(flow, str):
                flow = json.loads(flow)

            nodes = flow.get("drawflow", {}).get("Home", {}).get("data", {})
            definitions = {}
            for node in nodes.values():
                if node.get("name") != "TagMapper":
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

            signature = json.dumps(definitions, sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str)
            if signature != self._flow_definition_signature:
                self._flow_definition_signature = signature
                self._flow_definitions = definitions
            return self._flow_definitions
        except Exception as exc:
            print("REPORT FLOW DEFINITION LOAD ERROR:", exc)
            return self._flow_definitions

    def _management_context_tags(self, data):
        """Resolve ContractCode/ProductCode from ManagementPanel registers.

        Register addresses are configured per company inside the Flow's
        ManagementPanel node. Nothing is hard-coded here.
        """
        if self.company_id is None:
            return {}

        registers = data.get("Registers", {}) or {}
        if not isinstance(registers, dict):
            return {}

        try:
            flow = get_company_flow(self.company_id)
            if isinstance(flow, str):
                flow = json.loads(flow)
            nodes = flow.get("drawflow", {}).get("Home", {}).get("data", {})

            config = {}
            for node in nodes.values():
                if not isinstance(node, dict) or node.get("name") != "ManagementPanel":
                    continue
                raw = node.get("data", {}) or {}
                config = raw.get("config", raw) or {}
                break

            def read_register(field):
                address = config.get(field)
                if address in (None, ""):
                    return None
                key = str(address).strip()
                if key in registers:
                    return registers[key]
                try:
                    return registers.get(str(int(float(key))))
                except (TypeError, ValueError):
                    return None

            result = {}
            contract = read_register("contract_code_register")
            product = read_register("product_code_register")
            if contract is not None:
                result["ContractCode"] = contract
            if product is not None:
                result["ProductCode"] = product
            return result
        except Exception as exc:
            print("REPORT MANAGEMENT REGISTER LOAD ERROR:", exc)
            return {}

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

    def _time_due(self, tag, interval):
        try:
            seconds = float(interval)
        except (TypeError, ValueError):
            return False
        if seconds <= 0:
            return False
        now = time.monotonic()
        last = self._last_snapshot_at.get(("TIME", str(tag).strip().lower()), 0.0)
        if now - last >= seconds:
            self._last_snapshot_at[("TIME", str(tag).strip().lower())] = now
            return True
        return False

    def _trigger_due_from_registers(self, tag, definition, registers):
        trigger_register = definition.get("trigger_register")
        trigger_value = definition.get("trigger_value")
        if trigger_register in (None, ""):
            return False

        current = None
        if str(trigger_register) in registers:
            current = registers[str(trigger_register)]
        elif trigger_register in registers:
            current = registers[trigger_register]

        if current is None:
            return False

        key = ("TRIGGER", str(tag).strip().lower(), str(trigger_register))
        previous = self._trigger_memory.get(key)
        self._trigger_memory[key] = current

        try:
            current_number = float(current)
            previous_number = None if previous is None else float(previous)
            target = float(trigger_value)
        except (TypeError, ValueError):
            current_number = current
            previous_number = previous
            target = trigger_value

        return (
            previous_number == 0
            and current_number == target
        )

    def _trigger_event_due_from_edge_rows(self, products, definitions):
        """Detect a report trigger from the EDGE rows generated by SCADA_FLOW_EDGE.

        Trigger tags are only sent by the Edge application on a 0 -> trigger
        transition. The raw trigger register itself is therefore not required
        on the server. This also survives FlowRunner/PLCReader polling because
        the event is detected from persisted Edge rows.
        """
        if self.company_id is None:
            return False

        trigger_groups = {}
        for product in products:
            if not isinstance(product, dict):
                continue

            tag = str(product.get("tag", "")).strip()
            if not tag:
                continue

            definition = definitions.get(tag.lower())
            if not definition:
                continue

            if str(definition.get("storage", "TIME")).strip().upper() != "TRIGGER":
                continue

            trigger_register = definition.get("trigger_register")
            trigger_value = definition.get("trigger_value")
            if trigger_register in (None, ""):
                continue

            group_key = (str(trigger_register), str(trigger_value))
            trigger_groups.setdefault(group_key, []).append(tag)

        if not trigger_groups:
            return False

        conn = None
        try:
            conn = get_connection()

            for group_key, tags in trigger_groups.items():
                placeholders = ",".join("?" for _ in tags)
                rows = conn.execute(
                    f"""
                    SELECT TagName, ID, Timestamp
                    FROM PLC_Data
                    WHERE CompanyID = ?
                      AND UPPER(COALESCE(StorageType, '')) = 'EDGE'
                      AND LOWER(TagName) IN ({placeholders})
                    ORDER BY ID DESC
                    LIMIT {max(len(tags) * 2, 10)}
                    """,
                    [int(self.company_id), *[tag.lower() for tag in tags]],
                ).fetchall()

                latest = {}
                for row in rows:
                    key = str(row["TagName"]).strip().lower()
                    if key not in latest:
                        latest[key] = row

                if not all(tag.lower() in latest for tag in tags):
                    continue

                current_ids = tuple(
                    sorted(
                        int(latest[tag.lower()]["ID"])
                        for tag in tags
                    )
                )

                last_ids = self._last_trigger_signatures.get(group_key)

                # Seed from the current persisted state after process startup.
                # Only a later Edge event is considered a new report trigger.
                if not self._trigger_initialized:
                    self._last_trigger_signatures[group_key] = current_ids
                    continue

                if last_ids == current_ids:
                    continue

                current_times = [
                    str(latest[tag.lower()]["Timestamp"] or "")
                    for tag in tags
                ]
                parsed_times = []
                for timestamp in current_times:
                    try:
                        parsed_times.append(
                            datetime.fromisoformat(
                                timestamp.replace("T", " ").replace("Z", "")
                            )
                        )
                    except Exception:
                        pass

                # B1/B2/B3 produced by one Edge rising-edge event are sent
                # sequentially. Require them to belong to the same short time
                # cluster before firing, preventing three snapshots for one pulse.
                if parsed_times:
                    span = max(parsed_times) - min(parsed_times)
                    if span.total_seconds() > 2.0:
                        continue

                self._last_trigger_signatures[group_key] = current_ids
                return True

            return False

        except Exception as exc:
            print("REPORT EDGE TRIGGER DETECTION ERROR:", exc)
            return False
        finally:
            if conn is not None:
                try:
                    conn.close()
                except Exception:
                    pass

    def _should_snapshot(self, data):
        if not isinstance(self.products, list) or not self.products:
            return False

        definitions = self._load_flow_definitions()
        registers = data.get("Registers", {}) or {}
        if not isinstance(registers, dict):
            registers = {}

        time_ready = False
        trigger_ready = False

        for product in self.products:
            if not isinstance(product, dict):
                continue

            tag = str(product.get("tag", "")).strip()
            if not tag:
                continue

            definition = definitions.get(tag.lower())
            if not definition:
                continue

            mode = str(definition.get("storage", "TIME")).strip().upper()
            if mode == "TIME":
                if self._time_due(tag, definition.get("interval", 0)):
                    time_ready = True
            elif mode == "TRIGGER":
                # Prefer the direct register path when available. In the
                # server-side Edge architecture the raw trigger register may
                # not be part of PLCReader output, so the persistent Edge-row
                # detector below is the authoritative fallback.
                if self._trigger_due_from_registers(tag, definition, registers):
                    trigger_ready = True

        if trigger_ready:
            return True

        if self._trigger_event_due_from_edge_rows(self.products, definitions):
            return True

        return time_ready

    def _runtime_tags_for_products(self, data):
        live_tags = data.get("Tags", {}) or {}
        if not isinstance(live_tags, dict):
            live_tags = {}

        requested_tags = [
            str(item.get("tag", "")).strip()
            for item in self.products
            if isinstance(item, dict) and str(item.get("tag", "")).strip()
        ]

        resolved = {}
        live_lookup = {
            str(name).strip().lower(): (name, value)
            for name, value in live_tags.items()
        }
        missing = []

        for tag in requested_tags:
            item = live_lookup.get(tag.lower())
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
        if not self._should_snapshot(data):
            self._trigger_initialized = True
            return 0

        self._trigger_initialized = True
        tags = self._runtime_tags_for_products(data)
        tags.update(self._management_context_tags(data))
        if not tags:
            print("REPORT SNAPSHOT SKIPPED: no runtime values")
            return 0

        report_products = list(self.products)
        if "ContractCode" in tags and not any(
            isinstance(item, dict) and str(item.get("context_role", "")).strip().lower() in ("contract", "contract_code", "contractid", "contract_id")
            for item in report_products
        ):
            report_products.append({"tag": "ContractCode", "name": "ContractCode", "context_role": "contract_code"})
        if "ProductCode" in tags and not any(
            isinstance(item, dict) and str(item.get("context_role", "")).strip().lower() in ("product", "product_code", "productid", "product_id")
            for item in report_products
        ):
            report_products.append({"tag": "ProductCode", "name": "ProductCode", "context_role": "product_code"})

        timestamp = data.get("Timestamp") or datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        report_id = save_report_snapshot(
            self.company_id,
            tags,
            report_products,
            timestamp=timestamp,
        )
        if report_id is None:
            print("REPORT SNAPSHOT SKIPPED: save returned None")
            return 0

        print(
            "REPORT SNAPSHOT SAVED:",
            "Company=", self.company_id,
            "ReportID=", report_id,
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
            "labels": [item["name"] for item in report["columns"]],
            "datasets": [{"label": "مجموع گزارش", "data": report["totals"]}],
        }
        print(
            "REPORT OUTPUT:",
            "Company:", company_id,
            "Columns:", len(report["columns"]),
            "Rows:", len(report["rows"]),
            "Totals:", report["totals"],
        )
        return data
