# =====================================================
# SCADA_FLOW DATABASE FUNCTIONS
# SQLite Historian Version
# =====================================================


import os
import sqlite3

from config import DB_CONFIG
from datetime import datetime


def _format_timestamp(value):

    if value is None:
        return None

    if hasattr(value, "strftime"):
        return value.strftime("%Y-%m-%d %H:%M:%S")

    return str(value)


def row_value(row, column, fallback_index=0):

    try:
        return row[column]
    except (TypeError, KeyError, IndexError):
        return row[fallback_index]


# =====================================================
# DATABASE PATH
# =====================================================


def _get_db_path():

    base_dir = os.path.dirname(os.path.abspath(__file__))

    return os.path.join(base_dir, DB_CONFIG["path"])


# =====================================================
# DATABASE CONNECTION
# =====================================================


def get_connection():

    db_path = _get_db_path()

    os.makedirs(os.path.dirname(db_path), exist_ok=True)

    conn = sqlite3.connect(
        db_path,
        detect_types=sqlite3.PARSE_DECLTYPES | sqlite3.PARSE_COLNAMES,
        check_same_thread=False,
    )

    conn.row_factory = sqlite3.Row

    conn.execute("PRAGMA foreign_keys = ON")

    conn.execute("PRAGMA journal_mode = WAL")

    return conn


# =====================================================
# SCHEMA INITIALIZATION
# =====================================================


def init_database():

    conn = get_connection()

    cursor = conn.cursor()

    cursor.executescript(
        """
        CREATE TABLE IF NOT EXISTS Companies (
            CompanyID   INTEGER PRIMARY KEY AUTOINCREMENT,
            CompanyName TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS Users (
            UserID       INTEGER PRIMARY KEY AUTOINCREMENT,
            Username     TEXT UNIQUE NOT NULL,
            PasswordHash TEXT NOT NULL,
            CompanyID    INTEGER,
            Role         TEXT NOT NULL,
            Enabled      INTEGER NOT NULL DEFAULT 1,
            FOREIGN KEY (CompanyID) REFERENCES Companies(CompanyID)
        );

        CREATE TABLE IF NOT EXISTS PLCs (
            PLC_ID    INTEGER PRIMARY KEY AUTOINCREMENT,
            CompanyID INTEGER NOT NULL,
            PLC_Name  TEXT,
            PLC_IP    TEXT,
            PLC_Port  INTEGER DEFAULT 502,
            Slave_ID  INTEGER DEFAULT 1,
            FOREIGN KEY (CompanyID) REFERENCES Companies(CompanyID)
        );

        CREATE TABLE IF NOT EXISTS Tags (
            TagID           INTEGER PRIMARY KEY AUTOINCREMENT,
            CompanyID       INTEGER NOT NULL,
            TagName         TEXT NOT NULL,
            RegisterAddress INTEGER NOT NULL,
            DataType        TEXT DEFAULT 'INT',
            Description     TEXT,
            FOREIGN KEY (CompanyID) REFERENCES Companies(CompanyID)
        );

        CREATE TABLE IF NOT EXISTS PLC_Data (
            ID           INTEGER PRIMARY KEY AUTOINCREMENT,
            CompanyID    INTEGER,
            TagName      TEXT,
            Value        REAL,
            StorageType  TEXT,
            Timestamp    TEXT DEFAULT (datetime('now', 'localtime'))
        );

        CREATE TABLE IF NOT EXISTS AlarmHistory (
            AlarmID   INTEGER PRIMARY KEY AUTOINCREMENT,
            CompanyID INTEGER,
            AlarmText TEXT,
            AlarmValue REAL,
            Timestamp TEXT DEFAULT (datetime('now', 'localtime'))
        );

        CREATE TABLE IF NOT EXISTS Flows (
            FlowID       INTEGER PRIMARY KEY AUTOINCREMENT,
            CompanyID    INTEGER,
            FlowJson     TEXT,
            LastModified TEXT DEFAULT (datetime('now', 'localtime'))
        );

        CREATE TABLE IF NOT EXISTS TagHistory (
            ID        INTEGER PRIMARY KEY AUTOINCREMENT,
            CompanyID INTEGER,
            PLC_ID    INTEGER,
            TagName   TEXT,
            Value     REAL,
            Timestamp TEXT
        );

        CREATE INDEX IF NOT EXISTS idx_plc_data_lookup
            ON PLC_Data (CompanyID, TagName, Timestamp);

        CREATE INDEX IF NOT EXISTS idx_plc_data_cleanup
            ON PLC_Data (StorageType, Timestamp);
        """
    )

    cursor.execute("SELECT COUNT(*) FROM Companies")
    if cursor.fetchone()[0] == 0:
        _seed_demo_data(cursor)

    _ensure_flow_seeded(cursor)

    conn.commit()
    cursor.close()
    conn.close()


