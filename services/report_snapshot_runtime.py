"""Runtime report snapshot writer used by the Flow ReportOutput node.

Persistence is intentionally dumb: ReportOutput/preceding Flow nodes decide
which values exist and which columns are requested. This module does not read
ManagementPanel/ReportOutput configuration to invent calculations.
"""

import ast
import math
import sqlite3
from datetime import datetime

from database import get_connection
from services import report_plc as _report_plc


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
    used_names = set()

    # Every persisted value must be explicitly supplied by the Flow payload.
    # ReportOutput configuration merely chooses which values are columns.
    for item in all_products:
        tag = str(item.get("tag", "") or item.get("name", "")).strip()
        name = str(item.get("name", tag)).strip() or tag
        role = str(item.get("context_role", item.get("context", ""))).strip().lower()
        item_plc = _plc_id(item.get("plc_id", item.get("PLC_ID", plc_id)))
        if role or not tag:
            continue
        if item_plc is not None and plc_id is not None and item_plc != plc_id:
            continue

        found = lookup.get(tag.lower())
        if found is None or found[1] is None:
            continue
        try:
            if name.lower() not in used_names:
                values.append((name, float(found[1])))
                used_names.add(name.lower())
        except (TypeError, ValueError):
            continue

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
            [(name, value) for name, value in values],
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


_report_plc.save_report_snapshot = save_report_snapshot


__all__ = ["safe_flow_eval", "save_report_snapshot"]
