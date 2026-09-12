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
    return flow.get("drawflow", {}).get("Home", {}).get("data", {})


def _read_roles(nodes):
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
                users.append({"username": username, "role": role, "password": password, "enabled": enabled})
    return users


def _read_engaged_roles(nodes):
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
            role = str(item.get("role", "")).strip() if isinstance(item, dict) else str(item).strip()
            if role:
                roles.append(role.lower())
    return found_node, set(roles)


def _validate_flow_login(company_id, username, password):
    if company_id is None or not username or not password:
        return None
    nodes = _flow_nodes(company_id)
    if not nodes:
        print("FLOW LOGIN REJECTED: NO FLOW", company_id)
        return None
    username_key = username.strip().lower()
    matched = next((user for user in _read_roles(nodes) if user["username"].lower() == username_key), None)
    if matched is None or not matched["enabled"]:
        return None
    stored_password = matched["password"]
    password_ok = password == stored_password
    if not password_ok:
        try:
            password_ok = check_password_hash(stored_password, password)
        except Exception:
            password_ok = False
    if not password_ok:
        return None
    found_engaged, engaged_roles = _read_engaged_roles(nodes)
    if not found_engaged or matched["role"].strip().lower() not in engaged_roles:
        return None
    return matched


def _is_global_master_login(username):
    username_key = str(username or "").strip()
    if not username_key:
        return False
    conn = cursor = None
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute("""
            SELECT UserID FROM Users
            WHERE Username=? AND CompanyID IS NULL AND LOWER(Role)='master' AND Enabled=1
            LIMIT 1
        """, (username_key,))
        return cursor.fetchone() is not None
    except Exception as exc:
        print("MASTER LOGIN DETECTION ERROR:", exc)
        return False
    finally:
        if cursor is not None:
            cursor.close()
        if conn is not None:
            conn.close()


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
        user = _validate_flow_login(company_id, username, password)
        if user is None:
            return redirect(url_for("login", auth_error=("Invalid username, password, or the user's role is not enabled in RolesEngaged.")))
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
        company_id = request.args.get("company_id", type=int)
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
    try:
        flask_app = getattr(socketio, "app", None)
        if flask_app is not None:
            from flow_company_routes import flow_company_bp
            if "flow_company" not in flask_app.blueprints:
                flask_app.register_blueprint(flow_company_bp)
                print("FLOW COMPANY BLUEPRINT REGISTERED")
    except Exception as exc:
        print("FLOW COMPANY BLUEPRINT REGISTRATION ERROR:", exc)


# =====================================================
# SEND DASHBOARD DATA
# =====================================================

def send_dashboard_data(data):
    if socketio_instance is None:
        print("SOCKET.IO NOT INITIALIZED")
        return

    try:
        company_id = data.get("CompanyID") if isinstance(data, dict) else None
        room = f"company:{company_id}" if company_id is not None else None
        tags = data.get("Tags", {}) if isinstance(data, dict) else {}
        tag_values = data.get("TagValues", []) if isinstance(data, dict) else []
        timestamp = data.get("Timestamp") if isinstance(data, dict) else None

        if room is not None:
            socketio_instance.emit("tag_update", data, room=room)
        else:
            socketio_instance.emit("tag_update", data)

        # Prefer the PLC-aware TagValues list so identical tag names from
        # different PLCs remain distinguishable in the browser.
        if isinstance(tag_values, list) and tag_values:
            for item in tag_values:
                if not isinstance(item, dict):
                    continue
                tag = item.get("TagName", item.get("tag"))
                value = item.get("Value", item.get("value"))
                if tag is None:
                    continue
                payload = {
                    "CompanyID": company_id,
                    "PLC_ID": item.get("PLC_ID", item.get("plc_id")),
                    "Tag": tag,
                    "Value": value,
                    "Timestamp": item.get("Timestamp", timestamp),
                    "title": item.get("title", tag),
                    "unit": item.get("unit", ""),
                }
                if room is not None:
                    socketio_instance.emit("tag_update", payload, room=room)
                else:
                    socketio_instance.emit("tag_update", payload)
        elif isinstance(tags, dict):
            for tag, value in tags.items():
                payload = {"CompanyID": company_id, "PLC_ID": data.get("PLC_ID"), "Tag": tag, "Value": value, "Timestamp": timestamp}
                if room is not None:
                    socketio_instance.emit("tag_update", payload, room=room)
                else:
                    socketio_instance.emit("tag_update", payload)

        print("SOCKET DATA SENT", "COMPANY", company_id)
    except Exception as e:
        print("SOCKET SEND ERROR:", e)


def send_tag_data(tags, online=True, company_id=None):
    send_dashboard_data({"Online": online, "Tags": tags, "CompanyID": company_id})