def _seed_demo_data(cursor):

    cursor.execute(
        "INSERT INTO Companies (CompanyName) VALUES (?)",
        ("Demo Company",),
    )

    cursor.execute(
        """
        INSERT INTO Users (Username, PasswordHash, CompanyID, Role, Enabled)
        VALUES (?, ?, ?, ?, ?)
        """,
        ("master", "1234", None, "Master", 1),
    )

    cursor.execute(
        """
        INSERT INTO PLCs (CompanyID, PLC_Name, PLC_IP, PLC_Port, Slave_ID)
        VALUES (?, ?, ?, ?, ?)
        """,
        (1, "Kinco PLC", "192.168.1.10", 502, 1),
    )

    tags = [
        (1, "Voltage12", 135, "INT", "Line Voltage 1-2"),
        (1, "Voltage13", 136, "INT", "Line Voltage 1-3"),
        (1, "Voltage23", 137, "INT", "Line Voltage 2-3"),
        (1, "Voltage1", 138, "INT", "Phase Voltage 1"),
        (1, "Voltage2", 139, "INT", "Phase Voltage 2"),
        (1, "Voltage3", 140, "INT", "Phase Voltage 3"),
        (1, "Current1", 141, "INT", "Current Phase 1"),
        (1, "Current2", 142, "INT", "Current Phase 2"),
        (1, "Current3", 143, "INT", "Current Phase 3"),
    ]

    cursor.executemany(
        """
        INSERT INTO Tags (CompanyID, TagName, RegisterAddress, DataType, Description)
        VALUES (?, ?, ?, ?, ?)
        """,
        tags,
    )


def _ensure_flow_seeded(cursor):

    cursor.execute("SELECT COUNT(*) FROM Flows")
    if cursor.fetchone()[0] > 0:
        return

    base_dir = os.path.dirname(os.path.abspath(__file__))
    flow_path = os.path.join(base_dir, "flow.json")

    if not os.path.exists(flow_path):
        return

    with open(flow_path, encoding="utf-8") as f:
        flow_json = f.read()

    cursor.execute(
        """
        INSERT INTO Flows (CompanyID, FlowJson, LastModified)
        VALUES (?, ?, datetime('now', 'localtime'))
        """,
        (1, flow_json),
    )


# =====================================================
# INSERT HISTORIAN VALUE
# =====================================================


def insert_tag_value(company_id, tag, value, storage_type):

    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute(
        """
        INSERT INTO PLC_Data
        (CompanyID, TagName, Value, StorageType, Timestamp)
        VALUES (?, ?, ?, ?, ?)
        """,
        (
            company_id,
            tag,
            float(value),
            storage_type,
            datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        ),
    )

    conn.commit()
    cursor.close()
    conn.close()


# =====================================================
# GET STORED TAG HISTORY
# =====================================================


def get_tag_history(company_id, tag_name):

    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute(
        """
        SELECT Timestamp, Value
        FROM PLC_Data
        WHERE CompanyID = ? AND TagName = ?
        ORDER BY Timestamp ASC
        """,
        (company_id, tag_name),
    )

    rows = cursor.fetchall()
    conn.close()

    return rows


# =====================================================
# TREND DATA
# ONLY USED FOR TIME HISTORIAN TAGS
# =====================================================


def get_trend_data(company_id, tag_name, start=None, end=None):

    conn = get_connection()
    cursor = conn.cursor()

    start = _format_timestamp(start)
    end = _format_timestamp(end)

    if start is not None and end is not None:

        cursor.execute(
            """
            SELECT Timestamp, Value
            FROM PLC_Data
            WHERE CompanyID = ?
              AND LOWER(TagName) = LOWER(?)
              AND Timestamp BETWEEN ? AND ?
            ORDER BY Timestamp ASC
            """,
            (company_id, tag_name, start, end),
        )

    else:

        cursor.execute(
            """
            SELECT Timestamp, Value
            FROM PLC_Data
            WHERE CompanyID = ?
              AND LOWER(TagName) = LOWER(?)
            ORDER BY Timestamp ASC
            """,
            (company_id, tag_name),
        )

    rows = cursor.fetchall()
    conn.close()

    return rows


# =====================================================
# LATEST TAG VALUES (DASHBOARD)
# =====================================================


def get_latest_tag_values(company_id, tag_names):

    if not tag_names:
        return {}

    conn = get_connection()
    cursor = conn.cursor()
    result = {}

    for tag in tag_names:
        if not tag:
            continue

        cursor.execute(
            """
            SELECT Value, Timestamp
            FROM PLC_Data
            WHERE CompanyID = ?
              AND LOWER(TagName) = LOWER(?)
            ORDER BY Timestamp DESC
            LIMIT 1
            """,
            (company_id, tag),
        )

        row = cursor.fetchone()
        if row:
            result[tag] = {
                "value": row_value(row, "Value", 0),
                "timestamp": row_value(row, "Timestamp", 1),
            }

    conn.close()
    return result


# =====================================================
# FLOW
# =====================================================


def get_company_flow(company_id):

    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute(
        """
        SELECT FlowJson
        FROM Flows
        WHERE CompanyID = ?
        """,
        (company_id,),
    )

    row = cursor.fetchone()
    conn.close()

    if row:
        return row[0]

    return None


# =====================================================
# DELETE OLD TREND DATA
# =====================================================


def cleanup_old_trend_data():

    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute(
        """
        DELETE FROM PLC_Data
        WHERE StorageType = 'TIME'
          AND Timestamp < datetime('now', 'localtime', '-3 months')
        """
    )

    deleted = cursor.rowcount

    conn.commit()
    cursor.close()
    conn.close()

    print("OLD TREND DATA DELETED:", deleted)
