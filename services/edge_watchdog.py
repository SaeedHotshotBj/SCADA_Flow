# =====================================================
# SCADA_FLOW EDGE WATCHDOG
# =====================================================

import sqlite3
import threading
import time
from datetime import datetime, timedelta


EDGE_OFFLINE_TIMEOUT = 5
WATCHDOG_INTERVAL = 1

_started = False
_start_lock = threading.Lock()


def _get_connection():
    from database import get_connection
    return get_connection()


def _second(value):
    if value is None:
        return None
    text = str(value).strip().replace("T", " ")
    return text[:19]


def _insert_zero_if_needed(cursor, company_id, tag, timestamp):
    cursor.execute(
        """
        SELECT
            StorageType,
            Timestamp
        FROM PLC_Data
        WHERE CompanyID = ?
          AND LOWER(TagName) = LOWER(?)
        ORDER BY Timestamp DESC, ID DESC
        LIMIT 1
        """,
        (company_id, tag)
    )

    latest = cursor.fetchone()

    if latest:
        latest_type = str(latest["StorageType"] or "").upper()
        latest_timestamp = _second(latest["Timestamp"])

        if (
            latest_type == "EDGE_OFFLINE"
            and latest_timestamp is not None
        ):
            return False

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
        SELECT
            ?,
            ?,
            0,
            'EDGE_OFFLINE',
            ?
        WHERE NOT EXISTS
        (
            SELECT 1
            FROM PLC_Data
            WHERE CompanyID = ?
              AND LOWER(TagName) = LOWER(?)
              AND substr(Timestamp, 1, 19) = substr(?, 1, 19)
        )
        """,
        (
            company_id,
            tag,
            timestamp,
            company_id,
            tag,
            timestamp
        )
    )

    return cursor.rowcount > 0


def _cleanup_duplicate_seconds(cursor):
    """
    Keep exactly one PLC_Data row for each Company + Tag + second.
    The first stored row is retained.
    """

    cursor.execute(
        """
        DELETE FROM PLC_Data
        WHERE ID NOT IN
        (
            SELECT MIN(ID)
            FROM PLC_Data
            WHERE Timestamp IS NOT NULL
            GROUP BY
                CompanyID,
                LOWER(TagName),
                substr(Timestamp, 1, 19)
        )
        AND Timestamp IS NOT NULL
        """
    )


def _watch_once():
    conn = None
    cursor = None

    try:
        conn = _get_connection()
        cursor = conn.cursor()

        # First remove legacy duplicate samples. This is database-side,
        # so the Trend Viewer does not need any UI/chart modification.
        _cleanup_duplicate_seconds(cursor)

        now = datetime.now()
        cutoff = now - timedelta(seconds=EDGE_OFFLINE_TIMEOUT)
        cutoff_text = cutoff.strftime("%Y-%m-%d %H:%M:%S")
        now_text = now.strftime("%Y-%m-%d %H:%M:%S")

        cursor.execute(
            """
            SELECT
                CompanyID,
                TagName,
                MAX(Timestamp) AS LastEdgeTimestamp
            FROM PLC_Data
            WHERE StorageType = 'EDGE'
            GROUP BY CompanyID, LOWER(TagName)
            """
        )

        rows = cursor.fetchall()

        for row in rows:
            company_id = row["CompanyID"]
            tag = row["TagName"]
            last_edge = _second(row["LastEdgeTimestamp"])

            if not tag or not last_edge:
                continue

            # A fresh Edge sample means the stream is online again.
            if last_edge >= cutoff_text:
                continue

            _insert_zero_if_needed(
                cursor,
                company_id,
                tag,
                now_text
            )

        conn.commit()

    except Exception:
        if conn is not None:
            try:
                conn.rollback()
            except Exception:
                pass

    finally:
        if cursor is not None:
            try:
                cursor.close()
            except Exception:
                pass

        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass


def _watch_loop():
    while True:
        _watch_once()
        time.sleep(WATCHDOG_INTERVAL)


def start_edge_watchdog():
    global _started

    with _start_lock:
        if _started:
            return

        _started = True

        thread = threading.Thread(
            target=_watch_loop,
            name="SCADA_FLOW_EDGE_WATCHDOG",
            daemon=True
        )
        thread.start()
