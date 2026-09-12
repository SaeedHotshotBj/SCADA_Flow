"""Authoritative Edge TRIGGER event detector.

The service converts fresh EDGE historian rows for TRIGGER tags into durable,
de-duplicated runtime events. It does not create reports and it does not
monkey-patch PLCReader or SQLWriter.
"""

from datetime import datetime

from database import get_connection

STATE_TABLE = "FlowEdgeTriggerEventState"
SETTLE_SECONDS = 1.5


def _timestamp_age(value):
    try:
        text = str(value or "").strip().replace("T", " ").rstrip("Z")
        dt = datetime.fromisoformat(text)
        return max(0.0, (datetime.now() - dt).total_seconds())
    except Exception:
        return SETTLE_SECONDS + 1.0


def _row_key(row):
    return (str(row["Timestamp"] or ""), int(row["ID"] or 0))


def _ensure_state_table(conn):
    conn.execute(f"""
        CREATE TABLE IF NOT EXISTS {STATE_TABLE} (
            CompanyID INTEGER NOT NULL,
            TriggerRegister TEXT NOT NULL,
            LastTimestamp TEXT,
            LastID INTEGER,
            PRIMARY KEY (CompanyID, TriggerRegister)
        )
    """)
    conn.commit()


def _latest_rows(conn, company_id, definitions):
    groups = {}
    for definition in definitions or []:
        if not isinstance(definition, dict):
            continue
        if str(definition.get("storage", "TIME")).strip().upper() != "TRIGGER":
            continue
        name = str(definition.get("name", "")).strip()
        register = definition.get("trigger_register")
        if not name or register in (None, ""):
            continue
        groups.setdefault(str(register), []).append(name)

    result = {}
    for register, names in groups.items():
        rows = {}
        for name in names:
            row = conn.execute("""
                SELECT ID, TagName, Value, Timestamp
                FROM PLC_Data
                WHERE CompanyID = ?
                  AND LOWER(TagName) = LOWER(?)
                  AND UPPER(COALESCE(StorageType, '')) = 'EDGE'
                ORDER BY ID DESC
                LIMIT 1
            """, (int(company_id), name)).fetchone()
            if row is not None:
                rows[name] = row
        if rows:
            result[register] = rows
    return result


def _claim_event(conn, company_id, register, event_key):
    conn.execute("BEGIN IMMEDIATE")
    row = conn.execute(f"""
        SELECT LastTimestamp, LastID
        FROM {STATE_TABLE}
        WHERE CompanyID = ? AND TriggerRegister = ?
    """, (int(company_id), str(register))).fetchone()

    current_key = (str(event_key[0]), int(event_key[1]))
    if row is None:
        conn.execute(f"""
            INSERT INTO {STATE_TABLE}
            (CompanyID, TriggerRegister, LastTimestamp, LastID)
            VALUES (?, ?, ?, ?)
        """, (int(company_id), str(register), current_key[0], current_key[1]))
        conn.commit()
        return False

    previous_key = (str(row["LastTimestamp"] or ""), int(row["LastID"] or 0))
    if current_key <= previous_key:
        conn.rollback()
        return False

    conn.execute(f"""
        UPDATE {STATE_TABLE}
        SET LastTimestamp = ?, LastID = ?
        WHERE CompanyID = ? AND TriggerRegister = ?
    """, (current_key[0], current_key[1], int(company_id), str(register)))
    conn.commit()
    return True


class EdgeTriggerService:
    def enrich(self, payload):
        if not isinstance(payload, dict):
            return payload
        try:
            company_id = int(payload.get("CompanyID") or payload.get("company_id"))
            plc_id = int(payload.get("PLC_ID"))
        except (TypeError, ValueError):
            return payload

        definitions = payload.get("TagDefinitions", [])
        if not isinstance(definitions, list):
            return payload

        conn = get_connection()
        try:
            _ensure_state_table(conn)
            groups = _latest_rows(conn, company_id, definitions)
            if not groups:
                return payload

            tags = payload.setdefault("Tags", {})
            events = []
            for register, rows in groups.items():
                newest = max(rows.values(), key=_row_key)
                for row in rows.values():
                    tags[str(row["TagName"])] = row["Value"]

                if _timestamp_age(newest["Timestamp"]) < SETTLE_SECONDS:
                    continue
                if not _claim_event(conn, company_id, register, _row_key(newest)):
                    continue

                event_tags = {
                    str(row["TagName"]): row["Value"]
                    for row in rows.values()
                    if row["Value"] is not None
                }
                events.append({
                    "company_id": company_id,
                    "PLC_ID": plc_id,
                    "register": register,
                    "timestamp": str(newest["Timestamp"]),
                    "tags": event_tags,
                })

            if events:
                payload["EdgeTriggerEvents"] = events
            return payload
        finally:
            conn.close()


__all__ = ["EdgeTriggerService"]
