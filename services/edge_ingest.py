"""Atomic and idempotent ingestion for SCADA_FLOW_EDGE Store & Forward."""

from datetime import datetime
import json
import math

from database import get_connection, get_company_flow


def _edge_trace(message, *parts):
    """Emit a clearly searchable, stdout-only diagnostic trace for Edge ingest."""
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
    if parts:
        print(f"EDGE TRACE [{stamp}] {message}", *parts, flush=True)
    else:
        print(f"EDGE TRACE [{stamp}] {message}", flush=True)


def ensure_edge_event_schema():
    conn = get_connection()
    try:
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        if "PLC_Data" in tables:
            columns = {row[1] for row in conn.execute("PRAGMA table_info(PLC_Data)").fetchall()}
            _edge_trace("SCHEMA PLC_Data columns", sorted(columns))
            if "PLC_ID" not in columns:
                conn.execute("ALTER TABLE PLC_Data ADD COLUMN PLC_ID INTEGER")
                _edge_trace("SCHEMA PLC_Data ADD COLUMN", "PLC_ID")
            if "EventID" not in columns:
                conn.execute("ALTER TABLE PLC_Data ADD COLUMN EventID TEXT")
                _edge_trace("SCHEMA PLC_Data ADD COLUMN", "EventID")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_plc_data_company_plc_tag_time ON PLC_Data(CompanyID, PLC_ID, TagName, Timestamp)")
            conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS uq_plc_data_event_id ON PLC_Data(EventID) WHERE EventID IS NOT NULL")
        if "TagHistory" in tables:
            columns = {row[1] for row in conn.execute("PRAGMA table_info(TagHistory)").fetchall()}
            if "PLC_ID" not in columns:
                conn.execute("ALTER TABLE TagHistory ADD COLUMN PLC_ID INTEGER")
            if "EventID" not in columns:
                conn.execute("ALTER TABLE TagHistory ADD COLUMN EventID TEXT")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_tag_history_company_plc_tag_time ON TagHistory(CompanyID, PLC_ID, TagName, Timestamp)")
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
        _edge_trace("SCHEMA READY")
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
    _edge_trace("FLOW STORAGE LOOKUP START", f"CompanyID={company_id}")
    flow_json = get_company_flow(company_id)
    if not flow_json:
        _edge_trace("FLOW STORAGE LOOKUP MISS", "no company flow")
        return {}
    try:
        flow = json.loads(flow_json) if isinstance(flow_json, str) else flow_json
    except Exception as exc:
        _edge_trace("FLOW STORAGE LOOKUP ERROR", repr(exc))
        return {}

    nodes = flow.get("drawflow", {}).get("Home", {}).get("data", {}) or {}
    if not isinstance(nodes, dict):
        _edge_trace("FLOW STORAGE LOOKUP MISS", "invalid node container")
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
    _edge_trace("FLOW STORAGE COMPANY PLC IDS", company_plc_ids)
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

    _edge_trace("FLOW STORAGE PLC READER MAP", plc_reader_to_id)

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

            register = mapping.get("register")
            register_key = None
            try:
                if register not in (None, ""):
                    register_key = str(int(float(register)))
            except (TypeError, ValueError):
                register_key = str(register).strip() if register is not None else None

            for plc_id in mapping_plc_ids:
                if plc_id not in company_plc_ids:
                    continue
                allowed[(plc_id, name.lower())] = storage
                if register_key:
                    allowed[(plc_id, register_key)] = storage

    _edge_trace("FLOW STORAGE LOOKUP DONE", f"CompanyID={company_id}", f"entries={len(allowed)}")
    return allowed


