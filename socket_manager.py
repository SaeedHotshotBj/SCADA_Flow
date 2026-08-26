# =====================================================
# SCADA_FLOW SOCKET MANAGER
# DASHBOARD REALTIME DATA + FLOW AUTHENTICATION
# =====================================================

import json

from flask import request, redirect, url_for
from flask_socketio import join_room
from werkzeug.security import check_password_hash

from database import get_company_flow, get_connection


socketio_instance = None
_auth_guard_registered = False
_socket_handlers_registered = False


# =====================================================
# FLOW AUTHENTICATION
# =====================================================

def _flow_nodes(company_id):
    if company_id is None:
        return {}

    flow_json = get_company_flow(company_id)

    if not flow_json:
        return {}

    try:
        flow = json.loads(flow_json)
    except Exception as exc:
        print("FLOW AUTH JSON ERROR:", exc)
        return {}

    return (
        flow.get("drawflow", {})
        .get("Home", {})
        .get("data", {})
    )


def _read_roles(nodes):
    """Roles node is the only place where company users are defined."""
    users = []

    for node in nodes.values():
        if not isinstance(node, dict) or node.get("name") != "Roles":
            continue

        items = node.get("data", {}).get("roles", [])
        if not isinstance(items, list):
            continue

        for item in items:
            if not isinstance(item, dict):
                continue

            username = str(item.get("username", "")).strip()
            role = str(item.get("role", item.get("name", ""))).strip()
            password = str(item.get("password", ""))
            enabled = bool(item.get("enabled", True))

            if username and role:
                users.append({
                    "username": username,
                    "role": role,
                    "password": password,
                    "enabled": enabled,
                })

    return users


def _read_engaged_roles(nodes):
    """RolesEngaged is the only source for company-user login permission."""
    roles = []
    found_node = False

    for node in nodes.values():
        if not isinstance(node, dict) or node.get("name") != "RolesEngaged":
            continue

        found_node = True
        items = node.get("data", {}).get("roles", [])

        if isinstance(items, str):
            items = [items]

        if not isinstance(items, list):
            continue

        for item in items:
            if isinstance(item, dict):
                role = str(item.get("role", "")).strip()
            else:
                role = str(item).strip()

            if role:
                roles.append(role.lower())

    return found_node, set(roles)


def _validate_flow_login(company_id, username, password):
    """
    Authentication source of truth for normal company users:

        Roles        -> defines username/password/role
        RolesEngaged -> explicitly permits the role to log in

    The global Master account is intentionally NOT checked here.
    """

    if company_id is None or not username or not password:
        return None

    nodes = _flow_nodes(company_id)

    if not nodes:
        print("FLOW LOGIN REJECTED: NO FLOW", company_id)
        return None

    username_key = username.strip().lower()
    matched = None

    for user in _read_roles(nodes):
        if user["username"].lower() == username_key:
            matched = user
            break

    if matched is None:
        print("FLOW LOGIN REJECTED: USER NOT IN ROLES", username)
        return None

    if not matched["enabled"]:
        print("FLOW LOGIN REJECTED: USER DISABLED", username)
        return None

    stored_password = matched["password"]
    password_ok = password == stored_password

    if not password_ok:
        try:
            password_ok = check_password_hash(stored_password, password)
        except Exception:
            password_ok = False

    if not password_ok:
        print("FLOW LOGIN REJECTED: WRONG PASSWORD", username)
        return None

    found_engaged, engaged_roles = _read_engaged_roles(nodes)

    # There must be a RolesEngaged node and the user's role must
    # explicitly be selected in it. No RolesEngaged = no company login.
    if not found_engaged:
        print("FLOW LOGIN REJECTED: NO ROLES ENGAGED", username)
        return None

    if matched["role"].strip().lower() not in engaged_roles:
        print(
            "FLOW LOGIN REJECTED: ROLE NOT ENGAGED",
            username,
            matched["role"],
        )
        return None

    return matched


