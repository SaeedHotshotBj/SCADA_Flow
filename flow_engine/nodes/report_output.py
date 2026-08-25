# =====================================================
# SCADA_FLOW REPORT OUTPUT NODE
# DYNAMIC REPORT QUERY + SNAPSHOT OUTPUT
# =====================================================

import time
from datetime import datetime

import jdatetime

from database import get_latest_tag_values
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
        self.time_memory = {}
        self.trigger_memory = {}

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

    def _definitions_by_tag(self, data):
        definitions = data.get("TagDefinitions", [])
        if not isinstance(definitions, list):
            return {}

        result = {}
        for definition in definitions:
            if not isinstance(definition, dict):
                continue
            name = str(definition.get("name", "")).strip()
            if not name:
                continue
            result[name.lower()] = definition
        return result

    def _time_due(self, tag, interval):
        try:
            seconds = float(interval)
        except (TypeError, ValueError):
            seconds = 0.0

        if seconds <= 0:
            return False

        now = time.monotonic()
        last = self.time_memory.get(tag, 0.0)

        if now - last >= seconds:
            self.time_memory[tag] = now
            return True

        return False

    def _trigger_rising(self, tag, definition, registers):
        trigger_register = definition.get("trigger_register")
        trigger_value = definition.get("trigger_value")

        if trigger_register in (None, ""):
            return False

        key = str(trigger_register)
        if key in registers:
            current = registers[key]
        elif trigger_register in registers:
            current = registers[trigger_register]
        else:
            return False

        previous = self.trigger_memory.get(tag)
        self.trigger_memory[tag] = current

        try:
            current_number = float(current)
            previous_number = None if previous is None else float(previous)
            target = float(trigger_value)
        except (TypeError, ValueError):
            current_number = current
            previous_number = previous
            target = trigger_value

        return previous_number == 0 and current_number == target

    def _should_snapshot(self, data):
        if not isinstance(self.products, list) or not self.products:
            return False

        definitions = self._definitions_by_tag(data)
        registers = data.get("Registers", {}) or {}
        if not isinstance(registers, dict):
            registers = {}

        time_ready = False
        trigger_ready = False
        configured_mode_found = False

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
            configured_mode_found = True

            if mode == "TIME":
                if self._time_due(tag, definition.get("interval", 0)):
                    time_ready = True

            elif mode == "TRIGGER":
                if self._trigger_rising(
                    tag,
                    definition,
                    registers,
                ):
                    trigger_ready = True

        # If a ReportOutput product is configured but its TagMapper definition
        # is absent, do not invent a storage policy. The Flow must define it.
        if not configured_mode_found:
            return False

        return time_ready or trigger_ready

    def _runtime_tags_for_products(self, data):
        """Resolve values only for ReportOutput.products.

        Prefer the values traveling through the connected Flow branch. If a
        selected report tag is not present in that packet, load the latest
        value for that exact Company/Tag from the Historian. No other tags are
        introduced into the report.
        """
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
                continue
            resolved[item[0]] = item[1]

        if missing and self.company_id is not None:
            try:
                latest = get_latest_tag_values(
                    self.company_id,
                    missing,
                )
                for tag in missing:
                    item = latest.get(tag)
                    if not item:
                        continue
                    value = item.get("value")
                    if value is None:
                        continue
                    resolved[tag] = value
            except Exception as exc:
                print("REPORT LATEST VALUE LOAD ERROR:", exc)

        return resolved

    def _save_realtime_snapshot(self, data):
        if not self._should_snapshot(data):
            return 0

        tags = self._runtime_tags_for_products(data)
        if not tags:
            return 0

        timestamp = data.get("Timestamp")
        if not timestamp:
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        report_id = save_report_snapshot(
            self.company_id,
            tags,
            self.products,
            timestamp=timestamp,
        )

        if report_id is None:
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

        # -------------------------------------------------
        # REALTIME FLOW PATH
        # -------------------------------------------------
        if not data.get("ReportRequest"):
            try:
                data["Report_Written"] = self._save_realtime_snapshot(data)
            except Exception as exc:
                print("REPORT SNAPSHOT ERROR:", exc)
                data["Report_Written"] = 0
            return data

        # -------------------------------------------------
        # HISTORICAL REPORT REQUEST
        # -------------------------------------------------
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
            "columns": self.products,
            "rows": [],
            "totals": [0.0 for _ in self.products],
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
