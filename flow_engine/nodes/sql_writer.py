# =====================================================
# SCADA_FLOW SQL WRITER NODE
# PLC-aware historian / trigger / report storage.
# =====================================================

import json
import time
from datetime import datetime

from database import get_company_flow, get_connection
from services.historian_service import HistorianService
from services.plc_identity import insert_plc_data, ensure_plc_identity_schema
from services.tag_registry import TagRegistry
from services.report_service import save_report_snapshot


class SQLWriter:

    def __init__(self, config=None):
        self.config = config or {}
        self.company_id = self.config.get("company_id", 1)
        self.historian = HistorianService()
        self._last_definition_signature = None
        self._last_report_signature = None
        self._cached_report_products = []
        self._report_time_memory = {}

    def _plc_id(self, data):
        value = data.get("PLC_ID")
        if value is None and isinstance(data.get("PLC"), dict):
            value = data["PLC"].get("PLC_ID")
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    def _get_report_products(self):
        try:
            flow = get_company_flow(self.company_id)
            if not flow:
                return []
            if isinstance(flow, str):
                flow = json.loads(flow)
            nodes = flow.get("drawflow", {}).get("Home", {}).get("data", {})
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

    def _signature(self, value):
        try:
            return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        except (TypeError, ValueError):
            return repr(value)

    def _get_management_context_tags(self, registers):
        result = {}
        if not isinstance(registers, dict):
            return result
        try:
            flow = get_company_flow(self.company_id)
            if not flow:
                return result
            if isinstance(flow, str):
                flow = json.loads(flow)
            nodes = flow.get("drawflow", {}).get("Home", {}).get("data", {})
            config = None
            for node in nodes.values():
                if node.get("name") == "ManagementPanel":
                    raw = node.get("data", {}) or {}
                    config = raw.get("config", raw) or {}
                    break
            if not isinstance(config, dict):
                return result

            def reg(*names):
                for name in names:
                    value = config.get(name)
                    if value not in (None, ""):
                        try:
                            return int(float(value))
                        except (TypeError, ValueError):
                            pass
                return None

            for key, address in (
                ("ContractCode", reg("contract_code_register", "contractCodeRegister", "contract_code_plc_register", "contractCodePLCRegister")),
                ("ProductCode", reg("product_code_register", "productCodeRegister", "product_code_plc_register", "productCodePLCRegister")),
            ):
                if address is None:
                    continue
                for candidate in (str(address), address):
                    if candidate in registers and registers[candidate] not in (None, ""):
                        result[key] = registers[candidate]
                        break
        except Exception as exc:
            print("SQLWRITER MANAGEMENT CONTEXT ERROR:", exc)
        return result

    def _ensure_trigger_state_table(self):
        ensure_plc_identity_schema()
        conn = get_connection()
        try:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS FlowTriggerState (
                    CompanyID INTEGER NOT NULL,
                    PLC_ID INTEGER NOT NULL,
                    TriggerRegister TEXT NOT NULL,
                    LastValue REAL,
                    UpdatedAt TEXT NOT NULL,
                    PRIMARY KEY (CompanyID, PLC_ID, TriggerRegister)
                )
            """)
            # Migrate a legacy table whose primary key was CompanyID+TriggerRegister.
            cols = {row["name"] for row in conn.execute("PRAGMA table_info(FlowTriggerState)").fetchall()}
            if "PLC_ID" not in cols:
                conn.execute("ALTER TABLE FlowTriggerState ADD COLUMN PLC_ID INTEGER")
                conn.execute("UPDATE FlowTriggerState SET PLC_ID = 0 WHERE PLC_ID IS NULL")
            conn.commit()
        finally:
            conn.close()

    def _trigger_rising_edge(self, plc_id, trigger_register, current, target):
        self._ensure_trigger_state_table()
        conn = get_connection()
        try:
            conn.execute("BEGIN IMMEDIATE")
            key = str(trigger_register)
            row = conn.execute("""
                SELECT LastValue FROM FlowTriggerState
                WHERE CompanyID = ? AND PLC_ID = ? AND TriggerRegister = ?
            """, (int(self.company_id), int(plc_id), key)).fetchone()
            previous = None if row is None else row["LastValue"]
            try:
                current_number = float(current)
                target_number = float(target)
                previous_number = None if previous is None else float(previous)
                rising = previous_number == 0.0 and current_number == target_number
                stored = current_number
            except (TypeError, ValueError):
                rising = previous == 0 and current == target
                stored = current

            conn.execute("""
                INSERT INTO FlowTriggerState
                (CompanyID, PLC_ID, TriggerRegister, LastValue, UpdatedAt)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(CompanyID, PLC_ID, TriggerRegister)
                DO UPDATE SET LastValue=excluded.LastValue, UpdatedAt=excluded.UpdatedAt
            """, (int(self.company_id), int(plc_id), key, stored, datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
            conn.commit()
            return rising
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _register_value(self, registers, address):
        if str(address) in registers:
            return registers[str(address)]
        return registers.get(address)

    def _process_trigger_tags(self, plc_id, tags, definitions, registers, report_products, timestamp=None):
        context = self._get_management_context_tags(registers)
        tags.update({k: v for k, v in context.items() if v is not None})
        groups = {}
        for definition in definitions or []:
            if not isinstance(definition, dict) or str(definition.get("storage", "")).upper() != "TRIGGER":
                continue
            name = str(definition.get("name", "")).strip()
            trigger_register = definition.get("trigger_register")
            current = self._register_value(registers, trigger_register)
            if not name or trigger_register in (None, "") or current is None or name not in tags:
                continue
            groups.setdefault(str(trigger_register), {"current": current, "items": []})["items"].append(definition)

        trigger_names = []
        event_target = None
        for register_key, group in groups.items():
            targets = []
            for definition in group["items"]:
                target = definition.get("trigger_value")
                if target not in targets:
                    targets.append(target)
            for target in targets:
                if self._trigger_rising_edge(plc_id, register_key, group["current"], target):
                    event_target = target
                    for definition in group["items"]:
                        try:
                            same = float(group["current"]) == float(definition.get("trigger_value")) == float(target)
                        except (TypeError, ValueError):
                            same = group["current"] == definition.get("trigger_value") == target
                        name = str(definition.get("name", "")).strip()
                        if same and name in tags and tags[name] is not None:
                            insert_plc_data(self.company_id, plc_id, name, tags[name], "TRIGGER", timestamp)
                            trigger_names.append(name)
                    break

        if trigger_names and report_products:
            report_tags = {str(p.get("tag", "")).strip().lower() for p in report_products if isinstance(p, dict)}
            matched = next((n for n in trigger_names if n.lower() in report_tags), None)
            if matched:
                save_report_snapshot(
                    self.company_id, tags, report_products,
                    timestamp=timestamp,
                    trigger_tag=matched,
                    trigger_register=next((d.get("trigger_register") for d in definitions if str(d.get("name", "")).strip() == matched), None),
                    trigger_value=event_target,
                    plc_id=plc_id,
                )
        return len(trigger_names)

    def _save_edge_trigger_events(self, plc_id, tags, events, report_products, registers):
        if not plc_id or not isinstance(events, list) or not report_products:
            return 0
        context = self._get_management_context_tags(registers)
        saved = 0
        report_names = {str(p.get("tag", "")).strip().lower() for p in report_products if isinstance(p, dict)}
        for event in events:
            if not isinstance(event, dict):
                continue
            event_tags = event.get("tags", {}) or {}
            matched = next((str(n).strip() for n in event_tags if str(n).strip().lower() in report_names), None)
            if not matched:
                continue
            snapshot = dict(tags)
            snapshot.update({k: v for k, v in context.items() if v is not None})
            snapshot.update({str(k).strip(): v for k, v in event_tags.items() if v is not None})
            report_id = save_report_snapshot(
                self.company_id, snapshot, report_products,
                timestamp=str(event.get("timestamp") or datetime.now().strftime("%Y-%m-%d %H:%M:%S")).replace("T", " "),
                trigger_tag=matched,
                trigger_register=event.get("register"),
                trigger_value=event_tags.get(matched),
                plc_id=plc_id,
            )
            if report_id is not None:
                saved += 1
        return saved

    def _save_time_report_snapshot(self, plc_id, tags, definitions, report_products, timestamp=None):
        if not plc_id or not report_products:
            return 0
        definition_map = {str(d.get("name", "")).strip().lower(): d for d in definitions if isinstance(d, dict)}
        selected = []
        due = False
        now = time.monotonic()
        for product in report_products:
            if not isinstance(product, dict):
                continue
            tag = str(product.get("tag", "")).strip()
            definition = definition_map.get(tag.lower())
            if not tag or not definition or str(definition.get("storage", "TIME")).upper() != "TIME":
                continue
            selected.append(product)
            try:
                interval = float(definition.get("interval", 0) or 0)
            except (TypeError, ValueError):
                interval = 0
            key = (int(self.company_id), int(plc_id), tag.lower())
            if interval <= 0 or now - self._report_time_memory.get(key, 0) >= interval:
                due = True
        if not selected or not due:
            return 0
        snapshot = {p["tag"]: tags[p["tag"]] for p in selected if p.get("tag") in tags and tags[p.get("tag")] is not None}
        if not snapshot:
            return 0
        report_id = save_report_snapshot(self.company_id, snapshot, report_products, timestamp=timestamp, plc_id=plc_id)
        if report_id is None:
            return 0
        for p in selected:
            self._report_time_memory[(int(self.company_id), int(plc_id), str(p["tag"]).lower())] = now
        return 1

    def execute(self, data=None):
        data = data or {}
        plc_id = self._plc_id(data)
        if plc_id is None:
            raise ValueError("SQLWriter requires PLC_ID in the runtime payload")

        tags = data.get("Tags", {}) or {}
        definitions = data.get("TagDefinitions", []) or []
        registers = data.get("Registers", {}) or {}
        edge_events = data.get("EdgeTriggerEvents", []) or []
        if not definitions:
            return data

        signature = self._signature(definitions)
        if signature != self._last_definition_signature:
            ensure_plc_identity_schema()
            TagRegistry.sync(self.company_id, definitions)
            self._last_definition_signature = signature

        report_products = self._get_report_products()
        report_signature = self._signature(report_products)
        if report_signature != self._last_report_signature:
            self._cached_report_products = report_products
            self._last_report_signature = report_signature
        report_products = self._cached_report_products

        timestamp = data.get("Timestamp")
        edge_report_written = self._save_edge_trigger_events(plc_id, tags, edge_events, report_products, registers)
        trigger_written = 0
        if not edge_events:
            trigger_written = self._process_trigger_tags(plc_id, tags, definitions, registers, report_products, timestamp)

        non_trigger = [d for d in definitions if str(d.get("storage", "TIME")).upper() != "TRIGGER"]
        report_tags = [str(p.get("tag", "")).strip() for p in report_products if isinstance(p, dict) and str(p.get("tag", "")).strip()]
        report_written = edge_report_written or trigger_written
        if not report_written:
            report_written = self._save_time_report_snapshot(plc_id, tags, non_trigger, report_products, timestamp)

        written = self.historian.process(self.company_id, plc_id, tags, non_trigger, registers, report_tags=report_tags if not report_written else [])
        data["PLC_ID"] = plc_id
        data["SQL_Written"] = written + trigger_written + report_written
        data["Report_Written"] = trigger_written + report_written
        return data
