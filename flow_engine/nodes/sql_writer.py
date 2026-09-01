# =====================================================
# SCADA_FLOW SQL WRITER NODE
# HISTORIAN STORAGE ENGINE
# TIME + TRIGGER + REPORT GROUP STORAGE
# =====================================================

import json
import time
from datetime import datetime

from database import get_company_flow, get_connection, insert_tag_value
from services.historian_service import HistorianService
from services.tag_registry import TagRegistry
from services.report_service import save_report_snapshot


class SQLWriter:

    _report_product_cache = {}

    def __init__(self, config):
        self.config = config or {}
        self.company_id = self.config.get("company_id", 1)
        self.historian = HistorianService()
        self._last_definition_signature = None
        self._last_report_signature = None
        self._cached_report_products = []
        self._report_time_memory = {}

    def _get_report_products(self):
        try:
            flow = get_company_flow(self.company_id)
            if not flow:
                return []

            if isinstance(flow, str):
                flow = json.loads(flow)

            nodes = (
                flow
                .get("drawflow", {})
                .get("Home", {})
                .get("data", {})
            )

            for node in nodes.values():
                if node.get("name") != "ReportOutput":
                    continue

                data = node.get("data", {}) or {}
                config = data.get("config", data) or {}
                products = config.get("products", [])

                if isinstance(products, list):
                    return products

        except Exception as exc:
            print("SQLWRITER REPORT CONFIG ERROR:", exc)
            return []

        return []

    def _get_management_context_tags(self, registers):
        """Resolve ContractCode/ProductCode from ManagementPanel registers.

        ManagementPanel owns the register configuration. TagMapper normally
        exposes the same values as tags, but Trigger/report execution must also
        work when an intermediate branch only preserves the raw Registers map.
        """
        result = {}

        if not isinstance(registers, dict):
            return result

        try:
            flow = get_company_flow(self.company_id)
            if not flow:
                return result

            if isinstance(flow, str):
                flow = json.loads(flow)

            nodes = (
                flow
                .get("drawflow", {})
                .get("Home", {})
                .get("data", {})
            )

            management_config = None
            for node in nodes.values():
                if not isinstance(node, dict):
                    continue
                if node.get("name") != "ManagementPanel":
                    continue
                raw = node.get("data", {}) or {}
                management_config = raw.get("config", raw) or {}
                break

            if not isinstance(management_config, dict):
                return result

            def configured_register(*keys):
                for field in keys:
                    value = management_config.get(field)
                    if value not in (None, ""):
                        try:
                            return int(float(value))
                        except (TypeError, ValueError):
                            continue
                return None

            contract_register = configured_register(
                "contract_code_register",
                "contractCodeRegister",
                "contract_code_plc_register",
                "contractCodePLCRegister",
            )
            product_register = configured_register(
                "product_code_register",
                "productCodeRegister",
                "product_code_plc_register",
                "productCodePLCRegister",
            )

            def register_value(address):
                if address is None:
                    return None
                for key in (
                    str(address),
                    address,
                ):
                    if key in registers:
                        value = registers[key]
                        if value not in (None, ""):
                            return value
                return None

            contract_value = register_value(contract_register)
            product_value = register_value(product_register)

            if contract_value is not None:
                result["ContractCode"] = contract_value
            if product_value is not None:
                result["ProductCode"] = product_value

        except Exception as exc:
            print("SQLWRITER MANAGEMENT CONTEXT ERROR:", exc)

        return result

    def _signature(self, value):
        try:
            return json.dumps(
                value,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False
            )
        except (TypeError, ValueError):
            return repr(value)

    def _ensure_trigger_state_table(self):
        conn = get_connection()
        try:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS FlowTriggerState (
                    CompanyID INTEGER NOT NULL,
                    TriggerRegister TEXT NOT NULL,
                    LastValue REAL,
                    UpdatedAt TEXT NOT NULL,
                    PRIMARY KEY (CompanyID, TriggerRegister)
                )
                """
            )
            conn.commit()
        finally:
            conn.close()

    def _trigger_rising_edge(self, trigger_register, current, target):
        """Persist trigger state and return True only for 0 -> target."""
        self._ensure_trigger_state_table()

        conn = get_connection()
        try:
            conn.execute("BEGIN IMMEDIATE")

            register_key = str(trigger_register)
            row = conn.execute(
                """
                SELECT LastValue
                FROM FlowTriggerState
                WHERE CompanyID = ?
                  AND TriggerRegister = ?
                """,
                (
                    int(self.company_id),
                    register_key,
                ),
            ).fetchone()

            previous = (
                None
                if row is None
                else row["LastValue"]
            )

            try:
                current_number = float(current)
                target_number = float(target)
                previous_number = (
                    None
                    if previous is None
                    else float(previous)
                )

                rising = (
                    previous_number == 0.0
                    and current_number == target_number
                )

                stored_value = current_number

            except (TypeError, ValueError):
                rising = (
                    previous == 0
                    and current == target
                )
                stored_value = current

            conn.execute(
                """
                INSERT INTO FlowTriggerState
                (
                    CompanyID,
                    TriggerRegister,
                    LastValue,
                    UpdatedAt
                )
                VALUES (?, ?, ?, ?)
                ON CONFLICT(CompanyID, TriggerRegister)
                DO UPDATE SET
                    LastValue = excluded.LastValue,
                    UpdatedAt = excluded.UpdatedAt
                """,
                (
                    int(self.company_id),
                    register_key,
                    stored_value,
                    datetime.now().strftime(
                        "%Y-%m-%d %H:%M:%S"
                    ),
                ),
            )

            conn.commit()

            print(
                "TRIGGER STATE:",
                "Company=", self.company_id,
                "Register=", register_key,
                "Previous=", previous,
                "Current=", current,
                "Target=", target,
                "RISING=", rising,
            )

            return rising

        except Exception:
            try:
                conn.rollback()
            except Exception:
                pass
            raise
        finally:
            conn.close()

    def _register_value(self, registers, trigger_register):
        key = str(trigger_register)

        if key in registers:
            return registers[key]

        if trigger_register in registers:
            return registers[trigger_register]

        return None

    def _save_edge_trigger_events(
        self,
        tags,
        edge_trigger_events,
        report_products,
    ):
        """Create the Trigger Report from the event produced by PLCReader.

        Edge-trigger detection is already performed upstream by
        trigger_edge_report_fix.py. Those events are the authoritative trigger
        signal for the historian-backed Edge deployment, so this function is
        the single SQLWriter-owned ReportHistory persistence path for them.
        """
        if not report_products or not isinstance(edge_trigger_events, list):
            return 0

        report_tag_set = {
            str(item.get("tag", "")).strip().lower()
            for item in report_products
            if isinstance(item, dict)
            and str(item.get("tag", "")).strip()
        }

        if not report_tag_set:
            return 0

        saved = 0

        for event in edge_trigger_events:
            if not isinstance(event, dict):
                continue

            event_tags = event.get("tags", {}) or {}
            if not isinstance(event_tags, dict):
                continue

            matched_tags = {
                str(name).strip().lower()
                for name in event_tags.keys()
            }
            if not matched_tags.intersection(report_tag_set):
                continue

            snapshot_tags = dict(tags) if isinstance(tags, dict) else {}
            for name, value in event_tags.items():
                if value is not None:
                    snapshot_tags[str(name).strip()] = value

            # The ManagementPanel context comes from the same TagMapper payload.
            # Do not replace it with the historical event payload because the
            # event intentionally contains only the Trigger report tags.
            contract_code = snapshot_tags.get("ContractCode")
            product_code = snapshot_tags.get("ProductCode")

            event_timestamp = event.get("timestamp")
            if event_timestamp in (None, ""):
                event_timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

            trigger_tag = next(
                (
                    str(name).strip()
                    for name in event_tags.keys()
                    if str(name).strip().lower() in report_tag_set
                ),
                None,
            )

            trigger_register = event.get("register")

            try:
                trigger_value = next(
                    (
                        value
                        for name, value in event_tags.items()
                        if str(name).strip().lower() == str(trigger_tag or "").lower()
                    ),
                    None,
                )
            except Exception:
                trigger_value = None

            report_id = save_report_snapshot(
                self.company_id,
                snapshot_tags,
                report_products,
                timestamp=str(event_timestamp).replace("T", " "),
                trigger_tag=trigger_tag,
                trigger_register=trigger_register,
                trigger_value=trigger_value,
            )

            if report_id is None:
                continue

            print(
                "REPORT EDGE TRIGGER SNAPSHOT SAVED:",
                "Company=", self.company_id,
                "ReportID=", report_id,
                "TriggerTag=", trigger_tag,
                "TriggerRegister=", trigger_register,
                "TriggerValue=", trigger_value,
                "ContractCode=", contract_code,
                "ProductCode=", product_code,
            )
            saved += 1

        return saved

    def _process_trigger_tags(
        self,
        tags,
        definitions,
        registers,
        report_products,
        timestamp=None,
    ):
        """Store trigger tags only when their shared trigger register rises 0 -> target."""

        management_context = self._get_management_context_tags(registers)
        for context_name, context_value in management_context.items():
            if context_value is not None:
                tags[context_name] = context_value

        trigger_groups = {}

        report_tag_set = {
            str(item.get("tag", "")).strip().lower()
            for item in (report_products or [])
            if isinstance(item, dict)
            and str(item.get("tag", "")).strip()
        }

        for definition in definitions or []:
            if not isinstance(definition, dict):
                continue

            if str(
                definition.get("storage", "")
            ).strip().upper() != "TRIGGER":
                continue

            name = str(
                definition.get("name", "")
            ).strip()

            if not name:
                continue

            trigger_register = definition.get(
                "trigger_register"
            )

            if trigger_register in (None, ""):
                continue

            current = self._register_value(
                registers,
                trigger_register,
            )

            if current is None:
                continue

            key = str(trigger_register)
            trigger_groups.setdefault(
                key,
                {
                    "current": current,
                    "items": [],
                },
            )["items"].append(definition)

        trigger_events = []

        for register_key, group in trigger_groups.items():
            current = group["current"]

            targets = []
            for definition in group["items"]:
                target = definition.get("trigger_value")
                if target in targets:
                    continue
                targets.append(target)

            if not targets:
                continue

            event_target = None

            for target in targets:
                if self._trigger_rising_edge(
                    register_key,
                    current,
                    target,
                ):
                    event_target = target
                    break

            if event_target is None:
                continue

            for definition in group["items"]:
                target = definition.get("trigger_value")

                try:
                    same_target = (
                        float(current)
                        == float(target)
                        and float(event_target)
                        == float(target)
                    )
                except (
                    TypeError,
                    ValueError,
                ):
                    same_target = (
                        current == target
                        and event_target == target
                    )

                if not same_target:
                    continue

                name = str(
                    definition.get("name", "")
                ).strip()

                if name not in tags:
                    continue

                value = tags.get(name)

                if value is None:
                    continue

                insert_tag_value(
                    self.company_id,
                    name,
                    value,
                    "TRIGGER",
                    timestamp=timestamp,
                )

                trigger_events.append(name)

                print(
                    "TRIGGER INSERT:",
                    "Company=", self.company_id,
                    "Tag=", name,
                    "Value=", value,
                    "Register=", register_key,
                    "TriggerValue=", event_target,
                )

        report_triggered = any(
            name.lower() in report_tag_set
            for name in trigger_events
        )

        if (
            report_triggered
            and report_products
        ):
            report_timestamp = (
                timestamp
                or datetime.now().strftime(
                    "%Y-%m-%d %H:%M:%S"
                )
            )

            report_id = save_report_snapshot(
                self.company_id,
                tags,
                report_products,
                timestamp=report_timestamp,
                trigger_tag=trigger_events[0] if trigger_events else None,
                trigger_register=next(
                    (definition.get("trigger_register")
                     for definition in definitions
                     if isinstance(definition, dict)
                     and str(definition.get("name", "")).strip() in trigger_events
                     and definition.get("trigger_register") not in (None, "")),
                    None,
                ),
                trigger_value=event_target,
            )

            if report_id is not None:
                print(
                    "REPORT TRIGGER SNAPSHOT SAVED:",
                    "Company=", self.company_id,
                    "ReportID=", report_id,
                    "Tags=", trigger_events,
                    "ContractCode=", tags.get("ContractCode"),
                    "ProductCode=", tags.get("ProductCode"),
                )

        return len(trigger_events)

    def _save_time_report_snapshot(self, tags, definitions, report_products, timestamp=None):
        """Save a report snapshot when any selected ReportOutput tag is TIME-based."""
        if not report_products or not isinstance(tags, dict):
            return 0

        definition_map = {}
        for definition in definitions or []:
            if not isinstance(definition, dict):
                continue
            name = str(definition.get("name", "")).strip()
            if name:
                definition_map[name.lower()] = definition

        selected = []
        time_due = False
        now = time.monotonic()

        for product in report_products:
            if not isinstance(product, dict):
                continue

            tag = str(product.get("tag", "")).strip()
            if not tag:
                continue

            definition = definition_map.get(tag.lower())
            if not definition:
                continue

            mode = str(definition.get("storage", "TIME")).strip().upper()
            if mode != "TIME":
                continue

            selected.append(product)

            try:
                interval = float(definition.get("interval", 0) or 0)
            except (TypeError, ValueError):
                interval = 0.0

            key = (int(self.company_id), tag.lower())
            last = self._report_time_memory.get(key, 0.0)

            if interval <= 0 or now - last >= interval:
                time_due = True

        if not selected or not time_due:
            return 0

        snapshot_tags = {}
        tag_lookup = {
            str(name).strip().lower(): (name, value)
            for name, value in tags.items()
        }

        for product in report_products:
            if not isinstance(product, dict):
                continue
            tag = str(product.get("tag", "")).strip()
            if not tag:
                continue
            item = tag_lookup.get(tag.lower())
            if item is None or item[1] is None:
                continue
            snapshot_tags[item[0]] = item[1]

        if not snapshot_tags:
            return 0

        if timestamp is None:
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        report_id = save_report_snapshot(
            self.company_id,
            snapshot_tags,
            report_products,
            timestamp=timestamp,
        )

        if report_id is None:
            return 0

        for product in selected:
            tag = str(product.get("tag", "")).strip().lower()
            self._report_time_memory[(int(self.company_id), tag)] = now

        print(
            "REPORT TIME SNAPSHOT SAVED:",
            "Company=", self.company_id,
            "ReportID=", report_id,
            "Tags=", [p.get("tag") for p in report_products if isinstance(p, dict)]
        )

        return 1

    def execute(self, data=None):

        if data is None:
            data = {}

        tags = data.get("Tags", {})
        definitions = data.get("TagDefinitions", [])
        registers = data.get("Registers", {})
        edge_trigger_events = data.get("EdgeTriggerEvents", [])

        if not definitions:
            return data

        definition_signature = self._signature(
            definitions
        )

        if definition_signature != self._last_definition_signature:
            TagRegistry.sync(
                self.company_id,
                definitions
            )
            self._last_definition_signature = definition_signature

        report_products = self._get_report_products()
        report_signature = self._signature(
            report_products
        )

        if report_signature != self._last_report_signature:
            self._cached_report_products = report_products
            self._last_report_signature = report_signature

        report_products = self._cached_report_products

        report_tags = [
            str(item.get("tag", "")).strip()
            for item in report_products
            if (
                isinstance(item, dict)
                and str(item.get("tag", "")).strip()
            )
        ]

        timestamp = data.get("Timestamp")

        # EdgeTriggerEvents are already edge-detected upstream. They are the
        # authoritative Trigger signal for the historian-backed Edge path.
        # Use them directly so a missing raw trigger register cannot suppress
        # the report. The existing raw-register path remains as a fallback.
        edge_report_written = self._save_edge_trigger_events(
            tags,
            edge_trigger_events,
            report_products,
        )

        trigger_written = 0
        if not edge_trigger_events:
            trigger_written = self._process_trigger_tags(
                tags,
                definitions,
                registers,
                report_products,
                timestamp=timestamp,
            )
        else:
            # Still inject ManagementPanel context into the shared payload for
            # downstream branches, using the exact same register configuration.
            management_context = self._get_management_context_tags(registers)
            for context_name, context_value in management_context.items():
                if context_value is not None:
                    tags[context_name] = context_value

        non_trigger_definitions = [
            definition
            for definition in definitions
            if str(
                definition.get("storage", "TIME")
            ).strip().upper() != "TRIGGER"
        ]

        report_written = edge_report_written

        if trigger_written == 0 and edge_report_written == 0:
            report_written = self._save_time_report_snapshot(
                tags,
                non_trigger_definitions,
                report_products,
                timestamp=timestamp,
            )

        written = self.historian.process(
            self.company_id,
            tags,
            non_trigger_definitions,
            registers,
            report_tags=(
                report_tags
                if trigger_written == 0 and edge_report_written == 0
                else []
            )
        )

        data["SQL_Written"] = written + trigger_written + report_written
        data["Report_Written"] = trigger_written + report_written

        return data
