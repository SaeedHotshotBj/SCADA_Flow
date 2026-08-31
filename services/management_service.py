# =============================================================
# SCADA_FLOW MANAGEMENT SERVICE
# Generic contract / product / BOM storage and calculation engine.
# Business definitions are supplied by ManagementPanelOutput config.
# =============================================================

import json
import os
import sqlite3
from datetime import datetime

from database import get_connection, get_company_flow


# -------------------------------------------------------------
# DATABASE SCHEMA
# -------------------------------------------------------------

def init_management_database():
    conn = get_connection()
    try:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS Products (
                ProductID INTEGER PRIMARY KEY AUTOINCREMENT,
                CompanyID INTEGER NOT NULL,
                ProductCode TEXT,
                ProductName TEXT NOT NULL,
                Unit TEXT,
                CostBasis TEXT DEFAULT 'KG',
                Active INTEGER NOT NULL DEFAULT 1,
                CreatedAt TEXT DEFAULT (datetime('now','localtime')),
                UNIQUE (CompanyID, ProductName),
                FOREIGN KEY (CompanyID) REFERENCES Companies(CompanyID)
            );

            CREATE TABLE IF NOT EXISTS BOMVersions (
                BOMID INTEGER PRIMARY KEY AUTOINCREMENT,
                CompanyID INTEGER NOT NULL,
                ProductID INTEGER NOT NULL,
                VersionNo INTEGER NOT NULL DEFAULT 1,
                EffectiveFrom TEXT,
                Notes TEXT,
                CreatedAt TEXT DEFAULT (datetime('now','localtime')),
                FOREIGN KEY (CompanyID) REFERENCES Companies(CompanyID),
                FOREIGN KEY (ProductID) REFERENCES Products(ProductID)
            );

            CREATE TABLE IF NOT EXISTS BOMItems (
                BOMItemID INTEGER PRIMARY KEY AUTOINCREMENT,
                BOMID INTEGER NOT NULL,
                MaterialName TEXT NOT NULL,
                Basis TEXT NOT NULL,
                Quantity REAL NOT NULL DEFAULT 0,
                UnitCost REAL NOT NULL DEFAULT 0,
                Unit TEXT,
                FOREIGN KEY (BOMID) REFERENCES BOMVersions(BOMID) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS Contracts (
                ContractID INTEGER PRIMARY KEY AUTOINCREMENT,
                CompanyID INTEGER NOT NULL,
                ContractCode TEXT NOT NULL,
                ContractDate TEXT,
                ContractName TEXT NOT NULL,
                Description TEXT,
                CreatedAt TEXT DEFAULT (datetime('now','localtime')),
                UpdatedAt TEXT DEFAULT (datetime('now','localtime')),
                UNIQUE (CompanyID, ContractCode),
                FOREIGN KEY (CompanyID) REFERENCES Companies(CompanyID)
            );

            CREATE TABLE IF NOT EXISTS ContractItems (
                ContractItemID INTEGER PRIMARY KEY AUTOINCREMENT,
                ContractID INTEGER NOT NULL,
                ProductID INTEGER,
                ProductName TEXT NOT NULL,
                Quantity REAL NOT NULL DEFAULT 0,
                Unit TEXT,
                WeightKg REAL DEFAULT 0,
                LengthMeter REAL DEFAULT 0,
                DeliveryDate TEXT,
                Description TEXT,
                BOMID INTEGER,
                CostBasis TEXT,
                CostPerKg REAL DEFAULT 0,
                CostPerMeter REAL DEFAULT 0,
                EstimatedCost REAL DEFAULT 0,
                FOREIGN KEY (ContractID) REFERENCES Contracts(ContractID) ON DELETE CASCADE,
                FOREIGN KEY (ProductID) REFERENCES Products(ProductID),
                FOREIGN KEY (BOMID) REFERENCES BOMVersions(BOMID)
            );

            CREATE INDEX IF NOT EXISTS idx_products_company
                ON Products(CompanyID, ProductName);
            CREATE INDEX IF NOT EXISTS idx_bom_company_product
                ON BOMVersions(CompanyID, ProductID, VersionNo);
            CREATE INDEX IF NOT EXISTS idx_contracts_company_date
                ON Contracts(CompanyID, ContractDate);
            CREATE INDEX IF NOT EXISTS idx_contract_items_contract
                ON ContractItems(ContractID);
            """
        )
        conn.commit()
    finally:
        conn.close()


# -------------------------------------------------------------
# FLOW CONFIG
# -------------------------------------------------------------

def _nodes(flow):
    return (
        flow.get("drawflow", {})
            .get("Home", {})
            .get("data", {})
    ) if isinstance(flow, dict) else {}


def get_management_config(company_id):
    flow = get_company_flow(company_id)
    if not flow:
        return {}
    try:
        flow = json.loads(flow) if isinstance(flow, str) else flow
    except Exception:
        return {}

    for node in _nodes(flow).values():
        if not isinstance(node, dict):
            continue
        if node.get("name") != "ManagementPanelOutput":
            continue
        data = node.get("data", {}) or {}
        return data.get("config", data) or {}
    return {}


def ensure_management_flow(company_id):
    """Merge the editable management-flow template into a company's Flow
    only when the required management nodes are absent. The template is data,
    not Python business logic, so node definitions remain Flow-editable.
    """
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    template_path = os.path.join(base_dir, "management_flow_template.json")
    if not os.path.exists(template_path):
        return False

    flow_json = get_company_flow(company_id)
    if flow_json:
        try:
            flow = json.loads(flow_json) if isinstance(flow_json, str) else flow_json
        except Exception:
            flow = {"drawflow": {"Home": {"data": {}}}}
    else:
        flow = {"drawflow": {"Home": {"data": {}}}}

    with open(template_path, encoding="utf-8") as f:
        template = json.load(f)

    target_nodes = _nodes(flow)
    template_nodes = _nodes(template)
    existing_names = {str(n.get("name")) for n in target_nodes.values() if isinstance(n, dict)}

    changed = False
    max_id = max([int(k) for k in target_nodes.keys() if str(k).isdigit()] or [0])

    id_map = {}
    for old_id, node in template_nodes.items():
        if not isinstance(node, dict):
            continue
        if node.get("name") in existing_names:
            continue
        max_id += 1
        id_map[str(old_id)] = str(max_id)
        clone = json.loads(json.dumps(node, ensure_ascii=False))
        target_nodes[str(max_id)] = clone
        existing_names.add(node.get("name"))
        changed = True

    if not changed:
        return False

    # Remap only connections belonging to copied template nodes.
    for new_id, old_id in [(v, k) for k, v in id_map.items()]:
        node = target_nodes[new_id]
        for output in (node.get("outputs", {}) or {}).values():
            for connection in output.get("connections", []) or []:
                target = str(connection.get("node"))
                if target in id_map:
                    connection["node"] = id_map[target]
        for input_data in (node.get("inputs", {}) or {}).values():
            for connection in input_data.get("connections", []) or []:
                source = str(connection.get("node"))
                if source in id_map:
                    connection["node"] = id_map[source]

    conn = get_connection()
    try:
        flow_payload = json.dumps(flow, ensure_ascii=False)
        row = conn.execute(
            "SELECT FlowID FROM Flows WHERE CompanyID = ? LIMIT 1",
            (company_id,),
        ).fetchone()
        if row:
            conn.execute(
                "UPDATE Flows SET FlowJson = ?, LastModified = datetime('now','localtime') WHERE CompanyID = ?",
                (flow_payload, company_id),
            )
        else:
            conn.execute(
                "INSERT INTO Flows (CompanyID, FlowJson) VALUES (?, ?)",
                (company_id, flow_payload),
            )
        conn.commit()
        return True
    finally:
        conn.close()


# -------------------------------------------------------------
# DATE / NUMERIC HELPERS
# -------------------------------------------------------------

def _text(value):
    return str(value or "").strip()


def _num(value, default=0.0):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


# -------------------------------------------------------------
# PRODUCT + BOM
# -------------------------------------------------------------

def list_products(company_id):
    init_management_database()
    conn = get_connection()
    try:
        rows = conn.execute(
            """
            SELECT ProductID, ProductCode, ProductName, Unit, CostBasis, Active
            FROM Products
            WHERE CompanyID = ? AND Active = 1
            ORDER BY ProductName
            """,
            (company_id,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def _latest_bom(conn, company_id, product_id):
    row = conn.execute(
        """
        SELECT BOMID, VersionNo, EffectiveFrom, Notes
        FROM BOMVersions
        WHERE CompanyID = ? AND ProductID = ?
        ORDER BY VersionNo DESC, BOMID DESC
        LIMIT 1
        """,
        (company_id, product_id),
    ).fetchone()
    return dict(row) if row else None


def _bom_costs(conn, bom_id):
    rows = conn.execute(
        """
        SELECT Basis, Quantity, UnitCost
        FROM BOMItems
        WHERE BOMID = ?
        """,
        (bom_id,),
    ).fetchall()
    per_kg = 0.0
    per_meter = 0.0
    for row in rows:
        amount = _num(row["Quantity"]) * _num(row["UnitCost"])
        basis = _text(row["Basis"]).upper()
        if basis == "KG":
            per_kg += amount
        elif basis == "METER":
            per_meter += amount
    return per_kg, per_meter


def get_product_catalog(company_id):
    init_management_database()
    conn = get_connection()
    try:
        products = conn.execute(
            "SELECT * FROM Products WHERE CompanyID = ? AND Active = 1 ORDER BY ProductName",
            (company_id,),
        ).fetchall()
        result = []
        for product in products:
            item = dict(product)
            bom = _latest_bom(conn, company_id, product["ProductID"])
            item["BOM"] = bom
            item["CostPerKg"] = 0.0
            item["CostPerMeter"] = 0.0
            if bom:
                item["CostPerKg"], item["CostPerMeter"] = _bom_costs(conn, bom["BOMID"])
            result.append(item)
        return result
    finally:
        conn.close()


def save_product_bom(company_id, payload):
    init_management_database()
    product = payload.get("product", {}) or {}
    product_name = _text(product.get("product_name"))
    if not product_name:
        raise ValueError("Product name is required")

    product_code = _text(product.get("product_code"))
    unit = _text(product.get("unit"))
    cost_basis = _text(product.get("cost_basis") or "KG").upper()
    notes = _text(payload.get("notes"))
    effective_from = _text(payload.get("effective_from"))
    items = payload.get("items", [])
    if not isinstance(items, list):
        items = []

    conn = get_connection()
    try:
        conn.execute("BEGIN IMMEDIATE")
        existing = conn.execute(
            "SELECT ProductID FROM Products WHERE CompanyID = ? AND LOWER(ProductName) = LOWER(?) LIMIT 1",
            (company_id, product_name),
        ).fetchone()

        if existing:
            product_id = existing["ProductID"]
            conn.execute(
                "UPDATE Products SET ProductCode=?, Unit=?, CostBasis=?, Active=1 WHERE ProductID=?",
                (product_code, unit, cost_basis, product_id),
            )
        else:
            cur = conn.execute(
                """
                INSERT INTO Products (CompanyID, ProductCode, ProductName, Unit, CostBasis)
                VALUES (?, ?, ?, ?, ?)
                """,
                (company_id, product_code, product_name, unit, cost_basis),
            )
            product_id = cur.lastrowid

        last = conn.execute(
            "SELECT MAX(VersionNo) AS VersionNo FROM BOMVersions WHERE CompanyID=? AND ProductID=?",
            (company_id, product_id),
        ).fetchone()
        version = int(last["VersionNo"] or 0) + 1
        cur = conn.execute(
            """
            INSERT INTO BOMVersions (CompanyID, ProductID, VersionNo, EffectiveFrom, Notes)
            VALUES (?, ?, ?, ?, ?)
            """,
            (company_id, product_id, version, effective_from, notes),
        )
        bom_id = cur.lastrowid

        for item in items:
            if not isinstance(item, dict):
                continue
            material = _text(item.get("material_name"))
            if not material:
                continue
            basis = _text(item.get("basis") or cost_basis).upper()
            quantity = _num(item.get("quantity"))
            unit_cost = _num(item.get("unit_cost"))
            item_unit = _text(item.get("unit"))
            conn.execute(
                """
                INSERT INTO BOMItems (BOMID, MaterialName, Basis, Quantity, UnitCost, Unit)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (bom_id, material, basis, quantity, unit_cost, item_unit),
            )

        conn.commit()
        per_kg, per_meter = _bom_costs(conn, bom_id)
        return {
            "ProductID": product_id,
            "BOMID": bom_id,
            "VersionNo": version,
            "CostPerKg": per_kg,
            "CostPerMeter": per_meter,
        }
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# -------------------------------------------------------------
# CONTRACTS
# -------------------------------------------------------------

