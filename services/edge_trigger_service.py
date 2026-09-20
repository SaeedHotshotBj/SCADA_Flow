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


def _node_connections(nodes, node_id, direction="outputs"):
    node = nodes.get(str(node_id), {})
    section = node.get(direction, {}) if isinstance(node, dict) else {}
    if not isinstance(section, dict):
        return []
    result = []
    for item in section.values():
        if not isinstance(item, dict):
            continue
        connections = item.get("connections", [])
        if not isinstance(connections, list):
            continue
        for connection in connections:
            if isinstance(connection, dict) and connection.get("node") is not None:
                result.append(str(connection["node"]))
    return result


def _all_tag_definitions(nodes, plc_id, company_id=None):
    """Resolve TagMapper definitions using the actual PLCReader graph branch."""
    target_plc = int(plc_id)
    company_plc_ids = []

    needs_fallback_ids = any(
        isinstance(node, dict)
        and node.get("name") == "PLCReader"
        and (node.get("data", {}) or {}).get("plc_id", (node.get("data", {}) or {}).get("PLC_ID")) in (None, "")
        for node in nodes.values()
    )
    if company_id is not None and needs_fallback_ids:
        try:
            conn = get_connection()
            try:
                rows = conn.execute(
                    "SELECT PLC_ID FROM PLCs WHERE CompanyID=? ORDER BY PLC_ID",
                    (int(company_id),),
                ).fetchall()
                company_plc_ids = [int(row["PLC_ID"]) for row in rows]
            finally:
                conn.close()
        except Exception as exc:
            print("PRODUCTION FLOW PLC LOOKUP ERROR:", exc)

    plc_reader_to_id = {}
    used_ids = set()
    fallback_index = 0

    for node_id, node in nodes.items():
        if not isinstance(node, dict) or node.get("name") != "PLCReader":
            continue
        data = _node_config(node)
        raw = data.get("plc_id", data.get("PLC_ID"))
        try:
            reader_plc_id = int(raw) if raw not in (None, "") else None
        except (TypeError, ValueError):
            reader_plc_id = None

        if reader_plc_id is None:
            while (
                fallback_index < len(company_plc_ids)
                and company_plc_ids[fallback_index] in used_ids
            ):
                fallback_index += 1
            if fallback_index < len(company_plc_ids):
                reader_plc_id = company_plc_ids[fallback_index]
                fallback_index += 1

        if reader_plc_id is None or reader_plc_id in used_ids:
            continue
        used_ids.add(reader_plc_id)
        plc_reader_to_id[str(node_id)] = reader_plc_id

    result = []
    for node_id, node in nodes.items():
        if not isinstance(node, dict) or node.get("name") != "TagMapper":
            continue

        mappings = _node_config(node).get("mappings", [])
        if not isinstance(mappings, list):
            continue

        upstream_ids = []
        for source_id, source_plc_id in plc_reader_to_id.items():
            if str(node_id) in _node_connections(nodes, source_id, "outputs"):
                upstream_ids.append(source_plc_id)
        upstream_ids = list(dict.fromkeys(upstream_ids))

        for item in mappings:
            if not isinstance(item, dict) or not str(item.get("name", "")).strip():
                continue

            explicit = item.get("plc_id", item.get("PLC_ID"))
            if explicit not in (None, ""):
                try:
                    mapping_plc_ids = [int(explicit)]
                except (TypeError, ValueError):
                    continue
            elif upstream_ids:
                mapping_plc_ids = upstream_ids
            elif len(company_plc_ids) == 1:
                mapping_plc_ids = company_plc_ids
            else:
                continue

            if target_plc not in mapping_plc_ids:
                continue

            definition = dict(item)
            definition["plc_id"] = target_plc
            result.append(definition)

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
            definitions = _all_tag_definitions(_nodes(company_id), plc_id, company_id)

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
