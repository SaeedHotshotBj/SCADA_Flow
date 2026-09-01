# =====================================================
# SCADA_FLOW MANAGEMENT SERVICE
# Contracts + Products + BOM + Flow-defined calculations
# =====================================================

import json
from datetime import datetime

import jdatetime

from database import get_connection, get_company_flow
from flow_engine.nodes.expression_node import ExpressionNode
from services.report_service import get_report_products, ensure_report_tables


TABLES_READY = False


def _flow_nodes(company_id):
    flow = get_company_flow(company_id)
    if not flow:
        return {}
    if isinstance(flow, str):
        flow = json.loads(flow)
    return flow.get("drawflow", {}).get("Home", {}).get("data", {}) or {}


def _management_config(company_id):
    nodes = _flow_nodes(company_id)
    configs = []
    for node in nodes.values():
        if not isinstance(node, dict) or node.get("name") != "ManagementPanel":
            continue
        data = node.get("data", {}) or {}
        config = data.get("config", data) or {}
        configs.append(config)
    return configs[-1] if configs else {}


def _connected_roles(company_id):
    nodes = _flow_nodes(company_id)
    targets = {
        str(key)
        for key, node in nodes.items()
        if isinstance(node, dict) and node.get("name") == "ManagementPanel"
    }
    roles = []
    found = False
    for node in nodes.values():
        if not isinstance(node, dict) or node.get("name") != "RolesEngaged":
            continue
        connected = False
        for port in (node.get("outputs", {}) or {}).values():
            if not isinstance(port, dict):
                continue
            for connection in port.get("connections", []) or []:
                if isinstance(connection, dict) and str(connection.get("node", "")) in targets:
                    connected = True
                    break
            if connected:
                break
        if not connected:
            continue
        found = True
        selected = (node.get("data", {}) or {}).get("roles", [])
        if isinstance(selected, str):
            selected = [selected]
        if not isinstance(selected, list):
            continue
        for item in selected:
            role = item.get("role", "") if isinstance(item, dict) else item
            role = str(role).strip()
            if role and role.lower() not in {x.lower() for x in roles}:
                roles.append(role)
    return found, roles


def management_flow_allowed(company_id, user_role, is_master=False):
    if is_master:
        return True
    found, roles = _connected_roles(company_id)
    if not found:
        return False
    current = str(user_role or "").strip().lower()
    return any(current == str(role).strip().lower() for role in roles)


