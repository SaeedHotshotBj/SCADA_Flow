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
import os
import threading
import traceback
import uuid

from database import get_connection, insert_tag_value
from services.report_service import get_report_products, save_report_snapshot


_STATE_TABLE = "FlowEdgeTriggerEventState"
TRIGGER_SETTLE_SECONDS = 1.5
_TRACE_LOCK = threading.Lock()
_TRACE_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "logs",
    "report_trigger_trace.log",
)


def _trace(message, **fields):
    """Write a durable diagnostic trace without affecting SCADA execution."""
    try:
        os.makedirs(os.path.dirname(_TRACE_PATH), exist_ok=True)
        parts = [
            datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3],
            str(message),
        ]
        for key, value in fields.items():
            try:
                rendered = repr(value)
            except Exception:
                rendered = "<unprintable>"
            parts.append(f"{key}={rendered}")
        line = " | ".join(parts) + "\n"
        with _TRACE_LOCK:
            with open(_TRACE_PATH, "a", encoding="utf-8") as handle:
                handle.write(line)
    except Exception:
        # Diagnostics must never break the report path.
        pass


_TRACE_VERSION = "2026-09-01-v1"


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


def _timestamp_age(timestamp):
    try:
        text = str(timestamp or "").strip().replace("Z", "")
        dt = datetime.fromisoformat(text)
        return max(0.0, (datetime.now() - dt).total_seconds())
    except Exception:
        return TRIGGER_SETTLE_SECONDS + 1.0


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

    _trace(
        "TRIGGER_ROWS_QUERY_START",
        company_id=company_id,
        groups=groups,
    )

    if not groups:
        _trace("TRIGGER_ROWS_QUERY_NO_GROUPS", company_id=company_id)
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
                    _trace(
                        "TRIGGER_TAGHISTORY_ROW",
                        company_id=company_id,
                        register=register,
                        tag=name,
                        id=row["ID"],
                        value=row["Value"],
                        timestamp=row["Timestamp"],
                        age_seconds=_timestamp_age(row["Timestamp"]),
                    )
                else:
                    _trace(
                        "TRIGGER_TAGHISTORY_MISSING",
                        company_id=company_id,
                        register=register,
                        tag=name,
                    )

            if rows:
                result[register] = rows

        _trace(
            "TRIGGER_ROWS_QUERY_END",
            company_id=company_id,
            result_summary={
                register: {
                    name: {
                        "id": int(row["ID"]),
                        "value": row["Value"],
                        "timestamp": str(row["Timestamp"] or ""),
                    }
                    for name, row in rows.items()
                }
                for register, rows in result.items()
            },
        )
        return result
    finally:
        conn.close()


