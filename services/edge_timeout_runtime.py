import json
import threading
import time
from datetime import datetime

from database import get_connection


CHECK_INTERVAL = 1.0
DEFAULT_TIMEOUT = 10.0

_started = False
_thread = None
_lock = threading.Lock()


STATE_COLUMNS = {
    "CompanyID": "INTEGER PRIMARY KEY",
    "LastReceivedAt": "TEXT",
    "TimeoutSeconds": "REAL NOT NULL DEFAULT 10.0",
    "TimedOut": "INTEGER NOT NULL DEFAULT 0",
    "LastTimeoutAt": "TEXT",
    "UpdatedAt": "TEXT NOT NULL DEFAULT ''",
}

LOG_COLUMNS = {
    "LogID": "INTEGER PRIMARY KEY AUTOINCREMENT",
    "CompanyID": "INTEGER NOT NULL DEFAULT 0",
    "Level": "TEXT NOT NULL DEFAULT 'INFO'",
    "Message": "TEXT NOT NULL DEFAULT ''",
    "Timestamp": "TEXT NOT NULL DEFAULT ''",
}


def _now_string():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


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
        return datetime.fromisoformat(text)
    except Exception:
        return None


def _columns(conn, table_name):
    rows = conn.execute(
        f'PRAGMA table_info("{table_name}")'
    ).fetchall()
    return {str(row[1]) for row in rows}


def _ensure_table_columns(conn, table_name, definitions):
    existing = _columns(conn, table_name)

    for column, definition in definitions.items():
        if column in existing:
            continue

        if column in ("CompanyID", "LogID"):
            continue

        conn.execute(
            f'ALTER TABLE "{table_name}" '
            f'ADD COLUMN "{column}" {definition}'
        )


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

    _ensure_table_columns(
        conn,
        "EdgeTimeoutState",
        STATE_COLUMNS,
    )

    _ensure_table_columns(
        conn,
        "EdgeTimeoutDiagnosticLog",
        LOG_COLUMNS,
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
            (
                int(company_id),
                str(level).upper(),
                str(message),
                _now_string(),
            ),
        )
    except Exception as exc:
        print("EDGE TIMEOUT LOG ERROR:", exc)


def _load_configs(conn):
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
            _log(
                conn,
                company_id,
                "ERROR",
                f"Flow JSON parse failed: {exc}",
            )
            continue

        nodes = (
            flow.get("drawflow", {})
            .get("Home", {})
            .get("data", {})
            or {}
        )

        timeout = None

        for node in nodes.values():
            if not isinstance(node, dict):
                continue

            if node.get("name") != "EdgeTimeout":
                continue

            data = node.get("data", {}) or {}
            config = data.get("config", data) or {}

            try:
                timeout = float(
                    config.get(
                        "timeout_seconds",
                        DEFAULT_TIMEOUT,
                    )
                )
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
            if not isinstance(node, dict):
                continue

            if node.get("name") != "TagMapper":
                continue

            mappings = (
                node.get("data", {}) or {}
            ).get("mappings", []) or []

            for mapping in mappings:
                if not isinstance(mapping, dict):
                    continue

                tag = str(
                    mapping.get("name", "")
                ).strip()

                key = tag.lower()

                if tag and key not in seen:
                    seen.add(key)
                    tags.append(tag)

        result[company_id] = {
            "timeout": timeout,
            "tags": tags,
        }

    return result


def _get_memory_last_seen(company_id):
    try:
        from extensions import _edge_last_seen

        values = [
            value
            for (cid, _plc), value in _edge_last_seen.items()
            if int(cid) == int(company_id)
        ]

        return max(values) if values else None

    except Exception as exc:
        print(
            "EDGE TIMEOUT RECEIVE STATE ERROR:",
            exc,
        )
        return None