def _find_product(conn, company_id, product_name):
    return conn.execute(
        "SELECT * FROM Products WHERE CompanyID=? AND LOWER(ProductName)=LOWER(?) AND Active=1 LIMIT 1",
        (company_id, product_name),
    ).fetchone()


def _calculate_item_cost(conn, company_id, item):
    product_name = _text(item.get("product_name"))
    quantity = _num(item.get("quantity"))
    unit = _text(item.get("unit"))
    weight = _num(item.get("weight_kg"))
    length = _num(item.get("length_meter"))

    product = _find_product(conn, company_id, product_name)
    if not product:
        return {
            "ProductID": None,
            "BOMID": None,
            "CostBasis": "KG",
            "CostPerKg": 0.0,
            "CostPerMeter": 0.0,
            "EstimatedCost": 0.0,
        }

    bom = _latest_bom(conn, company_id, product["ProductID"])
    if not bom:
        return {
            "ProductID": product["ProductID"],
            "BOMID": None,
            "CostBasis": product["CostBasis"] or "KG",
            "CostPerKg": 0.0,
            "CostPerMeter": 0.0,
            "EstimatedCost": 0.0,
        }

    per_kg, per_meter = _bom_costs(conn, bom["BOMID"])
    basis = _text(product["CostBasis"] or "KG").upper()
    if basis == "METER":
        amount = length if length > 0 else (quantity if unit.upper() == "METER" else 0.0)
        estimated = amount * per_meter
    else:
        amount = weight if weight > 0 else (quantity if unit.upper() == "KG" else 0.0)
        estimated = amount * per_kg

    return {
        "ProductID": product["ProductID"],
        "BOMID": bom["BOMID"],
        "CostBasis": basis,
        "CostPerKg": per_kg,
        "CostPerMeter": per_meter,
        "EstimatedCost": estimated,
    }


