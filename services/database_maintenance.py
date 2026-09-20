"""Periodic SQLite maintenance for high-volume historian data.

This worker only removes bounded, non-reporting operational history and
checkpoints the SQLite WAL. It does not change Flow execution or Report logic.
"""

import threading
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from config import DB_MAINTENANCE_CONFIG
from database import get_connection


SCADA_TIMEZONE = ZoneInfo("Asia/Tehran")
_START_LOCK = threading.Lock()
_WORKER = None


def _positive_int(value, default):
    try:
        value = int(value)
    except (TypeError, ValueError):
        return int(default)
    return value if value > 0 else int(default)


INTERVAL_SECONDS = max(
    60,
    _positive_int(DB_MAINTENANCE_CONFIG.get("interval_seconds"), 900),
)
TRIGGER_RETENTION_DAYS = _positive_int(
    DB_MAINTENANCE_CONFIG.get("trigger_retention_days"),
    3,
)
EDGE_LEDGER_RETENTION_DAYS = _positive_int(
    DB_MAINTENANCE_CONFIG.get("edge_ledger_retention_days"),
    3,
)
PRODUCTION_EVENT_RETENTION_DAYS = _positive_int(
    DB_MAINTENANCE_CONFIG.get("production_event_retention_days"),
    90,
)
DELETE_BATCH_SIZE = max(
    100,
    _positive_int(DB_MAINTENANCE_CONFIG.get("delete_batch_size"), 5000),
)
WAL_CHECKPOINT = str(
    DB_MAINTENANCE_CONFIG.get("wal_checkpoint", "TRUNCATE")
).strip().upper()
if WAL_CHECKPOINT not in {"PASSIVE", "FULL", "RESTART", "TRUNCATE"}:
    WAL_CHECKPOINT = "TRUNCATE"


def _now():
    return datetime.now(SCADA_TIMEZONE).replace(tzinfo=None, microsecond=0)


def _cutoff_text(days, now=None):
    now = now or _now()
    cutoff = now - timedelta(days=int(days))
    return cutoff.strftime("%Y-%m-%d %H:%M:%S")


def _delete_batches(conn, table, id_column, condition_sql, cutoff, params=()):
    deleted = 0
    while True:
        sql = f"""
            DELETE FROM "{table}"
            WHERE "{id_column}" IN (
                SELECT "{id_column}"
                FROM "{table}"
                WHERE {condition_sql}
                ORDER BY "{id_column}"
                LIMIT ?
            )
        """
        cursor = conn.execute(
            sql,
            tuple(params) + (cutoff, DELETE_BATCH_SIZE)
            if params
            else (cutoff, DELETE_BATCH_SIZE),
        )
        count = max(0, int(cursor.rowcount))
        if count == 0:
            break
        conn.commit()
        deleted += count
        if count < DELETE_BATCH_SIZE:
            break
    return deleted


def cleanup_once():
    """Delete bounded high-volume operational history and checkpoint WAL."""
    conn = get_connection()
    conn.execute("PRAGMA busy_timeout = 10000")
    now = _now()

    stats = {
        "trigger_rows_deleted": 0,
        "edge_ledger_rows_deleted": 0,
        "production_event_rows_deleted": 0,
        "wal_checkpoint": None,
    }

    try:
        trigger_cutoff = _cutoff_text(TRIGGER_RETENTION_DAYS, now)
        stats["trigger_rows_deleted"] = _delete_batches(
            conn,
            "PLC_Data",
            "ID",
            """
            Timestamp < ?
            AND UPPER(COALESCE(StorageType, '')) IN ('TRIGGER', 'TRIGGER_SIGNAL')
            """,
            trigger_cutoff,
        )

        ledger_cutoff = _cutoff_text(EDGE_LEDGER_RETENTION_DAYS, now)
        stats["edge_ledger_rows_deleted"] = _delete_batches(
            conn,
            "EdgeEventLedger",
            "rowid",
            "EventTimestamp < ?",
            ledger_cutoff,
        )

        event_cutoff = _cutoff_text(PRODUCTION_EVENT_RETENTION_DAYS, now)
        stats["production_event_rows_deleted"] = _delete_batches(
            conn,
            "ProductionEvents",
            "rowid",
            "TriggerTimestamp < ?",
            event_cutoff,
        )

        try:
            conn.execute("PRAGMA optimize")
        except Exception:
            pass

        try:
            row = conn.execute(
                f"PRAGMA wal_checkpoint({WAL_CHECKPOINT})"
            ).fetchone()
            if row is not None:
                stats["wal_checkpoint"] = tuple(row)
        except Exception as exc:
            print("DATABASE WAL CHECKPOINT ERROR:", exc)

        return stats
    finally:
        conn.close()


class DatabaseMaintenanceWorker:
    def __init__(self):
        self.running = True

    def run(self):
        while self.running:
            try:
                stats = cleanup_once()
                if any(
                    stats.get(key, 0)
                    for key in (
                        "trigger_rows_deleted",
                        "edge_ledger_rows_deleted",
                        "production_event_rows_deleted",
                    )
                ):
                    print("DATABASE MAINTENANCE:", stats)
            except Exception as exc:
                print("DATABASE MAINTENANCE ERROR:", repr(exc))
            time.sleep(INTERVAL_SECONDS)

    def stop(self):
        self.running = False


def start():
    global _WORKER
    with _START_LOCK:
        if _WORKER is not None and _WORKER.running:
            return _WORKER

        _WORKER = DatabaseMaintenanceWorker()
        cleanup_once()

        thread = threading.Thread(
            target=_WORKER.run,
            name="SCADA-Database-Maintenance",
            daemon=True,
        )
        thread.start()
        print(
            "DATABASE MAINTENANCE WORKER STARTED:",
            f"interval={INTERVAL_SECONDS}s",
            f"trigger_retention={TRIGGER_RETENTION_DAYS}d",
            f"ledger_retention={EDGE_LEDGER_RETENTION_DAYS}d",
            f"production_event_retention={PRODUCTION_EVENT_RETENTION_DAYS}d",
        )
        return _WORKER


__all__ = [
    "cleanup_once",
    "DatabaseMaintenanceWorker",
    "start",
]
