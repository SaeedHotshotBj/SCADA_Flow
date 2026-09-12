"""Durable Master -> Edge -> PLC write command service."""

from datetime import datetime, timedelta

from database import get_connection


LEASE_SECONDS = 15


def _now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _validate_u16(value, field_name):
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        raise ValueError(f"{field_name} must be an integer")
    if parsed < 0 or parsed > 65535:
        raise ValueError(f"{field_name} must be between 0 and 65535")
    return parsed


def _create_edge_timeout_state(conn):
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS EdgeTimeoutState (
            CompanyID INTEGER NOT NULL,
            PLC_ID INTEGER NOT NULL,
            LastReceivedAt TEXT,
            TimeoutSeconds REAL NOT NULL DEFAULT 10.0,
            TimedOut INTEGER NOT NULL DEFAULT 0,
            LastTimeoutAt TEXT,
            UpdatedAt TEXT NOT NULL DEFAULT '',
            PRIMARY KEY (CompanyID, PLC_ID)
        )
        """
    )


def _ensure_legacy_edge_timeout_state(conn):
    # EdgeTimeoutState is operational cache, not historian data. Older builds
    # created it with CompanyID alone. Rebuild that cache when its primary key
    # cannot represent multiple PLCs for the same company.
    existing = conn.execute("PRAGMA table_info(EdgeTimeoutState)").fetchall()
    if existing:
        columns = {row["name"] for row in existing}
        pk_columns = [row["name"] for row in sorted(existing, key=lambda item: int(item["pk"])) if int(row["pk"] or 0) > 0]
        if "PLC_ID" not in columns or pk_columns != ["CompanyID", "PLC_ID"]:
            conn.execute("DROP TABLE IF EXISTS EdgeTimeoutState")

    _create_edge_timeout_state(conn)
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_edge_timeout_state_company_plc "
        "ON EdgeTimeoutState(CompanyID, PLC_ID)"
    )


def ensure_plc_write_schema():
    conn = get_connection()
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS PLCWriteCommands (
                CommandID INTEGER PRIMARY KEY AUTOINCREMENT,
                CompanyID INTEGER NOT NULL,
                PLC_ID INTEGER NOT NULL,
                Register INTEGER NOT NULL,
                Value INTEGER NOT NULL,
                Status TEXT NOT NULL DEFAULT 'Pending',
                AttemptCount INTEGER NOT NULL DEFAULT 0,
                CreatedAt TEXT NOT NULL,
                ClaimedAt TEXT,
                LeaseUntil TEXT,
                CompletedAt TEXT,
                ErrorMessage TEXT,
                FOREIGN KEY (CompanyID) REFERENCES Companies(CompanyID),
                FOREIGN KEY (PLC_ID) REFERENCES PLCs(PLC_ID)
            )
            """
        )

        columns = {
            row["name"]
            for row in conn.execute("PRAGMA table_info(PLCWriteCommands)").fetchall()
        }
        required = {
            "CompanyID": "INTEGER",
            "PLC_ID": "INTEGER",
            "Register": "INTEGER",
            "Value": "INTEGER",
            "Status": "TEXT NOT NULL DEFAULT 'Pending'",
            "AttemptCount": "INTEGER NOT NULL DEFAULT 0",
            "CreatedAt": "TEXT",
            "ClaimedAt": "TEXT",
            "LeaseUntil": "TEXT",
            "CompletedAt": "TEXT",
            "ErrorMessage": "TEXT",
        }
        for name, definition in required.items():
            if name not in columns:
                conn.execute(f'ALTER TABLE PLCWriteCommands ADD COLUMN "{name}" {definition}')

        conn.execute(
            "UPDATE PLCWriteCommands SET Status='Pending' "
            "WHERE Status IS NULL OR TRIM(Status)=''"
        )
        conn.execute(
            "UPDATE PLCWriteCommands SET AttemptCount=0 WHERE AttemptCount IS NULL"
        )

        _ensure_legacy_edge_timeout_state(conn)

        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_plc_write_pending "
            "ON PLCWriteCommands(PLC_ID, Status, CommandID)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_plc_write_company "
            "ON PLCWriteCommands(CompanyID, CreatedAt)"
        )
        conn.commit()
    finally:
        conn.close()


