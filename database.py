# =====================================================
# SCADA_FLOW DATABASE FUNCTIONS
# Multi-Company SQLite Historian
# =====================================================

import os
import json
import sqlite3
from datetime import datetime

from werkzeug.security import generate_password_hash

from config import DB_CONFIG


# =====================================================
# HELPERS
# =====================================================

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

    base_dir = os.path.dirname(
        os.path.abspath(__file__)
    )

    return os.path.join(
        base_dir,
        DB_CONFIG["path"]
    )


# =====================================================
# DATABASE CONNECTION
# =====================================================

def get_connection():

    db_path = _get_db_path()

    db_directory = os.path.dirname(db_path)

    if db_directory:
        os.makedirs(
            db_directory,
            exist_ok=True
        )

    conn = sqlite3.connect(
        db_path,
        detect_types=(
            sqlite3.PARSE_DECLTYPES |
            sqlite3.PARSE_COLNAMES
        ),
        check_same_thread=False,
    )

    conn.row_factory = sqlite3.Row

    conn.execute(
        "PRAGMA foreign_keys = ON"
    )

    conn.execute(
        "PRAGMA journal_mode = WAL"
    )

    return conn


# =====================================================
# DATABASE INITIALIZATION
# =====================================================

def init_database():

    conn = get_connection()
    cursor = conn.cursor()

    cursor.executescript(
        """
        CREATE TABLE IF NOT EXISTS Companies (
            CompanyID INTEGER PRIMARY KEY AUTOINCREMENT,
            CompanyName TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS Users (
            UserID INTEGER PRIMARY KEY AUTOINCREMENT,
            Username TEXT NOT NULL,
            PasswordHash TEXT NOT NULL,
            CompanyID INTEGER,
            Role TEXT NOT NULL,
            Enabled INTEGER NOT NULL DEFAULT 1,

            FOREIGN KEY (CompanyID)
                REFERENCES Companies(CompanyID),

            UNIQUE (
                Username,
                CompanyID
            )
        );

        CREATE TABLE IF NOT EXISTS PLCs (
            PLC_ID INTEGER PRIMARY KEY AUTOINCREMENT,
            CompanyID INTEGER NOT NULL,
            PLC_Name TEXT,
            PLC_IP TEXT,
            PLC_Port INTEGER DEFAULT 502,
            Slave_ID INTEGER DEFAULT 1,

            FOREIGN KEY (CompanyID)
                REFERENCES Companies(CompanyID)
        );

        CREATE TABLE IF NOT EXISTS Tags (
            TagID INTEGER PRIMARY KEY AUTOINCREMENT,
            CompanyID INTEGER NOT NULL,
            TagName TEXT NOT NULL,
            RegisterAddress INTEGER NOT NULL,
            DataType TEXT DEFAULT 'INT',
            Description TEXT,

            FOREIGN KEY (CompanyID)
                REFERENCES Companies(CompanyID)
        );

        CREATE TABLE IF NOT EXISTS PLC_Data (
            ID INTEGER PRIMARY KEY AUTOINCREMENT,
            CompanyID INTEGER NOT NULL,
            TagName TEXT NOT NULL,
            Value REAL,
            StorageType TEXT,
            Timestamp TEXT DEFAULT (
                datetime('now', 'localtime')
            ),

            FOREIGN KEY (CompanyID)
                REFERENCES Companies(CompanyID)
        );

        CREATE TABLE IF NOT EXISTS AlarmHistory (
            AlarmID INTEGER PRIMARY KEY AUTOINCREMENT,
            CompanyID INTEGER NOT NULL,
            AlarmText TEXT,
            AlarmValue REAL,
            Timestamp TEXT DEFAULT (
                datetime('now', 'localtime')
            ),

            FOREIGN KEY (CompanyID)
                REFERENCES Companies(CompanyID)
        );

        CREATE TABLE IF NOT EXISTS Flows (
            FlowID INTEGER PRIMARY KEY AUTOINCREMENT,
            CompanyID INTEGER NOT NULL,
            FlowJson TEXT NOT NULL,
            LastModified TEXT DEFAULT (
                datetime('now', 'localtime')
            ),

            FOREIGN KEY (CompanyID)
                REFERENCES Companies(CompanyID)
        );

        CREATE TABLE IF NOT EXISTS TagHistory (
            ID INTEGER PRIMARY KEY AUTOINCREMENT,
            CompanyID INTEGER NOT NULL,
            PLC_ID INTEGER,
            TagName TEXT NOT NULL,
            Value REAL,
            Timestamp TEXT,

            FOREIGN KEY (CompanyID)
                REFERENCES Companies(CompanyID),

            FOREIGN KEY (PLC_ID)
                REFERENCES PLCs(PLC_ID)
        );

        CREATE INDEX IF NOT EXISTS
            idx_plc_data_company_tag_time
        ON PLC_Data (
            CompanyID,
            TagName,
            Timestamp
        );

        CREATE INDEX IF NOT EXISTS
            idx_plc_data_cleanup
        ON PLC_Data (
            StorageType,
            Timestamp
        );

        CREATE INDEX IF NOT EXISTS
            idx_tag_history_company_tag_time
        ON TagHistory (
            CompanyID,
            TagName,
            Timestamp
        );

        CREATE INDEX IF NOT EXISTS
            idx_plcs_company
        ON PLCs (
            CompanyID
        );

        CREATE INDEX IF NOT EXISTS
            idx_tags_company
        ON Tags (
            CompanyID
        );

        CREATE INDEX IF NOT EXISTS
            idx_flows_company
        ON Flows (
            CompanyID
        );

        CREATE INDEX IF NOT EXISTS
            idx_alarms_company
        ON AlarmHistory (
            CompanyID,
            Timestamp
        );
        """
    )

    conn.commit()

    _migrate_users_table(cursor)

    cursor.execute(
        "SELECT COUNT(*) FROM Companies"
    )

    company_count = cursor.fetchone()[0]

    if company_count == 0:

        _seed_demo_data(cursor)

        _ensure_flow_seeded(
            cursor,
            1
        )

    else:

        _ensure_demo_company_user(
            cursor
        )

    conn.commit()

    cursor.close()
    conn.close()


