# =====================================================
# SCADA_FLOW SOCKET MANAGER
# DASHBOARD REALTIME DATA
# =====================================================

import json

from flask import request
from werkzeug.security import check_password_hash

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
    is allowed to use the application at all.

    The legacy Users table is intentionally NOT trusted for
    authentication here.
    """

    if company_id is None or not username or not password:
        return False

    flow_json = get_company_flow(company_id)

    if not flow_json:
        return False

    try:
        flow = json.loads(flow_json)
    except Exception:
        return False

    nodes = (
        flow
        .get("drawflow", {})
        .get("Home", {})
        .get("data", {})
    )

    if not isinstance(nodes, dict):
        return False

    username_key = str(username).strip().lower()
    matched_role = None
    matched_password = None
    matched_enabled = True

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

            matched_role = str(
                item.get(
                    "role",
                    item.get("name", "")
                )
            ).strip()

            matched_password = str(
                item.get("password", "")
            )

            matched_enabled = bool(
                item.get("enabled", True)
            )

            break

        if matched_role is not None:
            break

    if not matched_role or not matched_enabled:
        return False

    # -------------------------------------------------
    # PASSWORD = MUST MATCH THE ROLES BLOCK
    # -------------------------------------------------

    password_ok = False

    # Roles currently stores the configured password as the
    # value entered in the flow editor. Compare it directly.
    if password == matched_password:
        password_ok = True
    else:
        # Also accept a Werkzeug hash if a future Roles block
        # stores hashed credentials instead of plaintext.
        try:
            password_ok = check_password_hash(
                matched_password,
                password
            )
        except Exception:
            password_ok = False

    if not password_ok:
        return False

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

    # A company user is allowed to authenticate only when the
    # user's role is explicitly engaged somewhere in the flow.
    if not any(
        matched_role.lower() == role.lower()
        for role in engaged_roles
    ):
        return False

    return True


def _install_authentication_guard(socketio):
    """
    Install a small pre-login guard after the real Flask app
    has been created. This keeps the existing login route and
    all unrelated application behavior unchanged.
    """

    global _auth_guard_registered

    if _auth_guard_registered:
        return

    flask_app = getattr(socketio, "app", None)

    if flask_app is None:
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

        # Master authentication remains handled by the existing
        # dedicated master account in Users because it is global,
        # not company-scoped.
        if company_id is None:
            return None

        if not _validate_flow_login(
            company_id,
            username,
            password
        ):
            return (
                "Invalid company, username, password, or "
                "the user's role is not enabled in RolesEngaged.",
                401
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

    # Authentication is deliberately installed here because
    # app.py calls init_socketio() after its login route exists.
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
