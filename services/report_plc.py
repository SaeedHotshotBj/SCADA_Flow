import json
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


def _reverse_connections(nodes):
    reverse = {str(node_id): set() for node_id in nodes if isinstance(nodes.get(node_id), dict)}
    for source_id, node in nodes.items():
        if not isinstance(node, dict):
            continue
        for output in (node.get("outputs", {}) or {}).values():
            if not isinstance(output, dict):
                continue
            for connection in output.get("connections", []) or []:
                if not isinstance(connection, dict):
                    continue
                target = str(connection.get("node", ""))
                if target in reverse:
                    reverse[target].add(str(source_id))
    return reverse


def _ancestor_nodes(reverse, node_id):
    result = set()
    stack = [str(node_id)]
    while stack:
        current = stack.pop()
        for parent in reverse.get(current, set()):
            if parent in result:
                continue
            result.add(parent)
            stack.append(parent)
    return result


def _management_calculations(company_id, user_role=None, node_ids=None):
    """Read connected ManagementPanel metadata for report column discovery only."""
    result = []
    seen = set()
    allowed_nodes = None if node_ids is None else {str(item) for item in node_ids}
    for node_id, node in _flow_nodes(company_id).items():
        if allowed_nodes is not None and str(node_id) not in allowed_nodes:
            continue
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
        nodes = _flow_nodes(company_id)
        reverse = _reverse_connections(nodes)
        connected_management_nodes = set()

        for node_id, node in nodes.items():
            if not isinstance(node, dict) or node.get("name") != "ReportOutput":
                continue
            data = node.get("data", {}) or {}
            config = data.get("config", data) or {}
            configured = config.get("products", []) if isinstance(config, dict) else []
            if isinstance(configured, list):
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

            for ancestor_id in _ancestor_nodes(reverse, node_id):
                if nodes.get(ancestor_id, {}).get("name") == "ManagementPanel":
                    connected_management_nodes.add(ancestor_id)

        for calculation in _management_calculations(
            company_id,
            user_role,
            node_ids=connected_management_nodes,
        ):
            key = (calculation["name"].lower(), None, "")
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

    ensure_report_tables()
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
    contract, product = _context(all_products, tags)
    lookup = {str(key).strip().lower(): (key, value) for key, value in tags.items()}
    values = []
    seen_value_names = set()

    for item in all_products:
        tag = str(item.get("tag", "")).strip()
        role = str(item.get("context_role", item.get("context", ""))).strip().lower()
        if not tag or role:
            continue
        item_plc = _plc_id(item.get("plc_id", item.get("PLC_ID", plc_id)))
        if item_plc is not None and plc_id is not None and item_plc != plc_id:
            continue
        found = lookup.get(tag.lower())
        if found is None or found[1] is None:
            continue
        name = str(item.get("name", tag)).strip() or tag
        if name.lower() in seen_value_names:
            continue
        try:
            values.append((name, float(found[1])))
            seen_value_names.add(name.lower())
        except (TypeError, ValueError):
            pass

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
        return report_id
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


def get_report_data(company_id, start, end, plc_id=None, user_role=None):
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
    keys = [str(item.get("name", item.get("tag", ""))).strip().lower() for item in products]
    placeholders = ",".join("?" for _ in keys)

    conn = get_connection()
    try:
        rows = conn.execute(
            f"""
            SELECT h.ReportID, h.Timestamp, h.ContractCode, h.ProductCode,
                   h.PLC_ID, h.TriggerTag, h.TriggerRegister, h.TriggerValue,
                   h.TriggerEdge, h.StartTimestamp, h.EndTimestamp,
                   h.DurationSeconds, h.StartComplete, h.TriggerEventID,
                   v.TagName, v.Value, v.ReportValueID
            FROM ReportHistory h
            LEFT JOIN ReportValues v ON v.ReportID=h.ReportID
            WHERE h.CompanyID=?
              AND datetime(h.Timestamp)>=datetime(?)
              AND datetime(h.Timestamp)<=datetime(?)
              AND (v.TagName IS NULL OR LOWER(v.TagName) IN ({placeholders}))
            ORDER BY datetime(h.Timestamp), h.ReportID, COALESCE(v.ReportValueID,0)
            """,
            [
                company_id,
                start.strftime("%Y-%m-%d %H:%M:%S"),
                end.strftime("%Y-%m-%d %H:%M:%S"),
            ] + keys,
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