def ingest_items(items):
    _edge_trace("BATCH RECEIVED", f"type={type(items).__name__}", f"count={len(items) if isinstance(items, list) else 'N/A'}")
    ensure_edge_event_schema()
    if not isinstance(items, list):
        _edge_trace("BATCH REJECTED", "items is not a list")
        raise ValueError("items must be a list")

    acks = []
    errors = []
    inserted = 0
    conn = get_connection()
    flow_cache = {}
    try:
        conn.execute("PRAGMA busy_timeout = 30000")
        conn.execute("BEGIN IMMEDIATE")
        _edge_trace("DB TRANSACTION BEGIN")

        for index, item in enumerate(items, start=1):
            if not isinstance(item, dict):
                _edge_trace("ITEM SKIP", f"index={index}", f"type={type(item).__name__}")
                continue

            event_id = str(item.get("EventID", "")).strip()
            _edge_trace(
                "ITEM RECEIVED",
                f"index={index}",
                f"EventID={event_id}",
                f"PLC_ID={item.get('PLC_ID')}",
                f"TagName={item.get('TagName')}",
                f"Value={item.get('Value')}",
                f"Timestamp={item.get('Timestamp')}",
            )
            if not event_id:
                errors.append({"EventID": event_id, "Error": "EventID is required"})
                _edge_trace("ITEM REJECTED", f"index={index}", "EventID is required")
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
                _edge_trace("ITEM PARSE ERROR", f"EventID={event_id}", repr(exc))
                continue

            _edge_trace("ITEM PARSED", f"EventID={event_id}", f"PLC_ID={plc_id}", f"Tag={tag}", f"Value={value}", f"Timestamp={timestamp}")

            plc = conn.execute(
                "SELECT PLC_ID, CompanyID FROM PLCs WHERE PLC_ID=?",
                (plc_id,),
            ).fetchone()
            if plc is None:
                errors.append({"EventID": event_id, "Error": "PLC not found"})
                _edge_trace("ITEM REJECTED", f"EventID={event_id}", f"PLC_ID={plc_id}", "PLC not found")
                continue

            company_id = int(plc["CompanyID"])
            _edge_trace("PLC RESOLVED", f"EventID={event_id}", f"CompanyID={company_id}", f"PLC_ID={plc_id}")

            if company_id not in flow_cache:
                flow_cache[company_id] = _flow_tag_storage(company_id)
            storage_map = flow_cache[company_id]
            storage_type = storage_map.get((plc_id, tag.lower()))
            if storage_type is None:
                storage_type = storage_map.get((plc_id, tag))

            _edge_trace("STORAGE RESOLUTION", f"EventID={event_id}", f"CompanyID={company_id}", f"PLC_ID={plc_id}", f"Tag={tag}", f"StorageType={storage_type}")

            if storage_type is None:
                error = "Tag is not defined for this PLC by the company Flow"
                errors.append({"EventID": event_id, "Error": error})
                _edge_trace("ITEM REJECTED", f"EventID={event_id}", f"Reason={error}")
                continue

            existing = conn.execute(
                "SELECT EventID FROM EdgeEventLedger WHERE EventID=?",
                (event_id,),
            ).fetchone()
            if existing is not None:
                stored = conn.execute(
                    "SELECT ID FROM PLC_Data WHERE EventID=? LIMIT 1",
                    (event_id,),
                ).fetchone()
                if stored is not None:
                    acks.append(event_id)
                    _edge_trace("ITEM DUPLICATE ACK", f"EventID={event_id}", f"PLC_Data.ID={stored['ID']}")
                    continue
                _edge_trace("ITEM LEDGER ORPHAN", f"EventID={event_id}", "ledger exists but PLC_Data row is missing")
                conn.execute(
                    "DELETE FROM EdgeEventLedger WHERE EventID=?",
                    (event_id,),
                )

            savepoint = "edge_event"
            try:
                conn.execute(f"SAVEPOINT {savepoint}")
                received_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f").rstrip("0").rstrip(".")

                cursor = conn.execute(
                    """
                    INSERT INTO PLC_Data
                    (CompanyID, PLC_ID, TagName, Value, StorageType, Timestamp, EventID)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (company_id, plc_id, tag, value, storage_type, timestamp, event_id),
                )

                row_id = cursor.lastrowid
                _edge_trace("PLC_DATA INSERT", f"EventID={event_id}", f"row_id={row_id}", f"CompanyID={company_id}", f"PLC_ID={plc_id}", f"Tag={tag}", f"StorageType={storage_type}")

                verify = conn.execute(
                    "SELECT ID, CompanyID, PLC_ID, TagName, Value, Timestamp, StorageType, EventID FROM PLC_Data WHERE ID=?",
                    (row_id,),
                ).fetchone()
                if verify is None:
                    raise RuntimeError("PLC_Data insert verification failed")

                _edge_trace(
                    "PLC_DATA VERIFY",
                    f"EventID={event_id}",
                    f"row={dict(verify)}",
                )

                conn.execute(
                    """
                    INSERT INTO EdgeEventLedger
                    (EventID, CompanyID, PLC_ID, TagName, EventTimestamp, ReceivedAt)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (event_id, company_id, plc_id, tag, timestamp, received_at),
                )
                _edge_trace("LEDGER INSERT", f"EventID={event_id}")

                try:
                    conn.execute(
                        """
                        INSERT INTO TagHistory
                        (CompanyID, PLC_ID, TagName, Value, Timestamp, EventID)
                        VALUES (?, ?, ?, ?, ?, ?)
                        """,
                        (company_id, plc_id, tag, value, timestamp, event_id),
                    )
                    _edge_trace("TAG_HISTORY INSERT", f"EventID={event_id}")
                except Exception as history_exc:
                    print("EDGE TAG HISTORY WRITE WARNING:", event_id, history_exc, flush=True)
                    _edge_trace("TAG_HISTORY WARNING", f"EventID={event_id}", repr(history_exc))

                conn.execute(f"RELEASE SAVEPOINT {savepoint}")
                acks.append(event_id)
                inserted += 1
                _edge_trace("ITEM SUCCESS BEFORE COMMIT", f"EventID={event_id}", f"row_id={row_id}")

            except Exception as exc:
                try:
                    conn.execute(f"ROLLBACK TO SAVEPOINT {savepoint}")
                    conn.execute(f"RELEASE SAVEPOINT {savepoint}")
                except Exception as rollback_exc:
                    _edge_trace("SAVEPOINT ROLLBACK ERROR", f"EventID={event_id}", repr(rollback_exc))

                errors.append({"EventID": event_id, "Error": str(exc)})
                print(
                    "EDGE INGEST WRITE ERROR:",
                    "CompanyID=", company_id,
                    "PLC_ID=", plc_id,
                    "Tag=", tag,
                    "EventID=", event_id,
                    "Reason=", exc,
                    flush=True,
                )
                _edge_trace("ITEM WRITE ERROR", f"EventID={event_id}", repr(exc))

        conn.commit()
        _edge_trace("DB TRANSACTION COMMIT", f"received={len(items)}", f"inserted={inserted}", f"acks={len(acks)}", f"errors={len(errors)}")
        print(
            "EDGE INGEST RESULT:",
            "received=", len(items),
            "inserted=", inserted,
            "acks=", len(acks),
            "errors=", len(errors),
            flush=True,
        )
        return {"acks": acks, "errors": errors, "inserted": inserted}
    except Exception as exc:
        conn.rollback()
        _edge_trace("DB TRANSACTION ROLLBACK", repr(exc))
        raise
    finally:
        conn.close()
        _edge_trace("DB CONNECTION CLOSED")


__all__ = ["ensure_edge_event_schema", "ingest_items"]
