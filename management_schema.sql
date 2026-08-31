-- SCADA_FLOW Management Panel schema

CREATE TABLE IF NOT EXISTS Contracts (
    ContractID INTEGER PRIMARY KEY AUTOINCREMENT,
    CompanyID INTEGER NOT NULL,
    ContractCode TEXT NOT NULL,
    ContractDate TEXT NOT NULL,
    ContractName TEXT NOT NULL,
    DeliveryDate TEXT,
    Description TEXT,
    CreatedAt TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    UNIQUE (CompanyID, ContractCode)
);

CREATE TABLE IF NOT EXISTS Products (
    ProductID INTEGER PRIMARY KEY AUTOINCREMENT,
    CompanyID INTEGER NOT NULL,
    ProductCode TEXT NOT NULL,
    ProductName TEXT NOT NULL,
    Unit TEXT DEFAULT '',
    CreatedAt TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    UNIQUE (CompanyID, ProductCode)
);

CREATE TABLE IF NOT EXISTS ContractProducts (
    ContractProductID INTEGER PRIMARY KEY AUTOINCREMENT,
    ContractID INTEGER NOT NULL,
    ProductID INTEGER NOT NULL,
    OrderedQuantity REAL NOT NULL DEFAULT 0,
    DeliveryDate TEXT,
    Description TEXT,
    UNIQUE (ContractID, ProductID)
);

CREATE TABLE IF NOT EXISTS ProductBOM (
    BOMID INTEGER PRIMARY KEY AUTOINCREMENT,
    ProductID INTEGER NOT NULL UNIQUE,
    CostPerKg REAL DEFAULT 0,
    CostPerMeter REAL DEFAULT 0,
    Notes TEXT,
    UpdatedAt TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);

-- Existing ReportHistory must contain these two context columns.
-- Runtime migration is performed by services/report_service.py.
