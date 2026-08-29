# =====================================================
# SCADA FLOW
# EDGE TIMEOUT SERVICE
# =====================================================

import json
import threading
import time
from datetime import datetime

from database import get_connection


WORKER_INTERVAL_SECONDS = 1.0
DEFAULT_TIMEOUT_SECONDS = 10.0
_worker_started = False
_worker_lock = threading.Lock()


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


def _now_string():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _ensure_state_table(conn):
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
    conn.commit()


def _load_company_timeout_configs(conn):
    rows = conn.execute(
        """
        SELECT CompanyID, FlowJson
        FROM Flows
        ORDER BY CompanyID
        """
    ).fetchall()

    result = {}

    for row in rows:
        company_id = int(row["CompanyID"])
        try:
            flow = json.loads(row["FlowJson"] or "{}")
        except Exception as exc:
            print("EDGE TIMEOUT LOG: INVALID FLOW JSON", "Company=", company_id, "Error=", exc)
            continue

        nodes = (
            flow.get("drawflow", {})
            .get("Home", {})
            .get("data", {})
        )

        timeout = None
        tag_names = []

        edge_timeout_nodes = []
        for node_id, node in nodes.items():
            if node.get("name") == "EdgeTimeout" or node.get("class") == "EdgeTimeout":
                edge_timeout_nodes.append((str(node_id), node))

        print(
            "EDGE TIMEOUT LOG: FLOW CHECK",
            "Company=", company_id,
            "Nodes=", len(nodes),
            "EdgeTimeoutNodes=", len(edge_timeout_nodes),
        )

        for node_id, node in edge_timeout_nodes:
            data = node.get("data", {}) or {}
            config = data.get("config", data) or {}
            raw_timeout = config.get("timeout_seconds", config.get("timeout", DEFAULT_TIMEOUT_SECONDS))
            try:
                timeout = float(raw_timeout)
            except (TypeError, ValueError):
                timeout = DEFAULT_TIMEOUT_SECONDS
            if timeout <= 0:
                timeout = DEFAULT_TIMEOUT_SECONDS
            print(
                "EDGE TIMEOUT LOG: CONFIG FOUND",
                "Company=", company_id,
                "Node=", node_id,
                "RawTimeout=", raw_timeout,
                "TimeoutSeconds=", timeout,
            )
            break

        if timeout is None:
            print("EDGE TIMEOUT LOG: NO EDGE TIMEOUT CONFIG", "Company=", company_id)
            continue

        seen_tags = set()
        for node in nodes.values():
            if node.get("name") != "TagMapper" and node.get("class") != "TagMapper":
                continue

            data = node.get("data", {}) or {}
            mappings = data.get("mappings", []) or []
            for mapping in mappings:
                if not isinstance(mapping, dict):
                    continue
                name = str(mapping.get("name", "")).strip()
                key = name.lower()
                if name and key not in seen_tags:
                    tag_names.append(name)
                    seen_tags.add(key)

        print(
            "EDGE TIMEOUT LOG: TAGS",
            "Company=", company_id,
            "Count=", len(tag_names),
            "Tags=", tag_names,
        )

        if tag_names:
            result[company_id] = {
                "timeout": timeout,
                "tags": tag_names,
            }

    return result


def _latest_edge_timestamp(conn, company_id):
    row = conn.execute(
        """
        SELECT MAX(Timestamp) AS LastReceivedAt
        FROM PLC_Data
        WHERE CompanyID = ?
          AND UPPER(COALESCE(StorageType, '')) = 'EDGE'
        """,
        (int(company_id),),
    ).fetchone()

    value = row["LastReceivedAt"] if row else None
    print("EDGE TIMEOUT LOG: LATEST EDGE", "Company=", company_id, "LastReceivedAt=", value)
    return value


def _upsert_state(
    conn,
    company_id,
    timeout_seconds,
    last_received,
    timed_out,
    last_timeout_at=None,
):
    now = _now_string()
    conn.execute(
        """
        INSERT INTO EdgeTimeoutState
        (CompanyID, LastReceivedAt, TimeoutSeconds, TimedOut, LastTimeoutAt, UpdatedAt)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(CompanyID) DO UPDATE SET
            LastReceivedAt = excluded.LastReceivedAt,
            TimeoutSeconds = excluded.TimeoutSeconds,
            TimedOut = excluded.TimedOut,
            LastTimeoutAt = excluded.LastTimeoutAt,
            UpdatedAt = excluded.UpdatedAt
        """,
        (
            int(company_id),
            last_received,
            float(timeout_seconds),
            1 if timed_out else 0,
            last_timeout_at,
            now,
        ),
    )


def _get_state(conn, company_id):
    return conn.execute(
        "SELECT * FROM EdgeTimeoutState WHERE CompanyID = ?",
        (int(company_id),),
    ).fetchone()


