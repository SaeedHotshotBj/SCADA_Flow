# =====================================================
# SCADA_FLOW SOCKET MANAGER
# DASHBOARD REALTIME DATA
# =====================================================

import json

from flask import request
from werkzeug.security import check_password_hash, generate_password_hash

from database import get_connection, get_company_flow


socketio_instance = None
_auth_guard_registered = False


# =====================================================
# AUTHENTICATION GUARD
# =====================================================

def _validate_flow_login(company_id, username, password):
    """
    Validate company users directly from the saved flow.

    Roles is the source of truth for:
        username -> password -> role

    RolesEngaged is the source of truth for whether that role
    is allowed to authenticate.
    """

    if company_id is None or not username or not password:
        return None

    flow_json = get_company_flow(company_id)

    if not flow_json:
        return None

    try:
        flow = json.loads(flow_json)
    except Exception:
        return None

    nodes = (
        flow
        .get("drawflow", {})
        .get("Home", {})
        .get("data", {})
    )

    if not isinstance(nodes, dict):
        return None

    username_key = str(username).strip().lower()
    matched = None

    # -------------------------------------------------
    # ROLES = USER DEFINITIONS
    # -------------------------------------------------

    for node in nodes.values():

        if not isinstance(node, dict):
            continue

        if node.get("name") != "Roles":
            continue

        role_list = (
            node
            .get("data", {})
            .get("roles", [])
        )

        if not isinstance(role_list, list):
            continue

        for item in role_list:

            if not isinstance(item, dict):
                continue

            item_username = str(
                item.get("username", "")
            ).strip()

            if item_username.lower() != username_key:
                continue

            matched = {
                "username": item_username,
                "role": str(
                    item.get(
                        "role",
                        item.get("name", "")
                    )
                ).strip(),
                "password": str(
                    item.get("password", "")
                ),
                "enabled": bool(
                    item.get("enabled", True)
                )
            }
            break

        if matched is not None:
            break

    if not matched:
        return None

    if not matched["role"] or not matched["enabled"]:
        return None

    # -------------------------------------------------
    # PASSWORD = MUST MATCH ROLES BLOCK
    # -------------------------------------------------

    password_ok = (
        password == matched["password"]
    )

    if not password_ok:
        try:
            password_ok = check_password_hash(
                matched["password"],
                password
            )
        except Exception:
            password_ok = False

    if not password_ok:
        return None

    # -------------------------------------------------
    # ROLES ENGAGED = ALLOWED ROLES
    # -------------------------------------------------

    engaged_roles = []

    for node in nodes.values():

        if not isinstance(node, dict):
            continue

        if node.get("name") != "RolesEngaged":
            continue

        role_list = (
            node
            .get("data", {})
            .get("roles", [])
        )

        if isinstance(role_list, str):
            role_list = [role_list]

        if not isinstance(role_list, list):
            continue

        for item in role_list:

            if isinstance(item, dict):
                role_name = str(
                    item.get("role", "")
                ).strip()
            else:
                role_name = str(item).strip()

            if role_name:
                engaged_roles.append(role_name)

    # A company user can authenticate only if their role is
    # explicitly present in at least one RolesEngaged node.
    if not any(
        matched["role"].lower() == role.lower()
        for role in engaged_roles
    ):
        return None

    return matched


def _sync_authenticated_flow_user(company_id, user):
    """
    Synchronize the validated Roles user into Users so the
    existing application login/session code can continue to
    operate without changing unrelated routes.

    Authentication has already been decided from the flow.
    Users is only a session/application compatibility store.
    """

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
              AND CompanyID = ?
            LIMIT 1
            """,
            (
                user["username"],
                company_id
            )
        )

        existing = cursor.fetchone()
        password_hash = generate_password_hash(
            user["password"]
        )

        if existing:
            cursor.execute(
                """
                UPDATE Users
                SET
                    PasswordHash = ?,
                    Role = ?,
                    Enabled = 1
                WHERE UserID = ?
                """,
                (
                    password_hash,
                    user["role"],
                    existing["UserID"]
                )
            )
        else:
            cursor.execute(
                """
                INSERT INTO Users
                (
                    Username,
                    PasswordHash,
                    CompanyID,
                    Role,
                    Enabled
                )
                VALUES
                (
                    ?,
                    ?,
                    ?,
                    ?,
                    1
                )
                """,
                (
                    user["username"],
                    password_hash,
                    company_id,
                    user["role"]
                )
            )

        conn.commit()

    except Exception as e:
        if conn is not None:
            try:
                conn.rollback()
            except Exception:
                pass
        print("FLOW USER SYNC ERROR:", e)

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


def _install_authentication_guard(socketio):
    """
    Install the authentication guard after the Flask app has
    been created and the /login route has been registered.
    """

    global _auth_guard_registered

    if _auth_guard_registered:
        return

    flask_app = getattr(socketio, "app", None)

    if flask_app is None:
        print("AUTH GUARD: Flask app not available")
        return

    @flask_app.before_request
    def flow_authentication_guard():

        # Only intercept company login submissions.
        if request.path != "/login":
            return None

        if request.method != "POST":
            return None

        company_id = request.form.get(
            "company_id",
            type=int
        )

        username = str(
            request.form.get("username", "")
        ).strip()

        password = request.form.get(
            "password",
            ""
        )

        # Master is global and remains handled by the existing
        # dedicated master account in Users.
        if company_id is None:
            return None

        validated_user = _validate_flow_login(
            company_id,
            username,
            password
        )

        if validated_user is None:
            print(
                "FLOW LOGIN REJECTED:",
                username,
                "COMPANY:",
                company_id
            )
            return (
                "Invalid company, username, password, or the "
                "user role is not enabled in RolesEngaged.",
                401
            )

        # Keep the existing login route/session behavior working,
        # but make sure its Users record exactly matches the flow.
        _sync_authenticated_flow_user(
            company_id,
            validated_user
        )

        print(
            "FLOW LOGIN ACCEPTED:",
            validated_user["username"],
            "ROLE:",
            validated_user["role"],
            "COMPANY:",
            company_id
        )

        return None

    _auth_guard_registered = True


# =====================================================
# INITIALIZE
# =====================================================

def init_socketio(socketio):

    global socketio_instance

    # Keep the SocketIO instance for realtime emits.
    # Flask routes are registered by SCADAFlowSocketIO.run()
    # after the real Flask app object exists.
    socketio_instance = socketio

    # app.py calls this after the Flask login route exists.
    _install_authentication_guard(socketio)


# =====================================================
# SEND DASHBOARD DATA
# =====================================================

def send_dashboard_data(data):

    if socketio_instance is None:

        print(
            "SOCKET.IO NOT INITIALIZED"
        )

        return

    try:

        socketio_instance.emit(
            "tag_update",
            data
        )

        print(
            "SOCKET DATA SENT"
        )

    except Exception as e:

        print(
            "SOCKET SEND ERROR:",
            e
        )


# =====================================================
# OPTIONAL MANUAL EMIT
# =====================================================

def send_tag_data(tags, online=True):

    send_dashboard_data({
        "Online": online,
        "Tags": tags,
    })
