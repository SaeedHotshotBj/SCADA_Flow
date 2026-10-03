"""Remote Edge management service.

The service provides authenticated, outbound-polled management for Windows Edge
computers. It intentionally exposes a fixed command set instead of arbitrary
shell execution.
"""

import hashlib
import hmac
import json
import secrets
import time
from pathlib import PurePosixPath

from database import get_connection


PAIRING_TTL_SECONDS = 24 * 60 * 60
EDGE_ONLINE_SECONDS = 20
COMMAND_STALE_SECONDS = 120
MAX_FILE_B64_LENGTH = 14 * 1024 * 1024

COMMANDS = {
    "STATUS",
    "START",
    "STOP",
    "RESTART",
    "LIST_FILES",
    "READ_FILE",
    "WRITE_FILE",
    "DELETE_FILE",
}


def _hash_token(token):
    return hashlib.sha256(str(token).encode("utf-8")).hexdigest()


def _normalize_edge_id(value):
    value = str(value or "").strip()
    if not value or len(value) > 128:
        raise ValueError("Invalid Edge ID")
    return value


def _normalize_company_id(value):
    try:
        value = int(value)
    except (TypeError, ValueError):
        raise ValueError("Invalid Company ID")
    if value <= 0:
        raise ValueError("Invalid Company ID")
    return value


def _validate_relative_path(value):
    path = str(value or "").strip().replace("\\", "/")
    if not path or len(path) > 512:
        raise ValueError("File path is required")

    pure = PurePosixPath(path)
    if pure.is_absolute() or path.startswith("/"):
        raise ValueError("Absolute paths are not allowed")

    parts = [part for part in pure.parts if part not in ("", ".")]
    if not parts or any(part == ".." for part in parts):
        raise ValueError("Path traversal is not allowed")

    return "/".join(parts)


