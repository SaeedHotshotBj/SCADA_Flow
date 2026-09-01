# =====================================================
# SCADA_FLOW MANAGEMENT CRUD
# Contract + Product/BOM edit/delete operations
# =====================================================

from services.management_service import ensure_management_tables, _parse_jalali, jalali_display
from database import get_connection


def _company_contract(conn, company_id, contract_code):
    return conn.execute(
        """
        SELECT ContractID, CompanyID, ContractCode, ContractDate,
               ContractName, Description
        FROM Contracts
        WHERE CompanyID = ? AND LOWER(ContractCode) = LOWER(?)
        LIMIT 1
        """,
        (int(company_id), str(contract_code or "").strip()),
    ).fetchone()


def get_contract_by_code(company_id, contract_code):
    ensure_management_tables()
    code = str(contract_code or "").strip()
    if not code:
        raise ValueError("Contract code is required")

    conn = get_connection()
    try:
        contract = _company_contract(conn, company_id, code)
        if contract is None:
            raise ValueError("Contract not found")

        rows = conn.execute(
            """
            SELECT cp.ContractProductID, cp.OrderedQuantity,
                   cp.DeliveryDate, cp.Description,
                   p.ProductID, p.ProductCode, p.ProductName, p.Unit
            FROM ContractProducts cp
            INNER JOIN Products p ON p.ProductID = cp.ProductID
            WHERE cp.ContractID = ?
            ORDER BY cp.ContractProductID ASC
            """,
            (int(contract["ContractID"]),),
        ).fetchall()

        return {
            "ContractID": int(contract["ContractID"]),
            "ContractCode": contract["ContractCode"],
            "ContractDate": jalali_display(contract["ContractDate"])[:10],
            "ContractName": contract["ContractName"],
            "Description": contract["Description"] or "",
            "products": [
                {
                    "ContractProductID": int(row["ContractProductID"]),
                    "ProductID": int(row["ProductID"]),
                    "product_code": row["ProductCode"],
                    "product_name": row["ProductName"],
                    "ordered_quantity": row["OrderedQuantity"],
                    "unit": row["Unit"] or "",
                    "delivery_date": jalali_display(row["DeliveryDate"])[:10],
                    "description": row["Description"] or "",
                }
                for row in rows
            ],
        }
    finally:
        conn.close()


def _get_or_create_product(conn, company_id, item):
    code = str(item.get("product_code", "")).strip()
    name = str(item.get("product_name", "")).strip()
    unit = str(item.get("unit", "")).strip()
    if not code or not name:
        raise ValueError("Each contract product needs a code and name")

    product = conn.execute(
        """
        SELECT ProductID
        FROM Products
        WHERE CompanyID = ? AND ProductCode = ?
        LIMIT 1
        """,
        (int(company_id), code),
    ).fetchone()

    if product is None:
        cur = conn.execute(
            """
            INSERT INTO Products (CompanyID, ProductCode, ProductName, Unit)
            VALUES (?, ?, ?, ?)
            """,
            (int(company_id), code, name, unit),
        )
        return int(cur.lastrowid)

    product_id = int(product["ProductID"])
    conn.execute(
        """
        UPDATE Products
           SET ProductName = ?,
               Unit = CASE WHEN ? <> '' THEN ? ELSE Unit END
         WHERE ProductID = ? AND CompanyID = ?
        """,
        (name, unit, unit, product_id, int(company_id)),
    )
    return product_id


def _validate_contract_products(items):
    if not isinstance(items, list) or not items:
        raise ValueError("At least one contract product is required")

    normalized = []
    for item in items:
        if not isinstance(item, dict):
            continue

        code = str(item.get("product_code", "")).strip()
        name = str(item.get("product_name", "")).strip()
        if not code or not name:
            continue

        try:
            quantity = float(item.get("ordered_quantity", 0) or 0)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"Invalid ordered quantity for {code}") from exc
        if quantity < 0:
            raise ValueError(f"Ordered quantity cannot be negative for {code}")

        delivery = _parse_jalali(item.get("delivery_date"))
        if not delivery:
            raise ValueError(f"Invalid Jalali delivery date for {code}")

        normalized.append({
            "product_code": code,
            "product_name": name,
            "ordered_quantity": quantity,
            "unit": str(item.get("unit", "")).strip(),
            "delivery_date": delivery,
            "description": str(item.get("description", "")).strip(),
        })

    if not normalized:
        raise ValueError("At least one valid contract product is required")
    return normalized


