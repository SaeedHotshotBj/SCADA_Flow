"""Atomic and idempotent ingestion for SCADA_FLOW_EDGE Store & Forward."""

from datetime import datetime, timedelta
import json
import math
import sqlite3
import threading

from database import get_connection, get_company_flow


_EDGE_SCHEMA_LOCK = threading.Lock()
_EDGE_SCHEMA_READY = False

_TREND_TABLE_BY_RESOLUTION = {
    "minute": "TrendMinute",
    "hour": "TrendHour",
    "day": "TrendDay",
}

_ALLOWED_HISTORY_RESOLUTIONS = {"minute", "hour", "day"}



def ensure_edge_event_schema():
    global _EDGE_SCHEMA_READY

    if _EDGE_SCHEMA_READY:
        return

    with _EDGE_SCHEMA_LOCK:
        if _EDGE_SCHEMA_READY:
            return

        conn = get_connection()
        try:
            conn.execute("PRAGMA busy_timeout = 30000")
            conn.execute("BEGIN IMMEDIATE")

            _ensure_trend_tables(conn)
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
            _EDGE_SCHEMA_READY = True
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()


def _ensure_trend_tables(conn):
    for table in ("TrendMinute", "TrendHour", "TrendDay"):
        conn.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {table} (
                ID INTEGER PRIMARY KEY AUTOINCREMENT,
                CompanyID INTEGER NOT NULL,
                PLC_ID INTEGER,
                TagName TEXT NOT NULL,
                PeriodStart TEXT NOT NULL,
                PeriodEnd TEXT NOT NULL,
                FirstValue REAL,
                LastValue REAL,
                MinValue REAL,
                MaxValue REAL,
                WeightedAverage REAL,
                DurationSeconds REAL NOT NULL DEFAULT 0,
                SampleCount INTEGER NOT NULL DEFAULT 0
            )
            """
        )
        columns = {row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
        if "PLC_ID" not in columns:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN PLC_ID INTEGER")
        suffix = table.replace("Trend", "").lower()
        conn.execute(
            f"DROP INDEX IF EXISTS uq_trend_{suffix}_company_tag_period"
        )
        canonical = f"uq_trend_{suffix}_company_plc_tag_period"
        try:
            conn.execute(
                f"CREATE UNIQUE INDEX IF NOT EXISTS {canonical} "
                f"ON {table}(CompanyID, PLC_ID, TagName, PeriodStart)"
            )
        except sqlite3.IntegrityError:
            conn.execute(
                f"""
                DELETE FROM {table}
                WHERE ID NOT IN (
                    SELECT MIN(ID)
                    FROM {table}
                    GROUP BY CompanyID, PLC_ID, TagName, PeriodStart
                )
                """
            )
            conn.execute(
                f"CREATE UNIQUE INDEX IF NOT EXISTS {canonical} "
                f"ON {table}(CompanyID, PLC_ID, TagName, PeriodStart)"
            )
        conn.execute(
            f"CREATE INDEX IF NOT EXISTS idx_trend_{suffix}_company_plc_tag_time "
            f"ON {table}(CompanyID, PLC_ID, TagName, PeriodStart)"
        )


def _normalize_history_resolution(value):
    raw = str(value or "MINUTE").strip().upper()
    if raw in {"MINUTE", "HOUR", "DAY"}:
        return raw.lower()
    # Older flows used ALL/NONE. New TIME storage requires one concrete
    # server-upload resolution; legacy ALL is kept operational as minute.
    if raw in {"", "ALL", "NONE", "OFF"}:
        return "minute"
    return None

def _period_end(timestamp, resolution):
    dt = _parse_timestamp(timestamp)
    if dt is None:
        raise ValueError("Invalid aggregate PeriodStart")
    resolution = str(resolution).lower()
    if resolution == "minute":
        end = dt + timedelta(minutes=1)
    elif resolution == "hour":
        end = dt + timedelta(hours=1)
    elif resolution == "day":
        end = dt + timedelta(days=1)
    elif resolution == "month":
        if dt.month == 12:
            end = dt.replace(year=dt.year + 1, month=1, day=1)
        else:
            end = dt.replace(month=dt.month + 1, day=1)
    else:
        raise ValueError("Unsupported aggregate resolution")
    return end.strftime("%Y-%m-%d %H:%M:%S.%f").rstrip("0").rstrip(".")


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



def _node_config(node):
    data = node.get("data", {}) or {}
    config = data.get("config", data)
    if isinstance(config, dict):
        merged = dict(config)
        merged.update({key: value for key, value in data.items() if key != "config"})
        return merged
    return {}


def _node_inputs(nodes, node_id):
    node = nodes.get(str(node_id), {})
    result = []
    for item in (node.get("inputs", {}) or {}).values():
        if not isinstance(item, dict):
            continue
        for connection in item.get("connections", []) or []:
            if isinstance(connection, dict) and connection.get("node") is not None:
                result.append(str(connection["node"]))
    return result


def _flow_reader_plcs(nodes, company_plc_ids):
    result = {}
    fallback = company_plc_ids[0] if len(company_plc_ids) == 1 else None
    for node_id, node in nodes.items():
        if not isinstance(node, dict) or node.get("name") != "PLCReader":
            continue
        config = _node_config(node)
        raw = config.get("plc_id", config.get("PLC_ID"))
        try:
            plc_id = int(raw) if raw not in (None, "") else fallback
        except (TypeError, ValueError):
            plc_id = fallback
        if plc_id in company_plc_ids:
            result[str(node_id)] = plc_id
    return result


def _flow_tagmapper_plcs(nodes, reader_plcs, company_plc_ids):
    result = {}
    for node_id, node in nodes.items():
        if not isinstance(node, dict) or node.get("name") != "TagMapper":
            continue

        config = _node_config(node)
        mappings = config.get("mappings", [])
        ids = set()

        if isinstance(mappings, list):
            for mapping in mappings:
                if not isinstance(mapping, dict):
                    continue
                raw = mapping.get("plc_id", mapping.get("PLC_ID"))
                if raw in (None, ""):
                    continue
                try:
                    plc_id = int(raw)
                except (TypeError, ValueError):
                    continue
                if plc_id in company_plc_ids:
                    ids.add(plc_id)

        queue = _node_inputs(nodes, node_id)
        seen = set()
        while queue:
            source = queue.pop(0)
            if source in seen:
                continue
            seen.add(source)
            if source in reader_plcs:
                ids.add(reader_plcs[source])
                continue
            queue.extend(_node_inputs(nodes, source))

        if not ids and len(company_plc_ids) == 1:
            ids.add(company_plc_ids[0])

        result[str(node_id)] = sorted(ids)

    return result


def _flow_expression_plcs(nodes, expression_node_id, reader_plcs, mapper_plcs, company_plc_ids):
    queue = _node_inputs(nodes, expression_node_id)
    result = set()
    seen = set()

    while queue:
        source = queue.pop(0)
        if source in seen:
            continue
        seen.add(source)

        node = nodes.get(source, {})
        name = node.get("name") if isinstance(node, dict) else None

        if source in reader_plcs:
            result.add(reader_plcs[source])
            continue

        if name == "TagMapper":
            result.update(mapper_plcs.get(source, []))

        queue.extend(_node_inputs(nodes, source))

    if not result and len(company_plc_ids) == 1:
        result.add(company_plc_ids[0])

    return sorted(result)


def _flow_tag_storage(company_id):
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
            if storage not in {"TIME", "LIVE", "TRIGGER"}:
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


    # ExpressionNode outputs are historical calculated tags. They are
    # accepted by the server only as precomputed aggregate values; the raw
    # formula samples remain local to Edge.
    reader_plcs = _flow_reader_plcs(nodes, company_plc_ids)
    mapper_plcs = _flow_tagmapper_plcs(nodes, reader_plcs, company_plc_ids)

    for node_id, node in nodes.items():
        if not isinstance(node, dict) or node.get("name") != "ExpressionNode":
            continue

        config = _node_config(node)
        expressions = config.get("expressions", [])
        if not isinstance(expressions, list):
            continue

        node_plcs = _flow_expression_plcs(
            nodes,
            node_id,
            reader_plcs,
            mapper_plcs,
            company_plc_ids,
        )

        for expression in expressions:
            if not isinstance(expression, dict):
                continue
            name = str(expression.get("name", expression.get("result_name", ""))).strip()
            if not name:
                continue

            explicit = expression.get("plc_id", expression.get("PLC_ID"))
            plc_ids = list(node_plcs)
            if explicit not in (None, ""):
                try:
                    explicit_id = int(explicit)
                    plc_ids = [explicit_id] if explicit_id in company_plc_ids else []
                except (TypeError, ValueError):
                    pass

            for plc_id in plc_ids:
                allowed[(plc_id, name.lower())] = "CALCULATED"

    return allowed


def get_flow_storage_type(company_id, plc_id, tag_name):
    storage_map = _flow_tag_storage(company_id)
    try:
        plc_id = int(plc_id)
    except (TypeError, ValueError):
        return None
    tag = str(tag_name or "").strip()
    if not tag:
        return None
    return storage_map.get((plc_id, tag.lower())) or storage_map.get((plc_id, tag))


def get_flow_time_tags(company_id):
    """Return Flow-defined raw TIME tags whose history is precomputed on Edge."""
    flow_json = get_company_flow(company_id)
    if not flow_json:
        return []

    try:
        flow = json.loads(flow_json) if isinstance(flow_json, str) else flow_json
    except Exception:
        return []

    nodes = flow.get("drawflow", {}).get("Home", {}).get("data", {}) or {}
    if not isinstance(nodes, dict):
        return []

    conn = get_connection()
    try:
        company_plc_ids = {
            int(row["PLC_ID"])
            for row in conn.execute(
                "SELECT PLC_ID FROM PLCs WHERE CompanyID=? ORDER BY PLC_ID",
                (int(company_id),),
            ).fetchall()
        }
    finally:
        conn.close()

    result = []
    seen = set()
    storage_map = _flow_tag_storage(company_id)

    for node in nodes.values():
        if not isinstance(node, dict) or node.get("name") != "TagMapper":
            continue

        config = _node_config(node)
        mappings = config.get("mappings", [])
        if not isinstance(mappings, list):
            continue

        for mapping in mappings:
            if not isinstance(mapping, dict):
                continue

            storage = str(
                mapping.get("storage", "TIME")
            ).strip().upper()
            if storage != "TIME":
                continue

            tag = str(mapping.get("name", "")).strip()
            if not tag:
                continue

            explicit = mapping.get("plc_id", mapping.get("PLC_ID"))
            plc_ids = []
            if explicit not in (None, ""):
                try:
                    plc_ids = [int(explicit)]
                except (TypeError, ValueError):
                    plc_ids = []
            if not plc_ids:
                # Prefer the PLCs already inferred by the general storage map.
                plc_ids = [
                    int(key[0])
                    for key, value in storage_map.items()
                    if (
                        isinstance(key, tuple)
                        and len(key) == 2
                        and str(key[1]).lower() == tag.lower()
                        and value == "TIME"
                    )
                ]

            for plc_id in dict.fromkeys(plc_ids):
                if plc_id not in company_plc_ids:
                    continue

                key = (int(plc_id), tag.lower())
                if key in seen:
                    continue
                seen.add(key)

                result.append({
                    "tag": tag,
                    "title": str(
                        mapping.get("title", mapping.get("label", tag))
                    ).strip() or tag,
                    "unit": str(mapping.get("unit", "")).strip(),
                    "PLC_ID": int(plc_id),
                    "plc_id": int(plc_id),
                    "history_resolution": _normalize_history_resolution(
                        mapping.get("history_resolution", mapping.get("HistoryResolution", "MINUTE"))
                    ) or "minute",
                })

    return result


def get_flow_historical_tags(company_id):
    """Return Flow-defined TIME tags available in Historical Trend."""
    return get_flow_time_tags(company_id)

def get_flow_calculated_tags(company_id):
    flow_json = get_company_flow(company_id)
    if not flow_json:
        return []

    try:
        flow = json.loads(flow_json) if isinstance(flow_json, str) else flow_json
    except Exception:
        return []

    nodes = flow.get("drawflow", {}).get("Home", {}).get("data", {}) or {}
    if not isinstance(nodes, dict):
        return []

    conn = get_connection()
    try:
        company_plc_ids = [
            int(row["PLC_ID"])
            for row in conn.execute(
                "SELECT PLC_ID FROM PLCs WHERE CompanyID=? ORDER BY PLC_ID",
                (int(company_id),),
            ).fetchall()
        ]
    finally:
        conn.close()

    reader_plcs = _flow_reader_plcs(nodes, company_plc_ids)
    mapper_plcs = _flow_tagmapper_plcs(nodes, reader_plcs, company_plc_ids)
    result = []
    seen = set()

    for node_id, node in nodes.items():
        if not isinstance(node, dict) or node.get("name") != "ExpressionNode":
            continue

        config = _node_config(node)
        expressions = config.get("expressions", [])
        if not isinstance(expressions, list):
            continue

        node_plcs = _flow_expression_plcs(
            nodes,
            node_id,
            reader_plcs,
            mapper_plcs,
            company_plc_ids,
        )

        for expression in expressions:
            if not isinstance(expression, dict):
                continue

            name = str(expression.get("name", expression.get("result_name", ""))).strip()
            expression_text = str(expression.get("expression", "")).strip()
            if not name or not expression_text:
                continue

            explicit = expression.get("plc_id", expression.get("PLC_ID"))
            plc_ids = list(node_plcs)
            if explicit not in (None, ""):
                try:
                    explicit_id = int(explicit)
                    plc_ids = [explicit_id] if explicit_id in company_plc_ids else []
                except (TypeError, ValueError):
                    pass

            for plc_id in plc_ids:
                key = (int(plc_id), name.lower())
                if key in seen:
                    continue
                seen.add(key)
                result.append({
                    "tag": name,
                    "title": str(expression.get("label", name)).strip() or name,
                    "unit": str(expression.get("unit", "")).strip(),
                    "PLC_ID": int(plc_id),
                    "plc_id": int(plc_id),
                    "history_resolution": _normalize_history_resolution(
                        expression.get("history_resolution", expression.get("HistoryResolution", "ALL"))
                    ) or "ALL",
                })

    return result


def get_flow_history_resolution(company_id, plc_id, tag_name):
    try:
        plc_id = int(plc_id)
    except (TypeError, ValueError):
        return None

    wanted = str(tag_name or "").strip().lower()
    if not wanted:
        return None

    for item in get_flow_time_tags(company_id):
        try:
            item_plc = int(item.get("PLC_ID", item.get("plc_id")))
        except (TypeError, ValueError):
            continue
        if item_plc == plc_id and str(item.get("tag", "")).strip().lower() == wanted:
            return _normalize_history_resolution(item.get("history_resolution"))
    return None

def _insert_trend_aggregate(conn, company_id, plc_id, tag, value, timestamp, resolution, item):
    table = _TREND_TABLE_BY_RESOLUTION.get(str(resolution or "").strip().lower())
    if table is None:
        raise ValueError("Unsupported TIME aggregate resolution")

    period_start = str(item.get("PeriodStart") or timestamp).strip()
    period_end = str(item.get("PeriodEnd") or "").strip()
    if not period_start or not period_end:
        raise ValueError("TIME aggregate requires PeriodStart and PeriodEnd")

    def required_number(key, fallback=None):
        raw = item.get(key)
        if raw in (None, ""):
            if fallback is not None:
                return float(fallback)
            raise ValueError(f"{key} is required")
        return _validate_numeric(raw)

    try:
        sample_count = int(item.get("SampleCount"))
    except (TypeError, ValueError):
        raise ValueError("SampleCount must be an integer")
    if sample_count < 1:
        raise ValueError("SampleCount must be greater than zero")

    duration_seconds = _validate_numeric(item.get("DurationSeconds"))
    if duration_seconds <= 0:
        raise ValueError("DurationSeconds must be greater than zero")

    first_value = required_number("FirstValue", value)
    last_value = required_number("LastValue", value)
    minimum = required_number("MinValue", value)
    maximum = required_number("MaxValue", value)
    weighted_average = required_number(
        "WeightedAverage",
        item.get("Value", value),
    )

    conn.execute(
        f"""
        INSERT INTO {table}
        (CompanyID, PLC_ID, TagName, PeriodStart, PeriodEnd,
         FirstValue, LastValue, MinValue, MaxValue, WeightedAverage,
         DurationSeconds, SampleCount)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(CompanyID, PLC_ID, TagName, PeriodStart) DO UPDATE SET
            PeriodEnd=excluded.PeriodEnd,
            FirstValue=excluded.FirstValue,
            LastValue=excluded.LastValue,
            MinValue=excluded.MinValue,
            MaxValue=excluded.MaxValue,
            WeightedAverage=excluded.WeightedAverage,
            DurationSeconds=excluded.DurationSeconds,
            SampleCount=excluded.SampleCount
        """,
        (
            int(company_id),
            int(plc_id),
            str(tag),
            period_start,
            period_end,
            first_value,
            last_value,
            minimum,
            maximum,
            weighted_average,
            duration_seconds,
            sample_count,
        ),
    )
    return "upserted"

def _insert_or_ack_existing(conn, event_id, company_id, plc_id, tag, value, timestamp, storage_type):
    try:
        conn.execute(
            """
            INSERT INTO PLC_Data
            (CompanyID, PLC_ID, TagName, Value, StorageType, Timestamp, EventID)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (company_id, plc_id, tag, value, storage_type, timestamp, event_id),
        )
        return "inserted"
    except sqlite3.IntegrityError:
        existing = conn.execute(
            "SELECT ID, CompanyID, PLC_ID, TagName, Value, StorageType, Timestamp, EventID FROM PLC_Data WHERE EventID=? LIMIT 1",
            (event_id,),
        ).fetchone()
        if existing is None:
            raise

        received_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f").rstrip("0").rstrip(".")
        conn.execute(
            """
            INSERT OR IGNORE INTO EdgeEventLedger
            (EventID, CompanyID, PLC_ID, TagName, EventTimestamp, ReceivedAt)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                event_id,
                existing["CompanyID"],
                existing["PLC_ID"],
                existing["TagName"],
                existing["Timestamp"],
                received_at,
            ),
        )
        return "existing"


def ingest_items(items):
    """Ingest Edge Store & Forward records.

    TIME records are precomputed aggregates and are stored only in their
    Flow-selected Trend table. TRIGGER records retain their existing durable
    PLC_Data/TagHistory behavior. LIVE records never belong in this endpoint.
    """
    ensure_edge_event_schema()
    if not isinstance(items, list):
        raise ValueError("items must be a list")

    acks = []
    errors = []
    inserted = 0

    conn = get_connection()
    flow_cache = {}
    try:
        conn.execute("PRAGMA busy_timeout = 30000")
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
                timestamp = _parse_timestamp(
                    item.get("PeriodStart", item.get("Timestamp"))
                )
            except Exception as exc:
                errors.append({"EventID": event_id, "Error": str(exc)})
                continue

            plc = conn.execute(
                "SELECT PLC_ID, CompanyID FROM PLCs WHERE PLC_ID=?",
                (plc_id,),
            ).fetchone()
            if plc is None:
                errors.append({"EventID": event_id, "Error": "PLC not found"})
                continue

            company_id = int(plc["CompanyID"])
            if company_id not in flow_cache:
                flow_cache[company_id] = _flow_tag_storage(company_id)
            storage_map = flow_cache[company_id]
            storage_type = storage_map.get((plc_id, tag.lower()))
            if storage_type is None:
                storage_type = storage_map.get((plc_id, tag))

            incoming_storage = str(
                item.get("StorageType", storage_type or "")
            ).strip().upper()

            if incoming_storage == "LIVE":
                errors.append({
                    "EventID": event_id,
                    "Error": "LIVE data must use the live endpoint",
                })
                continue

            if incoming_storage == "TIME":
                if storage_type != "TIME":
                    errors.append({
                        "EventID": event_id,
                        "Error": "TIME tag is not defined by the company Flow",
                    })
                    continue

                resolution = _normalize_history_resolution(
                    item.get("HistoryResolution")
                )
                flow_resolution = get_flow_history_resolution(
                    company_id,
                    plc_id,
                    tag,
                )
                if resolution is None or flow_resolution is None:
                    errors.append({
                        "EventID": event_id,
                        "Error": "TIME history resolution is not defined by the company Flow",
                    })
                    continue
                if resolution != flow_resolution:
                    errors.append({
                        "EventID": event_id,
                        "Error": "TIME history resolution does not match the company Flow",
                    })
                    continue

            elif incoming_storage in {"TRIGGER", "TRIGGER_SIGNAL"}:
                if storage_type != "TRIGGER" and not (
                    incoming_storage == "TRIGGER_SIGNAL"
                    and tag.startswith("__TRIGGER_REGISTER_")
                ):
                    errors.append({
                        "EventID": event_id,
                        "Error": "TRIGGER tag is not defined by the company Flow",
                    })
                    continue
            else:
                errors.append({
                    "EventID": event_id,
                    "Error": "StorageType must be TIME or TRIGGER for Store & Forward",
                })
                continue

            existing = conn.execute(
                "SELECT EventID FROM EdgeEventLedger WHERE EventID=?",
                (event_id,),
            ).fetchone()
            if existing is not None:
                acks.append(event_id)
                continue

            savepoint = "edge_event"
            try:
                conn.execute(f"SAVEPOINT {savepoint}")

                if incoming_storage == "TIME":
                    insert_state = _insert_trend_aggregate(
                        conn,
                        company_id,
                        plc_id,
                        tag,
                        value,
                        timestamp,
                        resolution,
                        item,
                    )
                else:
                    insert_state = _insert_or_ack_existing(
                        conn,
                        event_id,
                        company_id,
                        plc_id,
                        tag,
                        value,
                        timestamp,
                        "TRIGGER" if incoming_storage == "TRIGGER" else "TRIGGER_SIGNAL",
                    )

                received_at = datetime.now().strftime(
                    "%Y-%m-%d %H:%M:%S.%f"
                ).rstrip("0").rstrip(".")

                conn.execute(
                    """
                    INSERT INTO EdgeEventLedger
                    (EventID, CompanyID, PLC_ID, TagName, EventTimestamp, ReceivedAt)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (event_id, company_id, plc_id, tag, timestamp, received_at),
                )

                if incoming_storage in {"TRIGGER", "TRIGGER_SIGNAL"}:
                    try:
                        conn.execute(
                            """
                            INSERT INTO TagHistory
                            (CompanyID, PLC_ID, TagName, Value, Timestamp, EventID)
                            VALUES (?, ?, ?, ?, ?, ?)
                            """,
                            (company_id, plc_id, tag, value, timestamp, event_id),
                        )
                    except Exception as history_exc:
                        print("EDGE TAG HISTORY WRITE WARNING:", event_id, history_exc)

                conn.execute(f"RELEASE SAVEPOINT {savepoint}")
                acks.append(event_id)
                if insert_state != "existing":
                    inserted += 1

            except Exception as exc:
                try:
                    conn.execute(f"ROLLBACK TO SAVEPOINT {savepoint}")
                    conn.execute(f"RELEASE SAVEPOINT {savepoint}")
                except Exception:
                    pass
                errors.append({"EventID": event_id, "Error": str(exc)})
                print(
                    "EDGE INGEST WRITE ERROR:",
                    "CompanyID=", company_id,
                    "PLC_ID=", plc_id,
                    "Tag=", tag,
                    "EventID=", event_id,
                    "Reason=", exc,
                )

        conn.commit()
        print(
            "EDGE INGEST RESULT:",
            "received=", len(items),
            "inserted=", inserted,
            "acks=", len(acks),
            "errors=", len(errors),
        )
        return {"acks": acks, "errors": errors, "inserted": inserted}
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

__all__ = [
    "ensure_edge_event_schema",
    "ingest_items",
    "get_flow_storage_type",
    "get_flow_time_tags",
    "get_flow_calculated_tags",
    "get_flow_historical_tags",
    "get_flow_history_resolution",
]
