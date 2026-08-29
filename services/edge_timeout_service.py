"""Server-side Edge timeout historian watchdog.

When an Edge stops sending data, write one synthetic zero into PLC_Data for
EVERY TagMapper tag whose storage is TIME.  The zero is written once per
outage.  Trigger/non-TIME tags are never zeroed.
"""

import json
import os
import threading
import time
from datetime import datetime
from zoneinfo import ZoneInfo

from database import get_connection


CHECK_INTERVAL = 1.0
DEFAULT_TIMEOUT = 10.0
SCADA_TIMEZONE = ZoneInfo("Asia/Tehran")

_started = False
_thread = None
_lock = threading.Lock()


def _now_string():
    return datetime.now(SCADA_TIMEZONE).replace(tzinfo=None).strftime(
        "%Y-%m-%d %H:%M:%S"
    )


def _parse_timestamp(value):
    if value is None:
        return None

    text = str(value).strip().replace("T", " ")
    if text.endswith("Z"):
        text = text[:-1]

    for fmt in (
        "%Y-%m-%d %H:%M:%S.%f",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
    ):
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
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS EdgeTimeoutState (
            CompanyID INTEGER PRIMARY KEY,
            LastReceivedAt TEXT,
            TimeoutSeconds REAL NOT NULL DEFAULT 10.0,
            TimedOut INTEGER NOT NULL DEFAULT 0,
            LastTimeoutAt TEXT,
            UpdatedAt TEXT NOT NULL DEFAULT ''
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS EdgeTimeoutDiagnosticLog (
            LogID INTEGER PRIMARY KEY AUTOINCREMENT,
            CompanyID INTEGER NOT NULL DEFAULT 0,
            Level TEXT NOT NULL DEFAULT 'INFO',
            Message TEXT NOT NULL DEFAULT '',
            Timestamp TEXT NOT NULL DEFAULT ''
        )
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_edge_timeout_log_company_time
        ON EdgeTimeoutDiagnosticLog (CompanyID, Timestamp)
        """
    )
    conn.commit()


def _log(conn, company_id, level, message):
    try:
        conn.execute(
            """
            INSERT INTO EdgeTimeoutDiagnosticLog
            (CompanyID, Level, Message, Timestamp)
            VALUES (?, ?, ?, ?)
            """,
            (int(company_id), str(level).upper(), str(message), _now_string()),
        )
        conn.commit()
    except Exception as exc:
        print("EDGE TIMEOUT LOG ERROR:", exc)
        try:
            conn.rollback()
        except Exception:
            pass


def _load_configs(conn):
    """Return timeout plus ONLY TIME-storage TagMapper tags per company."""
    result = {}

    rows = conn.execute(
        """
        SELECT CompanyID, FlowJson
        FROM Flows
        WHERE CompanyID IS NOT NULL
        ORDER BY CompanyID
        """
    ).fetchall()

    for row in rows:
        company_id = int(row["CompanyID"])

        try:
            flow = json.loads(row["FlowJson"] or "{}")
        except Exception as exc:
            _log(conn, company_id, "ERROR", f"Flow JSON parse failed: {exc}")
            continue

        nodes = (
            flow.get("drawflow", {})
            .get("Home", {})
            .get("data", {})
            or {}
        )

        timeout = None
        for node in nodes.values():
            if not isinstance(node, dict) or node.get("name") != "EdgeTimeout":
                continue

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

            data = node.get("data", {}) or {}
            config = data.get("config", data) or {}
            mappings = config.get("mappings", []) or []

            if not isinstance(mappings, list):
                continue

            for mapping in mappings:
                if not isinstance(mapping, dict):
                    continue

                if str(mapping.get("storage", "TIME")).strip().upper() != "TIME":
                    continue

                tag = str(mapping.get("name", "")).strip()
                key = tag.lower()
                if tag and key not in seen:
                    seen.add(key)
                    tags.append(tag)

        result[company_id] = {"timeout": timeout, "tags": tags}

    return result


def _last_edge_timestamp(conn, company_id):
    """The latest real Edge row is the persistent source of truth."""
    row = conn.execute(
        """
        SELECT MAX(Timestamp) AS LastReceivedAt
        FROM PLC_Data
        WHERE CompanyID = ?
          AND UPPER(COALESCE(StorageType, '')) = 'EDGE'
        """,
        (int(company_id),),
    ).fetchone()
    return row["LastReceivedAt"] if row else None


def _get_state(conn, company_id):
    row = conn.execute(
        """
        SELECT CompanyID, LastReceivedAt, TimeoutSeconds, TimedOut,
               LastTimeoutAt, UpdatedAt
        FROM EdgeTimeoutState
        WHERE CompanyID = ?
        LIMIT 1
        """,
        (int(company_id),),
    ).fetchone()
    return dict(row) if row else None


def _ensure_state(conn, company_id, timeout, last_received):
    """Create the state row before attempting an atomic timeout claim."""
    state = _get_state(conn, company_id)
    if state is not None:
        return state

    now = _now_string()
    conn.execute(
        """
        INSERT OR IGNORE INTO EdgeTimeoutState
        (CompanyID, LastReceivedAt, TimeoutSeconds, TimedOut, LastTimeoutAt, UpdatedAt)
        VALUES (?, ?, ?, 0, NULL, ?)
        """,
        (int(company_id), last_received, float(timeout), now),
    )
    conn.commit()
    return _get_state(conn, company_id)


def _set_state(conn, company_id, timeout, last_received, timed_out, last_timeout_at=None):
    now = _now_string()
    conn.execute(
        """
        UPDATE EdgeTimeoutState
        SET LastReceivedAt=?, TimeoutSeconds=?, TimedOut=?,
            LastTimeoutAt=?, UpdatedAt=?
        WHERE CompanyID=?
        """,
        (
            last_received,
            float(timeout),
            1 if timed_out else 0,
            last_timeout_at,
            now,
            int(company_id),
        ),
    )
    conn.commit()


def _claim_timeout(conn, company_id, timeout, last_received):
    """Atomically change 0 -> 1 so only one worker can fire the outage."""
    timeout_at = _now_string()
    cursor = conn.execute(
        """
        UPDATE EdgeTimeoutState
        SET TimedOut=1,
            LastReceivedAt=?,
            TimeoutSeconds=?,
            LastTimeoutAt=?,
            UpdatedAt=?
        WHERE CompanyID=?
          AND COALESCE(TimedOut, 0)=0
        """,
        (
            last_received,
            float(timeout),
            timeout_at,
            timeout_at,
            int(company_id),
        ),
    )
    conn.commit()
    return cursor.rowcount == 1, timeout_at if cursor.rowcount == 1 else None


def _insert_zero_rows(conn, company_id, tags):
    """Insert one zero per configured TIME tag into PLC_Data."""
    stamp = _now_string()
    count = 0

    for tag in tags:
        conn.execute(
            """
            INSERT INTO PLC_Data
            (CompanyID, TagName, Value, StorageType, Timestamp)
            VALUES (?, ?, 0, 'TIME', ?)
            """,
            (int(company_id), str(tag), stamp),
        )
        count += 1

    conn.commit()
    return count, stamp


def check_once():
    conn = get_connection()

    try:
        _ensure_tables(conn)
        configs = _load_configs(conn)

        if not configs:
            return

        now = datetime.now(SCADA_TIMEZONE).replace(tzinfo=None)

        for company_id, cfg in configs.items():
            timeout = float(cfg["timeout"])
            tags = list(cfg["tags"])
            last_edge = _last_edge_timestamp(conn, company_id)

            if last_edge is None:
                _ensure_state(conn, company_id, timeout, None)
                continue

            parsed = _parse_timestamp(last_edge)
            if parsed is None:
                _log(conn, company_id, "ERROR", f"Invalid EDGE timestamp: {last_edge}")
                continue

            elapsed = max(0.0, (now - parsed).total_seconds())
            state = _ensure_state(conn, company_id, timeout, str(last_edge))
            timed_out = bool(state.get("TimedOut"))

            if elapsed < timeout:
                if timed_out:
                    _set_state(
                        conn,
                        company_id,
                        timeout,
                        str(last_edge),
                        False,
                        state.get("LastTimeoutAt"),
                    )
                    _log(conn, company_id, "INFO", "EDGE RECOVERED")
                else:
                    _set_state(
                        conn,
                        company_id,
                        timeout,
                        str(last_edge),
                        False,
                        state.get("LastTimeoutAt"),
                    )
                continue

            if timed_out:
                continue

            claimed, timeout_at = _claim_timeout(
                conn,
                company_id,
                timeout,
                str(last_edge),
            )

            if not claimed:
                continue

            count, stamp = _insert_zero_rows(conn, company_id, tags)

            _log(
                conn,
                company_id,
                "WARNING",
                f"TIMEOUT FIRED timeout={timeout}s elapsed={round(elapsed,3)}s "
                f"zero_rows={count} zero_timestamp={stamp}",
            )

            print(
                "EDGE TIMEOUT:",
                "Company=", company_id,
                "Timeout=", timeout,
                "Elapsed=", round(elapsed, 2),
                "ZeroRows=", count,
            )

    except Exception as exc:
        try:
            conn.rollback()
        except Exception:
            pass
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

    if os.environ.get("SCADA_SKIP_EDGE_TIMEOUT_WORKER", "").strip().lower() in {
        "1", "true", "yes", "on"
    }:
        return None

    with _lock:
        if _thread is not None and _thread.is_alive():
            return _thread

        _started = True
        _thread = threading.Thread(
            target=_worker,
            name="SCADA-EdgeTimeout-Runtime",
            daemon=True,
        )
        _thread.start()
        print("EDGE TIMEOUT RUNTIME WORKER STARTED")
        return _thread


__all__ = ["check_once", "start_worker"]
