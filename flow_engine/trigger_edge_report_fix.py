# ============================================================
# SCADA_FLOW TRIGGER REPORT FIX
#
# Edge sends TRIGGER tags (B1/B2/B3) only when the PLC trigger
# register makes a 0 -> configured trigger transition. The
# server-side PLCReader historically expected the trigger
# register itself to be present in the Edge historian, so the
# report path could never see the event.
#
# This module bridges that protocol correctly:
#   Edge TRIGGER tag rows -> fresh event detection -> Report
#
# State is persisted in SQLite, so a server restart cannot
# repeatedly create a report from the same old Edge row.
# ============================================================

from datetime import datetime

from database import get_connection, insert_tag_value
from services.report_service import get_report_products, save_report_snapshot


_STATE_TABLE = "FlowEdgeTriggerEventState"


def _ensure_state_table():
    conn = get_connection()
    try:
        conn.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {_STATE_TABLE} (
                CompanyID INTEGER NOT NULL,
                TriggerRegister TEXT NOT NULL,
                LastTimestamp TEXT,
                LastID INTEGER,
                Initialized INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (CompanyID, TriggerRegister)
            )
            """
        )
        conn.commit()
    finally:
        conn.close()


def _row_key(row):
    return (
        str(row["Timestamp"] or ""),
        int(row["ID"] or 0),
    )


def _latest_trigger_rows(company_id, definitions):
    groups = {}

    for definition in definitions or []:
        if not isinstance(definition, dict):
            continue

        if str(definition.get("storage", "")).strip().upper() != "TRIGGER":
            continue

        name = str(definition.get("name", "")).strip()
        if not name:
            continue

        register = definition.get("trigger_register")
        if register in (None, ""):
            continue

        key = str(register)
        groups.setdefault(key, []).append(name)

    if not groups:
        return {}

    conn = get_connection()
    try:
        result = {}

        for register, names in groups.items():
            rows = {}

            for name in names:
                row = conn.execute(
                    """
                    SELECT ID, TagName, Value, Timestamp
                    FROM TagHistory
                    WHERE CompanyID = ?
                      AND TagName = ?
                    ORDER BY Timestamp DESC, ID DESC
                    LIMIT 1
                    """,
                    (int(company_id), name),
                ).fetchone()

                if row is not None:
                    rows[name] = row

            if rows:
                result[register] = rows

        return result
    finally:
        conn.close()


def _event_is_new(company_id, register, event_key):
    _ensure_state_table()

    conn = get_connection()
    try:
        row = conn.execute(
            f"""
            SELECT LastTimestamp, LastID, Initialized
            FROM {_STATE_TABLE}
            WHERE CompanyID = ?
              AND TriggerRegister = ?
            """,
            (int(company_id), str(register)),
        ).fetchone()

        if row is None:
            conn.execute(
                f"""
                INSERT INTO {_STATE_TABLE}
                (CompanyID, TriggerRegister, LastTimestamp, LastID, Initialized)
                VALUES (?, ?, ?, ?, 1)
                """,
                (
                    int(company_id),
                    str(register),
                    event_key[0],
                    event_key[1],
                ),
            )
            conn.commit()
            return False

        previous_key = (
            str(row["LastTimestamp"] or ""),
            int(row["LastID"] or 0),
        )

        if event_key <= previous_key:
            return False

        conn.execute(
            f"""
            UPDATE {_STATE_TABLE}
            SET LastTimestamp = ?,
                LastID = ?,
                Initialized = 1
            WHERE CompanyID = ?
              AND TriggerRegister = ?
            """,
            (
                event_key[0],
                event_key[1],
                int(company_id),
                str(register),
            ),
        )
        conn.commit()
        return True

    finally:
        conn.close()


def enrich_plc_reader_result(original_execute, reader, data):
    """Run the normal PLCReader and add fresh Edge TRIGGER tags."""
    result = original_execute(reader, data)
    if result is None:
        result = data or {}

    definitions = result.get("TagDefinitions", [])
    company_id = reader._get_config("company_id")

    try:
        company_id = int(company_id)
    except (TypeError, ValueError):
        return result

    groups = _latest_trigger_rows(company_id, definitions)
    if not groups:
        return result

    tags = result.setdefault("Tags", {})
    trigger_events = []

    for register, rows in groups.items():
        newest = max(rows.values(), key=_row_key)
        event_key = _row_key(newest)

        is_new = _event_is_new(
            company_id,
            register,
            event_key,
        )

        # Always expose the latest trigger values to downstream nodes.
        # Only a newly observed Edge row becomes a report event.
        for row in rows.values():
            name = str(row["TagName"]).strip()
            value = row["Value"]
            if name and value is not None:
                tags[name] = value

        if is_new:
            event_timestamp = str(newest["Timestamp"])
            trigger_events.append({
                "register": register,
                "timestamp": event_timestamp,
                "tags": {
                    str(row["TagName"]).strip(): row["Value"]
                    for row in rows.values()
                    if str(row["TagName"]).strip() and row["Value"] is not None
                },
            })

            print(
                "EDGE TRIGGER EVENT:",
                "Company=", company_id,
                "Register=", register,
                "Timestamp=", event_timestamp,
                "Tags=", trigger_events[-1]["tags"],
            )

    if trigger_events:
        result["EdgeTriggerEvents"] = trigger_events

    return result


def save_edge_trigger_reports(original_execute, writer, data):
    """Run SQLWriter, then persist one ReportHistory snapshot per fresh Edge event."""
    result = original_execute(writer, data)
    if result is None:
        result = data or {}

    events = result.get("EdgeTriggerEvents", [])
    if not isinstance(events, list) or not events:
        return result

    products = get_report_products(writer.company_id)
    if not products:
        return result

    for event in events:
        event_tags = event.get("tags", {})
        if not isinstance(event_tags, dict) or not event_tags:
            continue

        timestamp = event.get("timestamp")
        if not timestamp:
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        # Persist the trigger values in the normal PLC_Data historian too.
        for tag_name, value in event_tags.items():
            if value is None:
                continue
            try:
                insert_tag_value(
                    writer.company_id,
                    tag_name,
                    value,
                    "TRIGGER",
                    timestamp=timestamp,
                )
            except Exception as exc:
                print(
                    "EDGE TRIGGER PLC_DATA INSERT ERROR:",
                    tag_name,
                    repr(exc),
                )

        report_id = save_report_snapshot(
            writer.company_id,
            event_tags,
            products,
            timestamp=timestamp,
        )

        if report_id is not None:
            print(
                "EDGE TRIGGER REPORT SAVED:",
                "Company=", writer.company_id,
                "ReportID=", report_id,
                "Register=", event.get("register"),
                "Timestamp=", timestamp,
                "Tags=", list(event_tags.keys()),
            )

    result["Report_Written"] = int(result.get("Report_Written", 0) or 0) + len(events)
    return result


def install():
    from flow_engine.nodes.plc_reader import PLCReader
    from flow_engine.nodes.sql_writer import SQLWriter

    if not getattr(PLCReader, "_edge_trigger_report_fix", False):
        original_plc_execute = PLCReader.execute

        def plc_execute(self, data=None):
            return enrich_plc_reader_result(
                original_plc_execute,
                self,
                data,
            )

        PLCReader.execute = plc_execute
        PLCReader._edge_trigger_report_fix = True

    if not getattr(SQLWriter, "_edge_trigger_report_fix", False):
        original_sql_execute = SQLWriter.execute

        def sql_execute(self, data=None):
            return save_edge_trigger_reports(
                original_sql_execute,
                self,
                data,
            )

        SQLWriter.execute = sql_execute
        SQLWriter._edge_trigger_report_fix = True

    print("EDGE TRIGGER REPORT FIX INSTALLED")


install()
