from flask_socketio import SocketIO

import json
import threading
import time
from datetime import datetime


# =====================================================
# EDGE OFFLINE WATCHDOG
# =====================================================
# Dashboard timeout is configured by DashboardOutput in
# the company's Flow Designer. This watchdog only records
# the last successful Edge request received by the server.
# It never writes synthetic values into the historian.
# =====================================================

EDGE_OFFLINE_TIMEOUT = 2.0
EDGE_WATCHDOG_INTERVAL = 0.5
_edge_watchdog_started = False
_edge_watchdog_lock = threading.Lock()

# (CompanyID, PLC_ID) -> server monotonic receive time
_edge_last_seen = {}


def _record_edge_request(response):
    """Record the server-side receive time of a successful Edge request."""

    try:
        payload = response.get_json(silent=True)
        if not isinstance(payload, dict):
            return

        company_id = payload.get("CompanyID")
        plc_id = payload.get("PLC_ID")

        if company_id is None or plc_id is None:
            return

        _edge_last_seen[(int(company_id), str(plc_id))] = time.monotonic()

    except Exception as exc:
        print("EDGE RECEIVE STATE ERROR:", exc)


def _get_dashboard_timeout(company_id):
    """Read Edge Timeout from the company's DashboardOutput node."""

    default_timeout = 10.0

    try:
        from database import get_company_flow

        flow_json = get_company_flow(company_id)
        if not flow_json:
            return default_timeout

        flow = json.loads(flow_json) if isinstance(flow_json, str) else flow_json
        nodes = (
            flow.get("drawflow", {})
            .get("Home", {})
            .get("data", {})
        )

        for node in nodes.values():
            if not isinstance(node, dict) or node.get("name") != "DashboardOutput":
                continue

            data = node.get("data", {}) or {}
            config = data.get("config", data) or {}

            try:
                timeout = float(config.get("timeout", default_timeout))
            except (TypeError, ValueError):
                timeout = default_timeout

            return max(0.0, timeout)

    except Exception as exc:
        print("DASHBOARD TIMEOUT CONFIG ERROR:", exc)

    return default_timeout


def _parse_timestamp(value):
    if value is None:
        return None

    try:
        text = str(value).strip().replace("Z", "+00:00")
        dt = datetime.fromisoformat(text)

        if dt.tzinfo is not None:
            dt = dt.astimezone().replace(tzinfo=None)

        return dt

    except Exception:
        return None


def _latest_edge_plc_for_tag(conn, company_id, tag):
    try:
        row = conn.execute(
            """
            SELECT PLC_ID, Timestamp
            FROM TagHistory
            WHERE CompanyID = ?
              AND LOWER(TagName) = LOWER(?)
            ORDER BY ID DESC
            LIMIT 1
            """,
            (company_id, tag),
        ).fetchone()

        if not row:
            return None, None

        return row["PLC_ID"], row["Timestamp"]

    except Exception as exc:
        print("EDGE TAG STATE ERROR:", exc)
        return None, None


def _apply_dashboard_edge_timeout(response):
    """Zero stale Edge values only after DashboardOutput's configured timeout.

    This changes only the live dashboard response. No synthetic zero is written
    to PLC_Data or TagHistory, so historian/trend data remains real.
    """

    if response.status_code != 200:
        return response

    try:
        payload = response.get_json(silent=True)
        if not isinstance(payload, dict):
            return response

        company_id = None

        try:
            from flask import request, session

            if str(session.get("role", "")).strip().lower() == "master":
                company_id = request.args.get("company_id", type=int)
                if company_id is None:
                    company_id = session.get("selected_company_id")
            else:
                company_id = session.get("company_id")
        except Exception:
            company_id = None

        if company_id is None:
            return response

        try:
            company_id = int(company_id)
        except (TypeError, ValueError):
            return response

        timeout = _get_dashboard_timeout(company_id)
        tags = payload.get("Tags", {}) or {}

        if not isinstance(tags, dict) or not tags:
            return response

        conn = None
        try:
            from database import get_connection

            conn = get_connection()

            now_monotonic = time.monotonic()
            any_fresh = False
            stale_count = 0

            for tag in list(tags.keys()):
                plc_id, edge_timestamp = _latest_edge_plc_for_tag(
                    conn,
                    company_id,
                    tag,
                )

                if plc_id is None:
                    continue

                last_seen = _edge_last_seen.get(
                    (company_id, str(plc_id))
                )

                # After a server restart there may be no in-memory receive
                # time yet. Fall back to the actual Edge timestamp once.
                if last_seen is None:
                    parsed = _parse_timestamp(edge_timestamp)
                    if parsed is not None:
                        age = max(
                            0.0,
                            (datetime.now() - parsed).total_seconds(),
                        )
                    else:
                        age = 0.0
                else:
                    age = max(
                        0.0,
                        now_monotonic - last_seen,
                    )

                if age >= timeout:
                    tags[tag] = 0
                    stale_count += 1
                else:
                    any_fresh = True

            payload["Tags"] = tags
            payload["Online"] = any_fresh

            if stale_count:
                print(
                    "DASHBOARD EDGE TIMEOUT:",
                    "Company:", company_id,
                    "Timeout:", timeout,
                    "Stale Tags:", stale_count,
                )

            response.set_data(
                json.dumps(payload, ensure_ascii=False),
            )
            response.content_type = "application/json"

        finally:
            if conn is not None:
                try:
                    conn.close()
                except Exception:
                    pass

    except Exception as exc:
        print("DASHBOARD EDGE TIMEOUT ERROR:", exc)

    return response


def _watchdog_once():
    # Keep this lightweight. The actual dashboard timeout is configured in
    # DashboardOutput and evaluated when /dashboard/latest is requested.
    now = time.monotonic()

    for key, last_seen in list(_edge_last_seen.items()):
        if now - last_seen > 86400:
            _edge_last_seen.pop(key, None)


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

        print("EDGE WATCHDOG STARTED")


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
            @app.before_request
            def _reset_session_for_login_post():
                from flask import request, session
                if request.method == "POST" and request.path == "/login":
                    session.clear()
        except Exception as exc:
            print("LOGIN SESSION RESET REGISTRATION ERROR:", exc)

        # -------------------------------------------------
        # MASTER SESSION NORMALIZATION
        # -------------------------------------------------
        try:
            @app.before_request
            def _normalize_master_session():
                from flask import session

                if (
                    str(session.get("role", "")).strip().lower() == "master"
                    and session.get("user_id") is not None
                    and session.get("auth_login_time") is None
                ):
                    session["auth_login_time"] = time.time()
                    session.modified = True

        except Exception as exc:
            print("MASTER SESSION NORMALIZATION REGISTRATION ERROR:", exc)

        # -------------------------------------------------
        # EDGE / DASHBOARD TIMEOUT
        # -------------------------------------------------
        try:
            @app.after_request
            def _edge_dashboard_timeout(response):
                from flask import request

                if request.path == "/api/data" and response.status_code == 200:
                    _record_edge_request(response)

                if request.path == "/dashboard/latest":
                    response = _apply_dashboard_edge_timeout(response)

                return response

        except Exception as exc:
            print("EDGE DASHBOARD TIMEOUT REGISTRATION ERROR:", exc)

        _start_edge_watchdog()

        return super().run(app, *args, **kwargs)


socketio = SCADAFlowSocketIO()
