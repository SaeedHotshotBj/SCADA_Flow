from flask_socketio import SocketIO

import threading
import time

from services.hardcoded_master import handle_master_login


# =====================================================
# EDGE OFFLINE WATCHDOG
# =====================================================
# The watchdog monitors Edge heartbeat only.
# IMPORTANT: it must NOT write synthetic zero values into
# PLC_Data. A temporary Edge communication gap must never
# overwrite the last valid dashboard value with zero.
# Real PLC zero values are handled by PLCReader's zero debounce.
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
        active_keys = set()

        for row in rows:
            company_id = row["CompanyID"]
            tag_name = row["TagName"]
            last_edge_id = row["LastEdgeID"]

            if not tag_name or last_edge_id is None:
                continue

            key = (company_id, str(tag_name).strip().lower())
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

            if now_monotonic - float(state["last_seen"]) > EDGE_OFFLINE_TIMEOUT:
                print(
                    "EDGE WATCHDOG: stale tag; preserving last value:",
                    company_id,
                    tag_name,
                    "age=",
                    round(now_monotonic - float(state["last_seen"]), 2),
                    "seconds",
                )

        for key in list(_edge_watch_state.keys()):
            if key not in active_keys:
                del _edge_watch_state[key]

    except Exception as exc:
        print("EDGE WATCHDOG ERROR:", exc)

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
            "seconds - ZERO WRITE DISABLED",
        )


class SCADAFlowSocketIO(SocketIO):

    def run(self, app, *args, **kwargs):
        try:
            from flow_company_routes import register_flow_company_routes
            register_flow_company_routes(app)
            print("FLOW COMPANY ROUTES REGISTERED")
        except Exception as exc:
            print("FLOW COMPANY ROUTE REGISTRATION ERROR:", exc)

        try:
            self.app = app
            from socket_manager import init_socketio
            init_socketio(self)
            print("FLOW AUTHENTICATION GUARD REGISTERED")
        except Exception as exc:
            print("FLOW AUTHENTICATION GUARD ERROR:", exc)

        try:
            handle_master_login(app)
            print("HARDCODED MASTER LOGIN REGISTERED")
        except Exception as exc:
            print("HARDCODED MASTER LOGIN REGISTRATION ERROR:", exc)

        try:
            @app.before_request
            def _reset_session_for_login_post():
                from flask import request, session
                if request.method == "POST" and request.path == "/login":
                    session.clear()
        except Exception as exc:
            print("LOGIN SESSION RESET REGISTRATION ERROR:", exc)

        _start_edge_watchdog()

        return super().run(app, *args, **kwargs)


socketio = SCADAFlowSocketIO()
