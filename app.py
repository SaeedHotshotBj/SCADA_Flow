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

from services.management_service import (
    ensure_management_flow,
    get_management_config,
    init_management_database,
)
from services.management_runner import execute_management_flow
from services.management_access import allowed as management_access_allowed

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
init_management_database()


# =====================================================
# EDGE TIMEOUT WORKER
# Start only after Flask app creation and database initialization.
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


def is_master():
    return str(session.get("role", "")).strip().lower() == "master"


def get_session_company_id():
    company_id = session.get("company_id")
    if company_id is None:
        return None
    try:
        return int(company_id)
    except (TypeError, ValueError):
        return None


def login_required(view_func):
    @wraps(view_func)
    def wrapped(*args, **kwargs):
        if not is_logged_in():
            return redirect(url_for("login", next=request.path))
        return view_func(*args, **kwargs)
    return wrapped


def company_required(view_func):
    @wraps(view_func)
    def wrapped(*args, **kwargs):
        if not is_logged_in():
            return redirect(url_for("login", next=request.path))

        if not is_master():
            company_id = get_session_company_id()
            if company_id is None:
                return render_template("access_denied.html"), 403

        return view_func(*args, **kwargs)
    return wrapped


def get_request_company_id():
    if not is_logged_in():
        return None

    if not is_master():
        return get_session_company_id()

    company_id = request.args.get("company_id", type=int)

    if company_id is None:
        company_id = request.headers.get("X-Company-ID", type=int)

    return company_id


# =====================================================
# LOGIN COMPANY LIST
# =====================================================

def _get_companies_for_login():
    conn = None
    cursor = None
    try:
        conn = get_connection()
        cursor = conn.cursor()

        cursor.execute(
            """
            SELECT CompanyID, CompanyName
            FROM Companies
            ORDER BY CompanyName
            """
        )

        rows = cursor.fetchall()

        return [
            {
                "CompanyID": row["CompanyID"],
                "CompanyName": row["CompanyName"],
            }
            for row in rows
        ]

    except Exception as e:
        print("LOGIN COMPANY LOAD ERROR:", e)
        return []

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


# =====================================================
# FLOW ROLE ACCESS CONTROL
# =====================================================

def _get_company_flow_nodes(company_id):
    if company_id is None:
        return {}

    flow = get_flow_data(company_id)
    if not flow:
        return {}

    try:
        return (
            flow.get("drawflow", {})
            .get("Home", {})
            .get("data", {})
        )
    except Exception as e:
        print("FLOW NODE LOAD ERROR:", e)
        return {}


def _get_flow_roles(company_id):
    nodes = _get_company_flow_nodes(company_id)
    roles = []

    for node_id, node in nodes.items():
        if node.get("name") != "Roles":
            continue

        role_list = node.get("data", {}).get("roles", [])
        if not isinstance(role_list, list):
            continue

        for item in role_list:
            if not isinstance(item, dict):
                continue

            role = str(item.get("role", item.get("name", ""))).strip()
            if not role:
                continue

            roles.append({
                "node_id": str(node_id),
                "role": role,
                "username": str(item.get("username", "")).strip(),
                "password": item.get("password", ""),
                "enabled": bool(item.get("enabled", True)),
            })

    return roles


def _get_roles_engaged_for_page(company_id, page_node_name):
    nodes = _get_company_flow_nodes(company_id)
    if not nodes:
        return []

    target_node_ids = [
        str(node_id)
        for node_id, node in nodes.items()
        if node.get("name") == page_node_name
    ]

    if not target_node_ids:
        return []

    engaged_nodes = []

    for node_id, node in nodes.items():
        if node.get("name") != "RolesEngaged":
            continue

        outputs = node.get("outputs", {})
        connected = False

        if isinstance(outputs, dict):
            for output in outputs.values():
                if not isinstance(output, dict):
                    continue

                connections = output.get("connections", [])
                if not isinstance(connections, list):
                    continue

                for connection in connections:
                    target_id = str(connection.get("node", ""))
                    if target_id in target_node_ids:
                        connected = True
                        break

                if connected:
                    break

        if connected:
            engaged_nodes.append((str(node_id), node))

    selected_roles = []

    for engaged_id, engaged_node in engaged_nodes:
        roles = engaged_node.get("data", {}).get("roles", [])

        if isinstance(roles, str):
            roles = [roles]

        if not isinstance(roles, list):
            continue

        for role in roles:
            if isinstance(role, dict):
                role_name = str(role.get("role", "")).strip()
            else:
                role_name = str(role).strip()

            if role_name:
                selected_roles.append(role_name)

    result = []
    for role in selected_roles:
        if role not in result:
            result.append(role)

    return result


def _user_has_flow_access(company_id, page_node_name):
    if is_master():
        return True

    if company_id is None:
        return False

    user_role = str(session.get("role", "")).strip()
    if not user_role:
        return False

    allowed_roles = _get_roles_engaged_for_page(company_id, page_node_name)

    if not allowed_roles:
        return True

    return any(
        user_role.lower() == str(role).lower()
        for role in allowed_roles
    )


def flow_role_required(page_node_name):
    def decorator(view_func):
        @wraps(view_func)
        def wrapped(*args, **kwargs):
            if not is_logged_in():
                return redirect(url_for("login", next=request.path))

            if is_master():
                return view_func(*args, **kwargs)

            company_id = get_session_company_id()
            if company_id is None:
                return jsonify({
                    "status": "error",
                    "message": "No company assigned"
                }), 403

            if not _user_has_flow_access(company_id, page_node_name):
                return render_template("access_denied.html"), 403

            return view_func(*args, **kwargs)

        return wrapped

    return decorator
