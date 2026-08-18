from flask_socketio import SocketIO

import threading
import time

from flask import request, redirect, url_for, session

from services.hardcoded_master import (
    is_hardcoded_master_credentials,
    set_hardcoded_master_session,
    is_hardcoded_master_session,
)


# =====================================================
# EDGE OFFLINE WATCHDOG
# =====================================================

EDGE_OFFLINE_TIMEOUT = 2.0
EDGE_WATCHDOG_INTERVAL = 0.5
_edge_watchdog_started = False
_edge_watchdog_lock = threading.Lock()
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
            SELECT CompanyID, TagName, MAX(ID) AS LastEdgeID
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
        threading.Thread(
            target=_edge_watchdog_loop,
            name="SCADA_FLOW_EDGE_WATCHDOG",
            daemon=True,
        ).start()

        print(
            "EDGE WATCHDOG STARTED - TIMEOUT:",
            EDGE_OFFLINE_TIMEOUT,
            "seconds - ZERO WRITE DISABLED",
        )


def _install_hardcoded_master_auth(app):
    """
    Keep the hardcoded master completely outside Users/RolesEngaged.

    The normal login route in app.py remains unchanged for company users.
    This before_request handler catches only master/1234 and creates a
    dedicated session before the normal /login route can query Users.
    """

    @app.before_request
    def _hardcoded_master_login():
        if request.path != "/login" or request.method != "POST":
            return None

        username = request.form.get("username", "")
        password = request.form.get("password", "")

        if not is_hardcoded_master_credentials(username, password):
            return None

        set_hardcoded_master_session()

        print("HARDCODED MASTER LOGIN SUCCESS")
        print("SESSION AFTER MASTER LOGIN:", dict(session))

        return redirect(url_for("master_companies"))


def _patch_app_session_validation():
    """
    Extend app.py's existing is_logged_in() without replacing the normal
    database authentication used by all other users.
    """

    try:
        import app as app_module
        original_is_logged_in = app_module.is_logged_in

        def _is_logged_in_with_master():
            if is_hardcoded_master_session():
                return True
            return original_is_logged_in()

        app_module.is_logged_in = _is_logged_in_with_master
        print("HARDCODED MASTER SESSION VALIDATION ENABLED")

    except Exception as exc:
        print("HARDCODED MASTER SESSION PATCH ERROR:", exc)


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

        _install_hardcoded_master_auth(app)
        _patch_app_session_validation()
        _start_edge_watchdog()

        return super().run(app, *args, **kwargs)


socketio = SCADAFlowSocketIO()
