import json
import threading
import os
import time
from functools import wraps

from flask import (
    Flask,
    render_template,
    request,
    jsonify,
    session,
    redirect,
    url_for,
)

from werkzeug.security import (
    check_password_hash,
    generate_password_hash,
)

from extensions import socketio

from dashboard_data import get_flow_tags

from config import (
    FLASK_CONFIG,
    SOCKETIO_CONFIG,
    FLOW_CONFIG,
)

from flow_runner import FlowRunner

from flow_engine.node_registry import NODE_REGISTRY

from socket_manager import init_socketio

from services.dashboard_service import get_dashboard_widgets

from database import (
    get_connection,
    get_company_flow,
    get_trend_data,
    cleanup_old_trend_data,
    init_database,
    get_latest_tag_values,
)


# =====================================================
# FLASK APPLICATION
# =====================================================

app = Flask(__name__)


# =====================================================
# FLASK SESSION CONFIGURATION
# =====================================================

app.config["SECRET_KEY"] = FLASK_CONFIG["SECRET_KEY"]

app.config["SESSION_COOKIE_NAME"] = "scada_flow_session"
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"

app.config["SESSION_COOKIE_SECURE"] = False
app.config["PERMANENT_SESSION_LIFETIME"] = 86400


# =====================================================
# AUTHENTICATION / PAGE CACHE PROTECTION
# =====================================================

@app.before_request
def _reject_stale_logout_session():
    if request.path in ("/login", "/logout"):
        return None

    if request.path.startswith("/static/"):
        return None

    if request.cookies.get("scada_logout_marker") == "1":
        session.clear()
        session.modified = True
        return redirect(url_for("login", next=request.path))

    return None


@app.after_request
def _auth_no_cache(response):
    if request.path == "/login" and request.method == "POST" and session.get("user_id") is not None:
        response.delete_cookie("scada_logout_marker", path="/")

    protected_paths = (
        "/dashboard",
        "/dashboard/latest",
        "/home",
    )

    if (
        request.path in protected_paths
        or request.path.startswith("/dashboard/")
    ):
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"

    if request.path in ("/login", "/logout"):
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"

    return response


# =====================================================
# DATABASE INITIALIZATION
# =====================================================

init_database()


# =====================================================
# EDGE TIMEOUT WORKER
# Start only after Flask app creation and database initialization.
# This avoids the circular/early-startup path through services/__init__.py.
# =====================================================

try:
    from services.edge_timeout_service import start_worker as _start_edge_timeout_worker
    _start_edge_timeout_worker()
    print("EDGE TIMEOUT WORKER BOOTSTRAP OK: app.py after init_database")
except Exception as _edge_timeout_start_error:
    print("EDGE TIMEOUT WORKER BOOTSTRAP ERROR:", _edge_timeout_start_error)


# =====================================================
# SERVER-SIDE AUTH SESSION REVOCATION
# =====================================================

def _init_auth_session_revocations():
    conn = None
    cursor = None
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS AuthSessionRevocations (
                UserID INTEGER PRIMARY KEY,
                RevokedAt REAL NOT NULL
            )
            """
        )
        conn.commit()
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


_init_auth_session_revocations()


# =====================================================
# GLOBAL FLOW STATE
# =====================================================

flow_runner_instance = None
trend_runtime_tags = []


# =====================================================
# AUTHENTICATION HELPERS
# =====================================================

def is_logged_in():
    user_id = session.get("user_id")

    if user_id is None:
        return False

    login_time = session.get("auth_login_time")
    if login_time is None:
        session.clear()
        session.modified = True
        return False

    conn = None
    cursor = None

    try:
        conn = get_connection()
        cursor = conn.cursor()

        cursor.execute(
            """
            SELECT UserID, Username, CompanyID, Role, Enabled
            FROM Users
            WHERE UserID = ?
            LIMIT 1
            """,
            (user_id,)
        )

        user = cursor.fetchone()

        cursor.execute(
            "SELECT RevokedAt FROM AuthSessionRevocations WHERE UserID = ?",
            (user_id,)
        )
        revocation = cursor.fetchone()

        if revocation is not None and float(revocation["RevokedAt"]) >= float(login_time):
            session.clear()
            session.modified = True
            return False

        if not user or not user["Enabled"]:
            session.clear()
            session.modified = True
            return False

        if str(session.get("username", "")).strip() != str(user["Username"]).strip():
            session.clear()
            session.modified = True
            return False

        if str(session.get("role", "")).strip().lower() != str(user["Role"]).strip().lower():
            session.clear()
            session.modified = True
            return False

        session_company = session.get("company_id")
        db_company = user["CompanyID"]

        if session_company != db_company:
            try:
                if session_company is not None and int(session_company) == int(db_company):
                    pass
                else:
                    session.clear()
                    session.modified = True
                    return False
            except (TypeError, ValueError):
                session.clear()
                session.modified = True
                return False

        return True

    except Exception as e:
        print("SESSION VALIDATION ERROR:", e)
        session.clear()
        session.modified = True
        return False

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
