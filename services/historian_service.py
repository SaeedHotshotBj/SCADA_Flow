# =====================================================
# SCADA_FLOW HISTORIAN SERVICE
# TIME + TRIGGER + REPORT GROUP STORAGE ENGINE
# CHANGE-BASED HISTORIAN STORAGE
# =====================================================

import time
from datetime import datetime

from database import insert_tag_value, get_latest_tag_values
from services.report_service import save_report_snapshot


ZERO_DEBOUNCE_SECONDS = 2.0


class HistorianService:

    def __init__(self):
        self.time_memory = {}
        self.trigger_memory = {}
        self.zero_memory = {}
        self.latest_memory = {}
        self.latest_loaded = set()

    def check_time(self, definition):
        name = definition.get("name")
        interval = definition.get("interval", 0)
        if not interval:
            return False
        now = time.time()
        last = self.time_memory.get(name, 0)
        if now - last >= float(interval):
            self.time_memory[name] = now
            return True
        return False

    def check_trigger(self, definition, registers):
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

        name = definition.get("name")
        previous = self.trigger_memory.get(name)
        self.trigger_memory[name] = current

        try:
            target = float(trigger_value)
            current_number = float(current)
            previous_number = None if previous is None else float(previous)
        except (TypeError, ValueError):
            target = trigger_value
            current_number = current
            previous_number = previous

        return (
            previous_number == 0
            and current_number == target
        )

    def check_report_trigger(self, definitions, report_tags, registers):
        selected = set(str(tag).strip().lower() for tag in report_tags)

        for definition in definitions:
            name = str(definition.get("name", "")).strip()
            if not name or name.lower() not in selected:
                continue
            if str(definition.get("storage", "")).upper() != "TRIGGER":
                continue

            trigger_register = definition.get("trigger_register")
            trigger_value = definition.get("trigger_value")
            if trigger_register is None:
                continue

            if str(trigger_register) in registers:
                current = registers[str(trigger_register)]
            elif trigger_register in registers:
                current = registers[trigger_register]
            else:
                continue

            memory_key = "__REPORT__:" + ",".join(sorted(selected))
            previous = self.trigger_memory.get(memory_key)
            self.trigger_memory[memory_key] = current

            try:
                current_number = float(current)
                previous_number = None if previous is None else float(previous)
                target = float(trigger_value)
            except (TypeError, ValueError):
                current_number = current
                previous_number = previous
                target = trigger_value

            if previous_number == 0 and current_number == target:
                return definition
            return None

        return None

    def _latest_key(self, company_id, name):
        return (
            int(company_id),
            str(name).strip().lower()
        )

    def _load_latest_value(self, company_id, name):
        key = self._latest_key(
            company_id,
            name
        )

        if key in self.latest_loaded:
            return self.latest_memory.get(key)

        try:
            latest = get_latest_tag_values(
                company_id,
                [name]
            )
            previous = latest.get(name)
            self.latest_loaded.add(key)

            if previous is None:
                self.latest_memory[key] = None
                return None

            value = previous.get("value")
            self.latest_memory[key] = value
            return value

        except Exception:
            self.latest_loaded.add(key)
            self.latest_memory[key] = None
            return None

    def _value_changed(self, company_id, name, value):
        """Return True only when the value differs from the cached latest value."""
        key = self._latest_key(
            company_id,
            name
        )

        previous_value = self._load_latest_value(
            company_id,
            name
        )

        if previous_value is None:
            return True

        try:
            return float(previous_value) != float(value)
        except (TypeError, ValueError):
            return str(previous_value) != str(value)

    def _is_zero(self, value):
        try:
            return float(value) == 0.0
        except (TypeError, ValueError):
            return False

    def _zero_debounced(self, company_id, name, value):
        key = (int(company_id), str(name).strip().lower())
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

    def _insert_changed(self, company_id, name, value, storage_type, timestamp=None):
        if self._zero_debounced(
            company_id,
            name,
            value
        ):
            return False

        if not self._value_changed(
            company_id,
            name,
            value
        ):
            return False

        insert_tag_value(
            company_id,
            name,
            value,
            storage_type,
            timestamp=timestamp
        )

        key = self._latest_key(
            company_id,
            name
        )

        self.latest_memory[key] = value
        self.latest_loaded.add(key)

        return True

    def process_report_group(
        self,
        company_id,
        tags,
        definitions,
        registers,
        report_products
    ):
        if not report_products:
            return 0

        report_tags = []
        seen = set()

        for product in report_products:
            if not isinstance(product, dict):
                continue

            tag = str(
                product.get("tag", "")
            ).strip()

            if not tag:
                continue

            key = tag.lower()

            if key in seen:
                continue

            seen.add(key)
            report_tags.append(tag)

        if not report_tags:
            return 0

        if self.check_report_trigger(
            definitions,
            report_tags,
            registers
        ) is None:
            return 0

        timestamp = datetime.now().strftime(
            "%Y-%m-%d %H:%M:%S"
        )

        tag_lookup = {
            str(name).strip().lower(): (name, value)
            for name, value in tags.items()
        }

        snapshot_id = save_report_snapshot(
            company_id,
            tags,
            report_products,
            timestamp=timestamp,
        )

        if snapshot_id is None:
            return 0

        written = 0

        for tag in report_tags:
            item = tag_lookup.get(
                tag.lower()
            )

            if item is None:
                continue

            actual_name, value = item

            if value is None:
                continue

            if self._insert_changed(
                company_id,
                actual_name,
                value,
                "REPORT_TRIGGER",
                timestamp
            ):
                written += 1

        return written

    def process(
        self,
        company_id,
        tags,
        definitions,
        registers,
        report_tags=None
    ):
        written = 0
        report_keys = set(
            str(tag).strip().lower()
            for tag in (report_tags or [])
        )

        for definition in definitions:
            name = definition.get("name")

            if not name or name not in tags:
                continue

            if str(name).strip().lower() in report_keys:
                continue

            value = tags[name]

            if value is None:
                continue

            mode = str(
                definition.get(
                    "storage",
                    "TIME"
                )
            ).upper()

            save = (
                self.check_time(definition)
                if mode == "TIME"
                else self.check_trigger(
                    definition,
                    registers
                )
                if mode == "TRIGGER"
                else False
            )

            if save and self._insert_changed(
                company_id,
                name,
                value,
                mode
            ):
                written += 1

        return written
