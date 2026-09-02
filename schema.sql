-- =====================================================
-- SCADA_FLOW SQLite Schema - PLC-aware identity
-- =====================================================
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS Companies (
    CompanyID INTEGER PRIMARY KEY AUTOINCREMENT,
    CompanyName TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS Users (
    UserID INTEGER PRIMARY KEY AUTOINCREMENT,
    Username TEXT UNIQUE NOT NULL,
    PasswordHash TEXT NOT NULL,
    CompanyID INTEGER,
    Role TEXT NOT NULL,
    Enabled INTEGER NOT NULL DEFAULT 1,
    FOREIGN KEY (CompanyID) REFERENCES Companies(CompanyID)
);

CREATE TABLE IF NOT EXISTS PLCs (
    PLC_ID INTEGER PRIMARY KEY AUTOINCREMENT,
    CompanyID INTEGER NOT NULL,
    PLC_Name TEXT,
    PLC_IP TEXT,
    PLC_Port INTEGER DEFAULT 502,
    Slave_ID INTEGER DEFAULT 1,
    FOREIGN KEY (CompanyID) REFERENCES Companies(CompanyID)
);

CREATE TABLE IF NOT EXISTS Tags (
    TagID INTEGER PRIMARY KEY AUTOINCREMENT,
    CompanyID INTEGER NOT NULL,
    PLC_ID INTEGER NOT NULL,
    TagName TEXT NOT NULL,
    RegisterAddress INTEGER NOT NULL,
    DataType TEXT DEFAULT 'INT',
    Description TEXT,
    FOREIGN KEY (CompanyID) REFERENCES Companies(CompanyID),
    FOREIGN KEY (PLC_ID) REFERENCES PLCs(PLC_ID),
    UNIQUE (CompanyID, PLC_ID, TagName)
);

CREATE TABLE IF NOT EXISTS PLC_Data (
    ID INTEGER PRIMARY KEY AUTOINCREMENT,
    CompanyID INTEGER NOT NULL,
    PLC_ID INTEGER NOT NULL,
    TagName TEXT NOT NULL,
    Value REAL,
    StorageType TEXT,
    Timestamp TEXT DEFAULT (datetime('now', 'localtime')),
    FOREIGN KEY (CompanyID) REFERENCES Companies(CompanyID),
    FOREIGN KEY (PLC_ID) REFERENCES PLCs(PLC_ID)
);

CREATE TABLE IF NOT EXISTS AlarmHistory (
    AlarmID INTEGER PRIMARY KEY AUTOINCREMENT,
    CompanyID INTEGER NOT NULL,
    PLC_ID INTEGER,
    AlarmText TEXT,
    AlarmValue REAL,
    Timestamp TEXT DEFAULT (datetime('now', 'localtime')),
    FOREIGN KEY (CompanyID) REFERENCES Companies(CompanyID),
    FOREIGN KEY (PLC_ID) REFERENCES PLCs(PLC_ID)
);

CREATE TABLE IF NOT EXISTS Flows (
    FlowID INTEGER PRIMARY KEY AUTOINCREMENT,
    CompanyID INTEGER,
    FlowJson TEXT,
    LastModified TEXT DEFAULT (datetime('now', 'localtime'))
);

CREATE TABLE IF NOT EXISTS TagHistory (
    ID INTEGER PRIMARY KEY AUTOINCREMENT,
    CompanyID INTEGER NOT NULL,
    PLC_ID INTEGER NOT NULL,
    TagName TEXT NOT NULL,
    Value REAL,
    Timestamp TEXT,
    FOREIGN KEY (CompanyID) REFERENCES Companies(CompanyID),
    FOREIGN KEY (PLC_ID) REFERENCES PLCs(PLC_ID)
);

CREATE TABLE IF NOT EXISTS ReportHistory (
    ReportID INTEGER PRIMARY KEY AUTOINCREMENT,
    CompanyID INTEGER,
    PLC_ID INTEGER,
    Timestamp TEXT NOT NULL,
    TriggerTag TEXT,
    TriggerRegister TEXT,
    TriggerValue REAL,
    ContractCode TEXT,
    ProductCode TEXT,
    FOREIGN KEY (CompanyID) REFERENCES Companies(CompanyID),
    FOREIGN KEY (PLC_ID) REFERENCES PLCs(PLC_ID)
);

CREATE TABLE IF NOT EXISTS ReportValues (
    ReportValueID INTEGER PRIMARY KEY AUTOINCREMENT,
    ReportID INTEGER NOT NULL,
    TagName TEXT NOT NULL,
    Value REAL,
    FOREIGN KEY (ReportID) REFERENCES ReportHistory(ReportID) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_plc_data_lookup ON PLC_Data (CompanyID, PLC_ID, TagName, Timestamp);
CREATE INDEX IF NOT EXISTS idx_plc_data_cleanup ON PLC_Data (StorageType, Timestamp);
CREATE INDEX IF NOT EXISTS idx_tag_history_lookup ON TagHistory (CompanyID, PLC_ID, TagName, Timestamp);
CREATE INDEX IF NOT EXISTS idx_tags_lookup ON Tags (CompanyID, PLC_ID, TagName);
CREATE INDEX IF NOT EXISTS idx_alarm_history_lookup ON AlarmHistory (CompanyID, PLC_ID, Timestamp);
CREATE INDEX IF NOT EXISTS idx_report_history_lookup ON ReportHistory (CompanyID, PLC_ID, Timestamp);
