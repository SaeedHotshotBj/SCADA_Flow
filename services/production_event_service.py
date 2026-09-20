"""Flow-driven production trigger state machine.

TRIGGER tags define a trigger register/value and an edge (rise/fall).
The service persists the signal state so a production cycle can span many
PLC scans and survives application restarts.
"""

import json
import math
import uuid
from datetime import datetime

from database import get_connection

STATE_TABLE = "FlowTriggerState"
EVENT_TABLE = "ProductionEvents"


def _timestamp(value):
    text = str(value or "").strip().replace("T", " ")
    if not text:
        return datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")
    return text


def _same_value(left, right):
    try:
        return math.isclose(float(left), float(right), rel_tol=0.0, abs_tol=1e-9)
    except (TypeError, ValueError):
        return str(left) == str(right)


def _duration_seconds(start, end):
    try:
        start_dt = datetime.fromisoformat(str(start).replace("T", " "))
        end_dt = datetime.fromisoformat(str(end).replace("T", " "))
        return max(0.0, (end_dt - start_dt).total_seconds())
    except (TypeError, ValueError):
        return 0.0


def _snapshot(values):
    result = {}
    for name, value in (values or {}).items():
        try:
            number = float(value)
            if math.isfinite(number):
                result[str(name)] = number
        except (TypeError, ValueError):
            continue
    return result


def ensure_production_event_schema():
    conn = get_connection()
    try:
        conn.executescript(
            f"""
            CREATE TABLE IF NOT EXISTS {STATE_TABLE} (
                CompanyID INTEGER NOT NULL,
                PLC_ID INTEGER NOT NULL,
                TriggerRegister INTEGER NOT NULL,
                ExpectedValue TEXT NOT NULL,
                Active INTEGER NOT NULL DEFAULT 0,
                StartTimestamp TEXT,
                StartSnapshotJSON TEXT,
                LastSignalID INTEGER,
                LastSignalTimestamp TEXT,
                LastSignalValue REAL,
                PRIMARY KEY (CompanyID, PLC_ID, TriggerRegister, ExpectedValue)
            );

            CREATE TABLE IF NOT EXISTS {EVENT_TABLE} (
                EventID TEXT PRIMARY KEY,
                CompanyID INTEGER NOT NULL,
                PLC_ID INTEGER NOT NULL,
                TriggerRegister INTEGER NOT NULL,
                ExpectedValue REAL,
                TriggerValue REAL,
                Edge TEXT NOT NULL,
                TriggerTimestamp TEXT NOT NULL,
                StartTimestamp TEXT,
                EndTimestamp TEXT,
                DurationSeconds REAL NOT NULL DEFAULT 0,
                StartComplete INTEGER NOT NULL DEFAULT 1,
                TagsJSON TEXT,
                StartTagsJSON TEXT,
                CreatedAt TEXT NOT NULL DEFAULT (datetime('now','localtime'))
            );

            CREATE INDEX IF NOT EXISTS idx_production_events_company_time
            ON {EVENT_TABLE}(CompanyID, PLC_ID, TriggerTimestamp);
            """
        )
        conn.commit()
    finally:
        conn.close()


def trigger_definitions(definitions, register):
    """Return unique trigger conditions for one physical trigger register.

    Multiple TRIGGER TagMapper mappings may intentionally share the same
    register/value (for example several production fields captured on one
    machine-cycle trigger). They describe one physical edge, not multiple
    production events.
    """
    result = []
    seen = set()
    for item in definitions or []:
        if not isinstance(item, dict):
            continue
        if str(item.get("storage", "TIME")).strip().upper() != "TRIGGER":
            continue
        try:
            if int(item.get("trigger_register")) != int(register):
                continue
            expected = float(item.get("trigger_value", 0))
        except (TypeError, ValueError):
            continue
        edge = str(item.get("trigger_edge", "rise")).strip().lower()
        if edge not in {"rise", "fall"}:
            edge = "rise"
        key = (expected, edge)
        if key in seen:
            continue
        seen.add(key)
        result.append((item, edge))
    return result