# =====================================================
# USERS TABLE MIGRATION
# =====================================================

def _migrate_users_table(cursor):

    cursor.execute(
        """
        SELECT sql
        FROM sqlite_master
        WHERE type = 'table'
          AND name = 'Users'
        """
    )

    row = cursor.fetchone()

    if not row:
        return

    table_sql = row[0] or ""

    if (
        "Username TEXT UNIQUE" not in table_sql
        and
        "Username TEXT UNIQUE NOT NULL" not in table_sql
    ):
        return

    cursor.execute(
        """
        ALTER TABLE Users
        RENAME TO Users_old
        """
    )

    cursor.execute(
        """
        CREATE TABLE Users (
            UserID INTEGER PRIMARY KEY AUTOINCREMENT,
            Username TEXT NOT NULL,
            PasswordHash TEXT NOT NULL,
            CompanyID INTEGER,
            Role TEXT NOT NULL,
            Enabled INTEGER NOT NULL DEFAULT 1,

            FOREIGN KEY (CompanyID)
                REFERENCES Companies(CompanyID),

            UNIQUE (
                Username,
                CompanyID
            )
        )
        """
    )

    cursor.execute(
        """
        INSERT INTO Users
        (
            UserID,
            Username,
            PasswordHash,
            CompanyID,
            Role,
            Enabled
        )
        SELECT
            UserID,
            Username,
            PasswordHash,
            CompanyID,
            Role,
            Enabled
        FROM Users_old
        """
    )

    cursor.execute(
        """
        DROP TABLE Users_old
        """
    )


# =====================================================
# DEMO DATA
# =====================================================