def queue_write(company_id, plc_id, register, value):
    ensure_plc_write_schema()
    register = _validate_u16(register, "Register")
    value = _validate_u16(value, "Value")
    company_id = int(company_id)
    plc_id = int(plc_id)

    conn = get_connection()
    try:
        plc = conn.execute(
            "SELECT PLC_ID, CompanyID FROM PLCs WHERE PLC_ID=? AND CompanyID=?",
            (plc_id, company_id),
        ).fetchone()
        if plc is None:
            raise ValueError("Selected PLC does not belong to the selected company")

        now = _now()
        cur = conn.execute(
            """
            INSERT INTO PLCWriteCommands
            (CompanyID, PLC_ID, Register, Value, Status, AttemptCount, CreatedAt)
            VALUES (?, ?, ?, ?, 'Pending', 0, ?)
            """,
            (company_id, plc_id, register, value, now),
        )
        command_id = int(cur.lastrowid)
        conn.commit()
        return get_command(company_id, command_id)
    finally:
        conn.close()


def _release_expired_claims(conn):
    conn.execute(
        """
        UPDATE PLCWriteCommands
        SET Status='Pending', ClaimedAt=NULL, LeaseUntil=NULL
        WHERE Status='Claimed'
          AND LeaseUntil IS NOT NULL
          AND LeaseUntil <= ?
        """,
        (_now(),),
    )


def claim_next_command(plc_id):
    plc_id = int(plc_id)
    ensure_plc_write_schema()
    conn = get_connection()
    try:
        conn.execute("BEGIN IMMEDIATE")
        _release_expired_claims(conn)
        row = conn.execute(
            """
            SELECT CommandID, CompanyID, PLC_ID, Register, Value, Status,
                   AttemptCount, CreatedAt
            FROM PLCWriteCommands
            WHERE PLC_ID=? AND Status='Pending'
            ORDER BY CommandID ASC
            LIMIT 1
            """,
            (plc_id,),
        ).fetchone()
        if row is None:
            conn.commit()
            return None

        now = datetime.now()
        lease_until = (now + timedelta(seconds=LEASE_SECONDS)).strftime("%Y-%m-%d %H:%M:%S")
        conn.execute(
            """
            UPDATE PLCWriteCommands
            SET Status='Claimed', ClaimedAt=?, LeaseUntil=?, AttemptCount=AttemptCount+1
            WHERE CommandID=? AND Status='Pending'
            """,
            (now.strftime("%Y-%m-%d %H:%M:%S"), lease_until, int(row["CommandID"])),
        )
        conn.commit()
        return {
            "CommandID": int(row["CommandID"]),
            "CompanyID": int(row["CompanyID"]),
            "PLC_ID": int(row["PLC_ID"]),
            "Register": int(row["Register"]),
            "Value": int(row["Value"]),
        }
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def complete_command(command_id, plc_id, success, error_message=None):
    command_id = int(command_id)
    plc_id = int(plc_id)
    status = "Success" if bool(success) else "Failed"
    conn = get_connection()
    try:
        conn.execute(
            """
            UPDATE PLCWriteCommands
            SET Status=?, CompletedAt=?, ErrorMessage=?, LeaseUntil=NULL, ClaimedAt=NULL
            WHERE CommandID=? AND PLC_ID=? AND Status IN ('Claimed','Pending')
            """,
            (status, _now(), str(error_message)[:1000] if error_message else None, command_id, plc_id),
        )
        conn.commit()
        return get_command(None, command_id)
    finally:
        conn.close()


def get_command(company_id, command_id):
    conn = get_connection()
    try:
        sql = "SELECT * FROM PLCWriteCommands WHERE CommandID=?"
        params = [int(command_id)]
        if company_id is not None:
            sql += " AND CompanyID=?"
            params.append(int(company_id))
        row = conn.execute(sql, params).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


__all__ = [
    "ensure_plc_write_schema",
    "queue_write",
    "claim_next_command",
    "complete_command",
    "get_command",
]
