# SCADA_FLOW REPORT SERVICE WRAPPER
# Delegates legacy report behavior while adding ContractCode/ProductCode
# context columns to ReportHistory.

from services import report_service_legacy as _legacy
from database import get_connection


def ensure_report_tables():
    _legacy.ensure_report_tables()
    conn = get_connection()
    try:
        columns = {row["name"] for row in conn.execute('PRAGMA table_info("ReportHistory")').fetchall()}
        if "ContractCode" not in columns:
            conn.execute('ALTER TABLE "ReportHistory" ADD COLUMN ContractCode TEXT')
        if "ProductCode" not in columns:
            conn.execute('ALTER TABLE "ReportHistory" ADD COLUMN ProductCode TEXT')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_report_history_company_context ON ReportHistory (CompanyID, ContractCode, ProductCode, Timestamp)')
        conn.commit()
    finally:
        conn.close()


def get_report_products(company_id):
    products = []
    try:
        nodes = _legacy._flow_nodes(company_id)
        for node in nodes.values():
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
                    "unit": str(item.get("unit", "")).strip(),
                    "context_role": str(item.get("context_role", item.get("context", ""))).strip().lower(),
                })
        unique = []
        seen = set()
        for product in products:
            key = (product["tag"].lower(), product.get("context_role", ""))
            if key in seen:
                continue
            seen.add(key)
            unique.append(product)
        return unique
    except Exception as exc:
        print("REPORT CONFIG ERROR:", exc)
        return []


def _context_from_products(report_products, tags):
    lookup = {str(k).strip().lower(): (k, v) for k, v in (tags or {}).items()}
    contract_code = None
    product_code = None
    for product in report_products or []:
        if not isinstance(product, dict):
            continue
        role = str(product.get("context_role", product.get("context", ""))).strip().lower()
        tag = str(product.get("tag", "")).strip().lower()
        if role not in ("contract", "contract_code", "contractid", "contract_id", "product", "product_code", "productid", "product_id"):
            continue
        item = lookup.get(tag)
        if item is None or item[1] is None:
            continue
        value = str(item[1]).strip()
        if role in ("contract", "contract_code", "contractid", "contract_id"):
            contract_code = value
        else:
            product_code = value
    return contract_code, product_code


def save_report_snapshot(company_id, tags, report_products, timestamp=None):
    if company_id is None or not isinstance(tags, dict) or not report_products:
        return None
    if timestamp is None:
        from datetime import datetime
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    ensure_report_tables()
    contract_code, product_code = _context_from_products(report_products, tags)
    values = []
    seen = set()
    lookup = {str(k).strip().lower(): (k, v) for k, v in tags.items()}

    for product in report_products:
        if not isinstance(product, dict):
            continue
        requested = str(product.get("tag", "")).strip()
        role = str(product.get("context_role", product.get("context", ""))).strip().lower()
        if not requested or role:
            continue
        key = requested.lower()
        if key in seen:
            continue
        seen.add(key)
        item = lookup.get(key)
        if item is None or item[1] is None:
            continue
        try:
            values.append((item[0], float(item[1])))
        except (TypeError, ValueError):
            continue

    if not values:
        return None

    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO ReportHistory (CompanyID, ContractCode, ProductCode, Timestamp)
            VALUES (?, ?, ?, ?)
        """, (company_id, contract_code, product_code, timestamp))
        report_id = cursor.lastrowid
        cursor.executemany(
            "INSERT INTO ReportValues (ReportID, TagName, Value) VALUES (?, ?, ?)",
            [(report_id, tag_name, value) for tag_name, value in values],
        )
        conn.commit()
        return report_id
    except Exception:
        conn.rollback()
        raise
    finally:
        cursor.close()
        conn.close()


def get_report_data(company_id, start, end):
    products = [p for p in get_report_products(company_id) if not p.get("context_role")]
    result = {"columns": products, "rows": [], "totals": [0.0 for _ in products], "grand_total": 0.0}
    if company_id is None or not products or not start or not end:
        return result
    ensure_report_tables()
    tags = [p["tag"] for p in products]
    tag_keys = [str(tag).lower() for tag in tags]
    placeholders = ",".join("?" for _ in tags)
    conn = get_connection()
    try:
        fetched = conn.execute(f"""
            SELECT h.ReportID, h.Timestamp, h.ContractCode, h.ProductCode,
                   v.TagName, v.Value, v.ReportValueID
            FROM ReportHistory h
            INNER JOIN ReportValues v ON v.ReportID=h.ReportID
            WHERE h.CompanyID=?
              AND datetime(h.Timestamp)>=datetime(?)
              AND datetime(h.Timestamp)<=datetime(?)
              AND LOWER(v.TagName) IN ({placeholders})
            ORDER BY datetime(h.Timestamp), h.ReportID, v.ReportValueID
        """, [company_id, start.strftime("%Y-%m-%d %H:%M:%S"), end.strftime("%Y-%m-%d %H:%M:%S"), *tag_keys]).fetchall()
    finally:
        conn.close()

    grouped = {}
    for row in fetched:
        item = grouped.setdefault(row["ReportID"], {
            "timestamp": str(row["Timestamp"]),
            "contract_code": row["ContractCode"],
            "product_code": row["ProductCode"],
            "values": [None for _ in products],
        })
        key = str(row["TagName"]).strip().lower()
        try:
            idx = tag_keys.index(key)
        except ValueError:
            continue
        try:
            item["values"][idx] = float(row["Value"])
        except (TypeError, ValueError):
            pass

    totals = [0.0 for _ in products]
    rows = []
    for item in grouped.values():
        row_total = 0.0
        for idx, value in enumerate(item["values"]):
            if value is not None:
                totals[idx] += value
                row_total += value
        rows.append({
            "timestamp": item["timestamp"],
            "contract_code": item["contract_code"],
            "product_code": item["product_code"],
            "values": item["values"],
            "row_total": row_total,
        })
    result["rows"] = rows
    result["totals"] = [round(v, 3) for v in totals]
    result["grand_total"] = round(sum(totals), 3)
    return result


def __getattr__(name):
    return getattr(_legacy, name)