def _seed_demo_data(cursor):

    cursor.execute(
        """
        INSERT INTO Companies
        (
            CompanyName
        )
        VALUES
        (?)
        """,
        (
            "Demo Company",
        )
    )

    company_id = cursor.lastrowid

    cursor.execute(
        """
        INSERT INTO Users
        (
            Username,
            PasswordHash,
            CompanyID,
            Role,
            Enabled
        )
        VALUES
        (?, ?, ?, ?, ?)
        """,
        (
            "master",
            generate_password_hash("1234"),
            None,
            "Master",
            1
        )
    )

    cursor.execute(
        """
        INSERT INTO Users
        (
            Username,
            PasswordHash,
            CompanyID,
            Role,
            Enabled
        )
        VALUES
        (?, ?, ?, ?, ?)
        """,
        (
            "admin",
            generate_password_hash("1234"),
            company_id,
            "Admin",
            1
        )
    )

    cursor.execute(
        """
        INSERT INTO PLCs
        (
            CompanyID,
            PLC_Name,
            PLC_IP,
            PLC_Port,
            Slave_ID
        )
        VALUES
        (?, ?, ?, ?, ?)
        """,
        (
            company_id,
            "Kinco PLC",
            "192.168.1.10",
            502,
            1
        )
    )

    tags = [
        (
            company_id,
            "Voltage12",
            135,
            "INT",
            "Line Voltage 1-2"
        ),
        (
            company_id,
            "Voltage13",
            136,
            "INT",
            "Line Voltage 1-3"
        ),
        (
            company_id,
            "Voltage23",
            137,
            "INT",
            "Line Voltage 2-3"
        ),
        (
            company_id,
            "Voltage1",
            138,
            "INT",
            "Phase Voltage 1"
        ),
        (
            company_id,
            "Voltage2",
            139,
            "INT",
            "Phase Voltage 2"
        ),
        (
            company_id,
            "Voltage3",
            140,
            "INT",
            "Phase Voltage 3"
        ),
        (
            company_id,
            "Current1",
            141,
            "INT",
            "Current Phase 1"
        ),
        (
            company_id,
            "Current2",
            142,
            "INT",
            "Current Phase 2"
        ),
        (
            company_id,
            "Current3",
            143,
            "INT",
            "Current Phase 3"
        ),
    ]

    cursor.executemany(
        """
        INSERT INTO Tags
        (
            CompanyID,
            TagName,
            RegisterAddress,
            DataType,
            Description
        )
        VALUES
        (?, ?, ?, ?, ?)
        """,
        tags
    )


# =====================================================
# DEMO COMPANY USER
# =====================================================

def _ensure_demo_company_user(cursor):

    cursor.execute(
        """
        SELECT
            CompanyID
        FROM Companies
        WHERE CompanyName = ?
        LIMIT 1
        """,
        (
            "Demo Company",
        )
    )

    company = cursor.fetchone()

    if not company:
        return

    company_id = company["CompanyID"]

    cursor.execute(
        """
        SELECT
            UserID
        FROM Users
        WHERE Username = ?
          AND CompanyID = ?
        LIMIT 1
        """,
        (
            "admin",
            company_id
        )
    )

    if cursor.fetchone():
        return

    cursor.execute(
        """
        INSERT INTO Users
        (
            Username,
            PasswordHash,
            CompanyID,
            Role,
            Enabled
        )
        VALUES
        (?, ?, ?, ?, ?)
        """,
        (
            "admin",
            generate_password_hash("1234"),
            company_id,
            "Admin",
            1
        )
    )


# =====================================================
# FLOW SEED
# =====================================================

def _ensure_flow_seeded(
    cursor,
    company_id
):

    cursor.execute(
        """
        SELECT COUNT(*)
        FROM Flows
        WHERE CompanyID = ?
        """,
        (
            company_id,
        )
    )

    if cursor.fetchone()[0] > 0:
        return

    base_dir = os.path.dirname(
        os.path.abspath(__file__)
    )

    flow_path = os.path.join(
        base_dir,
        "flow.json"
    )

    if not os.path.exists(flow_path):
        return

    with open(
        flow_path,
        encoding="utf-8"
    ) as f:

        flow_json = f.read()

    cursor.execute(
        """
        INSERT INTO Flows
        (
            CompanyID,
            FlowJson,
            LastModified
        )
        VALUES
        (
            ?,
            ?,
            datetime(
                'now',
                'localtime'
            )
        )
        """,
        (
            company_id,
            flow_json
        )
    )


