"""Flow-driven trigger edge detector.

The Edge sends a synthetic signal tag for every configured trigger register.
This service consumes those ordered signals, maintains persistent state, and
creates ProductionEvents. TIME tags remain historian samples; TRIGGER tags are
sampled while their cycle is active and are therefore available for the full
production window.
"""

import json

from database import get_connection, get_company_flow
from services.production_event_service import (
    ensure_production_event_schema,
    process_trigger_signal,
)
from services.report_service import save_report_snapshot

SIGNAL_PREFIX = "__TRIGGER_REGISTER_"


def _nodes(company_id):
    flow = get_company_flow(company_id)
    if not flow:
        return {}
    if isinstance(flow, str):
        try:
            flow = json.loads(flow)
        except Exception:
            return {}
    return flow.get("drawflow", {}).get("Home", {}).get("data", {}) or {}


def _node_config(node):
    data = node.get("data", {}) or {}
    config = data.get("config", data)
    if not isinstance(config, dict):
        return {}
    merged = dict(config)
    merged.update({key: value for key, value in data.items() if key != "config"})
    return merged


def _trigger_registers(definitions):
    result = set()
    for item in definitions or []:
        if not isinstance(item, dict):
            continue
        if str(item.get("storage", "TIME")).strip().upper() != "TRIGGER":
            continue
        try:
            result.add(int(item.get("trigger_register")))
        except (TypeError, ValueError):
            continue
    return sorted(result)


def _all_tag_definitions(nodes, plc_id):
    result = []
    target_plc = int(plc_id)
    for node in nodes.values():
        if not isinstance(node, dict) or node.get("name") != "TagMapper":
            continue
        mappings = _node_config(node).get("mappings", [])
        if not isinstance(mappings, list):
            continue
        for item in mappings:
            if not isinstance(item, dict) or not str(item.get("name", "")).strip():
                continue
            explicit = item.get("plc_id", item.get("PLC_ID"))
            if explicit not in (None, ""):
                try:
                    item_plc = int(explicit)
                except (TypeError, ValueError):
                    continue
            else:
                item_plc = target_plc
            if item_plc == target_plc:
                result.append(item)
    return result


def _latest_snapshot(conn, company_id, plc_id, definitions, at_id):
    snapshot = {}
    names = []
    for item in definitions or []:
        name = str(item.get("name", "")).strip()
        if name and name.lower() not in {x.lower() for x in names}:
            names.append(name)

    for name in names:
        row = conn.execute(
            """
            SELECT TagName, Value
            FROM PLC_Data
            WHERE CompanyID=? AND PLC_ID=?
              AND LOWER(TagName)=LOWER(?)
              AND ID <= ?
            ORDER BY ID DESC
            LIMIT 1
            """,
            (int(company_id), int(plc_id), name, int(at_id)),
        ).fetchone()
        if row is not None:
            snapshot[str(row["TagName"])] = row["Value"]
    return snapshot


def _report_configs(nodes):
    configs = []
    for node_id, node in nodes.items():
        if not isinstance(node, dict) or node.get("name") != "ReportOutput":
            continue
        inputs = node.get("inputs", {}) or {}
        connected = any(
            isinstance(item, dict) and bool(item.get("connections", []))
            for item in inputs.values()
        )
        if not connected:
            continue
        products = _node_config(node).get("products", [])
        clean = [
            item for item in products
            if isinstance(item, dict) and str(item.get("tag", "")).strip()
        ]
        if clean:
            configs.append((str(node_id), clean))
    return configs


def _write_event_reports(company_id, event):
    nodes = _nodes(company_id)
    configs = _report_configs(nodes)
    if not configs:
        return

    tags = dict(event.get("tags") or {})
    tags["WorkTimeSeconds"] = event.get("duration_seconds", 0.0)
    tags["DurationSeconds"] = event.get("duration_seconds", 0.0)
    tags["WorkTimeMinutes"] = float(event.get("duration_seconds", 0.0) or 0.0) / 60.0
    tags["WorkTimeHours"] = float(event.get("duration_seconds", 0.0) or 0.0) / 3600.0
    if event.get("start_timestamp"):
        tags["ProductionStartTimestamp"] = event["start_timestamp"]
    if event.get("end_timestamp"):
        tags["ProductionEndTimestamp"] = event["end_timestamp"]

    for node_id, products in configs:
        try:
            save_report_snapshot(
                company_id,
                tags,
                products,
                timestamp=event.get("timestamp"),
                trigger_tag=f"{SIGNAL_PREFIX}{event.get('register')}",
                trigger_register=event.get("register"),
                trigger_value=event.get("trigger_value"),
                plc_id=event.get("PLC_ID"),
                trigger_edge=event.get("edge"),
                start_timestamp=event.get("start_timestamp"),
                end_timestamp=event.get("end_timestamp"),
                duration_seconds=event.get("duration_seconds", 0),
                start_complete=event.get("start_complete", 1),
                trigger_event_id=event.get("event_id"),
                report_node_id=node_id,
            )
        except Exception as exc:
            print(
                "PRODUCTION REPORT ERROR:",
                "CompanyID=", company_id,
                "ReportNode=", node_id,
                "EventID=", event.get("event_id"),
                "Reason=", exc,
            )


class EdgeTriggerService:
    def enrich(self, payload):
        if not isinstance(payload, dict):
            return payload

        try:
            company_id = int(payload.get("CompanyID") or payload.get("company_id"))
            plc_id = int(payload.get("PLC_ID"))
        except (TypeError, ValueError):
            return payload

        definitions = payload.get("TagDefinitions", [])
        if not isinstance(definitions, list) or not definitions:
            definitions = _all_tag_definitions(_nodes(company_id), plc_id)

        registers = _trigger_registers(definitions)
        if not registers:
            return payload

        ensure_production_event_schema()
        emitted = []
        conn = get_connection()
        try:
            for register in registers:
                signal_name = f"{SIGNAL_PREFIX}{register}"
                rows = conn.execute(
                    """
                    SELECT ID, Value, Timestamp
                    FROM PLC_Data
                    WHERE CompanyID=? AND PLC_ID=?
                      AND LOWER(TagName)=LOWER(?)
                      AND StorageType='TRIGGER_SIGNAL'
                      AND ID > COALESCE(
                          (SELECT MIN(LastSignalID)
                           FROM FlowTriggerState
                           WHERE CompanyID=? AND PLC_ID=? AND TriggerRegister=?),
                          0
                      )
                    ORDER BY ID ASC
                    LIMIT 500
                    """,
                    (
                        company_id,
                        plc_id,
                        signal_name,
                        company_id,
                        plc_id,
                        register,
                    ),
                ).fetchall()

                for row in rows:
                    snapshot = _latest_snapshot(
                        conn,
                        company_id,
                        plc_id,
                        definitions,
                        row["ID"],
                    )
                    events = process_trigger_signal(
                        company_id,
                        plc_id,
                        register,
                        row["Value"],
                        row["Timestamp"],
                        row["ID"],
                        definitions,
                        snapshot,
                    )
                    for event in events:
                        event["timestamp"] = event.get("timestamp") or row["Timestamp"]
                        _write_event_reports(company_id, event)
                        emitted.append(event)
        finally:
            conn.close()

        if emitted:
            payload.setdefault("EdgeTriggerEvents", []).extend(emitted)

        return payload


__all__ = ["EdgeTriggerService"]