def _event_is_new(company_id, register, event_key, trace_id=None):
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
            _trace(
                "TRIGGER_STATE_SEEDED",
                trace_id=trace_id,
                company_id=company_id,
                register=register,
                event_key=event_key,
            )
            return False

        previous_key = (
            str(row["LastTimestamp"] or ""),
            int(row["LastID"] or 0),
        )

        if event_key <= previous_key:
            _trace(
                "TRIGGER_EVENT_NOT_NEW",
                trace_id=trace_id,
                company_id=company_id,
                register=register,
                previous_key=previous_key,
                current_key=event_key,
            )
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
        _trace(
            "TRIGGER_EVENT_NEW",
            trace_id=trace_id,
            company_id=company_id,
            register=register,
            previous_key=previous_key,
            current_key=event_key,
        )
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

    _trace(
        "PLC_ENRICH_START",
        trace_id=str(uuid.uuid4()),
        trace_version=_TRACE_VERSION,
        company_id=company_id,
        data_keys=sorted(str(key) for key in result.keys()),
        tag_definitions=definitions,
        tags=result.get("Tags", {}),
        registers=result.get("Registers", {}),
        timestamp=result.get("Timestamp"),
    )

    try:
        company_id = int(company_id)
    except (TypeError, ValueError):
        _trace("PLC_ENRICH_INVALID_COMPANY", company_id=company_id)
        return result

    groups = _latest_trigger_rows(company_id, definitions)
    if not groups:
        _trace("PLC_ENRICH_NO_TRIGGER_ROWS", company_id=company_id)
        return result

    tags = result.setdefault("Tags", {})
    trigger_events = []

    for register, rows in groups.items():
        newest = max(rows.values(), key=_row_key)
        event_key = _row_key(newest)
        age = _timestamp_age(newest["Timestamp"])

        _trace(
            "TRIGGER_GROUP_EVALUATE",
            company_id=company_id,
            register=register,
            tags=list(rows.keys()),
            newest_tag=newest["TagName"],
            newest_id=newest["ID"],
            newest_timestamp=newest["Timestamp"],
            newest_value=newest["Value"],
            age_seconds=age,
            settle_seconds=TRIGGER_SETTLE_SECONDS,
        )

        # Edge sends B1/B2/B3 as separate HTTP requests. Wait until the
        # newest row is stable before declaring one trigger event. This
        # prevents one PLC trigger from producing multiple reports while
        # the three tag requests are arriving milliseconds apart.
        if age < TRIGGER_SETTLE_SECONDS:
            _trace(
                "TRIGGER_GROUP_WAIT_SETTLE",
                company_id=company_id,
                register=register,
                age_seconds=age,
            )
            for row in rows.values():
                name = str(row["TagName"]).strip()
                value = row["Value"]
                if name and value is not None:
                    tags[name] = value
            continue

        trace_id = str(uuid.uuid4())
        is_new = _event_is_new(
            company_id,
            register,
            event_key,
            trace_id=trace_id,
        )

        # Always expose the latest trigger values to downstream nodes.
        # Only a newly observed Edge row becomes a report event.
        for row in rows.values():
            name = str(row["TagName"]).strip()
            value = row["Value"]
            if name and value is not None:
                tags[name] = value

        _trace(
            "TRIGGER_GROUP_RESULT",
            trace_id=trace_id,
            company_id=company_id,
            register=register,
            is_new=is_new,
            output_tags=tags,
        )

        if is_new:
            event_timestamp = str(newest["Timestamp"])
            event_tags = {
                str(row["TagName"]).strip(): row["Value"]
                for row in rows.values()
                if str(row["TagName"]).strip() and row["Value"] is not None
            }
            trigger_events.append({
                "register": register,
                "timestamp": event_timestamp,
                "tags": event_tags,
            })

            _trace(
                "EDGE_TRIGGER_EVENT_CREATED",
                trace_id=trace_id,
                company_id=company_id,
                register=register,
                timestamp=event_timestamp,
                tags=event_tags,
            )

            print(
                "EDGE TRIGGER EVENT:",
                "Company=", company_id,
                "Register=", register,
                "Timestamp=", event_timestamp,
                "Tags=", event_tags,
            )

    if trigger_events:
        result["EdgeTriggerEvents"] = trigger_events

    _trace(
        "PLC_ENRICH_END",
        company_id=company_id,
        edge_trigger_events=result.get("EdgeTriggerEvents", []),
        final_tags=result.get("Tags", {}),
    )

    return result


def _report_history_latest(company_id, timestamp):
    """Read back the just-created ReportHistory row for diagnostics."""
    conn = get_connection()
    try:
        row = conn.execute(
            """
            SELECT ReportID, CompanyID, Timestamp, TriggerTag,
                   TriggerRegister, TriggerValue, ContractCode, ProductCode
            FROM ReportHistory
            WHERE CompanyID = ?
            ORDER BY ReportID DESC
            LIMIT 1
            """,
            (int(company_id),),
        ).fetchone()
        if row is None:
            return None
        return {
            key: row[key]
            for key in (
                "ReportID",
                "CompanyID",
                "Timestamp",
                "TriggerTag",
                "TriggerRegister",
                "TriggerValue",
                "ContractCode",
                "ProductCode",
            )
        }
    finally:
        conn.close()


