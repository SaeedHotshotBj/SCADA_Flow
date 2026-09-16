# =====================================================
# SCADA_FLOW SOCKET MANAGER
# DASHBOARD REALTIME DATA + FLOW AUTHENTICATION
# =====================================================

import json

from flask import request, redirect, url_for, session
from flask_socketio import join_room
from werkzeug.security import check_password_hash

from database import get_company_flow, get_connection


socketio_instance = None
_auth_guard_registered = False
_socket_handlers_registered = False


def _role_room(company_id, role):
    try:
        company_id = int(company_id)
    except (TypeError, ValueError):
        return None
    normalized = str(role or "").strip().lower()
    if not normalized or normalized == "master":
        return None
    return f"company:{company_id}:role:{normalized}"


def _roles(value):
    if isinstance(value, (list, tuple, set)):
        return {str(item).strip().lower() for item in value if str(item).strip()}
    return {
        item.strip().lower()
        for item in str(value or "").replace(";", ",").split(",")
        if item.strip()
    }


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
# SOCKET.IO COMPANY + ROLE ISOLATION
# =====================================================

def _install_socket_handlers(socketio):
    global _socket_handlers_registered

    if _socket_handlers_registered:
        return True

    @socketio.on("connect")
    def _dashboard_socket_connect():
        """Join authenticated company and, for normal users, role rooms."""
        user_role = str(session.get("role", "")).strip().lower()
        is_master = user_role == "master"

        session_company = session.get("company_id")
        requested_company = request.args.get("company_id", type=int)

        if is_master:
            company_id = requested_company or session.get("selected_company_id")
        else:
            company_id = session_company

        try:
            company_id = int(company_id) if company_id is not None else None
        except (TypeError, ValueError):
            company_id = None

        if company_id is not None:
            room = f"company:{company_id}"
            join_room(room)
            print("SOCKET JOINED COMPANY ROOM:", room)

            role_room = _role_room(company_id, user_role)
            if role_room:
                join_room(role_room)
                print("SOCKET JOINED ROLE ROOM:", role_room)
        else:
            print("SOCKET CONNECTED WITHOUT COMPANY ID")

        return True

    _socket_handlers_registered = True
    print("SOCKET COMPANY + ROLE ROOM HANDLERS REGISTERED")
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

def _filtered_dashboard_payload(data, allowed_role=None):
    """Return only Flow-authorized realtime dashboard fields for one role."""
    if not isinstance(data, dict):
        return data

    result = dict(data)
    tag_values = data.get("TagValues", [])
    original_tags = data.get("Tags", {}) or {}

    if not isinstance(tag_values, list):
        return result

    filtered = []
    tags = {}
    normalized_role = str(allowed_role or "").strip().lower()

    for item in tag_values:
        if not isinstance(item, dict):
            continue
        allowed = _roles(item.get("AllowedRoles"))
        if allowed and normalized_role not in allowed and normalized_role != "master":
            continue
        clean = dict(item)
        clean.pop("AllowedRoles", None)
        filtered.append(clean)
        tag = clean.get("TagName", clean.get("tag"))
        if tag is not None:
            tags[str(tag)] = clean.get("Value", clean.get("value"))

    result["TagValues"] = filtered
    result["Tags"] = tags if tag_values else original_tags
    return result


def send_dashboard_data(data):
    if socketio_instance is None:
        print("SOCKET.IO NOT INITIALIZED")
        return

    try:
        company_id = data.get("CompanyID") if isinstance(data, dict) else None
        timestamp = data.get("Timestamp") if isinstance(data, dict) else None
        tag_values = data.get("TagValues", []) if isinstance(data, dict) else []

        if isinstance(tag_values, list) and tag_values:
            unrestricted = []
            restricted_roles = set()
            for item in tag_values:
                if not isinstance(item, dict):
                    continue
                allowed = _roles(item.get("AllowedRoles"))
                if allowed:
                    restricted_roles.update(allowed)
                else:
                    unrestricted.append(item)

            base_room = f"company:{company_id}" if company_id is not None else None

            if base_room and unrestricted:
                payload = _filtered_dashboard_payload(data, allowed_role=None)
                socketio_instance.emit("tag_update", payload, room=base_room)

            roles_to_emit = sorted(role for role in restricted_roles if role != "master")
            for role in roles_to_emit:
                role_room = _role_room(company_id, role) if company_id is not None else None
                if not role_room:
                    continue
                payload = _filtered_dashboard_payload(data, allowed_role=role)
                socketio_instance.emit("tag_update", payload, room=role_room)

            # Preserve PLC-aware per-tag updates while enforcing exactly the
            # same Flow-derived role policy as the aggregate payload.
            for item in tag_values:
                if not isinstance(item, dict):
                    continue
                tag = item.get("TagName", item.get("tag"))
                value = item.get("Value", item.get("value"))
                if tag is None:
                    continue
                allowed = _roles(item.get("AllowedRoles"))
                payload = {
                    "CompanyID": company_id,
                    "PLC_ID": item.get("PLC_ID", item.get("plc_id")),
                    "Tag": tag,
                    "Value": value,
                    "Timestamp": item.get("Timestamp", timestamp),
                    "title": item.get("title", tag),
                    "unit": item.get("unit", ""),
                }
                if not allowed:
                    if base_room:
                        socketio_instance.emit("tag_update", payload, room=base_room)
                    else:
                        socketio_instance.emit("tag_update", payload)
                    continue
                for role in sorted(allowed):
                    role_room = _role_room(company_id, role) if company_id is not None else None
                    if role_room:
                        socketio_instance.emit("tag_update", payload, room=role_room)

        else:
            tags = data.get("Tags", {}) if isinstance(data, dict) else {}
            room = f"company:{company_id}" if company_id is not None else None
            if room is not None:
                socketio_instance.emit("tag_update", data, room=room)
            else:
                socketio_instance.emit("tag_update", data)
            if isinstance(tags, dict):
                for tag, value in tags.items():
                    payload = {
                        "CompanyID": company_id,
                        "PLC_ID": data.get("PLC_ID"),
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


__all__ = [
    "init_socketio",
    "send_dashboard_data",
    "send_tag_data",
]
