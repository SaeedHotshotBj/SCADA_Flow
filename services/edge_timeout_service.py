"""Server-side Edge timeout watchdog, isolated per CompanyID + PLC_ID."""

import json
import os
import threading
import time
from datetime import datetime
from zoneinfo import ZoneInfo

from database import get_connection
from services.plc_identity import ensure_plc_identity_schema

CHECK_INTERVAL = 1.0
DEFAULT_TIMEOUT = 10.0
SCADA_TIMEZONE = ZoneInfo("Asia/Tehran")
_started = False
_thread = None
_lock = threading.Lock()


def _now_string():
    return datetime.now(SCADA_TIMEZONE).replace(tzinfo=None).strftime("%Y-%m-%d %H:%M:%S")


def _parse_timestamp(value):
    if value is None:
        return None
    text = str(value).strip().replace("T", " ").rstrip("Z")
    for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            pass
    try:
        parsed = datetime.fromisoformat(text)
        if parsed.tzinfo is not None:
            parsed = parsed.astimezone(SCADA_TIMEZONE).replace(tzinfo=None)
        return parsed
    except Exception:
        return None


def _ensure_tables(conn):
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS EdgeTimeoutState (
            CompanyID INTEGER NOT NULL,
            PLC_ID INTEGER NOT NULL,
            LastReceivedAt TEXT,
            TimeoutSeconds REAL NOT NULL DEFAULT 10.0,
            TimedOut INTEGER NOT NULL DEFAULT 0,
            LastTimeoutAt TEXT,
            UpdatedAt TEXT NOT NULL DEFAULT '',
            PRIMARY KEY (CompanyID, PLC_ID)
        );
        CREATE TABLE IF NOT EXISTS EdgeTimeoutDiagnosticLog (
            LogID INTEGER PRIMARY KEY AUTOINCREMENT,
            CompanyID INTEGER NOT NULL DEFAULT 0,
            PLC_ID INTEGER,
            Level TEXT NOT NULL DEFAULT 'INFO',
            Message TEXT NOT NULL DEFAULT '',
            Timestamp TEXT NOT NULL DEFAULT ''
        );
        CREATE INDEX IF NOT EXISTS idx_edge_timeout_log_company_plc_time
            ON EdgeTimeoutDiagnosticLog (CompanyID, PLC_ID, Timestamp);
    """)
    conn.commit()


def _log(conn, company_id, plc_id, level, message, commit=True):
    try:
        conn.execute("""
            INSERT INTO EdgeTimeoutDiagnosticLog
            (CompanyID, PLC_ID, Level, Message, Timestamp)
            VALUES (?, ?, ?, ?, ?)
        """, (int(company_id), int(plc_id), str(level).upper(), str(message), _now_string()))
        if commit:
            conn.commit()
    except Exception as exc:
        print("EDGE TIMEOUT LOG ERROR:", exc)
        if commit:
            conn.rollback()


def _load_configs(conn):
    result = {}
    rows = conn.execute("SELECT CompanyID, FlowJson FROM Flows WHERE CompanyID IS NOT NULL ORDER BY CompanyID").fetchall()
    for row in rows:
        company_id = int(row["CompanyID"])
        try:
            flow = json.loads(row["FlowJson"] or "{}")
        except Exception as exc:
            _log(conn, company_id, 0, "ERROR", f"Flow JSON parse failed: {exc}")
            continue
        nodes = flow.get("drawflow", {}).get("Home", {}).get("data", {}) or {}
        timeout = DEFAULT_TIMEOUT
        for node in nodes.values():
            if not isinstance(node, dict) or node.get("name") != "EdgeTimeout":
                continue
            config = (node.get("data", {}) or {}).get("config", node.get("data", {}) or {})
            try:
                timeout = float(config.get("timeout_seconds", DEFAULT_TIMEOUT))
            except (TypeError, ValueError):
                timeout = DEFAULT_TIMEOUT
            if timeout <= 0:
                timeout = DEFAULT_TIMEOUT
            break

        groups = {}
        for node in nodes.values():
            if not isinstance(node, dict) or node.get("name") != "TagMapper":
                continue
            config = (node.get("data", {}) or {}).get("config", node.get("data", {}) or {})
            mappings = config.get("mappings", []) or []
            if not isinstance(mappings, list):
                continue
            for mapping in mappings:
                if not isinstance(mapping, dict) or str(mapping.get("storage", "TIME")).upper() != "TIME":
                    continue
                tag = str(mapping.get("name", "")).strip()
                try:
                    plc_id = int(mapping.get("plc_id", mapping.get("PLC_ID")))
                except (TypeError, ValueError):
                    continue
                if tag:
                    groups.setdefault(plc_id, set()).add(tag)

        for plc_id, tags in groups.items():
            result[(company_id, plc_id)] = {"timeout": timeout, "tags": sorted(tags)}
    return result


def _last_edge_timestamp(conn, company_id, plc_id):
    row = conn.execute("""
        SELECT MAX(Timestamp) AS LastReceivedAt
        FROM PLC_Data
        WHERE CompanyID = ? AND PLC_ID = ?
          AND UPPER(COALESCE(StorageType, '')) = 'EDGE'
    """, (int(company_id), int(plc_id))).fetchone()
    return row["LastReceivedAt"] if row else None


def _get_state(conn, company_id, plc_id):
    row = conn.execute("""
        SELECT CompanyID, PLC_ID, LastReceivedAt, TimeoutSeconds, TimedOut, LastTimeoutAt, UpdatedAt
        FROM EdgeTimeoutState
        WHERE CompanyID = ? AND PLC_ID = ? LIMIT 1
    """, (int(company_id), int(plc_id))).fetchone()
    return dict(row) if row else None


def _ensure_state(conn, company_id, plc_id, timeout, last_received):
    state = _get_state(conn, company_id, plc_id)
    if state is not None:
        return state
    conn.execute("""
        INSERT OR IGNORE INTO EdgeTimeoutState
        (CompanyID, PLC_ID, LastReceivedAt, TimeoutSeconds, TimedOut, LastTimeoutAt, UpdatedAt)
        VALUES (?, ?, ?, ?, 0, NULL, ?)
    """, (int(company_id), int(plc_id), last_received, float(timeout), _now_string()))
    conn.commit()
    return _get_state(conn, company_id, plc_id)


def _set_state(conn, company_id, plc_id, timeout, last_received, timed_out, last_timeout_at=None, commit=True):
    conn.execute("""
        UPDATE EdgeTimeoutState
        SET LastReceivedAt=?, TimeoutSeconds=?, TimedOut=?, LastTimeoutAt=?, UpdatedAt=?
        WHERE CompanyID=? AND PLC_ID=?
    """, (last_received, float(timeout), 1 if timed_out else 0, last_timeout_at, _now_string(), int(company_id), int(plc_id)))
    if commit:
        conn.commit()


def _insert_zero_rows(conn, company_id, plc_id, tags):
    stamp = _now_string()
    for tag in tags:
        conn.execute("""
            INSERT INTO PLC_Data (CompanyID, PLC_ID, TagName, Value, StorageType, Timestamp)
            VALUES (?, ?, ?, 0, 'TIME', ?)
        """, (int(company_id), int(plc_id), str(tag), stamp))
    return len(tags), stamp


def _fire_timeout_transaction(conn, company_id, plc_id, timeout, last_edge, tags, elapsed):
    conn.execute("BEGIN IMMEDIATE")
    try:
        state = _get_state(conn, company_id, plc_id)
        if state and bool(state.get("TimedOut")):
            conn.rollback()
            return False, 0, None
        timeout_at = _now_string()
        _set_state(conn, company_id, plc_id, timeout, str(last_edge), True, timeout_at, commit=False)
        count, stamp = _insert_zero_rows(conn, company_id, plc_id, tags)
        _log(conn, company_id, plc_id, "WARNING", f"TIMEOUT FIRED timeout={timeout}s elapsed={round(elapsed,3)}s zero_rows={count}", commit=False)
        conn.commit()
        return True, count, stamp
    except Exception:
        conn.rollback()
        raise


def check_once():
    ensure_plc_identity_schema()
    conn = get_connection()
    try:
        _ensure_tables(conn)
        configs = _load_configs(conn)
        now = datetime.now(SCADA_TIMEZONE).replace(tzinfo=None)
        for (company_id, plc_id), cfg in configs.items():
            timeout = float(cfg["timeout"])
            tags = list(cfg["tags"])
            last_edge = _last_edge_timestamp(conn, company_id, plc_id)
            if last_edge is None:
                _ensure_state(conn, company_id, plc_id, timeout, None)
                continue
            parsed = _parse_timestamp(last_edge)
            if parsed is None:
                _log(conn, company_id, plc_id, "ERROR", f"Invalid EDGE timestamp: {last_edge}")
                continue
            elapsed = max(0.0, (now - parsed).total_seconds())
            state = _ensure_state(conn, company_id, plc_id, timeout, str(last_edge))
            if elapsed < timeout:
                if bool(state.get("TimedOut")):
                    _set_state(conn, company_id, plc_id, timeout, str(last_edge), False, state.get("LastTimeoutAt"))
                    _log(conn, company_id, plc_id, "INFO", "EDGE RECOVERED")
                continue
            if bool(state.get("TimedOut")):
                continue
            try:
                claimed, count, stamp = _fire_timeout_transaction(conn, company_id, plc_id, timeout, last_edge, tags, elapsed)
                if claimed:
                    print("EDGE TIMEOUT:", "Company=", company_id, "PLC_ID=", plc_id, "ZeroRows=", count)
            except Exception as exc:
                _log(conn, company_id, plc_id, "ERROR", f"TIMEOUT ZERO INSERT FAILED; retry next check: {type(exc).__name__}: {exc}")
    except Exception as exc:
        conn.rollback()
        print("EDGE TIMEOUT WORKER ERROR:", type(exc).__name__, exc)
    finally:
        conn.close()


def _worker():
    time.sleep(1.0)
    while True:
        try:
            check_once()
        except Exception as exc:
            print("EDGE TIMEOUT WORKER ERROR:", exc)
        time.sleep(CHECK_INTERVAL)


def start_worker():
    global _started, _thread
    if os.environ.get("SCADA_SKIP_EDGE_TIMEOUT_WORKER", "").strip().lower() in {"1", "true", "yes", "on"}:
        return None
    ensure_plc_identity_schema()
    with _lock:
        if _thread is not None and _thread.is_alive():
            return _thread
        _started = True
        _thread = threading.Thread(target=_worker, name="SCADA-EdgeTimeout-Runtime", daemon=True)
        _thread.start()
        print("EDGE TIMEOUT RUNTIME WORKER STARTED")
        return _thread


__all__ = ["check_once", "start_worker"]