# =====================================================
# COMPANIES
# =====================================================

def get_companies():

    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute(
        """
        SELECT
            CompanyID,
            CompanyName
        FROM Companies
        ORDER BY CompanyID
        """
    )

    rows = cursor.fetchall()

    cursor.close()
    conn.close()

    return rows


def get_company(
    company_id
):

    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute(
        """
        SELECT
            CompanyID,
            CompanyName
        FROM Companies
        WHERE CompanyID = ?
        """,
        (
            company_id,
        )
    )

    row = cursor.fetchone()

    cursor.close()
    conn.close()

    return row


def create_company(
    company_name
):

    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute(
        """
        INSERT INTO Companies
        (
            CompanyName
        )
        VALUES
        (?)
        """,
        (
            company_name,
        )
    )

    company_id = cursor.lastrowid

    conn.commit()

    cursor.close()
    conn.close()

    return company_id


# =====================================================
# COMPANY PLCS
# =====================================================

def get_company_plcs(
    company_id
):

    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute(
        """
        SELECT
            PLC_ID,
            CompanyID,
            PLC_Name,
            PLC_IP,
            PLC_Port,
            Slave_ID
        FROM PLCs
        WHERE CompanyID = ?
        ORDER BY PLC_ID
        """,
        (
            company_id,
        )
    )

    rows = cursor.fetchall()

    cursor.close()
    conn.close()

    return rows


# =====================================================
# COMPANY TAGS
# =====================================================

def get_company_tags(
    company_id
):

    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute(
        """
        SELECT
            TagID,
            CompanyID,
            TagName,
            RegisterAddress,
            DataType,
            Description
        FROM Tags
        WHERE CompanyID = ?
        ORDER BY TagID
        """,
        (
            company_id,
        )
    )

    rows = cursor.fetchall()

    cursor.close()
    conn.close()

    return rows


# =====================================================
# COMPANY USERS / ROLES
# =====================================================

def get_company_users(
    company_id
):

    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute(
        """
        SELECT
            UserID,
            Username,
            PasswordHash,
            CompanyID,
            Role,
            Enabled
        FROM Users
        WHERE CompanyID = ?
        ORDER BY Role, Username
        """,
        (
            company_id,
        )
    )

    rows = cursor.fetchall()

    cursor.close()
    conn.close()

    return rows


def get_company_roles(
    company_id
):

    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute(
        """
        SELECT DISTINCT
            Role
        FROM Users
        WHERE CompanyID = ?
          AND Enabled = 1
          AND Role IS NOT NULL
          AND TRIM(Role) <> ''
        ORDER BY Role
        """,
        (
            company_id,
        )
    )

    rows = cursor.fetchall()

    cursor.close()
    conn.close()

    return [
        row["Role"]
        for row in rows
    ]


def get_user_by_username(
    company_id,
    username
):

    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute(
        """
        SELECT
            UserID,
            Username,
            PasswordHash,
            CompanyID,
            Role,
            Enabled
        FROM Users
        WHERE CompanyID = ?
          AND Username = ?
        LIMIT 1
        """,
        (
            company_id,
            username
        )
    )

    row = cursor.fetchone()

    cursor.close()
    conn.close()

    return row


def create_company_user(
    company_id,
    username,
    password_hash,
    role,
    enabled=1
):

    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute(
        """
        INSERT INTO Users
        (
            Username,
            PasswordHash,
            CompanyID,
            Role,
            Enabled
        )
        VALUES
        (?, ?, ?, ?, ?)
        """,
        (
            username,
            password_hash,
            company_id,
            role,
            enabled
        )
    )

    user_id = cursor.lastrowid

    conn.commit()

    cursor.close()
    conn.close()

    return user_id


