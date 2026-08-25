# =====================================================
# SCADA_FLOW REPORT OUTPUT NODE
# DYNAMIC REPORT QUERY + SNAPSHOT OUTPUT
# =====================================================

import json
import time
from datetime import datetime

import jdatetime

from database import get_company_flow, get_latest_tag_values
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

    def _trigger_due(self, tag, definition, registers):
        trigger_register = definition.get("trigger_register")
        trigger_value = definition.get("trigger_value")
        if trigger_register in (None, ""):
            return False
        if str(trigger_register) in registers:
            current = registers[str(trigger_register)]
        elif trigger_register in registers:
            current = registers[trigger_register]
        else:
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

        return (previous_number is None and current_number == target) or (previous_number == 0 and current_number == target)

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
                if self._trigger_due(tag, definition, registers):
                    trigger_ready = True
        return time_ready or trigger_ready

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
        live_lookup = {str(name).strip().lower(): (name, value) for name, value in live_tags.items()}
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
            return 0
        tags = self._runtime_tags_for_products(data)
        if not tags:
            print("REPORT SNAPSHOT SKIPPED: no runtime values")
            return 0
        timestamp = data.get("Timestamp") or datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        report_id = save_report_snapshot(self.company_id, tags, self.products, timestamp=timestamp)
        if report_id is None:
            print("REPORT SNAPSHOT SKIPPED: save returned None")
            return 0
        print("REPORT SNAPSHOT SAVED:", "Company=", self.company_id, "ReportID=", report_id, "Columns=", [item.get("tag") for item in self.products if isinstance(item, dict)])
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

        report = {"columns": self.products, "rows": [], "totals": [0.0 for _ in self.products], "grand_total": 0}
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
        print("REPORT OUTPUT:", "Company:", company_id, "Columns:", len(report["columns"]), "Rows:", len(report["rows"]), "Totals:", report["totals"])
        return data