def _write_timeout_zeros(conn, company_id, tag_names):
    timestamp = _now_string()
    written = 0

    print(
        "EDGE TIMEOUT LOG: WRITING ZERO ROWS",
        "Company=", company_id,
        "Timestamp=", timestamp,
        "Tags=", tag_names,
    )

    for tag_name in tag_names:
        conn.execute(
            """
            INSERT INTO PLC_Data
            (CompanyID, TagName, Value, StorageType, Timestamp)
            VALUES (?, ?, 0, 'TIME', ?)
            """,
            (int(company_id), str(tag_name), timestamp),
        )
        written += 1

    print(
        "EDGE TIMEOUT LOG: ZERO ROWS QUEUED",
        "Company=", company_id,
        "Count=", written,
    )

    return written, timestamp


def check_once():
    conn = get_connection()
    try:
        _ensure_state_table(conn)
        configs = _load_company_timeout_configs(conn)

        print("EDGE TIMEOUT LOG: CHECK START", "ConfiguredCompanies=", list(configs.keys()))

        if not configs:
            print("EDGE TIMEOUT LOG: NOTHING TO CHECK")
            return

        for company_id, cfg in configs.items():
            timeout_seconds = float(cfg["timeout"])
            tag_names = cfg["tags"]

            state = _get_state(conn, company_id)
            previous_last_received = state["LastReceivedAt"] if state else None
            timed_out = bool(state["TimedOut"]) if state else False
            last_timeout_at = state["LastTimeoutAt"] if state else None

            latest_edge = _latest_edge_timestamp(conn, company_id)
            if latest_edge is not None:
                if previous_last_received != latest_edge:
                    print(
                        "EDGE TIMEOUT LOG: NEW EDGE DATA",
                        "Company=", company_id,
                        "Previous=", previous_last_received,
                        "Current=", latest_edge,
                    )
                    timed_out = False
                previous_last_received = latest_edge

            print(
                "EDGE TIMEOUT LOG: STATE",
                "Company=", company_id,
                "LastReceived=", previous_last_received,
                "TimedOut=", timed_out,
                "TimeoutSeconds=", timeout_seconds,
                "LastTimeoutAt=", last_timeout_at,
            )

            if previous_last_received is None:
                print("EDGE TIMEOUT LOG: NO LAST RECEIVED - SKIP", "Company=", company_id)
                continue

            latest_dt = _parse_timestamp(previous_last_received)
            if latest_dt is None:
                print(
                    "EDGE TIMEOUT LOG: TIMESTAMP PARSE FAILED",
                    "Company=", company_id,
                    "Timestamp=", previous_last_received,
                )
                continue

            _upsert_state(
                conn,
                company_id,
                timeout_seconds,
                previous_last_received,
                timed_out,
                last_timeout_at,
            )

            elapsed = (datetime.now() - latest_dt).total_seconds()

            print(
                "EDGE TIMEOUT LOG: TIMING",
                "Company=", company_id,
                "Now=", _now_string(),
                "LastReceived=", previous_last_received,
                "ElapsedSeconds=", round(elapsed, 3),
                "TimeoutSeconds=", timeout_seconds,
                "TimedOut=", timed_out,
            )

            if elapsed > timeout_seconds and not timed_out:
                print("EDGE TIMEOUT LOG: TIMEOUT CONDITION TRUE", "Company=", company_id)

                written, timeout_timestamp = _write_timeout_zeros(
                    conn,
                    company_id,
                    tag_names,
                )

                _upsert_state(
                    conn,
                    company_id,
                    timeout_seconds,
                    previous_last_received,
                    timed_out=True,
                    last_timeout_at=timeout_timestamp,
                )

                print(
                    "EDGE TIMEOUT:"
                    , "Company=", company_id
                    , "Timeout=", timeout_seconds
                    , "Elapsed=", round(elapsed, 2)
                    , "ZeroRows=", written
                    , "Timestamp=", timeout_timestamp
                )

            elif elapsed > timeout_seconds:
                print("EDGE TIMEOUT LOG: ALREADY TIMED OUT - NO DUPLICATE ZEROS", "Company=", company_id)

            else:
                print("EDGE TIMEOUT LOG: NOT TIMED OUT YET", "Company=", company_id)

            if latest_edge is not None and timed_out and latest_edge != state["LastReceivedAt"]:
                _upsert_state(
                    conn,
                    company_id,
                    timeout_seconds,
                    latest_edge,
                    timed_out=False,
                    last_timeout_at=last_timeout_at,
                )
                print(
                    "EDGE RECOVERED:",
                    "Company=", company_id,
                    "LastReceived=", latest_edge,
                )

        conn.commit()
        print("EDGE TIMEOUT LOG: CHECK COMMITTED")
    except Exception as exc:
        print("EDGE TIMEOUT LOG: CHECK ERROR", repr(exc))
        raise
    finally:
        conn.close()


def _worker():
    print("EDGE TIMEOUT LOG: WORKER THREAD ENTERED")
    while True:
        try:
            check_once()
        except Exception as exc:
            print("EDGE TIMEOUT ERROR:", exc)
        time.sleep(WORKER_INTERVAL_SECONDS)


def start_worker():
    global _worker_started
    with _worker_lock:
        if _worker_started:
            print("EDGE TIMEOUT LOG: WORKER ALREADY STARTED")
            return
        _worker_started = True
        threading.Thread(
            target=_worker,
            name="SCADA-Edge-Timeout",
            daemon=True,
        ).start()
        print("EDGE TIMEOUT WORKER STARTED")
