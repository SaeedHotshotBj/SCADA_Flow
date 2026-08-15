# =====================================================
# SCADA_FLOW HISTORIAN SERVICE
# TIME + TRIGGER + REPORT GROUP STORAGE ENGINE
# =====================================================

import time
from datetime import datetime

from database import insert_tag_value


class HistorianService:

    def __init__(self):
        self.time_memory = {}
        self.trigger_memory = {}

    def check_time(self, definition):
        name = definition.get("name")
        interval = definition.get("interval", 0)
        if not interval:
            return False
        now = time.time()
        last = self.time_memory.get(name, 0)
        if now - last >= float(interval):
            self.time_memory[name] = now
            print("TIME TRIGGER:", name, "interval:", interval)
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

        if previous_number == 0 and current_number == target:
            print("TRIGGER EVENT:", name, "Register:", trigger_register, "Value:", current)
            return True
        return False

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
                print("REPORT TRIGGER EVENT:", "Register:", trigger_register, "Value:", current, "Tags:", sorted(selected))
                return definition
            return None

        return None

    def process_report_group(self, company_id, tags, definitions, registers, report_products):
        if not report_products:
            return 0

        report_tags = []
        seen = set()
        for product in report_products:
            if not isinstance(product, dict):
                continue
            tag = str(product.get("tag", "")).strip()
            if not tag:
                continue
            key = tag.lower()
            if key in seen:
                continue
            seen.add(key)
            report_tags.append(tag)

        if not report_tags:
            return 0

        if self.check_report_trigger(definitions, report_tags, registers) is None:
            return 0

        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        written = 0
        tag_lookup = {str(name).lower(): (name, value) for name, value in tags.items()}

        for tag in report_tags:
            item = tag_lookup.get(tag.lower())
            if item is None:
                continue
            actual_name, value = item
            if value is None:
                continue

            insert_tag_value(company_id, actual_name, value, "REPORT_TRIGGER", timestamp=timestamp)
            print("REPORT HISTORIAN INSERT:", actual_name, "=", value, "TIME:", timestamp)
            written += 1

        return written

    def process(self, company_id, tags, definitions, registers, report_tags=None):
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
            save = self.check_time(definition) if mode == "TIME" else self.check_trigger(definition, registers) if mode == "TRIGGER" else False

            if save:
                insert_tag_value(company_id, name, value, mode)
                print("HISTORIAN INSERT:", name, "=", value, "TIME:", time.strftime("%Y-%m-%d %H:%M:%S"))
                written += 1

        return written
