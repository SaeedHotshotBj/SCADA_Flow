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
        try:
            flow = json.loads(row["FlowJson"] or "{}")
        except Exception:
            continue

        nodes = (
            flow.get("drawflow", {})
            .get("Home", {})
            .get("data", {})
        )

        timeout = None
        tag_names = []

        for node in nodes.values():
            if node.get("name") == "EdgeTimeout":
                data = node.get("data", {}) or {}
                config = data.get("config", data) or {}
                try:
                    timeout = float(
                        config.get(
                            "timeout_seconds",
                            DEFAULT_TIMEOUT_SECONDS,
                        )
                    )
                except (TypeError, ValueError):
                    timeout = DEFAULT_TIMEOUT_SECONDS
                if timeout <= 0:
                    timeout = DEFAULT_TIMEOUT_SECONDS
                break

        if timeout is None:
            continue

        seen_tags = set()
        for node in nodes.values():
            if node.get("name") != "TagMapper":
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

        if tag_names:
            result[int(row["CompanyID"])] = {
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

    return row["LastReceivedAt"] if row else None


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

    return written, timestamp


def check_once():
    conn = get_connection()
    try:
        _ensure_state_table(conn)
        configs = _load_company_timeout_configs(conn)
        if not configs:
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
                    timed_out = False
                previous_last_received = latest_edge

            if previous_last_received is None:
                continue

            latest_dt = _parse_timestamp(previous_last_received)
            if latest_dt is None:
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

            if elapsed > timeout_seconds and not timed_out:
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
                    "EDGE TIMEOUT:",
                    "Company=", company_id,
                    "Timeout=", timeout_seconds,
                    "Elapsed=", round(elapsed, 2),
                    "ZeroRows=", written,
                )

            elif latest_edge is not None and timed_out and latest_edge != state["LastReceivedAt"]:
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
    finally:
        conn.close()


def _worker():
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
            return
        _worker_started = True
        threading.Thread(
            target=_worker,
            name="SCADA-Edge-Timeout",
            daemon=True,
        ).start()
        print("EDGE TIMEOUT WORKER STARTED")