def process_trigger_signal(
    company_id,
    plc_id,
    register,
    value,
    signal_timestamp,
    signal_id,
    definitions,
    snapshot_tags=None,
):
    """Process one ordered trigger signal and emit one event per unique edge."""
    ensure_production_event_schema()

    company_id = int(company_id)
    plc_id = int(plc_id)
    register = int(register)
    signal_id = int(signal_id)
    timestamp = _timestamp(signal_timestamp)
    snapshot = _snapshot(snapshot_tags)
    events = []

    conditions = {}
    for definition, configured_edge in trigger_definitions(definitions, register):
        try:
            expected = float(definition.get("trigger_value", 0))
        except (TypeError, ValueError):
            continue
        conditions.setdefault(str(expected), []).append(
            (definition, configured_edge, expected)
        )

    conn = get_connection()
    try:
        conn.execute("BEGIN IMMEDIATE")

        for expected_key, condition_definitions in conditions.items():
            expected = condition_definitions[0][2]
            state = conn.execute(
                f"""
                SELECT * FROM {STATE_TABLE}
                WHERE CompanyID=? AND PLC_ID=? AND TriggerRegister=? AND ExpectedValue=?
                """,
                (company_id, plc_id, register, expected_key),
            ).fetchone()

            active = _same_value(value, expected)

            # First observation establishes the baseline only. This prevents a
            # server restart during an already-active machine cycle from
            # generating a false rise event.
            if state is None:
                conn.execute(
                    f"""
                    INSERT INTO {STATE_TABLE}
                    (CompanyID, PLC_ID, TriggerRegister, ExpectedValue,
                     Active, StartTimestamp, StartSnapshotJSON,
                     LastSignalID, LastSignalTimestamp, LastSignalValue)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        company_id,
                        plc_id,
                        register,
                        expected_key,
                        int(active),
                        timestamp if active else None,
                        json.dumps(snapshot, ensure_ascii=False),
                        signal_id,
                        timestamp,
                        float(value),
                    ),
                )
                continue

            if state["LastSignalID"] is not None and signal_id <= int(state["LastSignalID"]):
                continue

            previous_active = bool(state["Active"])
            start_timestamp = state["StartTimestamp"]
            try:
                start_snapshot = json.loads(state["StartSnapshotJSON"] or "{}")
            except Exception:
                start_snapshot = {}

            transition = "rise" if (not previous_active and active) else (
                "fall" if (previous_active and not active) else None
            )

            if transition == "rise":
                conn.execute(
                    f"""
                    UPDATE {STATE_TABLE}
                    SET Active=1,
                        StartTimestamp=?,
                        StartSnapshotJSON=?,
                        LastSignalID=?,
                        LastSignalTimestamp=?,
                        LastSignalValue=?
                    WHERE CompanyID=? AND PLC_ID=? AND TriggerRegister=? AND ExpectedValue=?
                    """,
                    (
                        timestamp,
                        json.dumps(snapshot, ensure_ascii=False),
                        signal_id,
                        timestamp,
                        float(value),
                        company_id,
                        plc_id,
                        register,
                        expected_key,
                    ),
                )
                if any(edge == "rise" for _, edge, _ in condition_definitions):
                    event_id = uuid.uuid4().hex
                    conn.execute(
                        f"""
                        INSERT INTO {EVENT_TABLE}
                        (EventID, CompanyID, PLC_ID, TriggerRegister,
                         ExpectedValue, TriggerValue, Edge, TriggerTimestamp,
                         StartTimestamp, EndTimestamp, DurationSeconds,
                         StartComplete, TagsJSON, StartTagsJSON)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL, 0, 1, ?, ?)
                        """,
                        (
                            event_id,
                            company_id,
                            plc_id,
                            register,
                            expected,
                            float(value),
                            "rise",
                            timestamp,
                            timestamp,
                            json.dumps(snapshot, ensure_ascii=False),
                            json.dumps(snapshot, ensure_ascii=False),
                        ),
                    )
                    events.append({
                        "event_id": event_id,
                        "company_id": company_id,
                        "PLC_ID": plc_id,
                        "register": register,
                        "trigger_value": float(value),
                        "edge": "rise",
                        "timestamp": timestamp,
                        "start_timestamp": timestamp,
                        "end_timestamp": None,
                        "duration_seconds": 0.0,
                        "start_complete": 1,
                        "tags": snapshot,
                        "start_tags": snapshot,
                    })
                continue

            if transition == "fall":
                end_timestamp = timestamp
                duration = _duration_seconds(start_timestamp, end_timestamp) if start_timestamp else 0.0

                conn.execute(
                    f"""
                    UPDATE {STATE_TABLE}
                    SET Active=0,
                        StartTimestamp=NULL,
                        StartSnapshotJSON=NULL,
                        LastSignalID=?,
                        LastSignalTimestamp=?,
                        LastSignalValue=?
                    WHERE CompanyID=? AND PLC_ID=? AND TriggerRegister=? AND ExpectedValue=?
                    """,
                    (
                        signal_id,
                        timestamp,
                        float(value),
                        company_id,
                        plc_id,
                        register,
                        expected_key,
                    ),
                )

                if any(edge == "fall" for _, edge, _ in condition_definitions):
                    event_id = uuid.uuid4().hex
                    conn.execute(
                        f"""
                        INSERT INTO {EVENT_TABLE}
                        (EventID, CompanyID, PLC_ID, TriggerRegister,
                         ExpectedValue, TriggerValue, Edge, TriggerTimestamp,
                         StartTimestamp, EndTimestamp, DurationSeconds,
                         StartComplete, TagsJSON, StartTagsJSON)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            event_id,
                            company_id,
                            plc_id,
                            register,
                            expected,
                            float(value),
                            "fall",
                            end_timestamp,
                            start_timestamp,
                            end_timestamp,
                            duration,
                            int(bool(start_timestamp)),
                            json.dumps(snapshot, ensure_ascii=False),
                            json.dumps(start_snapshot, ensure_ascii=False),
                        ),
                    )
                    events.append({
                        "event_id": event_id,
                        "company_id": company_id,
                        "PLC_ID": plc_id,
                        "register": register,
                        "trigger_value": float(value),
                        "edge": "fall",
                        "timestamp": end_timestamp,
                        "start_timestamp": start_timestamp,
                        "end_timestamp": end_timestamp,
                        "duration_seconds": duration,
                        "start_complete": int(bool(start_timestamp)),
                        "tags": snapshot,
                        "start_tags": start_snapshot,
                    })
                continue

            conn.execute(
                f"""
                UPDATE {STATE_TABLE}
                SET LastSignalID=?, LastSignalTimestamp=?, LastSignalValue=?
                WHERE CompanyID=? AND PLC_ID=? AND TriggerRegister=? AND ExpectedValue=?
                """,
                (
                    signal_id,
                    timestamp,
                    float(value),
                    company_id,
                    plc_id,
                    register,
                    expected_key,
                ),
            )

        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    return events

__all__ = [
    "STATE_TABLE",
    "EVENT_TABLE",
    "ensure_production_event_schema",
    "trigger_definitions",
    "process_trigger_signal",
]