def ensure_management_tables():
    global TABLES_READY
    ensure_report_tables()
    conn = get_connection()
    try:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS Contracts (
            ContractID INTEGER PRIMARY KEY AUTOINCREMENT,
            CompanyID INTEGER NOT NULL,
            ContractCode TEXT NOT NULL,
            ContractDate TEXT NOT NULL,
            ContractName TEXT NOT NULL,
            DeliveryDate TEXT,
            Description TEXT,
            CreatedAt TEXT NOT NULL DEFAULT (datetime('now','localtime')),
            FOREIGN KEY (CompanyID) REFERENCES Companies(CompanyID),
            UNIQUE (CompanyID, ContractCode)
        );

        CREATE TABLE IF NOT EXISTS Products (
            ProductID INTEGER PRIMARY KEY AUTOINCREMENT,
            CompanyID INTEGER NOT NULL,
            ProductCode TEXT NOT NULL,
            ProductName TEXT NOT NULL,
            Unit TEXT DEFAULT '',
            CreatedAt TEXT NOT NULL DEFAULT (datetime('now','localtime')),
            FOREIGN KEY (CompanyID) REFERENCES Companies(CompanyID),
            UNIQUE (CompanyID, ProductCode)
        );

        CREATE TABLE IF NOT EXISTS ContractProducts (
            ContractProductID INTEGER PRIMARY KEY AUTOINCREMENT,
            ContractID INTEGER NOT NULL,
            ProductID INTEGER NOT NULL,
            OrderedQuantity REAL NOT NULL DEFAULT 0,
            DeliveryDate TEXT,
            Description TEXT,
            FOREIGN KEY (ContractID) REFERENCES Contracts(ContractID) ON DELETE CASCADE,
            FOREIGN KEY (ProductID) REFERENCES Products(ProductID),
            UNIQUE (ContractID, ProductID)
        );

        CREATE TABLE IF NOT EXISTS ProductBOM (
            BOMID INTEGER PRIMARY KEY AUTOINCREMENT,
            ProductID INTEGER NOT NULL UNIQUE,
            CostPerKg REAL DEFAULT 0,
            CostPerMeter REAL DEFAULT 0,
            Notes TEXT,
            UpdatedAt TEXT NOT NULL DEFAULT (datetime('now','localtime')),
            FOREIGN KEY (ProductID) REFERENCES Products(ProductID) ON DELETE CASCADE
        );

        CREATE INDEX IF NOT EXISTS idx_contracts_company_date
            ON Contracts (CompanyID, ContractDate);
        CREATE INDEX IF NOT EXISTS idx_contract_products_contract
            ON ContractProducts (ContractID, ProductID);
        CREATE INDEX IF NOT EXISTS idx_products_company_code
            ON Products (CompanyID, ProductCode);
        CREATE INDEX IF NOT EXISTS idx_product_bom_product
            ON ProductBOM (ProductID);

        """)
        conn.commit()
        TABLES_READY = True
    finally:
        conn.close()


def _normalize_digits(value):
    return str(value or "").translate(str.maketrans(
        "۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩",
        "01234567890123456789",
    ))


def _parse_jalali(value, end_of_day=False):
    if not value:
        return None
    text = _normalize_digits(value).strip().replace("-", "/").replace("T", " ")
    for fmt in ("%Y/%m/%d %H:%M:%S", "%Y/%m/%d %H:%M", "%Y/%m/%d"):
        try:
            dt = jdatetime.datetime.strptime(text, fmt).togregorian()
            if end_of_day and len(text) <= 10:
                dt = dt.replace(hour=23, minute=59, second=59)
            return dt.strftime("%Y-%m-%d %H:%M:%S")
        except Exception:
            pass
    return None


def jalali_display(value):
    if not value:
        return ""
    try:
        dt = datetime.strptime(str(value)[:19], "%Y-%m-%d %H:%M:%S")
        return jdatetime.datetime.fromgregorian(datetime=dt).strftime("%Y/%m/%d %H:%M")
    except Exception:
        return str(value)


def save_product(company_id, payload):
    ensure_management_tables()
    code = str(payload.get("product_code", "")).strip()
    name = str(payload.get("product_name", "")).strip()
    unit = str(payload.get("unit", "")).strip()
    if not code or not name:
        raise ValueError("Product code and product name are required")
    try:
        kg = float(payload.get("cost_per_kg", 0) or 0)
        meter = float(payload.get("cost_per_meter", 0) or 0)
    except (TypeError, ValueError) as exc:
        raise ValueError("BOM prices must be numeric") from exc
    if kg < 0 or meter < 0:
        raise ValueError("BOM prices cannot be negative")

    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT ProductID FROM Products WHERE CompanyID=? AND ProductCode=? ORDER BY ProductID LIMIT 1",
            (company_id, code),
        ).fetchone()
        if row is None:
            cursor = conn.execute("""
                INSERT INTO Products (CompanyID, ProductCode, ProductName, Unit)
                VALUES (?, ?, ?, ?)
            """, (company_id, code, name, unit))
            product_id = int(cursor.lastrowid)
        else:
            product_id = int(row["ProductID"])
            conn.execute("""
                UPDATE Products
                   SET ProductName=?,
                       Unit=CASE WHEN ?<>'' THEN ? ELSE Unit END
                 WHERE ProductID=?
            """, (name, unit, unit, product_id))

        bom = conn.execute(
            "SELECT BOMID FROM ProductBOM WHERE ProductID=? ORDER BY BOMID LIMIT 1",
            (product_id,),
        ).fetchone()
        notes = str(payload.get("notes", "")).strip()
        if bom is None:
            conn.execute("""
                INSERT INTO ProductBOM
                    (ProductID, CostPerKg, CostPerMeter, Notes, UpdatedAt)
                VALUES (?, ?, ?, ?, datetime('now','localtime'))
            """, (product_id, kg, meter, notes))
        else:
            conn.execute("""
                UPDATE ProductBOM
                   SET CostPerKg=?,
                       CostPerMeter=?,
                       Notes=?,
                       UpdatedAt=datetime('now','localtime')
                 WHERE BOMID=?
            """, (kg, meter, notes, int(bom["BOMID"])))
        conn.commit()
        return product_id
    finally:
        conn.close()


def save_contract(company_id, payload):
    ensure_management_tables()
    code = str(payload.get("contract_code", "")).strip()
    name = str(payload.get("contract_name", "")).strip()
    contract_date = _parse_jalali(payload.get("contract_date"))
    description = str(payload.get("description", "")).strip()
    products = payload.get("products", [])
    if not code or not name or not contract_date:
        raise ValueError("Contract code, name and a valid Jalali contract date are required")
    if not isinstance(products, list) or not products:
        raise ValueError("At least one contract product is required")

    conn = get_connection()
    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("""
            INSERT INTO Contracts
            (CompanyID, ContractCode, ContractDate, ContractName, DeliveryDate, Description)
            VALUES (?, ?, ?, ?, NULL, ?)
        """, (company_id, code, contract_date, name, description))
        contract_id = conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]

        inserted = 0
        for item in products:
            if not isinstance(item, dict):
                continue
            pcode = str(item.get("product_code", "")).strip()
            pname = str(item.get("product_name", "")).strip()
            if not pcode or not pname:
                continue
            try:
                ordered = float(item.get("ordered_quantity", 0) or 0)
            except (TypeError, ValueError) as exc:
                raise ValueError(f"Invalid ordered quantity for {pcode}") from exc
            if ordered < 0:
                raise ValueError(f"Ordered quantity cannot be negative for {pcode}")
            delivery = _parse_jalali(item.get("delivery_date"))
            if not delivery:
                raise ValueError(f"Invalid Jalali delivery date for {pcode}")
            unit = str(item.get("unit", "")).strip()
            product = conn.execute(
                "SELECT ProductID FROM Products WHERE CompanyID=? AND ProductCode=? ORDER BY ProductID LIMIT 1",
                (company_id, pcode),
            ).fetchone()
            if product is None:
                cursor = conn.execute("""
                    INSERT INTO Products (CompanyID, ProductCode, ProductName, Unit)
                    VALUES (?, ?, ?, ?)
                """, (company_id, pcode, pname, unit))
                product = conn.execute(
                    "SELECT ProductID FROM Products WHERE ProductID=?",
                    (cursor.lastrowid,),
                ).fetchone()
            else:
                conn.execute("""
                    UPDATE Products
                       SET ProductName=?,
                           Unit=CASE WHEN ?<>'' THEN ? ELSE Unit END
                     WHERE ProductID=?
                """, (pname, unit, unit, int(product["ProductID"])))
            conn.execute("""
                INSERT INTO ContractProducts
                (ContractID, ProductID, OrderedQuantity, DeliveryDate, Description)
                VALUES (?, ?, ?, ?, ?)
            """, (
                contract_id,
                int(product["ProductID"]),
                ordered,
                delivery,
                str(item.get("description", "")).strip(),
            ))
            inserted += 1

        if inserted == 0:
            raise ValueError("At least one valid product is required")
        conn.commit()
        return int(contract_id)
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def get_products(company_id):
    ensure_management_tables()
    conn = get_connection()
    try:
        rows = conn.execute("""
            SELECT p.ProductID, p.ProductCode, p.ProductName, p.Unit,
                   COALESCE(b.CostPerKg,0) AS CostPerKg,
                   COALESCE(b.CostPerMeter,0) AS CostPerMeter,
                   COALESCE(b.Notes,'') AS BOMNotes
            FROM Products p
            LEFT JOIN ProductBOM b ON b.ProductID=p.ProductID
            WHERE p.CompanyID=?
            ORDER BY p.ProductCode
        """, (company_id,)).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


def _report_columns(company_id):
    return [
        item for item in get_report_products(company_id)
        if not str(item.get("context_role", "")).strip()
    ]


def _management_calculations(company_id):
    config = _management_config(company_id)
    rows = config.get("calculations", []) if isinstance(config, dict) else []
    return [
        item for item in rows
        if isinstance(item, dict)
        and str(item.get("name", "")).strip()
        and str(item.get("expression", "")).strip()
    ]


def get_config(company_id):
    ensure_management_tables()
    config = _management_config(company_id)
    return {
        "configured": bool(config),
        "date_picker": config.get("DatePicker", "JalaliPicker"),
        "report_columns": _report_columns(company_id),
        "calculations": _management_calculations(company_id),
        "products": get_products(company_id),
    }


def _add_text_filter(where, args, column, value):
    value = str(value or "").strip()
    if value:
        where.append(f"LOWER(CAST({column} AS TEXT)) LIKE LOWER(?)")
        args.append(f"%{value}%")


def _query_base(company_id, filters):
    where = ["c.CompanyID = ?"]
    args = [int(company_id)]
    _add_text_filter(where, args, "c.ContractCode", filters.get("contract_code"))
    _add_text_filter(where, args, "c.ContractName", filters.get("contract_name"))
    _add_text_filter(where, args, "c.Description", filters.get("description"))
    _add_text_filter(where, args, "p.ProductCode", filters.get("product_code"))
    _add_text_filter(where, args, "p.ProductName", filters.get("product_name"))

    min_text = str(filters.get("min_ordered", "")).strip()
    max_text = str(filters.get("max_ordered", "")).strip()
    try:
        min_qty = float(min_text) if min_text else None
        max_qty = float(max_text) if max_text else None
    except ValueError as exc:
        raise ValueError("Ordered quantity filters must be numeric") from exc
    if min_qty is not None:
        where.append("cp.OrderedQuantity >= ?")
        args.append(min_qty)
    if max_qty is not None:
        where.append("cp.OrderedQuantity <= ?")
        args.append(max_qty)

    date_from = _parse_jalali(filters.get("contract_date_from"))
    date_to = _parse_jalali(filters.get("contract_date_to"), True)
    if date_from:
        where.append("datetime(c.ContractDate) >= datetime(?)")
        args.append(date_from)
    if date_to:
        where.append("datetime(c.ContractDate) <= datetime(?)")
        args.append(date_to)

    delivery_from = _parse_jalali(filters.get("delivery_date_from"))
    delivery_to = _parse_jalali(filters.get("delivery_date_to"), True)
    if delivery_from:
        where.append("datetime(cp.DeliveryDate) >= datetime(?)")
        args.append(delivery_from)
    if delivery_to:
        where.append("datetime(cp.DeliveryDate) <= datetime(?)")
        args.append(delivery_to)

    return where, args


def _report_values_for_pairs(conn, company_id, base_rows):
    pairs = []
    seen = set()
    for row in base_rows:
        key = (
            str(row["ContractCode"]).strip().lower(),
            str(row["ProductCode"]).strip().lower(),
        )
        if key in seen:
            continue
        seen.add(key)
        pairs.append((row["ContractCode"], row["ProductCode"]))
    if not pairs:
        return {}

    args = [int(company_id)]
    conditions = []
    for contract_code, product_code in pairs:
        conditions.append(
            "(LOWER(COALESCE(h.ContractCode,''))=LOWER(?) "
            "AND LOWER(COALESCE(h.ProductCode,''))=LOWER(?))"
        )
        args.extend([contract_code, product_code])

    rows = conn.execute(f"""
        SELECT h.ReportID, h.ContractCode, h.ProductCode, h.Timestamp,
               v.TagName, v.Value, v.ReportValueID
        FROM ReportHistory h
        INNER JOIN ReportValues v ON v.ReportID=h.ReportID
        WHERE h.CompanyID=? AND ({' OR '.join(conditions)})
        ORDER BY h.ReportID ASC, v.ReportValueID ASC
    """, args).fetchall()

    grouped = {}
    for row in rows:
        key = (
            str(row["ContractCode"] or "").strip().lower(),
            str(row["ProductCode"] or "").strip().lower(),
        )
        group = grouped.setdefault(key, {"tags": {}})
        tag = str(row["TagName"] or "").strip()
        if not tag:
            continue
        group["tags"].setdefault(tag, []).append(row["Value"])
    return grouped


def get_management_data(company_id, filters=None):
    filters = filters or {}
    ensure_management_tables()
    where, args = _query_base(company_id, filters)
    conn = get_connection()
    try:
        base_rows = conn.execute(f"""
            SELECT c.ContractID, c.ContractCode, c.ContractDate, c.ContractName,
                   c.Description AS ContractDescription,
                   cp.ContractProductID, cp.OrderedQuantity, cp.DeliveryDate,
                   p.ProductID, p.ProductCode, p.ProductName, p.Unit,
                   COALESCE(b.CostPerKg,0) AS CostPerKg,
                   COALESCE(b.CostPerMeter,0) AS CostPerMeter,
                   COALESCE(b.Notes,'') AS BOMNotes
            FROM Contracts c
            INNER JOIN ContractProducts cp ON cp.ContractID=c.ContractID
            INNER JOIN Products p ON p.ProductID=cp.ProductID
            LEFT JOIN ProductBOM b ON b.ProductID=p.ProductID
            WHERE {' AND '.join(where)}
            ORDER BY datetime(c.ContractDate) DESC, c.ContractID DESC, p.ProductCode ASC
        """, args).fetchall()

        if not base_rows:
            return {"columns": [], "rows": [], "count": 0}

        groups = _report_values_for_pairs(conn, company_id, base_rows)
        report_columns = _report_columns(company_id)
        calculations = _management_calculations(company_id)
        expression_node = ExpressionNode({"expressions": calculations}) if calculations else None

        columns = [
            {"key": "ContractCode", "label": "کد قرارداد", "unit": ""},
            {"key": "ContractDate", "label": "تاریخ عقد قرارداد", "unit": ""},
            {"key": "ContractName", "label": "نام قرارداد", "unit": ""},
            {"key": "ProductCode", "label": "کد محصول", "unit": ""},
            {"key": "ProductName", "label": "نوع محصول", "unit": ""},
            {"key": "OrderedQuantity", "label": "مقدار سفارش", "unit": ""},
            {"key": "DeliveryDate", "label": "تاریخ تحویل", "unit": ""},
            {"key": "Description", "label": "توضیحات", "unit": ""},
            {"key": "CostPerKg", "label": "BOM / kg", "unit": ""},
            {"key": "CostPerMeter", "label": "BOM / m", "unit": ""},
        ]
        columns.extend(
            {"key": p["tag"], "label": p["name"], "unit": p.get("unit", "")}
            for p in report_columns
        )
        columns.extend(
            {
                "key": str(item["name"]),
                "label": str(item.get("label", item["name"])),
                "unit": str(item.get("unit", "")),
            }
            for item in calculations
        )

        output_rows = []
        for row in base_rows:
            pair_key = (
                str(row["ContractCode"]).strip().lower(),
                str(row["ProductCode"]).strip().lower(),
            )
            source = groups.get(pair_key, {"tags": {}})
            tags = {}

            for tag, values in source["tags"].items():
                numeric_values = []
                for value in values:
                    try:
                        numeric_values.append(float(value))
                    except (TypeError, ValueError):
                        pass
                tags[tag] = numeric_values
                tags[f"{tag}_values"] = numeric_values
                tags[f"{tag}_last"] = numeric_values[-1] if numeric_values else None

            tags["OrderedQuantity"] = row["OrderedQuantity"]
            tags["CostPerKg"] = row["CostPerKg"]
            tags["CostPerMeter"] = row["CostPerMeter"]
            tags["ContractCode"] = row["ContractCode"]
            tags["ProductCode"] = row["ProductCode"]

            calculated = {}
            if expression_node:
                result = expression_node.execute({"Tags": tags}) or {}
                calculated = dict(result.get("Tags", {}))

            display = {
                "ContractCode": row["ContractCode"],
                "ContractDate": jalali_display(row["ContractDate"]),
                "ContractName": row["ContractName"],
                "ProductCode": row["ProductCode"],
                "ProductName": row["ProductName"],
                "OrderedQuantity": row["OrderedQuantity"],
                "DeliveryDate": jalali_display(row["DeliveryDate"]),
                "Description": row["ContractDescription"] or "",
                "CostPerKg": row["CostPerKg"],
                "CostPerMeter": row["CostPerMeter"],
            }
            for product in report_columns:
                display[product["tag"]] = tags.get(product["tag"], [])
            for item in calculations:
                display[str(item["name"])] = calculated.get(str(item["name"]))
            output_rows.append(display)

        return {"columns": columns, "rows": output_rows, "count": len(output_rows)}
    finally:
        conn.close()