def _is_global_master_login(username):
    """
    Identify the global Master account without consulting Roles or
    RolesEngaged. Master is a system account and must remain outside
    company flow role configuration.

    Password validation is intentionally left to app.py.
    """

    username_key = str(username or "").strip()

    if not username_key:
        return False

    conn = None
    cursor = None

    try:
        conn = get_connection()
        cursor = conn.cursor()

        cursor.execute(
            """
            SELECT UserID
            FROM Users
            WHERE Username = ?
              AND CompanyID IS NULL
              AND LOWER(Role) = 'master'
              AND Enabled = 1
            LIMIT 1
            """,
            (username_key,),
        )

        return cursor.fetchone() is not None

    except Exception as exc:
        print("MASTER LOGIN DETECTION ERROR:", exc)
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


# =====================================================
# AUTHENTICATION GUARD
# =====================================================

def _install_authentication_guard(socketio):
    global _auth_guard_registered

    if _auth_guard_registered:
        return True

    flask_app = getattr(socketio, "app", None)

    if flask_app is None:
        print("FLOW AUTH GUARD: Flask app is not available yet")
        return False

    @flask_app.before_request
    def flow_authentication_guard():
        if request.path != "/login" or request.method != "POST":
            return None

        company_id = request.form.get("company_id", type=int)
        username = str(request.form.get("username", "")).strip()
        password = request.form.get("password", "")

        if _is_global_master_login(username):
            print("FLOW AUTH: MASTER BYPASS", username)
            return None

        if company_id is None:
            return None

        user = _validate_flow_login(
            company_id,
            username,
            password,
        )

        if user is None:
            return redirect(
                url_for(
                    "login",
                    auth_error=(
                        "Invalid username, password, or the user's role "
                        "is not enabled in RolesEngaged."
                    ),
                )
            )

        request.flow_authenticated_user = user
        return None

    _auth_guard_registered = True
    print("FLOW AUTHENTICATION GUARD REGISTERED")
    return True


# =====================================================
# SOCKET.IO COMPANY ISOLATION
# =====================================================

def _install_socket_handlers(socketio):
    global _socket_handlers_registered

    if _socket_handlers_registered:
        return True

    @socketio.on("connect")
    def _dashboard_socket_connect():
        """Put each browser socket into its authenticated company room."""
        company_id = request.args.get("company_id", type=int)

        # The normal dashboard socket uses the authenticated Flask session.
        if company_id is None:
            try:
                from flask import session
                company_id = session.get("company_id")
            except Exception:
                company_id = None

        if company_id is not None:
            room = f"company:{company_id}"
            join_room(room)
            print("SOCKET JOINED COMPANY ROOM:", room)
        else:
            print("SOCKET CONNECTED WITHOUT COMPANY ID")

        return True

    _socket_handlers_registered = True
    print("SOCKET COMPANY ROOM HANDLER REGISTERED")
    return True


# =====================================================
# INITIALIZE
# =====================================================

def init_socketio(socketio):
    global socketio_instance
    socketio_instance = socketio
    _install_authentication_guard(socketio)
    _install_socket_handlers(socketio)


# =====================================================
# SEND DASHBOARD DATA
# =====================================================

def send_dashboard_data(data):
    if socketio_instance is None:
        print("SOCKET.IO NOT INITIALIZED")
        return

    try:
        tags = data.get("Tags", {}) if isinstance(data, dict) else {}
        company_id = data.get("CompanyID") if isinstance(data, dict) else None
        timestamp = data.get("Timestamp") if isinstance(data, dict) else None
        room = f"company:{company_id}" if company_id is not None else None

        # Never broadcast company-specific data to every logged-in dashboard.
        # A CompanyID creates a private Socket.IO room for that company.
        if room is not None:
            socketio_instance.emit("tag_update", data, room=room)
        else:
            socketio_instance.emit("tag_update", data)

        # Trend page consumes a normalized per-tag live event.
        if isinstance(tags, dict):
            for tag, value in tags.items():
                payload = {
                    "CompanyID": company_id,
                    "Tag": tag,
                    "Value": value,
                    "Timestamp": timestamp,
                }
                if room is not None:
                    socketio_instance.emit("tag_update", payload, room=room)
                else:
                    socketio_instance.emit("tag_update", payload)

        print("SOCKET DATA SENT", "COMPANY", company_id)
    except Exception as e:
        print("SOCKET SEND ERROR:", e)


# =====================================================
# OPTIONAL MANUAL EMIT
# =====================================================

def send_tag_data(tags, online=True, company_id=None):
    send_dashboard_data({
        "Online": online,
        "Tags": tags,
        "CompanyID": company_id,
    })
