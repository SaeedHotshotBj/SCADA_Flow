# Legacy-safe migration for Management Panel tables.
# Adds missing columns to existing SQLite tables without deleting data.

from database import get_connection


_EXPECTED_COLUMNS = {
    "Contracts": {
        "CompanyID": "INTEGER NOT NULL DEFAULT 0",
        "ContractCode": "TEXT NOT NULL DEFAULT ''",
        "ContractDate": "TEXT NOT NULL DEFAULT ''",
        "ContractName": "TEXT NOT NULL DEFAULT ''",
        "DeliveryDate": "TEXT",
        "Description": "TEXT",
        "CreatedAt": "TEXT",
    },
    "Products": {
        "CompanyID": "INTEGER NOT NULL DEFAULT 0",
        "ProductCode": "TEXT NOT NULL DEFAULT ''",
        "ProductName": "TEXT NOT NULL DEFAULT ''",
        "Unit": "TEXT DEFAULT ''",
        "CreatedAt": "TEXT",
    },
    "ContractProducts": {
        "ContractID": "INTEGER NOT NULL DEFAULT 0",
        "ProductID": "INTEGER NOT NULL DEFAULT 0",
        "OrderedQuantity": "REAL NOT NULL DEFAULT 0",
        "DeliveryDate": "TEXT",
        "Description": "TEXT",
    },
    "ProductBOM": {
        "ProductID": "INTEGER NOT NULL DEFAULT 0",
        "CostPerKg": "REAL DEFAULT 0",
        "CostPerMeter": "REAL DEFAULT 0",
        "Notes": "TEXT",
        "UpdatedAt": "TEXT",
    },
}


def ensure_management_schema():
    """Ensure legacy management tables contain every column used by the app.

    This intentionally does not rebuild tables or add UNIQUE indexes, because
    existing installations may contain historical duplicate rows. The save
    code performs explicit SELECT/INSERT/UPDATE operations and therefore does
    not require ON CONFLICT constraints.
    """
    conn = get_connection()
    try:
        for table, expected in _EXPECTED_COLUMNS.items():
            exists = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                (table,),
            ).fetchone()
            if not exists:
                continue

            existing = {
                row["name"]
                for row in conn.execute(f'PRAGMA table_info("{table}")').fetchall()
            }
            for column, definition in expected.items():
                if column in existing:
                    continue
                conn.execute(
                    f'ALTER TABLE "{table}" ADD COLUMN "{column}" {definition}'
                )

        conn.commit()
    finally:
        conn.close()
