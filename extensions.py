from flask_socketio import SocketIO

import threading
import time
from datetime import datetime


# =====================================================
# EDGE OFFLINE WATCHDOG
# =====================================================
# This watchdog is completely independent from the Trend
# page, Trend Query, Trend Output, and database trend reader.
# It only writes zero samples to the existing PLC_Data table
# when new Edge TagHistory rows stop arriving.
#
# IMPORTANT:
# The Edge Timestamp is supplied by the Edge PC. Do not use it
# as the server heartbeat because the Edge PC clock and VPS clock
# may differ. The TagHistory ID is used only to detect arrival of
# a new server-side row, and the watchdog uses server monotonic
# time to measure the 2-second timeout.
# =====================================================

EDGE_OFFLINE_TIMEOUT = 2.0
EDGE_WATCHDOG_INTERVAL = 0.5
_edge_watchdog_started = False
_edge_watchdog_lock = threading.Lock()

# (CompanyID, normalized TagName) -> {last_id, last_seen}
_edge_watch_state = {}


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
                MAX(ID) AS LastEdgeID
            FROM TagHistory
            GROUP BY CompanyID, LOWER(TagName)
            """
        )

        rows = cursor.fetchall()
        now_monotonic = time.monotonic()
        now_text = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        active_keys = set()

        for row in rows:
            company_id = row["CompanyID"]
            tag_name = row["TagName"]
            last_edge_id = row["LastEdgeID"]

            if not tag_name or last_edge_id is None:
                continue

            key = (
                company_id,
                str(tag_name).strip().lower()
            )

            active_keys.add(key)

            state = _edge_watch_state.get(key)

            # First observation after server start: initialize the
            # heartbeat state. Do not immediately declare the Edge offline.
            if state is None:
                _edge_watch_state[key] = {
                    "last_id": int(last_edge_id),
                    "last_seen": now_monotonic,
                    "tag_name": str(tag_name),
                }
                continue

            # A new TagHistory row means Edge data actually arrived at
            # the server, independent of the Edge PC clock.
            if int(last_edge_id) != int(state["last_id"]):
                state["last_id"] = int(last_edge_id)
                state["last_seen"] = now_monotonic
                state["tag_name"] = str(tag_name)
                continue

            # No new Edge sample has arrived for the timeout period.
            if (
                now_monotonic - float(state["last_seen"])
                <= EDGE_OFFLINE_TIMEOUT
            ):
                continue

            # Never add a zero when a real PLC_Data point already exists
            # in this exact server second.
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
                    now_text,
                    now_text,
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
                    now_text,
                )
            )

        # Remove state for tags that no longer exist in TagHistory.
        for key in list(_edge_watch_state.keys()):
            if key not in active_keys:
                del _edge_watch_state[key]

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