def ensure_edge_management_schema():
    conn = get_connection()
    try:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS EdgeDevices (
                EdgeID TEXT PRIMARY KEY,
                CompanyID INTEGER NOT NULL,
                Hostname TEXT NOT NULL DEFAULT '',
                Platform TEXT NOT NULL DEFAULT '',
                AgentVersion TEXT NOT NULL DEFAULT '',
                RootPath TEXT NOT NULL DEFAULT '',
                AppRunning INTEGER NOT NULL DEFAULT 0,
                AppPID INTEGER,
                LastSeen REAL,
                Status TEXT NOT NULL DEFAULT 'OFFLINE',
                RegisteredAt REAL NOT NULL,
                UpdatedAt REAL NOT NULL,
                TokenHash TEXT NOT NULL,
                FOREIGN KEY (CompanyID) REFERENCES Companies(CompanyID)
            );

            CREATE TABLE IF NOT EXISTS EdgePairingTokens (
                TokenID INTEGER PRIMARY KEY AUTOINCREMENT,
                CompanyID INTEGER NOT NULL,
                TokenHash TEXT NOT NULL,
                CreatedAt REAL NOT NULL,
                ExpiresAt REAL NOT NULL,
                UsedAt REAL,
                Enabled INTEGER NOT NULL DEFAULT 1,
                FOREIGN KEY (CompanyID) REFERENCES Companies(CompanyID)
            );

            CREATE TABLE IF NOT EXISTS EdgeCommands (
                CommandID INTEGER PRIMARY KEY AUTOINCREMENT,
                EdgeID TEXT NOT NULL,
                CompanyID INTEGER NOT NULL,
                CommandType TEXT NOT NULL,
                Path TEXT NOT NULL DEFAULT '',
                PayloadJson TEXT NOT NULL DEFAULT '{}',
                Status TEXT NOT NULL DEFAULT 'QUEUED',
                RequestedAt REAL NOT NULL,
                SentAt REAL,
                CompletedAt REAL,
                ResultJson TEXT NOT NULL DEFAULT '{}',
                FOREIGN KEY (EdgeID) REFERENCES EdgeDevices(EdgeID),
                FOREIGN KEY (CompanyID) REFERENCES Companies(CompanyID)
            );

            CREATE INDEX IF NOT EXISTS idx_edge_devices_company
                ON EdgeDevices (CompanyID, Status, LastSeen);

            CREATE INDEX IF NOT EXISTS idx_edge_commands_edge_status
                ON EdgeCommands (EdgeID, Status, RequestedAt);

            CREATE INDEX IF NOT EXISTS idx_edge_pairing_company
                ON EdgePairingTokens (CompanyID, Enabled, ExpiresAt);
            """
        )
        conn.commit()
    finally:
        conn.close()


def generate_pairing_token(company_id):
    company_id = _normalize_company_id(company_id)
    token = secrets.token_urlsafe(32)
    now = time.time()
    expires = now + PAIRING_TTL_SECONDS

    conn = get_connection()
    try:
        conn.execute(
            """
            UPDATE EdgePairingTokens
            SET Enabled = 0
            WHERE CompanyID = ?
              AND UsedAt IS NULL
              AND ExpiresAt > ?
            """,
            (company_id, now),
        )
        conn.execute(
            """
            INSERT INTO EdgePairingTokens
            (CompanyID, TokenHash, CreatedAt, ExpiresAt, Enabled)
            VALUES (?, ?, ?, ?, 1)
            """,
            (company_id, _hash_token(token), now, expires),
        )
        conn.commit()
    finally:
        conn.close()

    return {
        "token": token,
        "expires_at": expires,
        "ttl_seconds": PAIRING_TTL_SECONDS,
    }


def _load_edge(conn, edge_id):
    return conn.execute(
        """
        SELECT *
        FROM EdgeDevices
        WHERE EdgeID = ?
        LIMIT 1
        """,
        (edge_id,),
    ).fetchone()


def _authenticate_edge(conn, edge_id, token):
    edge = _load_edge(conn, edge_id)
    if not edge:
        return None

    if not hmac.compare_digest(
        str(edge["TokenHash"]),
        _hash_token(token),
    ):
        return None

    return edge


def register_or_heartbeat(payload):
    payload = payload if isinstance(payload, dict) else {}
    edge_id = _normalize_edge_id(payload.get("edge_id"))
    company_id = _normalize_company_id(payload.get("company_id"))
    token = str(payload.get("token") or "").strip()

    if not token:
        raise PermissionError("Management token is required")

    now = time.time()
    conn = get_connection()
    try:
        edge = _load_edge(conn, edge_id)

        if edge is None:
            pairing = conn.execute(
                """
                SELECT TokenID
                FROM EdgePairingTokens
                WHERE CompanyID = ?
                  AND TokenHash = ?
                  AND Enabled = 1
                  AND UsedAt IS NULL
                  AND ExpiresAt > ?
                ORDER BY TokenID DESC
                LIMIT 1
                """,
                (company_id, _hash_token(token), now),
            ).fetchone()

            if pairing is None:
                raise PermissionError("Invalid or expired pairing token")

            conn.execute(
                """
                INSERT INTO EdgeDevices
                (
                    EdgeID,
                    CompanyID,
                    Hostname,
                    Platform,
                    AgentVersion,
                    RootPath,
                    AppRunning,
                    AppPID,
                    LastSeen,
                    Status,
                    RegisteredAt,
                    UpdatedAt,
                    TokenHash
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'ONLINE', ?, ?, ?)
                """,
                (
                    edge_id,
                    company_id,
                    str(payload.get("hostname") or "")[:255],
                    str(payload.get("platform") or "")[:255],
                    str(payload.get("agent_version") or "")[:64],
                    str(payload.get("root_path") or "")[:1024],
                    1 if payload.get("app_running") else 0,
                    payload.get("app_pid"),
                    now,
                    now,
                    now,
                    _hash_token(token),
                ),
            )
            conn.execute(
                """
                UPDATE EdgePairingTokens
                SET UsedAt = ?, Enabled = 0
                WHERE TokenID = ?
                """,
                (now, pairing["TokenID"]),
            )
        else:
            if int(edge["CompanyID"]) != company_id:
                raise PermissionError("Edge belongs to another company")

            if not hmac.compare_digest(
                str(edge["TokenHash"]),
                _hash_token(token),
            ):
                raise PermissionError("Invalid management token")

            conn.execute(
                """
                UPDATE EdgeDevices
                SET Hostname = ?,
                    Platform = ?,
                    AgentVersion = ?,
                    RootPath = ?,
                    AppRunning = ?,
                    AppPID = ?,
                    LastSeen = ?,
                    Status = 'ONLINE',
                    UpdatedAt = ?
                WHERE EdgeID = ?
                """,
                (
                    str(payload.get("hostname") or "")[:255],
                    str(payload.get("platform") or "")[:255],
                    str(payload.get("agent_version") or "")[:64],
                    str(payload.get("root_path") or "")[:1024],
                    1 if payload.get("app_running") else 0,
                    payload.get("app_pid"),
                    now,
                    now,
                    edge_id,
                ),
            )

        conn.commit()
        return {
            "edge_id": edge_id,
            "company_id": company_id,
            "status": "ONLINE",
        }
    finally:
        conn.close()


def get_company_edges(company_id):
    company_id = _normalize_company_id(company_id)
    now = time.time()
    conn = get_connection()
    try:
        rows = conn.execute(
            """
            SELECT
                EdgeID,
                CompanyID,
                Hostname,
                Platform,
                AgentVersion,
                RootPath,
                AppRunning,
                AppPID,
                LastSeen,
                RegisteredAt,
                UpdatedAt
            FROM EdgeDevices
            WHERE CompanyID = ?
            ORDER BY Hostname, EdgeID
            """,
            (company_id,),
        ).fetchall()

        result = []
        for row in rows:
            last_seen = row["LastSeen"]
            online = (
                last_seen is not None
                and (now - float(last_seen)) <= EDGE_ONLINE_SECONDS
            )
            result.append(
                {
                    "edge_id": row["EdgeID"],
                    "company_id": int(row["CompanyID"]),
                    "hostname": row["Hostname"],
                    "platform": row["Platform"],
                    "agent_version": row["AgentVersion"],
                    "root_path": row["RootPath"],
                    "app_running": bool(row["AppRunning"]),
                    "app_pid": row["AppPID"],
                    "last_seen": last_seen,
                    "registered_at": row["RegisteredAt"],
                    "updated_at": row["UpdatedAt"],
                    "status": "ONLINE" if online else "OFFLINE",
                }
            )
        return result
    finally:
        conn.close()


def queue_command(company_id, edge_id, command_type, path="", payload=None):
    company_id = _normalize_company_id(company_id)
    edge_id = _normalize_edge_id(edge_id)
    command_type = str(command_type or "").strip().upper()

    if command_type not in COMMANDS:
        raise ValueError("Unsupported Edge management command")

    path = _validate_relative_path(path) if path else ""
    payload = payload if isinstance(payload, dict) else {}

    if command_type in {"READ_FILE", "WRITE_FILE", "DELETE_FILE"} and not path:
        raise ValueError("File path is required")

    if command_type == "WRITE_FILE":
        content_b64 = str(payload.get("content_b64") or "")
        if not content_b64:
            raise ValueError("File content is required")
        if len(content_b64) > MAX_FILE_B64_LENGTH:
            raise ValueError("File payload is too large")

    payload_json = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    now = time.time()

    conn = get_connection()
    try:
        edge = conn.execute(
            """
            SELECT EdgeID
            FROM EdgeDevices
            WHERE EdgeID = ?
              AND CompanyID = ?
            LIMIT 1
            """,
            (edge_id, company_id),
        ).fetchone()

        if not edge:
            raise ValueError("Edge is not registered for this company")

        cursor = conn.execute(
            """
            INSERT INTO EdgeCommands
            (
                EdgeID,
                CompanyID,
                CommandType,
                Path,
                PayloadJson,
                Status,
                RequestedAt
            )
            VALUES (?, ?, ?, ?, ?, 'QUEUED', ?)
            """,
            (
                edge_id,
                company_id,
                command_type,
                path,
                payload_json,
                now,
            ),
        )
        conn.commit()
        return int(cursor.lastrowid)
    finally:
        conn.close()


def poll_command(edge_id, token):
    edge_id = _normalize_edge_id(edge_id)
    token = str(token or "").strip()
    if not token:
        raise PermissionError("Management token is required")

    now = time.time()
    conn = get_connection()
    try:
        edge = _authenticate_edge(conn, edge_id, token)
        if edge is None:
            raise PermissionError("Invalid management token")

        conn.execute(
            """
            UPDATE EdgeCommands
            SET Status = 'QUEUED',
                SentAt = NULL
            WHERE EdgeID = ?
              AND Status = 'DISPATCHED'
              AND SentAt IS NOT NULL
              AND SentAt < ?
            """,
            (edge_id, now - COMMAND_STALE_SECONDS),
        )

        row = conn.execute(
            """
            SELECT
                CommandID,
                CompanyID,
                CommandType,
                Path,
                PayloadJson
            FROM EdgeCommands
            WHERE EdgeID = ?
              AND Status = 'QUEUED'
            ORDER BY CommandID
            LIMIT 1
            """,
            (edge_id,),
        ).fetchone()

        if not row:
            conn.commit()
            return None

        conn.execute(
            """
            UPDATE EdgeCommands
            SET Status = 'DISPATCHED',
                SentAt = ?
            WHERE CommandID = ?
              AND Status = 'QUEUED'
            """,
            (now, row["CommandID"]),
        )
        conn.commit()

        payload = {}
        try:
            payload = json.loads(row["PayloadJson"] or "{}")
        except Exception:
            payload = {}

        return {
            "command_id": int(row["CommandID"]),
            "company_id": int(row["CompanyID"]),
            "command": row["CommandType"],
            "path": row["Path"],
            "payload": payload,
        }
    finally:
        conn.close()


def record_command_result(edge_id, token, command_id, success, result):
    edge_id = _normalize_edge_id(edge_id)
    token = str(token or "").strip()

    try:
        command_id = int(command_id)
    except (TypeError, ValueError):
        raise ValueError("Invalid Command ID")

    result = result if isinstance(result, dict) else {"message": str(result)}
    status = "SUCCESS" if success else "ERROR"
    now = time.time()

    conn = get_connection()
    try:
        edge = _authenticate_edge(conn, edge_id, token)
        if edge is None:
            raise PermissionError("Invalid management token")

        row = conn.execute(
            """
            SELECT EdgeID
            FROM EdgeCommands
            WHERE CommandID = ?
              AND EdgeID = ?
            LIMIT 1
            """,
            (command_id, edge_id),
        ).fetchone()

        if not row:
            raise ValueError("Command not found")

        conn.execute(
            """
            UPDATE EdgeCommands
            SET Status = ?,
                CompletedAt = ?,
                ResultJson = ?
            WHERE CommandID = ?
              AND EdgeID = ?
            """,
            (
                status,
                now,
                json.dumps(result, ensure_ascii=False, separators=(",", ":")),
                command_id,
                edge_id,
            ),
        )
        conn.commit()
        return True
    finally:
        conn.close()


def get_command_status(company_id, command_id):
    company_id = _normalize_company_id(company_id)
    try:
        command_id = int(command_id)
    except (TypeError, ValueError):
        raise ValueError("Invalid Command ID")

    conn = get_connection()
    try:
        row = conn.execute(
            """
            SELECT
                CommandID,
                EdgeID,
                CompanyID,
                CommandType,
                Path,
                Status,
                RequestedAt,
                SentAt,
                CompletedAt,
                ResultJson
            FROM EdgeCommands
            WHERE CommandID = ?
              AND CompanyID = ?
            LIMIT 1
            """,
            (command_id, company_id),
        ).fetchone()

        if not row:
            raise ValueError("Command not found")

        try:
            result = json.loads(row["ResultJson"] or "{}")
        except Exception:
            result = {"message": row["ResultJson"] or ""}

        return {
            "command_id": int(row["CommandID"]),
            "edge_id": row["EdgeID"],
            "company_id": int(row["CompanyID"]),
            "command": row["CommandType"],
            "path": row["Path"],
            "status": row["Status"],
            "requested_at": row["RequestedAt"],
            "sent_at": row["SentAt"],
            "completed_at": row["CompletedAt"],
            "result": result,
        }
    finally:
        conn.close()
