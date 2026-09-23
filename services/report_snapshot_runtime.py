"""Runtime report snapshot writer used by the Flow report service.

This module keeps report persistence event-driven while fixing the expression
AST validation and ReportValues foreign-key binding used by ProductionEvents.
"""

import ast
import math
import sqlite3
from datetime import datetime

from database import get_connection
from services import report_plc as _report_plc
from services.edge_ingest_policy import install as _install_edge_ingest_policy

# Edge trigger samples are part of the same Flow-defined production-event
# pipeline. Install the ingestion policy as this runtime facade is loaded by
# FlowRunner during application startup, before Store & Forward requests.
_install_edge_ingest_policy()


def safe_flow_eval(expression, variables):
    tree = ast.parse(str(expression or ""), mode="eval")
    allowed_nodes = {
        ast.Expression,
        ast.Constant,
        ast.Name,
        ast.Load,
        ast.BinOp,
        ast.UnaryOp,
        ast.Add,
        ast.Sub,
        ast.Mult,
        ast.Div,
        ast.Mod,
        ast.Pow,
        ast.USub,
        ast.UAdd,
        ast.FloorDiv,
    }
    for node in ast.walk(tree):
        if not isinstance(node, tuple(allowed_nodes)):
            raise ValueError("Unsupported expression operation")
        if isinstance(node, ast.Constant) and not isinstance(node.value, (int, float)):
            raise ValueError("Expression must contain numeric constants only")
        if isinstance(node, ast.Name) and node.id not in variables:
            raise ValueError(f"Unknown variable: {node.id}")

    value = eval(
        compile(tree, "<flow-report-expression>", "eval"),
        {"__builtins__": {}},
        variables,
    )
    value = float(value)
    if not math.isfinite(value):
        raise ValueError("Expression result is not finite")
    return value


# Keep every caller that reaches report_plc internals on the corrected
# evaluator and writer. The public report service already imports this module;
# replacing both attributes here also prevents a stale direct import from
# falling back to the old implementation after this runtime has initialized.
_report_plc._safe_eval = safe_flow_eval


def _plc_id(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _snapshot_context(report_products, tags):
    return _report_plc._context(report_products, tags)


def save_report_snapshot(
    company_id,
    tags,
    report_products,
    timestamp=None,
    trigger_tag=None,
    trigger_register=None,
    trigger_value=None,
    plc_id=None,
    trigger_edge=None,
    start_timestamp=None,
    end_timestamp=None,
    duration_seconds=0,
    start_complete=1,
    trigger_event_id=None,
    report_node_id=None,
):
    if company_id is None or not isinstance(tags, dict):
        return None

    _report_plc.ensure_report_tables()
    all_products = [item for item in (report_products or []) if isinstance(item, dict)]
    calculation_definitions = _report_plc._all_management_calculations(company_id)

    if trigger_event_id and report_node_id:
        conn = get_connection()
        try:
            row = conn.execute(
                "SELECT ReportID FROM ReportHistory WHERE TriggerEventID=? AND ReportNodeID=? LIMIT 1",
                (str(trigger_event_id), str(report_node_id)),
            ).fetchone()
            if row:
                return int(row["ReportID"])
        finally:
            conn.close()

    duration = float(duration_seconds or 0.0)
    timestamp = timestamp or end_timestamp or start_timestamp or datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    plc_id = _plc_id(plc_id)
    contract, product = _snapshot_context(all_products, tags)
    lookup = {str(key).strip().lower(): (key, value) for key, value in tags.items()}
    values = []

    for item in all_products:
        tag = str(item.get("tag", "")).strip()
        role = str(item.get("context_role", item.get("context", ""))).strip().lower()
        source = item.get("source")
        item_plc = _plc_id(item.get("plc_id", item.get("PLC_ID", plc_id)))
        if role or source == "management_calculation" or not tag:
            continue
        if item_plc is not None and plc_id is not None and item_plc != plc_id:
            continue
        found = lookup.get(tag.lower())
        if found is None or found[1] is None:
            continue
        try:
            values.append((str(item.get("name", tag)).strip() or tag, float(found[1])))
        except (TypeError, ValueError):
            pass

    tagmapper_values = _report_plc._collect_tagmapper_values(tags, company_id, plc_id)

    variables = _report_plc._formula_variables(tags, duration)
    for calculation in calculation_definitions:
        try:
            result = safe_flow_eval(calculation["expression"], variables)
            values.append((calculation["name"], result))
            variables[calculation["name"]] = result
            alias = "".join(
                char if (char.isalnum() or char == "_") else "_"
                for char in calculation["name"]
            )
            if alias:
                variables[alias] = result
        except Exception as exc:
            print(
                "REPORT CALCULATION ERROR:",
                calculation["name"],
                calculation["expression"],
                exc,
            )

    existing_names = {key.lower() for key, _ in values}
    for name, value in (
        ("WorkTimeSeconds", duration),
        ("WorkTimeMinutes", duration / 60.0),
        ("WorkTimeHours", duration / 3600.0),
    ):
        if name.lower() not in existing_names:
            values.append((name, value))

    if not values:
        return None

    conn = get_connection()
    try:
        cur = conn.execute(
            """
            INSERT INTO ReportHistory
            (CompanyID, PLC_ID, Timestamp, TriggerTag, TriggerRegister,
             TriggerValue, TriggerEdge, StartTimestamp, EndTimestamp,
             DurationSeconds, StartComplete, TriggerEventID, ReportNodeID,
             ContractCode, ProductCode)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                company_id,
                plc_id,
                timestamp,
                trigger_tag,
                trigger_register,
                trigger_value,
                trigger_edge,
                start_timestamp,
                end_timestamp,
                duration,
                int(bool(start_complete)),
                str(trigger_event_id) if trigger_event_id else None,
                str(report_node_id) if report_node_id else None,
                contract,
                product,
            ),
        )
        report_id = cur.lastrowid
        conn.executemany(
            "INSERT INTO ReportValues(ReportID,TagName,Value) VALUES(?,?,?)",
            [(report_id, name, value) for name, value in values],
        )
        _report_plc._persist_report_tag_values(
            conn,
            report_id,
            tagmapper_values,
            plc_id,
        )
        conn.commit()
        return int(report_id)
    except sqlite3.IntegrityError:
        conn.rollback()
        if trigger_event_id and report_node_id:
            row = conn.execute(
                "SELECT ReportID FROM ReportHistory WHERE TriggerEventID=? AND ReportNodeID=? LIMIT 1",
                (str(trigger_event_id), str(report_node_id)),
            ).fetchone()
            return int(row["ReportID"]) if row else None
        raise
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# Make the compatibility import path use the same corrected event-driven
# implementation once this runtime has loaded.
_report_plc.save_report_snapshot = save_report_snapshot


__all__ = ["safe_flow_eval", "save_report_snapshot"]
