from flask_socketio import SocketIO

import threading
import time
from datetime import datetime, timedelta


# =====================================================
# EDGE OFFLINE WATCHDOG
# =====================================================
# This watchdog is completely independent from the Trend
# page, Trend Query, Trend Output, and database trend reader.
# It only writes zero samples to the existing PLC_Data table
# when the existing Edge TagHistory stream stops arriving.
# =====================================================

EDGE_OFFLINE_TIMEOUT = 2.0
EDGE_WATCHDOG_INTERVAL = 0.5
_edge_watchdog_started = False
_edge_watchdog_lock = threading.Lock()


def _watchdog_timestamp(value):
    if value is None:
        return None

    if hasattr(value, "strftime"):
        return value.strftime("%Y-%m-%d %H:%M:%S")

    return str(value).strip().replace("T", " ")[:19]


def _watchdog_once():
    conn = None
    cursor = None

    try:
        # Lazy import keeps the normal application import path unchanged.
        from database import get_connection

        conn = get_connection()
        cursor = conn.cursor()

        # TagHistory is the existing Edge historian source used by
        # PLCReader. Only tags that have actually received Edge data
        # are monitored.
        cursor.execute(
            """
            SELECT
                CompanyID,
                TagName,
                MAX(Timestamp) AS LastEdgeTimestamp
            FROM TagHistory
            WHERE Timestamp IS NOT NULL
            GROUP BY CompanyID, LOWER(TagName)
            """
        )

        rows = cursor.fetchall()
        now = datetime.now()
        cutoff = now - timedelta(seconds=EDGE_OFFLINE_TIMEOUT)
        cutoff_text = cutoff.strftime("%Y-%m-%d %H:%M:%S")
        second_text = now.strftime("%Y-%m-%d %H:%M:%S")

        for row in rows:
            company_id = row["CompanyID"]
            tag_name = row["TagName"]
            last_edge = _watchdog_timestamp(row["LastEdgeTimestamp"])

            if not tag_name or not last_edge:
                continue

            # Edge is still fresh.
            if last_edge >= cutoff_text:
                continue

            # Never add a zero when a real PLC_Data point already exists
            # in this exact second.
            cursor.execute(
                """
                SELECT ID
                FROM PLC_Data
                WHERE CompanyID = ?
                  AND LOWER(TagName) = LOWER(?)
                  AND Timestamp >= ?
                  AND Timestamp < datetime(?, '+1 second')
                LIMIT 1
                """,
                (
                    company_id,
                    tag_name,
                    second_text,
                    second_text,
                )
            )

            if cursor.fetchone():
                continue

            # Exactly one offline point for this tag/second.
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
                (?, ?, 0, 'EDGE_OFFLINE', ?)
                """,
                (
                    company_id,
                    tag_name,
                    second_text,
                )
            )

        conn.commit()

    except Exception as exc:
        print("EDGE WATCHDOG ERROR:", exc)

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


def _edge_watchdog_loop():
    # Let normal Flask/database initialization finish first.
    time.sleep(2)

    while True:
        _watchdog_once()
        time.sleep(EDGE_WATCHDOG_INTERVAL)


def _start_edge_watchdog():
    global _edge_watchdog_started

    with _edge_watchdog_lock:
        if _edge_watchdog_started:
            return

        _edge_watchdog_started = True

        thread = threading.Thread(
            target=_edge_watchdog_loop,
            name="SCADA_FLOW_EDGE_WATCHDOG",
            daemon=True,
        )
        thread.start()

        print(
            "EDGE WATCHDOG STARTED - TIMEOUT:",
            EDGE_OFFLINE_TIMEOUT,
            "seconds",
        )


class SCADAFlowSocketIO(SocketIO):

    def run(self, app, *args, **kwargs):
        """
        Register Flow Designer company-management routes
        after the Flask application has been created and
        immediately before the server starts.
        """

        try:
            from flow_company_routes import (
                register_flow_company_routes
            )

            register_flow_company_routes(app)

            print(
                "FLOW COMPANY ROUTES REGISTERED"
            )

        except Exception as exc:

            print(
                "FLOW COMPANY ROUTE REGISTRATION ERROR:",
                exc
            )

        _start_edge_watchdog()

        return super().run(
            app,
            *args,
            **kwargs
        )


socketio = SCADAFlowSocketIO()
