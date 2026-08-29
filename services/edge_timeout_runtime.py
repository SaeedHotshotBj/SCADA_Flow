import json
import threading
import time
from datetime import datetime

from database import get_connection


CHECK_INTERVAL = 1.0
DEFAULT_TIMEOUT = 10.0
_started = False
_lock = threading.Lock()


def _log(conn, company_id, level, message):
    try:
        conn.execute(
            "INSERT INTO EdgeTimeoutDiagnosticLog (CompanyID, Level, Message, Timestamp) VALUES (?, ?, ?, ?)",
            (int(company_id), str(level).upper(), str(message), datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
        )
    except Exception as exc:
        print("EDGE TIMEOUT LOG ERROR:", exc)


def _ensure_tables(conn):
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS EdgeTimeoutState (
            CompanyID INTEGER PRIMARY KEY,
            LastReceivedAt TEXT,
            TimeoutSeconds REAL NOT NULL,
            TimedOut INTEGER NOT NULL DEFAULT 0,
            LastTimeoutAt TEXT,
            UpdatedAt TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS EdgeTimeoutDiagnosticLog (
            LogID INTEGER PRIMARY KEY AUTOINCREMENT,
            CompanyID INTEGER NOT NULL,
            Level TEXT NOT NULL,
            Message TEXT NOT NULL,
            Timestamp TEXT NOT NULL
        )
        """
    )
    conn.commit()


def _configs(conn):
    result = {}
    rows = conn.execute("SELECT CompanyID, FlowJson FROM Flows WHERE CompanyID IS NOT NULL").fetchall()
    for row in rows:
        company_id = int(row["CompanyID"])
        try:
            flow = json.loads(row["FlowJson"] or "{}")
        except Exception as exc:
            _log(conn, company_id, "ERROR", f"Flow JSON parse failed: {exc}")
            continue
        nodes = flow.get("drawflow", {}).get("Home", {}).get("data", {}) or {}
        timeout = None
        for node in nodes.values():
            if not isinstance(node, dict):
                continue
            if node.get("name") == "EdgeTimeout":
                data = node.get("data", {}) or {}
                config = data.get("config", data) or {}
                try:
                    timeout = float(config.get("timeout_seconds", DEFAULT_TIMEOUT))
                except (TypeError, ValueError):
                    timeout = DEFAULT_TIMEOUT
                if timeout <= 0:
                    timeout = DEFAULT_TIMEOUT
                break
        if timeout is None:
            continue
        tags = []
        seen = set()
        for node in nodes.values():
            if not isinstance(node, dict) or node.get("name") != "TagMapper":
                continue
            for mapping in (node.get("data", {}) or {}).get("mappings", []) or []:
                if not isinstance(mapping, dict):
                    continue
                tag = str(mapping.get("name", "")).strip()
                if tag and tag.lower() not in seen:
                    seen.add(tag.lower())
                    tags.append(tag)
        result[company_id] = {"timeout": timeout, "tags": tags}
    return result


def _edge_last_seen(company_id):
    try:
        from extensions import _edge_last_seen as seen
        candidates = [v for (cid, _plc), v in seen.items() if int(cid) == int(company_id)]
        return max(candidates) if candidates else None
    except Exception as exc:
        print("EDGE TIMEOUT RECEIVE STATE ERROR:", exc)
        return None


def _set_state(conn, company_id, timeout, last_received, timed_out, last_timeout_at=None):
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    conn.execute(
        """
        INSERT INTO EdgeTimeoutState
        (CompanyID, LastReceivedAt, TimeoutSeconds, TimedOut, LastTimeoutAt, UpdatedAt)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(CompanyID) DO UPDATE SET
            LastReceivedAt=excluded.LastReceivedAt,
            TimeoutSeconds=excluded.TimeoutSeconds,
            TimedOut=excluded.TimedOut,
            LastTimeoutAt=excluded.LastTimeoutAt,
            UpdatedAt=excluded.UpdatedAt
        """,
        (int(company_id), last_received, float(timeout), 1 if timed_out else 0, last_timeout_at, now),
    )


def _zero_tags(conn, company_id, tags):
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    for tag in tags:
        conn.execute(
            "INSERT INTO PLC_Data (CompanyID, TagName, Value, StorageType, Timestamp) VALUES (?, ?, 0, 'TIME', ?)",
            (int(company_id), tag, stamp),
        )
    return len(tags), stamp


def check_once():
    conn = get_connection()
    try:
        _ensure_tables(conn)
        configs = _configs(conn)
        _log(conn, 0, "INFO", f"Runtime check configured_companies={len(configs)}")
        now_mono = time.monotonic()
        for company_id, cfg in configs.items():
            timeout = cfg["timeout"]
            tags = cfg["tags"]
            last_seen = _edge_last_seen(company_id)
            state = conn.execute("SELECT * FROM EdgeTimeoutState WHERE CompanyID = ?", (company_id,)).fetchone()
            timed_out = bool(state["TimedOut"]) if state else False
            if last_seen is None:
                _log(conn, company_id, "WARNING", f"No in-memory EDGE receive yet; timeout={timeout}s tags={len(tags)}")
                continue
            elapsed = now_mono - last_seen
            wall_now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            _set_state(conn, company_id, timeout, wall_now, timed_out)
            _log(conn, company_id, "INFO", f"check timeout={timeout}s elapsed={round(elapsed,3)}s timed_out={timed_out} tags={len(tags)}")
            if elapsed >= timeout and not timed_out:
                count, stamp = _zero_tags(conn, company_id, tags)
                _set_state(conn, company_id, timeout, wall_now, True, stamp)
                _log(conn, company_id, "WARNING", f"TIMEOUT FIRED elapsed={round(elapsed,3)}s zero_rows={count} timestamp={stamp}")
            elif elapsed < timeout and timed_out:
                _set_state(conn, company_id, timeout, wall_now, False, state["LastTimeoutAt"] if state else None)
                _log(conn, company_id, "INFO", "EDGE RECOVERED")
        conn.commit()
    finally:
        conn.close()


def _worker():
    time.sleep(3.0)
    while True:
        try:
            check_once()
        except Exception as exc:
            print("EDGE TIMEOUT RUNTIME ERROR:", exc)
        time.sleep(CHECK_INTERVAL)


def start_worker():
    global _started
    with _lock:
        if _started:
            return
        _started = True
        threading.Thread(target=_worker, name="SCADA-EdgeTimeout-Runtime", daemon=True).start()
        print("EDGE TIMEOUT RUNTIME WORKER STARTED")
