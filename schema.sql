-- =====================================================
-- SCADA_FLOW SQLite Schema
-- =====================================================

PRAGMA foreign_keys = ON;

-- =====================================
-- Companies
-- =====================================

CREATE TABLE IF NOT EXISTS Companies (
    CompanyID   INTEGER PRIMARY KEY AUTOINCREMENT,
    CompanyName TEXT NOT NULL
);

-- =====================================
-- Users
-- =====================================

CREATE TABLE IF NOT EXISTS Users (
    UserID       INTEGER PRIMARY KEY AUTOINCREMENT,
    Username     TEXT UNIQUE NOT NULL,
    PasswordHash TEXT NOT NULL,
    CompanyID    INTEGER,
    Role         TEXT NOT NULL,
    Enabled      INTEGER NOT NULL DEFAULT 1,
    FOREIGN KEY (CompanyID) REFERENCES Companies(CompanyID)
);

-- =====================================
-- PLC Configuration
-- =====================================

CREATE TABLE IF NOT EXISTS PLCs (
    PLC_ID    INTEGER PRIMARY KEY AUTOINCREMENT,
    CompanyID INTEGER NOT NULL,
    PLC_Name  TEXT,
    PLC_IP    TEXT,
    PLC_Port  INTEGER DEFAULT 502,
    Slave_ID  INTEGER DEFAULT 1,
    FOREIGN KEY (CompanyID) REFERENCES Companies(CompanyID)
);

-- =====================================
-- Tags
-- =====================================

CREATE TABLE IF NOT EXISTS Tags (
    TagID           INTEGER PRIMARY KEY AUTOINCREMENT,
    CompanyID       INTEGER NOT NULL,
    TagName         TEXT NOT NULL,
    RegisterAddress INTEGER NOT NULL,
    DataType        TEXT DEFAULT 'INT',
    Description     TEXT,
    FOREIGN KEY (CompanyID) REFERENCES Companies(CompanyID)
);

-- =====================================
-- Historian
-- =====================================

CREATE TABLE IF NOT EXISTS PLC_Data (
    ID          INTEGER PRIMARY KEY AUTOINCREMENT,
    CompanyID   INTEGER,
    TagName     TEXT,
    Value       REAL,
    StorageType TEXT,
    Timestamp   TEXT DEFAULT (datetime('now', 'localtime'))
);

-- =====================================
-- Alarm History
-- =====================================

CREATE TABLE IF NOT EXISTS AlarmHistory (
    AlarmID    INTEGER PRIMARY KEY AUTOINCREMENT,
    CompanyID  INTEGER,
    AlarmText  TEXT,
    AlarmValue REAL,
    Timestamp  TEXT DEFAULT (datetime('now', 'localtime'))
);

-- =====================================
-- Flow Storage
-- =====================================

CREATE TABLE IF NOT EXISTS Flows (
    FlowID       INTEGER PRIMARY KEY AUTOINCREMENT,
    CompanyID    INTEGER,
    FlowJson     TEXT,
    LastModified TEXT DEFAULT (datetime('now', 'localtime'))
);

-- =====================================
-- Tag History (optional extended storage)
-- =====================================

CREATE TABLE IF NOT EXISTS TagHistory (
    ID        INTEGER PRIMARY KEY AUTOINCREMENT,
    CompanyID INTEGER,
    PLC_ID    INTEGER,
    TagName   TEXT,
    Value     REAL,
    Timestamp TEXT
);

-- =====================================
-- Indexes
-- =====================================

CREATE INDEX IF NOT EXISTS idx_plc_data_lookup
    ON PLC_Data (CompanyID, TagName, Timestamp);

CREATE INDEX IF NOT EXISTS idx_plc_data_cleanup
    ON PLC_Data (StorageType, Timestamp);

-- =====================================
-- Demo seed data (run only on empty database)
-- =====================================

INSERT INTO Companies (CompanyName)
SELECT 'Demo Company'
WHERE NOT EXISTS (SELECT 1 FROM Companies);

INSERT INTO Users (Username, PasswordHash, CompanyID, Role, Enabled)
SELECT 'master', '1234', NULL, 'Master', 1
WHERE NOT EXISTS (SELECT 1 FROM Users WHERE Username = 'master');

INSERT INTO PLCs (CompanyID, PLC_Name, PLC_IP, PLC_Port, Slave_ID)
SELECT 1, 'Kinco PLC', '192.168.1.10', 502, 1
WHERE NOT EXISTS (SELECT 1 FROM PLCs);

INSERT INTO Tags (CompanyID, TagName, RegisterAddress, DataType, Description)
SELECT 1, 'Voltage12', 135, 'INT', 'Line Voltage 1-2'
WHERE NOT EXISTS (SELECT 1 FROM Tags WHERE TagName = 'Voltage12');

INSERT INTO Tags (CompanyID, TagName, RegisterAddress, DataType, Description)
SELECT 1, 'Voltage13', 136, 'INT', 'Line Voltage 1-3'
WHERE NOT EXISTS (SELECT 1 FROM Tags WHERE TagName = 'Voltage13');

INSERT INTO Tags (CompanyID, TagName, RegisterAddress, DataType, Description)
SELECT 1, 'Voltage23', 137, 'INT', 'Line Voltage 2-3'
WHERE NOT EXISTS (SELECT 1 FROM Tags WHERE TagName = 'Voltage23');

INSERT INTO Tags (CompanyID, TagName, RegisterAddress, DataType, Description)
SELECT 1, 'Voltage1', 138, 'INT', 'Phase Voltage 1'
WHERE NOT EXISTS (SELECT 1 FROM Tags WHERE TagName = 'Voltage1');

INSERT INTO Tags (CompanyID, TagName, RegisterAddress, DataType, Description)
SELECT 1, 'Voltage2', 139, 'INT', 'Phase Voltage 2'
WHERE NOT EXISTS (SELECT 1 FROM Tags WHERE TagName = 'Voltage2');

INSERT INTO Tags (CompanyID, TagName, RegisterAddress, DataType, Description)
SELECT 1, 'Voltage3', 140, 'INT', 'Phase Voltage 3'
WHERE NOT EXISTS (SELECT 1 FROM Tags WHERE TagName = 'Voltage3');

INSERT INTO Tags (CompanyID, TagName, RegisterAddress, DataType, Description)
SELECT 1, 'Current1', 141, 'INT', 'Current Phase 1'
WHERE NOT EXISTS (SELECT 1 FROM Tags WHERE TagName = 'Current1');

INSERT INTO Tags (CompanyID, TagName, RegisterAddress, DataType, Description)
SELECT 1, 'Current2', 142, 'INT', 'Current Phase 2'
WHERE NOT EXISTS (SELECT 1 FROM Tags WHERE TagName = 'Current2');

INSERT INTO Tags (CompanyID, TagName, RegisterAddress, DataType, Description)
SELECT 1, 'Current3', 143, 'INT', 'Current Phase 3'
WHERE NOT EXISTS (SELECT 1 FROM Tags WHERE TagName = 'Current3');
