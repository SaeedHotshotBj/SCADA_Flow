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

SIGNAL_PREFIX = "__TRIGGER_REGISTER_"


def _flow(company_id):
    raw = get_company_flow(company_id)
    if not raw:
        return {}
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except Exception:
            return {}
    return raw if isinstance(raw, dict) else {}


def _nodes(company_id):
    flow = _flow(company_id)
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
    seen = set()
    for item in definitions or []:
        name = str(item.get("name", "")).strip()
        key = name.lower()
        if name and key not in seen:
            seen.add(key)
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


def _write_event_reports(company_id, event):
    """Send the production event through the actual company Drawflow.

    No ReportOutput configuration is queried or evaluated here. The Flow
    graph itself decides whether a ReportOutput exists and which nodes execute
    before it.
    """
    flow = _flow(company_id)
    if not flow:
        return

    try:
        from flow_runner import FlowRunner
        FlowRunner(flow, company_id).execute_production_event(event)
    except Exception as exc:
        print(
            "PRODUCTION FLOW REPORT ERROR:",
            "CompanyID=", company_id,
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
