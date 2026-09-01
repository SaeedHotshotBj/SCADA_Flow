# Run this once from the repository root to repair the existing management DB logic.
# It deliberately uses SELECT/UPDATE/INSERT instead of SQLite UPSERT targets,
# so legacy databases without matching UNIQUE constraints are supported.
from pathlib import Path
p = Path('services/management_service.py')
s = p.read_text(encoding='utf-8')
# Remove fragile migration-only UNIQUE indexes.
for block in (
'''        CREATE UNIQUE INDEX IF NOT EXISTS ux_products_company_code
            ON Products (CompanyID, ProductCode);

''',
'''        CREATE UNIQUE INDEX IF NOT EXISTS ux_product_bom_product
            ON ProductBOM (ProductID);

''',
'''        CREATE UNIQUE INDEX IF NOT EXISTS ux_contracts_company_code
            ON Contracts (CompanyID, ContractCode);

''',
'''        CREATE UNIQUE INDEX IF NOT EXISTS ux_contract_products_pair
            ON ContractProducts (ContractID, ProductID);
'''):
    s = s.replace(block, '')
old = '''        conn.execute("""
            INSERT INTO Products (CompanyID, ProductCode, ProductName, Unit)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(CompanyID, ProductCode) DO UPDATE SET
                ProductName=excluded.ProductName,
                Unit=CASE WHEN excluded.Unit<>'' THEN excluded.Unit ELSE Products.Unit END
        """, (company_id, code, name, unit))
        row = conn.execute(
            "SELECT ProductID FROM Products WHERE CompanyID=? AND ProductCode=?",
            (company_id, code),
        ).fetchone()
        product_id = int(row["ProductID"])
        conn.execute("""
            INSERT INTO ProductBOM (ProductID, CostPerKg, CostPerMeter, Notes, UpdatedAt)
            VALUES (?, ?, ?, ?, datetime('now','localtime'))
            ON CONFLICT(ProductID) DO UPDATE SET
                CostPerKg=excluded.CostPerKg,
                CostPerMeter=excluded.CostPerMeter,
                Notes=excluded.Notes,
                UpdatedAt=excluded.UpdatedAt
        """, (product_id, kg, meter, str(payload.get("notes", "")).strip()))
'''
new = '''        row = conn.execute(
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
'''
if old not in s:
    raise SystemExit('save_product block not found')
s = s.replace(old, new, 1)
old2 = '''            conn.execute("""
                INSERT INTO Products (CompanyID, ProductCode, ProductName, Unit)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(CompanyID, ProductCode) DO UPDATE SET
                    ProductName=excluded.ProductName,
                    Unit=CASE WHEN excluded.Unit<>'' THEN excluded.Unit ELSE Products.Unit END
            """, (company_id, pcode, pname, unit))
            product = conn.execute(
                "SELECT ProductID FROM Products WHERE CompanyID=? AND ProductCode=?",
                (company_id, pcode),
            ).fetchone()
'''
new2 = '''            product = conn.execute(
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
'''
if old2 not in s:
    raise SystemExit('save_contract product block not found')
s = s.replace(old2, new2, 1)
p.write_text(s, encoding='utf-8')
