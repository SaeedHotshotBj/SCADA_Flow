import json
from datetime import datetime
from database import get_connection, get_company_flow

_CONTEXT_CONTRACT_ROLES = {"contract", "contract_code", "contractid", "contract_id"}
_CONTEXT_PRODUCT_ROLES = {"product", "product_code", "productid", "product_id"}


def _flow_nodes(company_id):
    flow = get_company_flow(company_id)
    if not flow:
        return {}
    if isinstance(flow, str):
        flow = json.loads(flow)
    return flow.get("drawflow", {}).get("Home", {}).get("data", {})


def _plc_id(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def ensure_report_tables():
    conn = get_connection()
    try:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS ReportHistory(
            ReportID INTEGER PRIMARY KEY AUTOINCREMENT,
            CompanyID INTEGER,
            PLC_ID INTEGER,
            Timestamp TEXT NOT NULL,
            TriggerTag TEXT,
            TriggerRegister TEXT,
            TriggerValue REAL,
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
        """)
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(ReportHistory)").fetchall()}
        for name, typ in [("PLC_ID", "INTEGER"), ("TriggerTag", "TEXT"), ("TriggerRegister", "TEXT"), ("TriggerValue", "REAL"), ("ContractCode", "TEXT"), ("ProductCode", "TEXT")]:
            if name not in cols:
                conn.execute(f"ALTER TABLE ReportHistory ADD COLUMN {name} {typ}")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_report_history_company_plc_time ON ReportHistory(CompanyID,PLC_ID,Timestamp)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_report_values_report_tag ON ReportValues(ReportID,TagName)")
        conn.commit()
    finally:
        conn.close()


def get_report_products(company_id):
    products = []
    try:
        for node in _flow_nodes(company_id).values():
            if not isinstance(node, dict) or node.get("name") != "ReportOutput":
                continue
            data = node.get("data", {}) or {}
            config = data.get("config", data) or {}
            configured = config.get("products", [])
            if not isinstance(configured, list):
                continue
            for item in configured:
                if not isinstance(item, dict):
                    continue
                tag = str(item.get("tag", "")).strip()
                if not tag:
                    continue
                products.append({
                    "name": str(item.get("name", tag)).strip() or tag,
                    "tag": tag,
                    "plc_id": _plc_id(item.get("plc_id", item.get("PLC_ID"))),
                    "unit": str(item.get("unit", "")).strip(),
                    "context_role": str(item.get("context_role", item.get("context", ""))).strip().lower(),
                })
        unique, seen = [], set()
        for product in products:
            key = (product["tag"].lower(), product.get("plc_id"), product.get("context_role", ""))
            if key not in seen:
                seen.add(key)
                unique.append(product)
        return unique
    except Exception as exc:
        print("REPORT CONFIG ERROR:", exc)
        return []


def _context(report_products, tags):
    lookup = {str(k).strip().lower(): v for k, v in (tags or {}).items()}
    contract = product = None
    for item in report_products or []:
        role = str(item.get("context_role", item.get("context", ""))).strip().lower()
        tag = str(item.get("tag", "")).strip().lower()
        if role in _CONTEXT_CONTRACT_ROLES and lookup.get(tag) not in (None, ""):
            contract = str(lookup[tag]).strip()
        if role in _CONTEXT_PRODUCT_ROLES and lookup.get(tag) not in (None, ""):
            product = str(lookup[tag]).strip()
    return contract, product


def save_report_snapshot(company_id, tags, report_products, timestamp=None, trigger_tag=None, trigger_register=None, trigger_value=None, plc_id=None):
    if company_id is None or not isinstance(tags, dict) or not report_products:
        return None
    ensure_report_tables()
    timestamp = timestamp or datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    plc_id = _plc_id(plc_id)
    contract, product = _context(report_products, tags)
    lookup = {str(k).strip().lower(): (k, v) for k, v in tags.items()}
    values = []
    seen = set()
    for item in report_products:
        if not isinstance(item, dict):
            continue
        tag = str(item.get("tag", "")).strip()
        role = str(item.get("context_role", item.get("context", ""))).strip().lower()
        item_plc = _plc_id(item.get("plc_id", item.get("PLC_ID", plc_id)))
        if not tag or role or tag.lower() in seen:
            continue
        if item_plc is not None and plc_id is not None and item_plc != plc_id:
            continue
        seen.add(tag.lower())
        found = lookup.get(tag.lower())
        if found is None or found[1] is None:
            continue
        try:
            values.append((found[0], float(found[1])))
        except (TypeError, ValueError):
            pass
    if not values:
        return None

    conn = get_connection()
    try:
        cur = conn.execute("""
            INSERT INTO ReportHistory
            (CompanyID, PLC_ID, Timestamp, TriggerTag, TriggerRegister, TriggerValue, ContractCode, ProductCode)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (company_id, plc_id, timestamp, trigger_tag, trigger_register, trigger_value, contract, product))
        report_id = cur.lastrowid
        conn.executemany("INSERT INTO ReportValues(ReportID,TagName,Value) VALUES(?,?,?)", [(report_id, n, v) for n, v in values])
        conn.commit()
        return report_id
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def get_report_data(company_id, start, end, plc_id=None):
    products = [p for p in get_report_products(company_id) if not p.get("context_role")]
    if plc_id is not None:
        products = [p for p in products if p.get("plc_id") in (None, int(plc_id))]
    result = {"columns": products, "rows": [], "totals": [0.0 for _ in products], "grand_total": 0.0}
    if company_id is None or not products or not start or not end:
        return result
    ensure_report_tables()
    tags = [p["tag"] for p in products]
    keys = [t.lower() for t in tags]
    placeholders = ",".join("?" for _ in tags)
    conn = get_connection()
    try:
        sql = f"""
            SELECT h.ReportID, h.Timestamp, h.ContractCode, h.ProductCode,
                   h.PLC_ID, v.TagName, v.Value, v.ReportValueID
            FROM ReportHistory h
            JOIN ReportValues v ON v.ReportID = h.ReportID
            WHERE h.CompanyID = ?
              AND datetime(h.Timestamp) >= datetime(?)
              AND datetime(h.Timestamp) <= datetime(?)
              AND LOWER(v.TagName) IN ({placeholders})
        """
        params = [company_id, start.strftime("%Y-%m-%d %H:%M:%S"), end.strftime("%Y-%m-%d %H:%M:%S")]
        if plc_id is not None:
            sql += " AND h.PLC_ID = ?"
            params.append(int(plc_id))
        sql += " ORDER BY datetime(h.Timestamp), h.ReportID, v.ReportValueID"
        rows = conn.execute(sql, params + keys).fetchall()
    finally:
        conn.close()

    grouped = {}
    for row in rows:
        item = grouped.setdefault(row["ReportID"], {
            "timestamp": str(row["Timestamp"]),
            "PLC_ID": row["PLC_ID"],
            "values": [None] * len(products),
            "contract_code": row["ContractCode"],
            "product_code": row["ProductCode"],
        })
        try:
            index = keys.index(str(row["TagName"]).strip().lower())
        except ValueError:
            continue
        try:
            item["values"][index] = float(row["Value"])
        except (TypeError, ValueError):
            pass

    totals = [0.0] * len(products)
    for item in grouped.values():
        row_total = 0.0
        for index, value in enumerate(item["values"]):
            if value is not None:
                totals[index] += value
                row_total += value
        item["row_total"] = row_total
        result["rows"].append(item)
    result["totals"] = [round(v, 3) for v in totals]
    result["grand_total"] = round(sum(totals), 3)
    return result
