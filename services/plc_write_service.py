"""Master -> Edge -> PLC holding-register write command queue."""

from datetime import datetime

from database import get_connection


TABLE_SQL = """
CREATE TABLE IF NOT EXISTS PLCWriteCommands (
    CommandID INTEGER PRIMARY KEY AUTOINCREMENT,
    CompanyID INTEGER NOT NULL,
    PLC_ID INTEGER NOT NULL,
    Register INTEGER NOT NULL,
    Value INTEGER NOT NULL,
    Status TEXT NOT NULL DEFAULT 'PENDING',
    CreatedAt TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    StartedAt TEXT,
    CompletedAt TEXT,
    ErrorMessage TEXT,
    FOREIGN KEY (CompanyID) REFERENCES Companies(CompanyID),
    FOREIGN KEY (PLC_ID) REFERENCES PLCs(PLC_ID)
);

CREATE INDEX IF NOT EXISTS idx_plc_write_pending
ON PLCWriteCommands (PLC_ID, Status, CommandID);

CREATE INDEX IF NOT EXISTS idx_plc_write_company
ON PLCWriteCommands (CompanyID, CreatedAt);
"""


def ensure_write_table():
    conn = get_connection()
    try:
        conn.executescript(TABLE_SQL)
        conn.commit()
    finally:
        conn.close()


def get_company_plcs(company_id):
    conn = get_connection()
    try:
        rows = conn.execute(
            """
            SELECT PLC_ID, PLC_Name, PLC_IP, PLC_Port, Slave_ID
            FROM PLCs
            WHERE CompanyID = ?
            ORDER BY PLC_ID
            """,
            (company_id,),
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


def create_write_command(company_id, plc_id, register, value):
    conn = get_connection()
    try:
        plc = conn.execute(
            """
            SELECT PLC_ID
            FROM PLCs
            WHERE PLC_ID = ?
              AND CompanyID = ?
            LIMIT 1
            """,
            (plc_id, company_id),
        ).fetchone()

        if plc is None:
            raise ValueError("PLC does not belong to the selected company")

        cursor = conn.execute(
            """
            INSERT INTO PLCWriteCommands
            (CompanyID, PLC_ID, Register, Value, Status)
            VALUES (?, ?, ?, ?, 'PENDING')
            """,
            (company_id, plc_id, register, value),
        )
        conn.commit()
        return int(cursor.lastrowid)
    finally:
        conn.close()


def get_command_status(company_id, command_id):
    conn = get_connection()
    try:
        row = conn.execute(
            """
            SELECT
                CommandID,
                CompanyID,
                PLC_ID,
                Register,
                Value,
                Status,
                CreatedAt,
                StartedAt,
                CompletedAt,
                ErrorMessage
            FROM PLCWriteCommands
            WHERE CommandID = ?
              AND CompanyID = ?
            LIMIT 1
            """,
            (command_id, company_id),
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def get_command_history(company_id, limit=20):
    limit = max(1, min(int(limit), 100))
    conn = get_connection()
    try:
        rows = conn.execute(
            f"""
            SELECT
                CommandID,
                PLC_ID,
                Register,
                Value,
                Status,
                CreatedAt,
                CompletedAt,
                ErrorMessage
            FROM PLCWriteCommands
            WHERE CompanyID = ?
            ORDER BY CommandID DESC
            LIMIT {limit}
            """,
            (company_id,),
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


def claim_next_command(plc_id):
    """Atomically claim one pending command for an Edge/PLC.

    A stale PROCESSING command older than 30 seconds is eligible for retry.
    """
    conn = get_connection()
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            """
            SELECT
                CommandID,
                CompanyID,
                PLC_ID,
                Register,
                Value
            FROM PLCWriteCommands
            WHERE PLC_ID = ?
              AND (
                    Status = 'PENDING'
                    OR (
                        Status = 'PROCESSING'
                        AND StartedAt < datetime('now', 'localtime', '-30 seconds')
                    )
              )
            ORDER BY CommandID ASC
            LIMIT 1
            """,
            (plc_id,),
        ).fetchone()

        if row is None:
            conn.commit()
            return None

        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        conn.execute(
            """
            UPDATE PLCWriteCommands
            SET Status = 'PROCESSING',
                StartedAt = ?,
                ErrorMessage = NULL
            WHERE CommandID = ?
            """,
            (now, row["CommandID"]),
        )
        conn.commit()
        return dict(row)
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def complete_command(command_id, plc_id, success, error_message=None):
    conn = get_connection()
    try:
        status = "SUCCESS" if success else "FAILED"
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        cursor = conn.execute(
            """
            UPDATE PLCWriteCommands
            SET Status = ?,
                CompletedAt = ?,
                ErrorMessage = ?
            WHERE CommandID = ?
              AND PLC_ID = ?
            """,
            (status, now, error_message, command_id, plc_id),
        )
        conn.commit()
        return cursor.rowcount > 0
    finally:
        conn.close()
