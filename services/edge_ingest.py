"""Atomic and idempotent ingestion for SCADA_FLOW_EDGE Store & Forward."""

from datetime import datetime
import json
import math

from database import get_connection, get_company_flow


def ensure_edge_event_schema():
    conn = get_connection()
    try:
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        if "PLC_Data" in tables:
            columns = {row[1] for row in conn.execute("PRAGMA table_info(PLC_Data)").fetchall()}
            if "PLC_ID" not in columns:
                conn.execute("ALTER TABLE PLC_Data ADD COLUMN PLC_ID INTEGER")
            if "EventID" not in columns:
                conn.execute("ALTER TABLE PLC_Data ADD COLUMN EventID TEXT")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_plc_data_company_plc_tag_time ON PLC_Data(CompanyID, PLC_ID, TagName, Timestamp)")
            conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS uq_plc_data_event_id ON PLC_Data(EventID) WHERE EventID IS NOT NULL")
        if "TagHistory" in tables:
            columns = {row[1] for row in conn.execute("PRAGMA table_info(TagHistory)").fetchall()}
            if "EventID" not in columns:
                conn.execute("ALTER TABLE TagHistory ADD COLUMN EventID TEXT")
            conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS uq_tag_history_event_id ON TagHistory(EventID) WHERE EventID IS NOT NULL")

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS EdgeEventLedger (
                EventID TEXT PRIMARY KEY,
                CompanyID INTEGER NOT NULL,
                PLC_ID INTEGER NOT NULL,
                TagName TEXT NOT NULL,
                EventTimestamp TEXT NOT NULL,
                ReceivedAt TEXT NOT NULL
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_edge_ledger_company_time ON EdgeEventLedger(CompanyID, PLC_ID, TagName, EventTimestamp)")
        conn.commit()
    finally:
        conn.close()


def _parse_timestamp(value):
    if value is None or str(value).strip() == "":
        return datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    text = str(value).strip().replace("T", " ")
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if dt.tzinfo is not None:
            from zoneinfo import ZoneInfo
            dt = dt.astimezone(ZoneInfo("Asia/Tehran")).replace(tzinfo=None)
        return dt.strftime("%Y-%m-%d %H:%M:%S.%f").rstrip("0").rstrip(".")
    except Exception:
        for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
            try:
                return datetime.strptime(text, fmt).strftime("%Y-%m-%d %H:%M:%S.%f").rstrip("0").rstrip(".")
            except ValueError:
                pass
    raise ValueError("Invalid Timestamp")


def _validate_numeric(value):
    if isinstance(value, bool):
        return int(value)
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise ValueError("Value must be numeric")
    if not math.isfinite(number):
        raise ValueError("Value must be finite")
    return number


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


def _flow_tag_storage(company_id):
    """Resolve tag storage and PLC identity strictly from the saved company Flow."""
    flow_json = get_company_flow(company_id)
    if not flow_json:
        return {}
    try:
        flow = json.loads(flow_json) if isinstance(flow_json, str) else flow_json
    except Exception:
        return {}

    nodes = flow.get("drawflow", {}).get("Home", {}).get("data", {}) or {}
    if not isinstance(nodes, dict):
        return {}

    conn = get_connection()
    try:
        plc_rows = conn.execute(
            "SELECT PLC_ID FROM PLCs WHERE CompanyID=? ORDER BY PLC_ID",
            (int(company_id),),
        ).fetchall()
    finally:
        conn.close()

    company_plc_ids = [int(row["PLC_ID"]) for row in plc_rows]
    plc_reader_to_id = {}
    used_ids = set()
    fallback_index = 0

    for node_id, node in nodes.items():
        if not isinstance(node, dict) or node.get("name") != "PLCReader":
            continue
        data = node.get("data", {}) or {}
        raw_plc_id = data.get("plc_id", data.get("PLC_ID"))
        plc_id = None
        try:
            if raw_plc_id not in (None, ""):
                plc_id = int(raw_plc_id)
        except (TypeError, ValueError):
            plc_id = None
        if plc_id is None:
            while fallback_index < len(company_plc_ids) and company_plc_ids[fallback_index] in used_ids:
                fallback_index += 1
            if fallback_index < len(company_plc_ids):
                plc_id = company_plc_ids[fallback_index]
                fallback_index += 1
        if plc_id is None or plc_id not in company_plc_ids or plc_id in used_ids:
            continue
        used_ids.add(plc_id)
        plc_reader_to_id[str(node_id)] = plc_id

    allowed = {}
    for node_id, node in nodes.items():
        if not isinstance(node, dict) or node.get("name") != "TagMapper":
            continue
        data = node.get("data", {}) or {}
        if isinstance(data.get("config"), dict):
            merged = dict(data["config"])
            merged.update({k: v for k, v in data.items() if k != "config"})
            data = merged
        mappings = data.get("mappings", [])
        if not isinstance(mappings, list):
            continue

        upstream_ids = [
            plc_reader_to_id[source]
            for source in plc_reader_to_id
            if str(node_id) in _node_connections(nodes, source, "outputs")
        ]
        upstream_ids = list(dict.fromkeys(upstream_ids))

        for mapping in mappings:
            if not isinstance(mapping, dict):
                continue
            name = str(mapping.get("name", "")).strip()
            if not name:
                continue
            storage = str(mapping.get("storage", "TIME")).upper().strip()
            if storage not in {"TIME", "TRIGGER"}:
                continue

            explicit = mapping.get("plc_id", mapping.get("PLC_ID"))
            mapping_plc_ids = []
            if explicit not in (None, ""):
                try:
                    mapping_plc_ids = [int(explicit)]
                except (TypeError, ValueError):
                    mapping_plc_ids = []
            elif upstream_ids:
                mapping_plc_ids = upstream_ids
            elif len(company_plc_ids) == 1:
                mapping_plc_ids = [company_plc_ids[0]]

            for plc_id in mapping_plc_ids:
                if plc_id in company_plc_ids:
                    allowed[(plc_id, name.lower())] = storage

    return allowed


def ingest_items(items):
    ensure_edge_event_schema()
    if not isinstance(items, list):
        raise ValueError("items must be a list")

    acks = []
    errors = []
    inserted = 0
    conn = get_connection()
    flow_cache = {}
    try:
        conn.execute("BEGIN IMMEDIATE")
        for item in items:
            if not isinstance(item, dict):
                continue
            event_id = str(item.get("EventID", "")).strip()
            if not event_id:
                errors.append({"EventID": event_id, "Error": "EventID is required"})
                continue
            try:
                plc_id = int(item.get("PLC_ID"))
                tag = str(item.get("TagName", "")).strip()
                if not tag:
                    raise ValueError("TagName is required")
                value = _validate_numeric(item.get("Value"))
                timestamp = _parse_timestamp(item.get("Timestamp"))
            except Exception as exc:
                errors.append({"EventID": event_id, "Error": str(exc)})
                continue

            plc = conn.execute("SELECT PLC_ID, CompanyID FROM PLCs WHERE PLC_ID=?", (plc_id,)).fetchone()
            if plc is None:
                errors.append({"EventID": event_id, "Error": "PLC not found"})
                continue
            company_id = int(plc["CompanyID"])

            if company_id not in flow_cache:
                flow_cache[company_id] = _flow_tag_storage(company_id)
            storage_type = flow_cache[company_id].get((plc_id, tag.lower()))
            if storage_type is None:
                errors.append({"EventID": event_id, "Error": "Tag is not defined for this PLC by the company Flow"})
                continue

            existing = conn.execute("SELECT EventID FROM EdgeEventLedger WHERE EventID=?", (event_id,)).fetchone()
            if existing is not None:
                acks.append(event_id)
                continue

            received_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f").rstrip("0").rstrip(".")
            conn.execute(
                "INSERT INTO EdgeEventLedger(EventID, CompanyID, PLC_ID, TagName, EventTimestamp, ReceivedAt) VALUES (?, ?, ?, ?, ?, ?)",
                (event_id, company_id, plc_id, tag, timestamp, received_at),
            )
            conn.execute(
                "INSERT INTO PLC_Data(CompanyID, PLC_ID, TagName, Value, StorageType, Timestamp, EventID) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (company_id, plc_id, tag, value, storage_type, timestamp, event_id),
            )
            conn.execute(
                "INSERT INTO TagHistory(CompanyID, PLC_ID, TagName, Value, Timestamp, EventID) VALUES (?, ?, ?, ?, ?, ?)",
                (company_id, plc_id, tag, value, timestamp, event_id),
            )
            acks.append(event_id)
            inserted += 1

        conn.commit()
        return {"acks": acks, "errors": errors, "inserted": inserted}
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


__all__ = ["ensure_edge_event_schema", "ingest_items"]