def _get_last_edge_timestamp(conn, company_id):
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
        SELECT
            CompanyID,
            LastReceivedAt,
            TimeoutSeconds,
            TimedOut,
            LastTimeoutAt,
            UpdatedAt
        FROM EdgeTimeoutState
        WHERE CompanyID = ?
        """,
        (int(company_id),),
    ).fetchone()

    return dict(row) if row is not None else None


def _set_state(
    conn,
    company_id,
    timeout,
    last_received,
    timed_out,
    last_timeout_at=None,
):
    conn.execute(
        """
        INSERT INTO EdgeTimeoutState
        (
            CompanyID,
            LastReceivedAt,
            TimeoutSeconds,
            TimedOut,
            LastTimeoutAt,
            UpdatedAt
        )
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(CompanyID)
        DO UPDATE SET
            LastReceivedAt = excluded.LastReceivedAt,
            TimeoutSeconds = excluded.TimeoutSeconds,
            TimedOut = excluded.TimedOut,
            LastTimeoutAt = excluded.LastTimeoutAt,
            UpdatedAt = excluded.UpdatedAt
        """,
        (
            int(company_id),
            last_received,
            float(timeout),
            1 if timed_out else 0,
            last_timeout_at,
            _now_string(),
        ),
    )


def _claim_timeout(
    conn,
    company_id,
    timeout,
    last_received,
):
    """Atomically claim the timeout transition exactly once."""
    timeout_at = _now_string()

    cursor = conn.execute(
        """
        UPDATE EdgeTimeoutState
        SET
            TimedOut = 1,
            LastReceivedAt = ?,
            TimeoutSeconds = ?,
            LastTimeoutAt = ?,
            UpdatedAt = ?
        WHERE CompanyID = ?
          AND COALESCE(TimedOut, 0) = 0
        """,
        (
            last_received,
            float(timeout),
            timeout_at,
            timeout_at,
            int(company_id),
        ),
    )

    if cursor.rowcount == 1:
        return True, timeout_at

    return False, None


def _zero_tags(conn, company_id, tags):
    stamp = _now_string()
    count = 0

    for tag in tags:
        conn.execute(
            """
            INSERT INTO PLC_Data
            (
                CompanyID,
                TagName,
                Value,
                StorageType,
                Timestamp
            )
            VALUES (?, ?, 0, 'TIME', ?)
            """,
            (
                int(company_id),
                str(tag),
                stamp,
            ),
        )
        count += 1

    return count, stamp


def check_once():
    conn = get_connection()

    try:
        _ensure_tables(conn)

        configs = _load_configs(conn)

        _log(
            conn,
            0,
            "INFO",
            (
                "Worker check started; "
                f"configured_companies={len(configs)}"
            ),
        )

        if not configs:
            conn.commit()
            return

        try:
            from extensions import _edge_last_seen
            receive_keys = len(_edge_last_seen)
        except Exception:
            receive_keys = -1

        _log(
            conn,
            0,
            "INFO",
            f"Edge receive-state entries={receive_keys}",
        )

        now_mono = time.monotonic()
        now_wall = datetime.now()

        for company_id, cfg in configs.items():
            timeout = float(cfg["timeout"])
            tags = cfg["tags"]

            state = _get_state(conn, company_id)

            timed_out = bool(
                state["TimedOut"]
            ) if state else False

            last_timeout_at = (
                state["LastTimeoutAt"]
                if state
                else None
            )

            memory_seen = _get_memory_last_seen(
                company_id
            )

            fallback_timestamp = _get_last_edge_timestamp(
                conn,
                company_id,
            )

            elapsed = None
            last_received_display = None

            if memory_seen is not None:
                elapsed = max(
                    0.0,
                    now_mono - memory_seen,
                )
                last_received_display = now_wall.strftime(
                    "%Y-%m-%d %H:%M:%S"
                )

            elif fallback_timestamp is not None:
                parsed = _parse_timestamp(
                    fallback_timestamp
                )

                if parsed is not None:
                    elapsed = max(
                        0.0,
                        (
                            now_wall - parsed
                        ).total_seconds(),
                    )

                last_received_display = str(
                    fallback_timestamp
                )

            if elapsed is None:
                _set_state(
                    conn,
                    company_id,
                    timeout,
                    None,
                    False,
                    last_timeout_at,
                )

                _log(
                    conn,
                    company_id,
                    "WARNING",
                    (
                        "No EDGE receive state yet; "
                        f"timeout={timeout}s "
                        f"tags={len(tags)}"
                    ),
                )
                continue

            _log(
                conn,
                company_id,
                "INFO",
                (
                    f"check timeout={timeout}s "
                    f"elapsed={round(elapsed, 3)}s "
                    f"timed_out={timed_out} "
                    f"last_edge={last_received_display} "
                    f"tags={len(tags)}"
                ),
            )

            if elapsed >= timeout:
                if timed_out:
                    continue

                claimed, timeout_at = _claim_timeout(
                    conn,
                    company_id,
                    timeout,
                    last_received_display,
                )

                if not claimed:
                    conn.commit()
                    _log(
                        conn,
                        company_id,
                        "INFO",
                        "TIMEOUT ALREADY CLAIMED BY ANOTHER WORKER",
                    )
                    conn.commit()
                    continue

                count, stamp = _zero_tags(
                    conn,
                    company_id,
                    tags,
                )

                _log(
                    conn,
                    company_id,
                    "WARNING",
                    (
                        f"TIMEOUT FIRED "
                        f"timeout={timeout}s "
                        f"elapsed={round(elapsed, 3)}s "
                        f"zero_rows={count} "
                        f"zero_timestamp={stamp}"
                    ),
                )

                print(
                    "EDGE TIMEOUT:",
                    "Company=",
                    company_id,
                    "Timeout=",
                    timeout,
                    "Elapsed=",
                    round(elapsed, 2),
                    "ZeroRows=",
                    count,
                )

            elif timed_out:
                _set_state(
                    conn,
                    company_id,
                    timeout,
                    last_received_display,
                    False,
                    last_timeout_at,
                )

                _log(
                    conn,
                    company_id,
                    "INFO",
                    "EDGE RECOVERED",
                )

        conn.commit()

    except Exception as exc:
        try:
            _ensure_tables(conn)
            _log(
                conn,
                0,
                "ERROR",
                (
                    "Worker exception: "
                    f"{type(exc).__name__}: "
                    f"{exc}"
                ),
            )
            conn.commit()
        except Exception:
            pass
        raise

    finally:
        conn.close()


def _worker():
    time.sleep(1.0)

    while True:
        try:
            check_once()
        except Exception as exc:
            print(
                "EDGE TIMEOUT WORKER ERROR:",
                exc,
            )

        time.sleep(CHECK_INTERVAL)


def start_worker():
    global _started, _thread

    with _lock:
        if (
            _thread is not None
            and _thread.is_alive()
        ):
            return

        _started = True

        _thread = threading.Thread(
            target=_worker,
            name="SCADA-EdgeTimeout-Runtime",
            daemon=True,
        )

        _thread.start()

        print(
            "EDGE TIMEOUT RUNTIME WORKER STARTED"
        )