def save_edge_trigger_reports(original_execute, writer, data):
    """Run SQLWriter, then persist one ReportHistory snapshot per fresh Edge event."""
    trace_id = str(uuid.uuid4())

    _trace(
        "REPORT_SAVE_START",
        trace_id=trace_id,
        company_id=getattr(writer, "company_id", None),
        data_keys=sorted(str(key) for key in (data or {}).keys()),
        input_tags=(data or {}).get("Tags", {}) if isinstance(data, dict) else {},
        input_events=(data or {}).get("EdgeTriggerEvents", []) if isinstance(data, dict) else [],
    )

    try:
        result = original_execute(writer, data)
    except Exception as exc:
        _trace(
            "SQLWRITER_EXECUTE_EXCEPTION",
            trace_id=trace_id,
            error=repr(exc),
            traceback=traceback.format_exc(),
        )
        raise

    if result is None:
        result = data or {}

    events = result.get("EdgeTriggerEvents", [])
    _trace(
        "REPORT_SAVE_AFTER_SQLWRITER",
        trace_id=trace_id,
        result_keys=sorted(str(key) for key in result.keys()),
        result_tags=result.get("Tags", {}),
        events=events,
    )

    if not isinstance(events, list) or not events:
        _trace(
            "REPORT_SAVE_NO_EVENTS",
            trace_id=trace_id,
            company_id=getattr(writer, "company_id", None),
        )
        return result

    products = get_report_products(writer.company_id)
    _trace(
        "REPORT_PRODUCTS_LOADED",
        trace_id=trace_id,
        company_id=writer.company_id,
        product_count=len(products) if isinstance(products, list) else None,
        products=products,
    )

    if not products:
        _trace(
            "REPORT_SAVE_NO_PRODUCTS",
            trace_id=trace_id,
            company_id=writer.company_id,
        )
        return result

    saved = 0

    for index, event in enumerate(events, start=1):
        event_tags = event.get("tags", {})
        event_trace_id = f"{trace_id}-{index}"

        _trace(
            "REPORT_EVENT_START",
            trace_id=event_trace_id,
            company_id=writer.company_id,
            event=event,
            event_tags=event_tags,
        )

        if not isinstance(event_tags, dict) or not event_tags:
            _trace(
                "REPORT_EVENT_SKIPPED_EMPTY_TAGS",
                trace_id=event_trace_id,
            )
            continue

        timestamp = event.get("timestamp")
        if not timestamp:
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        _trace(
            "REPORT_EVENT_CONTEXT_BEFORE_SAVE",
            trace_id=event_trace_id,
            timestamp=timestamp,
            event_tags=event_tags,
            company_id=writer.company_id,
        )

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
                _trace(
                    "TRIGGER_PLC_DATA_INSERT_OK",
                    trace_id=event_trace_id,
                    tag=tag_name,
                    value=value,
                    timestamp=timestamp,
                    storage_type="TRIGGER",
                )
            except Exception as exc:
                _trace(
                    "TRIGGER_PLC_DATA_INSERT_ERROR",
                    trace_id=event_trace_id,
                    tag=tag_name,
                    value=value,
                    error=repr(exc),
                    traceback=traceback.format_exc(),
                )
                print(
                    "EDGE TRIGGER PLC_DATA INSERT ERROR:",
                    tag_name,
                    repr(exc),
                )

        # Read the exact values that will be handed to report_service.
        try:
            current_products = get_report_products(writer.company_id)
        except Exception:
            current_products = products

        _trace(
            "REPORT_SAVE_CALL",
            trace_id=event_trace_id,
            company_id=writer.company_id,
            timestamp=timestamp,
            event_tags=event_tags,
            report_products=current_products,
            has_contract_direct=(
                "ContractCode" in event_tags or "contractcode" in {
                    str(key).strip().lower() for key in event_tags.keys()
                }
            ),
            has_product_direct=(
                "ProductCode" in event_tags or "productcode" in {
                    str(key).strip().lower() for key in event_tags.keys()
                }
            ),
        )

        try:
            report_id = save_report_snapshot(
                writer.company_id,
                event_tags,
                current_products,
                timestamp=timestamp,
            )
        except Exception as exc:
            _trace(
                "REPORT_SAVE_EXCEPTION",
                trace_id=event_trace_id,
                company_id=writer.company_id,
                error=repr(exc),
                traceback=traceback.format_exc(),
            )
            raise

        latest_history = _report_history_latest(
            writer.company_id,
            timestamp,
        )

        _trace(
            "REPORT_SAVE_RESULT",
            trace_id=event_trace_id,
            report_id=report_id,
            latest_report_history=latest_history,
        )

        if report_id is not None:
            saved += 1
            print(
                "EDGE TRIGGER REPORT SAVED:",
                "Company=", writer.company_id,
                "ReportID=", report_id,
                "Register=", event.get("register"),
                "Timestamp=", timestamp,
                "Tags=", list(event_tags.keys()),
            )
        else:
            _trace(
                "REPORT_SAVE_RETURNED_NONE",
                trace_id=event_trace_id,
                company_id=writer.company_id,
                latest_report_history=latest_history,
            )

    result["Report_Written"] = int(result.get("Report_Written", 0) or 0) + saved

    _trace(
        "REPORT_SAVE_END",
        trace_id=trace_id,
        company_id=writer.company_id,
        saved=saved,
        report_written=result.get("Report_Written"),
    )

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

    _trace(
        "TRACE_MODULE_INSTALLED",
        trace_version=_TRACE_VERSION,
        trace_path=_TRACE_PATH,
    )
    print("EDGE TRIGGER REPORT FIX INSTALLED")


install()
