import ast
import json
import math
import sqlite3
from datetime import datetime

from database import get_connection, get_company_flow

_CONTEXT_CONTRACT_ROLES = {"contract", "contract_code", "contractid", "contract_id"}
_CONTEXT_PRODUCT_ROLES = {"product", "product_code", "productid", "product_id"}


def _flow_nodes(company_id):
    flow = get_company_flow(company_id)
    if not flow:
        return {}
    if isinstance(flow, str):
        try:
            flow = json.loads(flow)
        except Exception:
            return {}
    return flow.get("drawflow", {}).get("Home", {}).get("data", {}) or {}


def _plc_id(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _roles(value):
    if isinstance(value, (list, tuple, set)):
        return {str(item).strip().lower() for item in value if str(item).strip()}
    text = str(value or "").replace(";", ",").replace("\n", ",")
    return {item.strip().lower() for item in text.split(",") if item.strip()}


def _allowed(item, user_role):
    configured = _roles(item.get("allowed_roles")) if isinstance(item, dict) else set()
    if not configured:
        return True
    return str(user_role or "").strip().lower() in configured


def _management_calculations(company_id, user_role=None):
    result = []
    seen = set()
    for node in _flow_nodes(company_id).values():
        if not isinstance(node, dict) or node.get("name") != "ManagementPanel":
            continue
        data = node.get("data", {}) or {}
        config = data.get("config", data) or {}
        calculations = config.get("calculations", []) if isinstance(config, dict) else []
        if not isinstance(calculations, list):
            continue
        for item in calculations:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name", item.get("result_name", ""))).strip()
            expression = str(item.get("expression", "")).strip()
            if not name or not expression or name.lower() in seen:
                continue
            if not _allowed(item, user_role):
                continue
            seen.add(name.lower())
            result.append({
                "name": name,
                "label": str(item.get("label", name)).strip() or name,
                "expression": expression,
                "unit": str(item.get("unit", "")).strip(),
                "allowed_roles": item.get("allowed_roles", ""),
                "source": "management_calculation",
            })
    return result


def _report_calculations(company_id, user_role=None):
    result = []
    seen = set()
    for node_id, node in _flow_nodes(company_id).items():
        if not isinstance(node, dict) or node.get("name") != "ReportOutput":
            continue
        data = node.get("data", {}) or {}
        config = data.get("config", data) or {}
        calculations = config.get("calculations", []) if isinstance(config, dict) else []
        if not isinstance(calculations, list):
            continue
        for item in calculations:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name", item.get("result_name", ""))).strip()
            expression = str(item.get("expression", "")).strip()
            if not name or not expression or name.lower() in seen:
                continue
            if not _allowed(item, user_role):
                continue
            seen.add(name.lower())
            result.append({
                "name": name,
                "label": str(item.get("label", name)).strip() or name,
                "expression": expression,
                "unit": str(item.get("unit", "")).strip(),
                "allowed_roles": item.get("allowed_roles", ""),
                "source": "report_calculation",
                "report_node_id": str(node_id),
                "storage_tag": "__REPORT_CALC__:" + name,
            })
    return result

def _all_management_calculations(company_id):
    return _management_calculations(company_id, None)


def _safe_eval(expression, variables):
    tree = ast.parse(expression, mode="eval")
    allowed_nodes = {
        ast.Expression,
        ast.Constant,
        ast.Name,
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
    value = eval(compile(tree, "<flow-report-expression>", "eval"), {"__builtins__": {}}, variables)
    value = float(value)
    if not math.isfinite(value):
        raise ValueError("Expression result is not finite")
    return value


def _formula_variables(tags, duration_seconds=0.0):
    variables = {}
    for name, value in (tags or {}).items():
        try:
            number = float(value)
        except (TypeError, ValueError):
            continue
        if not math.isfinite(number):
            continue
        key = str(name).strip()
        if not key:
            continue
        variables[key] = number
        alias = "".join(char if (char.isalnum() or char == "_") else "_" for char in key)
        if alias and alias[0].isdigit():
            alias = "_" + alias
        if alias:
            variables.setdefault(alias, number)
    duration = float(duration_seconds or 0.0)
    variables.update({
        "DurationSeconds": duration,
        "WorkTimeSeconds": duration,
        "WorkTimeMinutes": duration / 60.0,
        "WorkTimeHours": duration / 3600.0,
        "ProductionTimeSeconds": duration,
    })
    return variables


def ensure_report_tables():
    conn = get_connection()
    try:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS ReportHistory(
                ReportID INTEGER PRIMARY KEY AUTOINCREMENT,
                CompanyID INTEGER,
                PLC_ID INTEGER,
                Timestamp TEXT NOT NULL,
                TriggerTag TEXT,
                TriggerRegister TEXT,
                TriggerValue REAL,
                TriggerEdge TEXT,
                StartTimestamp TEXT,
                EndTimestamp TEXT,
                DurationSeconds REAL DEFAULT 0,
                StartComplete INTEGER DEFAULT 1,
                TriggerEventID TEXT,
                ReportNodeID TEXT,
                ContractCode TEXT,
                ProductCode TEXT,
                FOREIGN KEY(CompanyID) REFERENCES Companies(CompanyID)
            );
            CREATE TABLE IF NOT EXISTS ReportValues(
                ReportValueID INTEGER PRIMARY KEY AUTOINCREMENT,
                ReportID INTEGER NOT NULL,
                TagName TEXT NOT NULL,
                Value REAL,
                FOREIGN KEY(ReportID) REFERENCES ReportHistory(ReportID) ON DELETE CASCADE
            );
            """
        )
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(ReportHistory)").fetchall()}
        for name, typ in [
            ("TriggerEdge", "TEXT"),
            ("StartTimestamp", "TEXT"),
            ("EndTimestamp", "TEXT"),
            ("DurationSeconds", "REAL"),
            ("StartComplete", "INTEGER"),
            ("TriggerEventID", "TEXT"),
            ("ReportNodeID", "TEXT"),
        ]:
            if name not in columns:
                conn.execute(f"ALTER TABLE ReportHistory ADD COLUMN {name} {typ}")
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_report_history_company_plc_time "
            "ON ReportHistory(CompanyID, PLC_ID, Timestamp)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_report_values_report_tag "
            "ON ReportValues(ReportID, TagName)"
        )
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_report_event_node "
            "ON ReportHistory(TriggerEventID, ReportNodeID) "
            "WHERE TriggerEventID IS NOT NULL AND ReportNodeID IS NOT NULL"
        )
        conn.commit()
    finally:
        conn.close()


def get_report_products(company_id, user_role=None):
    products = []
    seen = set()
    try:
        for node_id, node in _flow_nodes(company_id).items():
            if not isinstance(node, dict) or node.get("name") != "ReportOutput":
                continue
            data = node.get("data", {}) or {}
            config = data.get("config", data) or {}
            configured = config.get("products", []) if isinstance(config, dict) else []
            if not isinstance(configured, list):
                continue
            for item in configured:
                if not isinstance(item, dict):
                    continue
                tag = str(item.get("tag", "")).strip()
                if not tag or not _allowed(item, user_role):
                    continue
                result = {
                    "name": str(item.get("name", tag)).strip() or tag,
                    "tag": tag,
                    "plc_id": _plc_id(item.get("plc_id", item.get("PLC_ID"))),
                    "unit": str(item.get("unit", "")).strip(),
                    "context_role": str(item.get("context_role", item.get("context", ""))).strip().lower(),
                    "allowed_roles": item.get("allowed_roles", ""),
                    "report_node_id": str(node_id),
                }
                key = (tag.lower(), result["plc_id"], result["context_role"])
                if key not in seen:
                    seen.add(key)
                    products.append(result)

        for calculation in _report_calculations(company_id, user_role):
            key = (calculation["name"].lower(), None, "report_calculation")
            if key in seen:
                continue
            seen.add(key)
            products.append(calculation)
        return products
    except Exception as exc:
        print("REPORT CONFIG ERROR:", exc)
        return []


def _context(report_products, tags):
    lookup = {str(key).strip().lower(): value for key, value in (tags or {}).items()}
    contract = None
    product = None
    for item in report_products or []:
        role = str(item.get("context_role", item.get("context", ""))).strip().lower()
        tag = str(item.get("tag", "")).strip().lower()
        if role in _CONTEXT_CONTRACT_ROLES and lookup.get(tag) not in (None, ""):
            contract = str(lookup[tag]).strip()
        if role in _CONTEXT_PRODUCT_ROLES and lookup.get(tag) not in (None, ""):
            product = str(lookup[tag]).strip()
    return contract, product


def save_report_snapshot(*args, **kwargs):
    """Compatibility wrapper; the event-driven writer lives in report_snapshot_runtime."""
    from services.report_snapshot_runtime import save_report_snapshot as runtime_save_report_snapshot
    return runtime_save_report_snapshot(*args, **kwargs)



def get_report_data(company_id, start, end, plc_id=None, user_role=None, contract_code=None, product_code=None):
    products = [item for item in get_report_products(company_id, user_role) if not item.get("context_role")]
    if plc_id is not None:
        products = [item for item in products if item.get("plc_id") in (None, int(plc_id))]

    result = {
        "columns": products,
        "rows": [],
        "totals": [0.0 for _ in products],
        "grand_total": 0.0,
    }
    if company_id is None or not products or not start or not end:
        return result

    ensure_report_tables()
    keys = [str(item.get("storage_tag", item.get("name", item.get("tag", "")))).strip().lower() for item in products]
    placeholders = ",".join("?" for _ in keys)

    conn = get_connection()
    try:
        
        report_where = [
            "h.CompanyID=?",
            "datetime(h.Timestamp)>=datetime(?)",
            "datetime(h.Timestamp)<=datetime(?)",
        ]
        query_args = [
            company_id,
            start.strftime("%Y-%m-%d %H:%M:%S"),
            end.strftime("%Y-%m-%d %H:%M:%S"),
        ]
        if str(contract_code or "").strip():
            report_where.append("LOWER(TRIM(COALESCE(h.ContractCode,''))) LIKE LOWER(?)")
            query_args.append("%" + str(contract_code).strip() + "%")
        if str(product_code or "").strip():
            report_where.append("LOWER(TRIM(COALESCE(h.ProductCode,''))) LIKE LOWER(?)")
            query_args.append("%" + str(product_code).strip() + "%")

        rows = conn.execute(
            f"""
            SELECT h.ReportID, h.Timestamp, h.ContractCode, h.ProductCode,
                   h.PLC_ID, h.TriggerTag, h.TriggerRegister, h.TriggerValue,
                   h.TriggerEdge, h.StartTimestamp, h.EndTimestamp,
                   h.DurationSeconds, h.StartComplete, h.TriggerEventID,
                   v.TagName, v.Value, v.ReportValueID
            FROM ReportHistory h
            LEFT JOIN ReportValues v ON v.ReportID=h.ReportID
            WHERE {" AND ".join(report_where)}
              AND (v.TagName IS NULL OR LOWER(v.TagName) IN ({placeholders}))
            ORDER BY datetime(h.Timestamp), h.ReportID, COALESCE(v.ReportValueID,0)
            """,
            query_args + keys,
        ).fetchall()
    finally:
        conn.close()

    index = {key: position for position, key in enumerate(keys)}
    grouped = {}

    for row in rows:
        item = grouped.setdefault(
            row["ReportID"],
            {
                "timestamp": str(row["Timestamp"]),
                "PLC_ID": row["PLC_ID"],
                "contract_code": row["ContractCode"],
                "product_code": row["ProductCode"],
                "trigger_tag": row["TriggerTag"],
                "trigger_register": row["TriggerRegister"],
                "trigger_value": row["TriggerValue"],
                "trigger_edge": row["TriggerEdge"],
                "start_timestamp": row["StartTimestamp"],
                "end_timestamp": row["EndTimestamp"],
                "duration_seconds": row["DurationSeconds"],
                "start_complete": row["StartComplete"],
                "trigger_event_id": row["TriggerEventID"],
                "values": [None] * len(products),
            },
        )
        tag = str(row["TagName"] or "").strip().lower()
        if tag not in index:
            continue
        try:
            item["values"][index[tag]] = float(row["Value"])
        except (TypeError, ValueError):
            pass

    totals = [0.0] * len(products)
    for item in grouped.values():
        row_total = 0.0
        for position, value in enumerate(item["values"]):
            if value is not None:
                totals[position] += value
                row_total += value
        item["row_total"] = row_total
        result["rows"].append(item)

    result["totals"] = [round(value, 3) for value in totals]
    result["grand_total"] = round(sum(totals), 3)
    return result