def update_contract(company_id, original_code, payload):
    ensure_management_tables()
    original_code = str(original_code or "").strip()
    new_code = str(payload.get("contract_code", "")).strip()
    name = str(payload.get("contract_name", "")).strip()
    contract_date = _parse_jalali(payload.get("contract_date"))
    description = str(payload.get("description", "")).strip()

    if not original_code:
        raise ValueError("Original contract code is required")
    if not new_code or not name or not contract_date:
        raise ValueError("Contract code, name and a valid Jalali contract date are required")

    products = _validate_contract_products(payload.get("products", []))

    conn = get_connection()
    try:
        conn.execute("BEGIN IMMEDIATE")
        contract = _company_contract(conn, company_id, original_code)
        if contract is None:
            raise ValueError("Contract not found")

        duplicate = conn.execute(
            """
            SELECT ContractID
            FROM Contracts
            WHERE CompanyID = ?
              AND LOWER(ContractCode) = LOWER(?)
              AND ContractID <> ?
            LIMIT 1
            """,
            (int(company_id), new_code, int(contract["ContractID"])),
        ).fetchone()
        if duplicate is not None:
            raise ValueError("Another contract already uses this contract code")

        conn.execute(
            """
            UPDATE Contracts
               SET ContractCode = ?,
                   ContractDate = ?,
                   ContractName = ?,
                   Description = ?
             WHERE ContractID = ? AND CompanyID = ?
            """,
            (
                new_code,
                contract_date,
                name,
                description,
                int(contract["ContractID"]),
                int(company_id),
            ),
        )

        conn.execute(
            "DELETE FROM ContractProducts WHERE ContractID = ?",
            (int(contract["ContractID"]),),
        )

        for item in products:
            product_id = _get_or_create_product(conn, company_id, item)
            conn.execute(
                """
                INSERT INTO ContractProducts
                    (ContractID, ProductID, OrderedQuantity, DeliveryDate, Description)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    int(contract["ContractID"]),
                    product_id,
                    item["ordered_quantity"],
                    item["delivery_date"],
                    item["description"],
                ),
            )

        conn.commit()
        return int(contract["ContractID"])
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def delete_contract(company_id, contract_code):
    ensure_management_tables()
    code = str(contract_code or "").strip()
    if not code:
        raise ValueError("Contract code is required")

    conn = get_connection()
    try:
        conn.execute("BEGIN IMMEDIATE")
        contract = _company_contract(conn, company_id, code)
        if contract is None:
            raise ValueError("Contract not found")

        contract_id = int(contract["ContractID"])
        conn.execute("DELETE FROM ContractProducts WHERE ContractID = ?", (contract_id,))
        conn.execute(
            "DELETE FROM Contracts WHERE ContractID = ? AND CompanyID = ?",
            (contract_id, int(company_id)),
        )
        conn.commit()
        return contract_id
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def update_product(company_id, product_id, payload):
    ensure_management_tables()
    try:
        product_id = int(product_id)
    except (TypeError, ValueError) as exc:
        raise ValueError("Invalid ProductID") from exc

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
        conn.execute("BEGIN IMMEDIATE")
        product = conn.execute(
            """
            SELECT ProductID
            FROM Products
            WHERE ProductID = ? AND CompanyID = ?
            LIMIT 1
            """,
            (product_id, int(company_id)),
        ).fetchone()
        if product is None:
            raise ValueError("Product not found")

        duplicate = conn.execute(
            """
            SELECT ProductID
            FROM Products
            WHERE CompanyID = ?
              AND LOWER(ProductCode) = LOWER(?)
              AND ProductID <> ?
            LIMIT 1
            """,
            (int(company_id), code, product_id),
        ).fetchone()
        if duplicate is not None:
            raise ValueError("Another product already uses this product code")

        conn.execute(
            """
            UPDATE Products
               SET ProductCode = ?,
                   ProductName = ?,
                   Unit = ?
             WHERE ProductID = ? AND CompanyID = ?
            """,
            (code, name, unit, product_id, int(company_id)),
        )

        bom = conn.execute(
            "SELECT BOMID FROM ProductBOM WHERE ProductID = ? LIMIT 1",
            (product_id,),
        ).fetchone()
        notes = str(payload.get("notes", "")).strip()

        if bom is None:
            conn.execute(
                """
                INSERT INTO ProductBOM
                    (ProductID, CostPerKg, CostPerMeter, Notes, UpdatedAt)
                VALUES (?, ?, ?, ?, datetime('now','localtime'))
                """,
                (product_id, kg, meter, notes),
            )
        else:
            conn.execute(
                """
                UPDATE ProductBOM
                   SET CostPerKg = ?,
                       CostPerMeter = ?,
                       Notes = ?,
                       UpdatedAt = datetime('now','localtime')
                 WHERE ProductID = ?
                """,
                (kg, meter, notes, product_id),
            )

        conn.commit()
        return product_id
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def delete_product(company_id, product_id):
    ensure_management_tables()
    try:
        product_id = int(product_id)
    except (TypeError, ValueError) as exc:
        raise ValueError("Invalid ProductID") from exc

    conn = get_connection()
    try:
        conn.execute("BEGIN IMMEDIATE")
        product = conn.execute(
            """
            SELECT ProductID, ProductCode
            FROM Products
            WHERE ProductID = ? AND CompanyID = ?
            LIMIT 1
            """,
            (product_id, int(company_id)),
        ).fetchone()
        if product is None:
            raise ValueError("Product not found")

        references = conn.execute(
            "SELECT COUNT(*) AS cnt FROM ContractProducts WHERE ProductID = ?",
            (product_id,),
        ).fetchone()
        if int(references["cnt"] or 0) > 0:
            raise ValueError(
                "This product is used by one or more contracts and cannot be deleted until those contract lines are removed"
            )

        conn.execute("DELETE FROM ProductBOM WHERE ProductID = ?", (product_id,))
        conn.execute(
            "DELETE FROM Products WHERE ProductID = ? AND CompanyID = ?",
            (product_id, int(company_id)),
        )
        conn.commit()
        return product_id
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
