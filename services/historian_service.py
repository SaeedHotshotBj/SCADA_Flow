# =====================================================
# SCADA_FLOW HISTORIAN SERVICE
# PLC-aware time + trigger + report storage.
# =====================================================

import time
from datetime import datetime

from database import get_connection
from services.plc_identity import ensure_plc_identity_schema, insert_plc_data, get_latest_tag_values
from services.report_service import save_report_snapshot

ZERO_DEBOUNCE_SECONDS = 2.0


class HistorianService:

    def __init__(self):
        ensure_plc_identity_schema()
        self.time_memory = {}
        self.trigger_memory = {}
        self.zero_memory = {}

    def check_time(self, company_id, plc_id, definition):
        name = str(definition.get("name", "")).strip().lower()
        interval = definition.get("interval", 0)
        if not interval:
            return False
        key = (int(company_id), int(plc_id), name)
        now = time.time()
        last = self.time_memory.get(key, 0)
        if now - last >= float(interval):
            self.time_memory[key] = now
            return True
        return False

    def check_trigger(self, company_id, plc_id, definition, registers):
        trigger_register = definition.get("trigger_register")
        trigger_value = definition.get("trigger_value")
        if trigger_register is None:
            return False

        if str(trigger_register) in registers:
            current = registers[str(trigger_register)]
        elif trigger_register in registers:
            current = registers[trigger_register]
        else:
            return False

        name = str(definition.get("name", "")).strip().lower()
        memory_key = (int(company_id), int(plc_id), "TRIGGER", name)
        previous = self.trigger_memory.get(memory_key)
        self.trigger_memory[memory_key] = current

        try:
            target = float(trigger_value)
            current_number = float(current)
            previous_number = None if previous is None else float(previous)
        except (TypeError, ValueError):
            target = trigger_value
            current_number = current
            previous_number = previous

        return previous_number == 0 and current_number == target

    def _value_changed(self, company_id, plc_id, name, value):
        try:
            latest = get_latest_tag_values(company_id, plc_id, [name])
            previous = latest.get(name)
            if previous is None:
                return True
            previous_value = previous.get("value")
            try:
                return float(previous_value) != float(value)
            except (TypeError, ValueError):
                return str(previous_value) != str(value)
        except Exception as exc:
            print("HISTORIAN CHANGE CHECK ERROR:", name, exc)
            return True

    def _is_zero(self, value):
        try:
            return float(value) == 0.0
        except (TypeError, ValueError):
            return False

    def _zero_debounced(self, company_id, plc_id, name, value):
        key = (int(company_id), int(plc_id), str(name).strip().lower())
        now = time.monotonic()
        if not self._is_zero(value):
            self.zero_memory.pop(key, None)
            return False
        first_zero = self.zero_memory.get(key)
        if first_zero is None:
            self.zero_memory[key] = now
            return True
        if now - first_zero < ZERO_DEBOUNCE_SECONDS:
            return True
        self.zero_memory.pop(key, None)
        return False

    def _insert_changed(self, company_id, plc_id, name, value, storage_type, timestamp=None):
        if self._zero_debounced(company_id, plc_id, name, value):
            return False
        if not self._value_changed(company_id, plc_id, name, value):
            return False
        insert_plc_data(company_id, plc_id, name, value, storage_type, timestamp=timestamp)
        return True

    def process(self, company_id, plc_id, tags, definitions, registers, report_tags=None):
        written = 0
        report_keys = set(str(tag).strip().lower() for tag in (report_tags or []))

        for definition in definitions:
            name = definition.get("name")
            if not name or name not in tags:
                continue
            if str(name).strip().lower() in report_keys:
                continue
            value = tags[name]
            if value is None:
                continue
            mode = str(definition.get("storage", "TIME")).upper()
            save = (
                self.check_time(company_id, plc_id, definition)
                if mode == "TIME"
                else self.check_trigger(company_id, plc_id, definition, registers)
                if mode == "TRIGGER"
                else False
            )
            if save and self._insert_changed(company_id, plc_id, name, value, mode):
                written += 1
        return written

    def process_report_group(self, company_id, plc_id, tags, definitions, registers, report_products):
        if not report_products:
            return 0
        report_tags = []
        seen = set()
        for product in report_products:
            if not isinstance(product, dict):
                continue
            tag = str(product.get("tag", "")).strip()
            if tag and tag.lower() not in seen:
                seen.add(tag.lower())
                report_tags.append(tag)
        if not report_tags:
            return 0
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        tag_lookup = {str(name).strip().lower(): (name, value) for name, value in tags.items()}
        snapshot_tags = {}
        for tag in report_tags:
            item = tag_lookup.get(tag.lower())
            if item and item[1] is not None:
                snapshot_tags[item[0]] = item[1]
        if not snapshot_tags:
            return 0
        snapshot_id = save_report_snapshot(company_id, snapshot_tags, report_products, timestamp=timestamp, plc_id=plc_id)
        if snapshot_id is None:
            return 0
        written = 0
        for tag in report_tags:
            item = tag_lookup.get(tag.lower())
            if item and item[1] is not None:
                if self._insert_changed(company_id, plc_id, item[0], item[1], "REPORT_TRIGGER", timestamp):
                    written += 1
        return written


try:
    from services.report_runtime import start as _start_report_runtime
    _start_report_runtime()
except Exception as _report_runtime_exc:
    print("REPORT RUNTIME START ERROR:", _report_runtime_exc)
