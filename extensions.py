from flask_socketio import SocketIO

import threading
import time
from datetime import datetime


# =====================================================
# EDGE OFFLINE WATCHDOG
# =====================================================
# Completely independent from Trend code.
# It checks Edge arrival state and writes zero only when
# an Edge tag has actually stopped sending for 2 seconds.
# Database write connections are opened only when a zero
# must actually be inserted, so normal Trend reads are not
# continuously competing with the watchdog for SQLite locks.
# =====================================================

EDGE_OFFLINE_TIMEOUT = 2.0
EDGE_WATCHDOG_INTERVAL = 0.5
_edge_watchdog_started = False
_edge_watchdog_lock = threading.Lock()

# (CompanyID, normalized TagName) -> {last_id, last_seen, tag_name}
_edge_watch_state = {}


def _watchdog_once():
    read_conn = None
    read_cursor = None

    try:
        from database import get_connection

        # -------------------------------------------------
        # READ-ONLY HEARTBEAT CHECK
        # -------------------------------------------------
        read_conn = get_connection()
        read_cursor = read_conn.cursor()

        read_cursor.execute(
            """
            SELECT
                CompanyID,
                TagName,
                MAX(ID) AS LastEdgeID
            FROM TagHistory
            GROUP BY CompanyID, LOWER(TagName)
            """
        )

        rows = read_cursor.fetchall()
        now_monotonic = time.monotonic()
        now_text = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        active_keys = set()
        stale_tags = []

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

            if state is None:
                _edge_watch_state[key] = {
                    "last_id": int(last_edge_id),
                    "last_seen": now_monotonic,
                    "tag_name": str(tag_name),
                }
                continue

            if int(last_edge_id) != int(state["last_id"]):
                state["last_id"] = int(last_edge_id)
                state["last_seen"] = now_monotonic
                state["tag_name"] = str(tag_name)
                continue

            if (
                now_monotonic - float(state["last_seen"])
                > EDGE_OFFLINE_TIMEOUT
            ):
                stale_tags.append((
                    company_id,
                    str(tag_name)
                ))

        for key in list(_edge_watch_state.keys()):
            if key not in active_keys:
                del _edge_watch_state[key]

    except Exception as exc:
        print("EDGE WATCHDOG ERROR:", exc)
        return

    finally:
        if read_cursor is not None:
            try:
                read_cursor.close()
            except Exception:
                pass

        if read_conn is not None:
            try:
                read_conn.close()
            except Exception:
                pass

    if not stale_tags:
        return

    # -------------------------------------------------
    # WRITE ONLY WHEN A ZERO IS REALLY NEEDED
    # -------------------------------------------------
    write_conn = None
    write_cursor = None

    try:
        from database import get_connection

        write_conn = get_connection()
        write_cursor = write_conn.cursor()

        for company_id, tag_name in stale_tags:

            write_cursor.execute(
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

            if write_cursor.fetchone():
                continue

            write_cursor.execute(
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

        write_conn.commit()

    except Exception as exc:
        print("EDGE WATCHDOG WRITE ERROR:", exc)

        if write_conn is not None:
            try:
                write_conn.rollback()
            except Exception:
                pass

    finally:
        if write_cursor is not None:
            try:
                write_cursor.close()
            except Exception:
                pass

        if write_conn is not None:
            try:
                write_conn.close()
            except Exception:
                pass


def _edge_watchdog_loop():
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

        # -------------------------------------------------
        # FLOW AUTHENTICATION GUARD
        # -------------------------------------------------
        # socket_manager.py needs the real Flask app before
        # it can install its before_request authentication
        # guard.  The SocketIO object is created before app.py
        # calls run(), so provide the app here and register the
        # guard before Flask starts serving requests.
        # -------------------------------------------------
        try:
            self.app = app

            from socket_manager import init_socketio

            init_socketio(self)

            print(
                "FLOW AUTHENTICATION GUARD REGISTERED"
            )

        except Exception as exc:
            print(
                "FLOW AUTHENTICATION GUARD ERROR:",
                exc
            )

        # -------------------------------------------------
        # LOGIN REQUEST MUST ALWAYS BE AUTHENTICATED FROM
        # THE SUBMITTED CREDENTIALS.
        #
        # If an old session already exists, /login POST must
        # not be short-circuited by app.py's "already logged
        # in" check. Clear only the session for a POST to
        # /login, then let the normal login route validate
        # username/password and Roles/Users rules.
        # -------------------------------------------------
        try:
            @app.before_request
            def _reset_session_for_login_post():
                from flask import request, session

                if (
                    request.method == "POST"
                    and request.path == "/login"
                ):
                    session.clear()

        except Exception as exc:
            print(
                "LOGIN SESSION RESET REGISTRATION ERROR:",
                exc
            )

        _start_edge_watchdog()

        return super().run(
            app,
            *args,
            **kwargs
        )


socketio = SCADAFlowSocketIO()