def save_contract(company_id, payload):
    init_management_database()
    contract = payload.get("contract", {}) or {}
    items = payload.get("items", [])
    if not isinstance(items, list) or not items:
        raise ValueError("At least one contract product is required")

    code = _text(contract.get("contract_code"))
    date = _text(contract.get("contract_date"))
    name = _text(contract.get("contract_name"))
    description = _text(contract.get("description"))
    if not code or not name:
        raise ValueError("Contract code and contract name are required")

    conn = get_connection()
    try:
        conn.execute("BEGIN IMMEDIATE")
        duplicate = conn.execute(
            "SELECT ContractID FROM Contracts WHERE CompanyID=? AND ContractCode=? LIMIT 1",
            (company_id, code),
        ).fetchone()
        if duplicate:
            raise ValueError("Contract code already exists")

        cur = conn.execute(
            """
            INSERT INTO Contracts (CompanyID, ContractCode, ContractDate, ContractName, Description, UpdatedAt)
            VALUES (?, ?, ?, ?, ?, datetime('now','localtime'))
            """,
            (company_id, code, date, name, description),
        )
        contract_id = cur.lastrowid

        stored_items = []
        for item in items:
            if not isinstance(item, dict):
                continue
            product_name = _text(item.get("product_name"))
            if not product_name:
                continue
            calc = _calculate_item_cost(conn, company_id, item)
            row = (
                contract_id,
                calc["ProductID"],
                product_name,
                _num(item.get("quantity")),
                _text(item.get("unit")),
                _num(item.get("weight_kg")),
                _num(item.get("length_meter")),
                _text(item.get("delivery_date")),
                _text(item.get("description")),
                calc["BOMID"],
                calc["CostBasis"],
                calc["CostPerKg"],
                calc["CostPerMeter"],
                calc["EstimatedCost"],
            )
            conn.execute(
                """
                INSERT INTO ContractItems
                (ContractID, ProductID, ProductName, Quantity, Unit, WeightKg, LengthMeter,
                 DeliveryDate, Description, BOMID, CostBasis, CostPerKg, CostPerMeter, EstimatedCost)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                row,
            )
            stored_items.append({
                "product_name": product_name,
                **calc,
            })

        if not stored_items:
            raise ValueError("At least one valid contract product is required")

        conn.commit()
        return {"ContractID": contract_id, "items": stored_items}
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def query_contracts(company_id, filters=None):
    init_management_database()
    filters = filters or {}
    where = ["c.CompanyID = ?"]
    params = [company_id]

    def add_like(key, column):
        value = _text(filters.get(key))
        if value:
            where.append(f"LOWER({column}) LIKE LOWER(?)")
            params.append(f"%{value}%")

    add_like("contract_code", "c.ContractCode")
    add_like("contract_name", "c.ContractName")
    add_like("description", "c.Description")
    add_like("product_name", "ci.ProductName")

    contract_date_from = _text(filters.get("contract_date_from"))
    contract_date_to = _text(filters.get("contract_date_to"))
    delivery_from = _text(filters.get("delivery_date_from"))
    delivery_to = _text(filters.get("delivery_date_to"))

    if contract_date_from:
        where.append("c.ContractDate >= ?")
        params.append(contract_date_from)
    if contract_date_to:
        where.append("c.ContractDate <= ?")
        params.append(contract_date_to)
    if delivery_from:
        where.append("ci.DeliveryDate >= ?")
        params.append(delivery_from)
    if delivery_to:
        where.append("ci.DeliveryDate <= ?")
        params.append(delivery_to)

    if filters.get("quantity_min") not in (None, ""):
        where.append("ci.Quantity >= ?")
        params.append(_num(filters.get("quantity_min")))
    if filters.get("quantity_max") not in (None, ""):
        where.append("ci.Quantity <= ?")
        params.append(_num(filters.get("quantity_max")))

    sql = f"""
        SELECT c.ContractID, c.ContractCode, c.ContractDate, c.ContractName, c.Description,
               ci.ContractItemID, ci.ProductName, ci.Quantity, ci.Unit,
               ci.WeightKg, ci.LengthMeter, ci.DeliveryDate, ci.Description AS ItemDescription,
               ci.CostBasis, ci.CostPerKg, ci.CostPerMeter, ci.EstimatedCost,
               ci.BOMID
        FROM Contracts c
        JOIN ContractItems ci ON ci.ContractID = c.ContractID
        WHERE {' AND '.join(where)}
        ORDER BY c.ContractDate DESC, c.ContractID DESC, ci.ContractItemID
    """

    conn = get_connection()
    try:
        rows = conn.execute(sql, params).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


def get_management_filter_options(company_id):
    return {
        "products": get_product_catalog(company_id),
    }
