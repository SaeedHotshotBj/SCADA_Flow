# =====================================================
# SCADA_FLOW HISTORIAN SERVICE
# Flow-defined TIME/TRIGGER historian storage only.
# =====================================================

import time

from services.plc_identity import ensure_plc_identity_schema, insert_plc_data, get_latest_tag_values

ZERO_DEBOUNCE_SECONDS = 2.0


class HistorianService:
    def __init__(self):
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

    @staticmethod
    def _trigger_edge_matches(previous, current, trigger_value, trigger_edge):
        if previous is None:
            return False

        try:
            current_number = float(current)
            target_number = float(trigger_value)
            previous_number = float(previous)
        except (TypeError, ValueError):
            edge = str(trigger_edge or "rise").strip().lower()
            if edge == "fall":
                return previous == trigger_value and current != trigger_value
            return previous != trigger_value and current == trigger_value

        edge = str(trigger_edge or "rise").strip().lower()
        if edge == "fall":
            return previous_number == target_number and current_number != target_number
        return previous_number != target_number and current_number == target_number

    def check_trigger(self, company_id, plc_id, definition, registers):
        trigger_register = definition.get("trigger_register")
        trigger_value = definition.get("trigger_value")
        if trigger_register is None:
            return False

        current = registers.get(str(trigger_register))
        if current is None:
            current = registers.get(trigger_register)
        if current is None:
            return False

        key = (int(company_id), int(plc_id), str(trigger_register))
        previous = self.trigger_memory.get(key)
        self.trigger_memory[key] = current

        return self._trigger_edge_matches(
            previous,
            current,
            trigger_value,
            definition.get("trigger_edge", "rise"),
        )

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

    @staticmethod
    def _is_zero(value):
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
        ensure_plc_identity_schema()
        written = 0
        report_keys = {str(tag).strip().lower() for tag in (report_tags or [])}
        trigger_previous = {}
        trigger_current = {}

        for definition in definitions or []:
            if not isinstance(definition, dict):
                continue
            name = str(definition.get("name", "")).strip()
            if not name or name not in tags:
                continue
            if name.lower() in report_keys:
                continue

            value = tags[name]
            if value is None:
                continue

            mode = str(definition.get("storage", "TIME")).strip().upper()
            if mode == "TIME":
                save = self.check_time(company_id, plc_id, definition)
            elif mode == "TRIGGER":
                trigger_register = definition.get("trigger_register")
                current = registers.get(str(trigger_register))
                if current is None and trigger_register is not None:
                    current = registers.get(trigger_register)

                if trigger_register is None or current is None:
                    save = False
                else:
                    state_key = (int(company_id), int(plc_id), str(trigger_register))
                    if state_key not in trigger_previous:
                        trigger_previous[state_key] = self.trigger_memory.get(state_key)
                        trigger_current[state_key] = current
                    save = self._trigger_edge_matches(
                        trigger_previous[state_key],
                        current,
                        definition.get("trigger_value"),
                        definition.get("trigger_edge", "rise"),
                    )
            else:
                save = False

            if save and self._insert_changed(
                company_id,
                plc_id,
                name,
                value,
                mode,
                timestamp=None,
            ):
                written += 1

        self.trigger_memory.update(trigger_current)
        return written

        return written


__all__ = ["HistorianService"]