# =====================================================
# INSERT HISTORIAN VALUE
# =====================================================

def insert_tag_value(
    company_id,
    tag,
    value,
    storage_type,
    timestamp=None
):

    if timestamp is None:

        timestamp = datetime.now().strftime(
            "%Y-%m-%d %H:%M:%S"
        )

    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute(
        """
        INSERT INTO PLC_Data
        (
            CompanyID,
            TagName,
            Value,
            StorageType,
            Timestamp
        )
        VALUES
        (?, ?, ?, ?, ?)
        """,
        (
            company_id,
            tag,
            float(value),
            storage_type,
            timestamp
        )
    )

    conn.commit()

    cursor.close()
    conn.close()


# =====================================================
# GET STORED TAG HISTORY
# =====================================================

def get_tag_history(
    company_id,
    tag_name
):

    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute(
        """
        SELECT
            Timestamp,
            Value
        FROM PLC_Data
        WHERE CompanyID = ?
          AND LOWER(TagName) = LOWER(?)
        ORDER BY Timestamp ASC
        """,
        (
            company_id,
            tag_name
        )
    )

    rows = cursor.fetchall()

    cursor.close()
    conn.close()

    return rows


# =====================================================
# TREND DATA
# =====================================================

def get_trend_data(
    company_id,
    tag_name,
    start=None,
    end=None
):

    conn = get_connection()
    cursor = conn.cursor()

    start = _format_timestamp(start)
    end = _format_timestamp(end)

    if start is not None and end is not None:

        cursor.execute(
            """
            SELECT
                Timestamp,
                Value
            FROM PLC_Data
            WHERE CompanyID = ?
              AND LOWER(TagName) = LOWER(?)
              AND Timestamp BETWEEN ? AND ?
            ORDER BY Timestamp ASC
            """,
            (
                company_id,
                tag_name,
                start,
                end
            )
        )

    else:

        cursor.execute(
            """
            SELECT
                Timestamp,
                Value
            FROM PLC_Data
            WHERE CompanyID = ?
              AND LOWER(TagName) = LOWER(?)
            ORDER BY Timestamp ASC
            """,
            (
                company_id,
                tag_name
            )
        )

    rows = cursor.fetchall()

    cursor.close()
    conn.close()

    return rows


# =====================================================
# LATEST TAG VALUES
# =====================================================

def get_latest_tag_values(
    company_id,
    tag_names
):

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
            SELECT
                Value,
                Timestamp
            FROM PLC_Data
            WHERE CompanyID = ?
              AND LOWER(TagName) = LOWER(?)
            ORDER BY Timestamp DESC
            LIMIT 1
            """,
            (
                company_id,
                tag
            )
        )

        row = cursor.fetchone()

        if row:

            result[tag] = {
                "value":
                    row_value(
                        row,
                        "Value",
                        0
                    ),

                "timestamp":
                    row_value(
                        row,
                        "Timestamp",
                        1
                    )
            }

    cursor.close()
    conn.close()

    return result


# =====================================================
# COMPANY FLOW
# =====================================================

def get_company_flow(
    company_id
):

    if company_id is None:
        return None

    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute(
        """
        SELECT
            FlowJson
        FROM Flows
        WHERE CompanyID = ?
        ORDER BY FlowID DESC
        LIMIT 1
        """,
        (
            company_id,
        )
    )

    row = cursor.fetchone()

    cursor.close()
    conn.close()

    if row:
        return row["FlowJson"]

    return None


# =====================================================
# FLOW ROLE HELPERS
# =====================================================

def _normalize_role_name(
    item
):

    if isinstance(item, dict):

        return str(
            item.get("role")
            or item.get("name")
            or ""
        ).strip()

    return str(item).strip()


def _extract_role_names(
    roles
):

    if not isinstance(roles, list):
        return []

    result = []

    for item in roles:

        role = _normalize_role_name(
            item
        )

        if role and role not in result:
            result.append(role)

    return result


def _get_flow_roles(
    company_id
):

    flow_json = get_company_flow(
        company_id
    )

    if not flow_json:
        return []

    try:

        flow = json.loads(
            flow_json
        )

    except Exception:

        return []

    nodes = (
        flow
        .get("drawflow", {})
        .get("Home", {})
        .get("data", {})
    )

    roles = []

    for node in nodes.values():

        if node.get("name") != "Roles":
            continue

        node_roles = (
            node
            .get("data", {})
            .get("roles", [])
        )

        if not isinstance(
            node_roles,
            list
        ):
            continue

        for item in node_roles:

            if not isinstance(
                item,
                dict
            ):
                continue

            role = str(
                item.get("role")
                or item.get("name")
                or ""
            ).strip()

            username = str(
                item.get("username", "")
            ).strip()

            password = str(
                item.get("password", "")
            )

            if not role or not username:
                continue

            roles.append(
                {
                    "role": role,
                    "username": username,
                    "password": password
                }
            )

    return roles


# =====================================================
# FLOW ROLE LOGIN
# =====================================================

def authenticate_flow_user(
    company_id,
    username,
    password
):

    roles = _get_flow_roles(
        company_id
    )

    username = str(
        username
    ).strip()

    password = str(
        password
    )

    for item in roles:

        if (
            item.get("username") == username
            and
            item.get("password") == password
        ):

            return {
                "username":
                    item["username"],

                "role":
                    item["role"],
            }

    return None


# =====================================================
# FLOW PAGE ACCESS
# =====================================================

def get_flow_page_access(
    company_id
):

    flow_json = get_company_flow(
        company_id
    )

    if not flow_json:
        return {}

    try:

        flow = json.loads(
            flow_json
        )

    except Exception:

        return {}

    nodes = (
        flow
        .get("drawflow", {})
        .get("Home", {})
        .get("data", {})
    )

    access = {}

    for node_id, node in nodes.items():

        if node.get("name") != "RolesEngaged":
            continue

        data = node.get(
            "data",
            {}
        )

        roles = _extract_role_names(
            data.get(
                "roles",
                []
            )
        )

        outputs = node.get(
            "outputs",
            {}
        )

        for output in outputs.values():

            for connection in output.get(
                "connections",
                []
            ):

                target = connection.get(
                    "node"
                )

                if target is None:
                    continue

                target = str(target)

                if target not in access:
                    access[target] = []

                for role in roles:

                    if role not in access[target]:

                        access[target].append(
                            role
                        )

    return access


# =====================================================
# FLOW NODE ID
# =====================================================

def get_flow_node_id(
    company_id,
    node_name
):

    flow_json = get_company_flow(
        company_id
    )

    if not flow_json:
        return None

    try:

        flow = json.loads(
            flow_json
        )

    except Exception:

        return None

    nodes = (
        flow
        .get("drawflow", {})
        .get("Home", {})
        .get("data", {})
    )

    for node_id, node in nodes.items():

        if node.get("name") == node_name:

            return str(node_id)

    return None


# =====================================================
# CHECK FLOW PAGE ACCESS
# =====================================================

def user_can_access_flow_page(
    company_id,
    role,
    node_id
):

    access = get_flow_page_access(
        company_id
    )

    if node_id is None:
        return True

    node_id = str(
        node_id
    )

    if node_id not in access:
        return True

    allowed_roles = access.get(
        node_id,
        []
    )

    return role in allowed_roles


# =====================================================
# USER PAGE ACCESS
# =====================================================

def get_user_page_access(
    company_id,
    role
):

    page_nodes = {
        "dashboard": "DashboardOutput",
        "trend": "TrendOutput",
    }

    allowed = {}

    for page, node_name in page_nodes.items():

        node_id = get_flow_node_id(
            company_id,
            node_name
        )

        allowed[page] = (
            user_can_access_flow_page(
                company_id,
                role,
                node_id
            )
        )

    allowed["date_filter"] = True
    allowed["flow"] = True

    return allowed


# =====================================================
# SAVE COMPANY FLOW
# =====================================================

def save_company_flow(
    company_id,
    flow_json
):

    if company_id is None:
        raise ValueError(
            "CompanyID is required"
        )

    try:

        flow = json.loads(
            flow_json
        )

    except Exception as e:

        raise ValueError(
            f"Invalid flow JSON: {e}"
        )

    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute(
        """
        SELECT
            FlowID
        FROM Flows
        WHERE CompanyID = ?
        LIMIT 1
        """,
        (
            company_id,
        )
    )

    row = cursor.fetchone()

    if row:

        cursor.execute(
            """
            UPDATE Flows
            SET
                FlowJson = ?,
                LastModified =
                    datetime(
                        'now',
                        'localtime'
                    )
            WHERE CompanyID = ?
            """,
            (
                flow_json,
                company_id
            )
        )

    else:

        cursor.execute(
            """
            INSERT INTO Flows
            (
                CompanyID,
                FlowJson,
                LastModified
            )
            VALUES
            (
                ?,
                ?,
                datetime(
                    'now',
                    'localtime'
                )
            )
            """,
            (
                company_id,
                flow_json
            )
        )

    # =================================================
    # SYNCHRONIZE ROLES -> USERS
    # =================================================

    nodes = (
        flow
        .get("drawflow", {})
        .get("Home", {})
        .get("data", {})
    )

    role_users = []

    for node in nodes.values():

        if node.get("name") != "Roles":
            continue

        data = node.get(
            "data",
            {}
        )

        roles = data.get(
            "roles",
            []
        )

        if not isinstance(
            roles,
            list
        ):
            continue

        for item in roles:

            if not isinstance(
                item,
                dict
            ):
                continue

            role = str(
                item.get("role")
                or item.get("name")
                or ""
            ).strip()

            username = str(
                item.get("username", "")
            ).strip()

            password = str(
                item.get("password", "")
            )

            if not role or not username:
                continue

            role_users.append(
                {
                    "role": role,
                    "username": username,
                    "password": password
                }
            )

    # -------------------------------------------------
    # Remove old company users
    # -------------------------------------------------

    cursor.execute(
        """
        DELETE FROM Users
        WHERE CompanyID = ?
        """,
        (
            company_id,
        )
    )

    # -------------------------------------------------
    # Recreate company users from Roles node
    # -------------------------------------------------

    for item in role_users:

        cursor.execute(
            """
            INSERT INTO Users
            (
                Username,
                PasswordHash,
                CompanyID,
                Role,
                Enabled
            )
            VALUES
            (?, ?, ?, ?, 1)
            """,
            (
                item["username"],
                generate_password_hash(
                    item["password"]
                ),
                company_id,
                item["role"]
            )
        )

    conn.commit()

    cursor.close()
    conn.close()


# =====================================================
# ALARM HISTORY
# =====================================================

def insert_alarm(
    company_id,
    alarm_text,
    alarm_value=None,
    timestamp=None
):

    if timestamp is None:

        timestamp = datetime.now().strftime(
            "%Y-%m-%d %H:%M:%S"
        )

    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute(
        """
        INSERT INTO AlarmHistory
        (
            CompanyID,
            AlarmText,
            AlarmValue,
            Timestamp
        )
        VALUES
        (?, ?, ?, ?)
        """,
        (
            company_id,
            alarm_text,
            alarm_value,
            timestamp
        )
    )

    conn.commit()

    cursor.close()
    conn.close()


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
          AND Timestamp <
              datetime(
                  'now',
                  'localtime',
                  '-3 months'
              )
        """
    )

    deleted = cursor.rowcount

    conn.commit()

    cursor.close()
    conn.close()

    print(
        "OLD TREND DATA DELETED:",
        deleted
    )