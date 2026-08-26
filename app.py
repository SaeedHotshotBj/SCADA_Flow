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

# Current SCADA_FLOW access is normal HTTP.
app.config["SESSION_COOKIE_SECURE"] = False

app.config["PERMANENT_SESSION_LIFETIME"] = 86400


# =====================================================
# AUTHENTICATION / PAGE CACHE PROTECTION
# =====================================================

@app.before_request
def _reject_stale_logout_session():
    """Reject an old authenticated session after an explicit logout.

    Flask sessions are client-side signed cookies.  A browser can retain an
    old cookie if cookie deletion is blocked or cached.  The logout marker
    gives the server a second, explicit signal that the browser has logged
    out and must authenticate again.
    """

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

    """Prevent browsers/proxies from showing authenticated pages after logout."""

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
    """Validate that the current session belongs to a real enabled user."""

    user_id = session.get("user_id")

    print(
        "SESSION CHECK:",
        dict(session)
    )

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
    return (
        str(
            session.get("role", "")
        ).strip().lower() == "master"
    )


def get_session_company_id():
    """
    Returns the company belonging to the logged-in user.

    Master users have company_id = None.
    """

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

            return redirect(
                url_for(
                    "login",
                    next=request.path
                )
            )

        return view_func(
            *args,
            **kwargs
        )

    return wrapped


def company_required(view_func):

    @wraps(view_func)
    def wrapped(*args, **kwargs):

        if not is_logged_in():

            return redirect(
                url_for(
                    "login",
                    next=request.path
                )
            )

        # Master has access to all companies.
        # Normal users must have a CompanyID.
        if not is_master():

            company_id = get_session_company_id()

            if company_id is None:

                if company_id is None:

                    return render_template(
                        "access_denied.html"
                    ), 403

        return view_func(
            *args,
            **kwargs
        )

    return wrapped


def get_request_company_id():
    """
    Determine which company the request belongs to.

    Normal company users:
        CompanyID comes ONLY from Flask session.

    Master:
        CompanyID can be explicitly selected using:
            ?company_id=1
        or:
            X-Company-ID: 1
    """

    if not is_logged_in():
        return None

    # -------------------------------------------------
    # NORMAL COMPANY USER
    # -------------------------------------------------

    if not is_master():

        return get_session_company_id()

    # -------------------------------------------------
    # MASTER
    # -------------------------------------------------

    company_id = request.args.get(
        "company_id",
        type=int
    )

    if company_id is None:

        company_id = request.headers.get(
            "X-Company-ID",
            type=int
        )

    return company_id


# =====================================================
# LOGIN COMPANY LIST
# =====================================================

def _get_companies_for_login():
    """
    Return companies that can be selected on the login page.

    This function was missing and caused:

        NameError:
        _get_companies_for_login is not defined
    """

    conn = None
    cursor = None

    try:

        conn = get_connection()
        cursor = conn.cursor()

        cursor.execute(
            """
            SELECT
                CompanyID,
                CompanyName
            FROM Companies
            ORDER BY CompanyName
            """
        )

        rows = cursor.fetchall()

        companies = []

        for row in rows:

            companies.append({
                "CompanyID": row["CompanyID"],
                "CompanyName": row["CompanyName"],
            })

        print(
            "LOGIN COMPANIES:",
            companies
        )

        return companies

    except Exception as e:

        print(
            "LOGIN COMPANY LOAD ERROR:",
            e
        )

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
    """
    Return all nodes from the company's saved flow.
    """

    if company_id is None:
        return {}

    flow = get_flow_data(company_id)

    if not flow:
        return {}

    try:

        return (
            flow
            .get("drawflow", {})
            .get("Home", {})
            .get("data", {})
        )

    except Exception as e:

        print(
            "FLOW NODE LOAD ERROR:",
            e
        )

        return {}


# =====================================================
# READ ROLES FROM ROLES NODES
# =====================================================

def _get_flow_roles(company_id):
    """
    Read role definitions from Roles nodes.

    Example:

    Roles node:

        Manager / manager / 1234
        Operator / operator / 1234
    """

    nodes = _get_company_flow_nodes(
        company_id
    )

    roles = []

    for node_id, node in nodes.items():

        if node.get("name") != "Roles":
            continue

        data = node.get(
            "data",
            {}
        )

        role_list = data.get(
            "roles",
            []
        )

        if not isinstance(
            role_list,
            list
        ):
            continue

        for item in role_list:

            if not isinstance(
                item,
                dict
            ):
                continue

            role = str(
                item.get(
                    "role",
                    item.get(
                        "name",
                        ""
                    )
                )
            ).strip()

            if not role:
                continue

            roles.append({

                "node_id":
                    str(node_id),

                "role":
                    role,

                "username":
                    str(
                        item.get(
                            "username",
                            ""
                        )
                    ).strip(),

                "password":
                    item.get(
                        "password",
                        ""
                    ),

                "enabled":
                    bool(
                        item.get(
                            "enabled",
                            True
                        )
                    )

            })

    return roles


# =====================================================
# ROLES ENGAGED ACCESS
# =====================================================

def _get_roles_engaged_for_page(
    company_id,
    page_node_name
):
    """
    Find RolesEngaged nodes connected to the requested
    front-page node.

    Example:

        RolesEngaged -> DashboardOutput

        RolesEngaged -> TrendOutput

    Only roles selected in a RolesEngaged node that is
    actually connected to the requested page are allowed.
    """

    nodes = _get_company_flow_nodes(
        company_id
    )

    if not nodes:
        return []

    # -------------------------------------------------
    # FIND TARGET PAGE NODES
    # -------------------------------------------------

    target_node_ids = []

    for node_id, node in nodes.items():

        if node.get("name") == page_node_name:

            target_node_ids.append(
                str(node_id)
            )

    if not target_node_ids:
        return []

    # -------------------------------------------------
    # FIND RolesEngaged NODES CONNECTED TO TARGET
    # -------------------------------------------------

    engaged_nodes = []

    for node_id, node in nodes.items():

        if node.get("name") != "RolesEngaged":
            continue

        outputs = node.get(
            "outputs",
            {}
        )

        connected = False

        if isinstance(
            outputs,
            dict
        ):

            for output in outputs.values():

                if not isinstance(
                    output,
                    dict
                ):
                    continue

                connections = output.get(
                    "connections",
                    []
                )

                if not isinstance(
                    connections,
                    list
                ):
                    continue

                for connection in connections:

                    target_id = str(
                        connection.get(
                            "node",
                            ""
                        )
                    )

                    if target_id in target_node_ids:

                        connected = True
                        break

                if connected:
                    break

        if connected:

            engaged_nodes.append(
                (
                    str(node_id),
                    node
                )
            )

    # -------------------------------------------------
    # READ SELECTED ROLES
    # -------------------------------------------------

    selected_roles = []

    for engaged_id, engaged_node in engaged_nodes:

        data = engaged_node.get(
            "data",
            {}
        )

        roles = data.get(
            "roles",
            []
        )

        if isinstance(
            roles,
            str
        ):

            roles = [
                roles
            ]

        if not isinstance(
            roles,
            list
        ):
            continue

        for role in roles:

            if isinstance(
                role,
                dict
            ):

                role_name = str(
                    role.get(
                        "role",
                        ""
                    )
                ).strip()

            else:

                role_name = str(
                    role
                ).strip()

            if role_name:

                selected_roles.append(
                    role_name
                )

    # -------------------------------------------------
    # UNIQUE
    # -------------------------------------------------

    result = []

    for role in selected_roles:

        if role not in result:

            result.append(role)

    print(
        "FLOW ACCESS:",
        page_node_name,
        "Company:",
        company_id,
        "Allowed Roles:",
        result
    )

    return result


# =====================================================
# CHECK USER FLOW ACCESS
# =====================================================

def _user_has_flow_access(
    company_id,
    page_node_name
):
    """
    Check whether the logged-in user is allowed
    to access a front page according to RolesEngaged.
    """

    # -------------------------------------------------
    # MASTER
    # -------------------------------------------------

    if is_master():

        return True

    # -------------------------------------------------
    # COMPANY
    # -------------------------------------------------

    if company_id is None:

        return False

    # -------------------------------------------------
    # USER ROLE
    # -------------------------------------------------

    user_role = str(
        session.get(
            "role",
            ""
        )
    ).strip()

    if not user_role:

        return False

    # -------------------------------------------------
    # GET ALLOWED ROLES
    # -------------------------------------------------

    allowed_roles = (
        _get_roles_engaged_for_page(
            company_id,
            page_node_name
        )
    )

    # -------------------------------------------------
    # NO RolesEngaged CONFIGURATION
    #
    # Preserve existing behavior:
    # if no RolesEngaged is configured for this page,
    # allow access.
    # -------------------------------------------------

    if not allowed_roles:

        return True

    # -------------------------------------------------
    # CHECK ROLE
    # -------------------------------------------------

    return any(
        user_role.lower()
        ==
        str(role).lower()
        for role in allowed_roles
    )


# =====================================================
# FLOW ROLE DECORATOR
# =====================================================

def flow_role_required(
    page_node_name
):
    """
    Decorator used by front-page routes.

    Example:

        @app.route("/dashboard")
        @login_required
        @flow_role_required("DashboardOutput")
        def dashboard():
            ...
    """

    def decorator(view_func):

        @wraps(view_func)
        def wrapped(*args, **kwargs):

            # -----------------------------------------
            # LOGIN
            # -----------------------------------------

            if not is_logged_in():

                return redirect(
                    url_for(
                        "login",
                        next=request.path
                    )
                )

            # -----------------------------------------
            # MASTER
            # -----------------------------------------

            if is_master():

                return view_func(
                    *args,
                    **kwargs
                )

            # -----------------------------------------
            # COMPANY
            # -----------------------------------------

            company_id = (
                get_session_company_id()
            )

            if company_id is None:

                return jsonify({

                    "status":
                        "error",

                    "message":
                        "No company assigned"

                }), 403

            # -----------------------------------------
            # ACCESS CHECK
            # -----------------------------------------

            if not _user_has_flow_access(
                company_id,
                page_node_name
            ):

                return render_template(
                    "access_denied.html"
                ), 403

            return view_func(
                *args,
                **kwargs
            )

        return wrapped

    return decorator


# =====================================================
# LOGIN
# =====================================================

@app.route(
    "/login",
    methods=["GET", "POST"]
)
def login():

    # =================================================
    # GET LOGIN PAGE / EXISTING SESSION
    # =================================================

    # Existing sessions may redirect only on GET.
    # POST always validates the submitted credentials.
    if request.method == "GET":

        if is_logged_in():

            print("ALREADY LOGGED IN:", dict(session))

            if is_master():
                return redirect(url_for("master_companies"))

            return redirect(url_for("dashboard"))

        companies = _get_companies_for_login()

        return render_template(
            "login.html",
            companies=companies
        )

    # =================================================
    # POST LOGIN
    # =================================================

    # A failed login attempt must never inherit a previous
    # authenticated session (for example manager -> logout -> a/1).
    # Start every POST authentication attempt from a clean session.
    session.clear()
    session.modified = True

    company_id = request.form.get(
        "company_id",
        type=int
    )

    username = (
        request.form.get(
            "username",
            ""
        )
        .strip()
    )

    password = request.form.get(
        "password",
        ""
    )

    print()
    print(
        "========== LOGIN ATTEMPT =========="
    )
    print(
        "USERNAME:",
        username
    )
    print(
        "COMPANY:",
        company_id
    )
    print(
        "PASSWORD RECEIVED:",
        bool(password)
    )
    print(
        "==================================="
    )

    # =================================================
    # VALIDATE INPUT
    # =================================================

    if not username or not password:

        return render_template(
            "login.html",
            companies=_get_companies_for_login(),
            error=(
                "Username and password are required."
            )
        )

    conn = None
    cursor = None

    try:

        conn = get_connection()
        cursor = conn.cursor()

        # =================================================
        # MASTER LOGIN
        # =================================================

        cursor.execute(
            """
            SELECT
                UserID,
                Username,
                PasswordHash,
                CompanyID,
                Role,
                Enabled
            FROM Users
            WHERE Username = ?
              AND CompanyID IS NULL
            LIMIT 1
            """,
            (
                username,
            )
        )

        master_user = cursor.fetchone()

        if (
            master_user
            and str(
                master_user["Role"]
            ).strip().lower() == "master"
            and master_user["Enabled"]
        ):

            password_hash = (
                master_user["PasswordHash"]
            )

            password_ok = False

            try:

                password_ok = (
                    check_password_hash(
                        password_hash,
                        password
                    )
                )

            except Exception:

                password_ok = False

            # -----------------------------------------
            # OLD PLAINTEXT PASSWORD COMPATIBILITY
            # -----------------------------------------

            if (
                not password_ok
                and password_hash == password
            ):

                password_ok = True

                new_hash = (
                    generate_password_hash(
                        password
                    )
                )

                cursor.execute(
                    """
                    UPDATE Users
                    SET PasswordHash = ?
                    WHERE UserID = ?
                    """,
                    (
                        new_hash,
                        master_user["UserID"]
                    )
                )

                conn.commit()

            # -----------------------------------------
            # MASTER LOGIN SUCCESS
            # -----------------------------------------

            if password_ok:

                session.clear()

                session["user_id"] = (
                    master_user["UserID"]
                )

                session["username"] = (
                    master_user["Username"]
                )

                session["role"] = (
                    master_user["Role"]
                )

                session["company_id"] = None

                session.permanent = True

                print()
                print(
                    "========== MASTER LOGIN SUCCESS =========="
                )
                print(
                    "USER:",
                    master_user["Username"]
                )
                print(
                    "SESSION AFTER LOGIN:",
                    dict(session)
                )
                print(
                    "=========================================="
                )

                next_url = request.form.get(
                    "next"
                )

                if (
                    next_url
                    and next_url.startswith("/")
                ):

                    return redirect(
                        next_url
                    )

                return redirect(
                    url_for(
                        "master_companies"
                    )
                )

        # =================================================
        # NORMAL COMPANY USER
        # =================================================

        if company_id is None:

            return render_template(
                "login.html",
                companies=(
                    _get_companies_for_login()
                ),
                error=(
                    "Please select a company."
                )
            )

        cursor.execute(
            """
            SELECT
                UserID,
                Username,
                PasswordHash,
                CompanyID,
                Role,
                Enabled
            FROM Users
            WHERE Username = ?
              AND CompanyID = ?
            LIMIT 1
            """,
            (
                username,
                company_id
            )
        )

        user = cursor.fetchone()

        # -----------------------------------------
        # USER NOT FOUND
        # -----------------------------------------

        if not user:

            print(
                "LOGIN FAILED: USER NOT FOUND"
            )

            return render_template(
                "login.html",
                companies=(
                    _get_companies_for_login()
                ),
                error=(
                    "Invalid company, username, or password."
                )
            )

        # -----------------------------------------
        # USER DISABLED
        # -----------------------------------------

        if not user["Enabled"]:

            print(
                "LOGIN FAILED: USER DISABLED"
            )

            return render_template(
                "login.html",
                companies=(
                    _get_companies_for_login()
                ),
                error=(
                    "This user account is disabled."
                )
            )

        # -----------------------------------------
        # PASSWORD
        # -----------------------------------------

        password_hash = (
            user["PasswordHash"]
        )

        password_ok = False

        try:

            password_ok = (
                check_password_hash(
                    password_hash,
                    password
                )
            )

        except Exception:

            password_ok = False

        # -----------------------------------------
        # OLD PLAINTEXT PASSWORD COMPATIBILITY
        # -----------------------------------------

        if (
            not password_ok
            and password_hash == password
        ):

            password_ok = True

            new_hash = (
                generate_password_hash(
                    password
                )
            )

            cursor.execute(
                """
                UPDATE Users
                SET PasswordHash = ?
                WHERE UserID = ?
                """,
                (
                    new_hash,
                    user["UserID"]
                )
            )

            conn.commit()

        # -----------------------------------------
        # WRONG PASSWORD
        # -----------------------------------------

        if not password_ok:

            print(
                "LOGIN FAILED: WRONG PASSWORD"
            )

            return render_template(
                "login.html",
                companies=(
                    _get_companies_for_login()
                ),
                error=(
                    "Invalid company, username, or password."
                )
            )

        # =================================================
        # LOGIN SUCCESS
        # =================================================

        session.clear()

        session["user_id"] = (
            user["UserID"]
        )

        session["username"] = (
            user["Username"]
        )

        session["role"] = (
            user["Role"]
        )

        session["company_id"] = (
            user["CompanyID"]
        )

        session["auth_login_time"] = time.time()

        session.permanent = True

        print()
        print(
            "========== USER LOGIN SUCCESS =========="
        )
        print(
            "USER:",
            user["Username"]
        )
        print(
            "ROLE:",
            user["Role"]
        )
        print(
            "COMPANY:",
            user["CompanyID"]
        )
        print(
            "SESSION AFTER LOGIN:",
            dict(session)
        )
        print(
            "========================================"
        )

        # =================================================
        # REDIRECT
        # =================================================

        next_url = request.form.get(
            "next"
        )

        if (
            next_url
            and next_url.startswith("/")
        ):

            return redirect(
                next_url
            )

        return redirect(
            url_for(
                "dashboard"
            )
        )

    except Exception as e:

        import traceback

        traceback.print_exc()

        return render_template(
            "login.html",
            companies=(
                _get_companies_for_login()
            ),
            error=str(e)
        )

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
# LOGOUT
# =====================================================

@app.route("/logout")
def logout():

    username = session.get(
        "username"
    )
    user_id = session.get("user_id")

    # Revoke this user's current authenticated session on the server.
    if user_id is not None:
        conn = None
        cursor = None
        try:
            conn = get_connection()
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO AuthSessionRevocations (UserID, RevokedAt)
                VALUES (?, ?)
                ON CONFLICT(UserID) DO UPDATE SET RevokedAt = excluded.RevokedAt
                """,
                (user_id, time.time())
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

    # Completely invalidate the Flask session.
    session.clear()
    session.modified = True
    session.permanent = False

    print(
        "USER LOGOUT:",
        username
    )

    response = redirect(
        url_for(
            "login"
        )
    )

    # Explicitly expire the session cookie as an additional safeguard
    # against a browser retaining an old authenticated cookie.
    response.delete_cookie(
        app.config.get(
            "SESSION_COOKIE_NAME",
            "session"
        ),
        path=app.config.get(
            "SESSION_COOKIE_PATH",
            "/"
        ),
        domain=app.config.get(
            "SESSION_COOKIE_DOMAIN"
        )
    )

    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"

    return response


# =====================================================
# CURRENT USER
# =====================================================

@app.route("/api/auth/me")
@login_required
def current_user():

    return jsonify({

        "authenticated":
            True,

        "user_id":
            session.get(
                "user_id"
            ),

        "username":
            session.get(
                "username"
            ),

        "role":
            session.get(
                "role"
            ),

        "company_id":
            session.get(
                "company_id"
            )

    })


# =====================================================
# SOCKET.IO INITIALIZATION
# =====================================================

socketio.init_app(
    app,
    cors_allowed_origins=(
        SOCKETIO_CONFIG[
            "cors_allowed_origins"
        ]
    )
)

init_socketio(
    socketio
)


# =====================================================
# LOAD FLOW
# =====================================================

def _read_flow_file():

    flow_path = FLOW_CONFIG[
        "flow_file"
    ]

    if not os.path.isabs(
        flow_path
    ):

        flow_path = os.path.join(
            os.path.dirname(
                os.path.abspath(
                    __file__
                )
            ),
            flow_path,
        )

    if not os.path.exists(
        flow_path
    ):

        return None

    with open(
        flow_path,
        encoding="utf-8"
    ) as f:

        return f.read()


def get_flow_data(
    company_id
):

    if company_id is None:

        return None

    flow_json = get_company_flow(
        company_id
    )

    if not flow_json:

        flow_json = _read_flow_file()

    if not flow_json:

        return None

    try:

        return json.loads(
            flow_json
        )

    except Exception as e:

        print(
            "FLOW JSON PARSE ERROR:",
            e
        )

        return None


def load_flow(
    company_id
):

    try:

        flow = get_flow_data(
            company_id
        )

        if not flow:

            print(
                "NO FLOW FOUND FOR COMPANY:",
                company_id
            )

            return None

        print(
            "FLOW LOADED:",
            company_id
        )

        return flow

    except Exception as e:

        print(
            "DATABASE FLOW LOAD ERROR:",
            e
        )

        return None


# =====================================================
# FLOW ENGINE THREAD
# =====================================================

def start_flow_engine():

    print(
        "FLOW ENGINE STARTING"
    )

    cleanup_old_trend_data()

    # Flow engine currently starts for the
    # configured server company.
    company_id = int(
        os.environ.get(
            "SCADA_COMPANY_ID",
            "1"
        )
    )

    flow = load_flow(
        company_id
    )

    if flow is None:

        print(
            "NO FLOW LOADED"
        )

        return

    global flow_runner_instance

    flow_runner_instance = FlowRunner(
        flow,
        company_id
    )

    flow_runner_instance.run()


# =====================================================
# SOCKET.IO EVENTS
# =====================================================

@socketio.on("connect")
def socket_connect():

    print(
        "Dashboard Connected"
    )


@socketio.on("disconnect")
def socket_disconnect():

    print(
        "Dashboard Disconnected"
    )


# =====================================================
# DATE FILTER
# =====================================================

@app.route(
    "/date_filter"
)
@login_required
@flow_role_required(
    "TrendOutput"
)
def date_filter_page():

    return render_template(
        "date_filter.html"
    )


# =====================================================
# TREND CONFIG
# =====================================================

@app.route(
    "/trend_config"
)
@login_required
@flow_role_required(
    "TrendOutput"
)
def trend_config():

    result = {

        "calendar":
            "Gregorian",

        "date_picker":
            "GregorianPicker",

        "tags":
            []

    }

    company_id = (
        get_request_company_id()
    )

    if company_id is None:

        return jsonify(
            result
        )

    flow_json = get_company_flow(
        company_id
    )

    if not flow_json:

        flow_json = _read_flow_file()

    if not flow_json:

        return jsonify(
            result
        )

    try:

        flow = json.loads(
            flow_json
        )

        nodes = (
            flow
            .get("drawflow", {})
            .get("Home", {})
            .get("data", {})
        )

        # -------------------------------------------------
        # TREND OUTPUT CONFIG
        # -------------------------------------------------

        for node in nodes.values():

            if node.get("name") != "TrendOutput":

                continue

            config = node.get(
                "data",
                {}
            )

            result["date_picker"] = (
                config.get(
                    "DatePicker",
                    "GregorianPicker"
                )
            )

            if (
                result["date_picker"]
                == "JalaliPicker"
            ):

                result["calendar"] = (
                    "Jalali"
                )

            else:

                result["calendar"] = (
                    "Gregorian"
                )

        # -------------------------------------------------
        # TREND TAGS FROM TAGMAPPER
        # -------------------------------------------------

        for node in nodes.values():

            if node.get("name") != "TagMapper":

                continue

            mappings = (
                node
                .get("data", {})
                .get("mappings", [])
            )

            if not isinstance(
                mappings,
                list
            ):
                continue

            for item in mappings:

                if (
                    str(
                        item.get(
                            "storage",
                            ""
                        )
                    ).upper()
                    != "TIME"
                ):
                    continue

                tag_name = item.get(
                    "name"
                )

                if not tag_name:
                    continue

                result["tags"].append({

                    "tag":
                        tag_name,

                    "title":
                        tag_name,

                    "unit":
                        item.get(
                            "unit",
                            ""
                        )

                })

            break

    except Exception as e:

        print(
            "TREND CONFIG ERROR:",
            e
        )

    return jsonify(
        result
    )


# =====================================================
# TREND TAGS
# =====================================================

@app.route(
    "/trend_tags"
)
@login_required
@flow_role_required(
    "TrendOutput"
)
def trend_tags():

    tags = []

    try:

        company_id = (
            get_request_company_id()
        )

        flow = get_flow_data(
            company_id
        )

        if not flow:

            return jsonify(
                []
            )

        nodes = (
            flow
            .get("drawflow", {})
            .get("Home", {})
            .get("data", {})
        )

        for node in nodes.values():

            if node.get("name") != "TagMapper":

                continue

            mappings = (
                node
                .get("data", {})
                .get("mappings", [])
            )

            if not isinstance(
                mappings,
                list
            ):
                continue

            for item in mappings:

                name = item.get(
                    "name"
                )

                if not name:

                    continue

                storage = item.get(
                    "storage",
                    ""
                )

                if (
                    str(storage).upper()
                    != "TIME"
                ):

                    continue

                tags.append({

                    "tag":
                        name,

                    "title":
                        name,

                    "unit":
                        item.get(
                            "unit",
                            ""
                        )

                })

            break

        print(
            "TREND TAGS:",
            tags
        )

        return jsonify(
            tags
        )

    except Exception as e:

        print(
            "TREND TAG ERROR:",
            e
        )

        return jsonify(
            []
        )


# =====================================================
# TREND PAGE
# =====================================================

@app.route(
    "/trend"
)
@login_required
@flow_role_required(
    "TrendOutput"
)
def trend():

    return render_template(
        "trend.html"
    )


# =====================================================
# FLOW TREND
# =====================================================

@app.route(
    "/flow_trend",
    methods=["POST"]
)
@login_required
@flow_role_required(
    "TrendOutput"
)
def flow_trend():

    try:

        company_id = (
            get_request_company_id()
        )

        if company_id is None:

            return jsonify({
                "datasets": []
            }), 403

        flow_json = get_company_flow(
            company_id
        )

        if not flow_json:

            return jsonify({
                "datasets": []
            })

        flow = json.loads(
            flow_json
        )

        runner = FlowRunner(
            flow,
            company_id
        )

        request_data = (
            request.get_json()
            or {}
        )

        result = (
            runner.execute_request(
                request_data
            )
        )

        return jsonify(
            result.get(
                "ChartData",
                {
                    "datasets": []
                }
            )
        )

    except Exception as e:

        print(
            "FLOW TREND ERROR:",
            e
        )

        return jsonify({
            "datasets": []
        })


# =====================================================
# TREND REQUEST
# =====================================================

@app.route(
    "/trend_request",
    methods=["POST"]
)
@login_required
@flow_role_required(
    "TrendOutput"
)
def trend_request():

    try:

        data = (
            request.get_json()
            or {}
        )

        print()
        print(
            "TREND REQUEST RECEIVED:"
        )
        print(
            data
        )
        print()

        tag = data.get(
            "tag"
        )

        start = data.get(
            "start"
        )

        end = data.get(
            "end"
        )

        print(
            "START RECEIVED =",
            start
        )

        print(
            "END RECEIVED =",
            end
        )

        calendar = data.get(
            "calendar",
            "Gregorian"
        )

        if not tag:

            return jsonify({
                "datasets": []
            })

        company_id = (
            get_request_company_id()
        )

        if company_id is None:

            return jsonify({
                "datasets": []
            }), 403

        flow = get_flow_data(
            company_id
        )

        if not flow:

            return jsonify({
                "datasets": []
            })

        runner = FlowRunner(
            flow,
            company_id
        )

        request_data = {

            "TrendRequest": {

                "Tag":
                    tag,

                "Tags":
                    [tag],

                "Start":
                    start,

                "End":
                    end,

                "Calendar":
                    calendar,

                "DatePicker":
                    (
                        "JalaliPicker"
                        if calendar == "Jalali"
                        else "GregorianPicker"
                    )

            }

        }

        result = (
            runner.execute_request(
                request_data
            )
        )

        chart_data = result.get(
            "ChartData",
            {
                "datasets": []
            }
        )

        # -------------------------------------------------
        # KEEP EVERY HISTORIAN POINT
        # -------------------------------------------------
        # Do not downsample or slice historian data.
        # TIME tags are stored at their configured interval
        # and every stored point must reach the Trend chart.

        print(
            "FINAL CHART DATASETS:",
            len(
                chart_data.get(
                    "datasets",
                    []
                )
            )
        )

        for ds in chart_data.get(
            "datasets",
            []
        ):

            print(
                ds.get("tag"),
                len(
                    ds.get(
                        "data",
                        []
                    )
                )
            )

        print()

        return jsonify(
            chart_data
        )

    except Exception as e:

        import traceback

        traceback.print_exc()

        return jsonify({

            "error":
                str(e)

        }), 500


# =====================================================
# DASHBOARD LATEST VALUES
# =====================================================

@app.route(
    "/dashboard/latest"
)
@login_required
def dashboard_latest():

    company_id = (
        get_request_company_id()
    )

    if company_id is None:

        return jsonify({

            "Online":
                False,

            "Tags":
                {},

            "Timestamps":
                {}

        })

    # IMPORTANT:
    # Do NOT use company 1 here.

    widgets = get_dashboard_widgets(
        company_id
    )

    tag_names = [

        widget.get("tag")

        for widget in widgets

        if widget.get("tag")

    ]

    latest = get_latest_tag_values(
        company_id,
        tag_names
    )

    tags = {}

    for tag, item in latest.items():

        tags[tag] = item["value"]

    return jsonify({

        "Online":
            bool(tags),

        "Tags":
            tags,

        "Timestamps": {

            tag:
                item["timestamp"]

            for tag, item
            in latest.items()

        },

    })


# =====================================================
# DASHBOARD
# =====================================================

@app.route(
    "/dashboard"
)
@login_required
@flow_role_required(
    "DashboardOutput"
)
def dashboard():

    company_id = (
        get_request_company_id()
    )

    if company_id is None:

        return redirect(
            url_for(
                "master_companies"
            )
        )

    widgets = get_dashboard_widgets(
        company_id
    )

    return render_template(

        "dashboard.html",

        widgets=widgets

    )


# =====================================================
# EDGE DATA RECEIVER
# =====================================================

@app.route(
    "/api/data",
    methods=["POST"]
)
def receive_edge_data():

    try:

        data = request.get_json()

        print(
            "\n========== EDGE DATA =========="
        )

        print(
            data
        )

        print(
            "===============================\n"
        )

        if not data:

            return jsonify({

                "status":
                    "error",

                "message":
                    "No data"

            }), 400

        plc_id = data.get(
            "PLC_ID"
        )

        tag = data.get(
            "TagName"
        )

        value = data.get(
            "Value"
        )

        timestamp = data.get(
            "Timestamp"
        )

        if plc_id is None:

            return jsonify({

                "status":
                    "error",

                "message":
                    "PLC_ID missing"

            }), 400

        if not tag:

            return jsonify({

                "status":
                    "error",

                "message":
                    "TagName missing"

            }), 400

        conn = get_connection()
        cursor = conn.cursor()

        # =================================================
        # GET COMPANY FROM PLC
        # =================================================

        cursor.execute(
            """
            SELECT CompanyID
            FROM PLCs
            WHERE PLC_ID = ?
            """,
            (
                plc_id,
            )
        )

        plc = cursor.fetchone()

        if not plc:

            cursor.close()
            conn.close()

            return jsonify({

                "status":
                    "error",

                "message":
                    "PLC not found"

            }), 404

        company_id = plc[
            "CompanyID"
        ]

        # =================================================
        # SAVE REALTIME DATA
        # =================================================

        cursor.execute(
            """
            INSERT INTO PLC_Data
            (
                CompanyID,
                TagName,
                Value,
                StorageType,
                Timestamp
            )
            VALUES
            (
                ?,
                ?,
                ?,
                ?,
                COALESCE(
                    ?,
                    datetime(
                        'now',
                        'localtime'
                    )
                )
            )
            """,
            (
                company_id,
                tag,
                value,
                "EDGE",
                timestamp
            )
        )

        # =================================================
        # SAVE HISTORY
        # =================================================

        cursor.execute(
            """
            INSERT INTO TagHistory
            (
                CompanyID,
                PLC_ID,
                TagName,
                Value,
                Timestamp
            )
            VALUES
            (
                ?,
                ?,
                ?,
                ?,
                COALESCE(
                    ?,
                    datetime(
                        'now',
                        'localtime'
                    )
                )
            )
            """,
            (
                company_id,
                plc_id,
                tag,
                value,
                timestamp
            )
        )

        conn.commit()

        cursor.close()
        conn.close()

        # =================================================
        # DASHBOARD UPDATE
        # =================================================

        socketio.emit(
            "tag_update",
            {

                "Online":
                    True,

                "CompanyID":
                    company_id,

                "PLC_ID":
                    plc_id,

                "Tag":
                    tag,

                "Value":
                    value,

                "Timestamp":
                    timestamp

            }
        )

        print(
            "DASHBOARD UPDATE SENT:",
            "Company:",
            company_id,
            "PLC:",
            plc_id,
            "Tag:",
            tag,
            "Value:",
            value
        )

        return jsonify({

            "status":
                "ok",

            "CompanyID":
                company_id,

            "PLC_ID":
                plc_id,

            "tag":
                tag,

            "value":
                value

        })

    except Exception as e:

        import traceback

        traceback.print_exc()

        return jsonify({

            "status":
                "error",

            "message":
                str(e)

        }), 500


# =====================================================
# HOME
# =====================================================

@app.route("/")
def home():

    if not is_logged_in():

        return redirect(
            url_for(
                "login"
            )
        )

    if is_master():

        return redirect(
            url_for(
                "master_companies"
            )
        )

    return redirect(
        url_for(
            "dashboard"
        )
    )


# =====================================================
# COMPANY FLOW JSON
# =====================================================

@app.route(
    "/flow.json"
)
@login_required
def get_flow_json():

    try:

        company_id = (
            get_request_company_id()
        )

        if company_id is None:

            return jsonify({

                "error":
                    "Company not selected"

            }), 403

        flow_json = get_company_flow(
            company_id
        )

        if not flow_json:

            flow_json = _read_flow_file()

        if not flow_json:

            return jsonify({})

        return jsonify(
            json.loads(
                flow_json
            )
        )

    except Exception as e:

        print(
            "FLOW JSON LOAD ERROR:",
            e
        )

        return jsonify({})


# =====================================================
# FLOW EDITOR
# =====================================================

@app.route(
    "/flow"
)
@login_required
def flow_editor():

    # -------------------------------------------------
    # MASTER
    # -------------------------------------------------

    if is_master():

        company_id = request.args.get(
            "company_id",
            type=int
        )

        if company_id is None:

            return redirect(
                url_for(
                    "master_companies"
                )
            )

    # -------------------------------------------------
    # COMPANY USER
    # -------------------------------------------------

    else:

        company_id = (
            get_session_company_id()
        )

        if company_id is None:

            return jsonify({

                "status":
                    "error",

                "message":
                    "No company assigned"

            }), 403

        # =================================================
        # FLOW DESIGNER ACCESS
        # Master and Admin can edit the flow.
        # =================================================

        role = str(
            session.get("role", "")
        ).strip().lower()

        if role not in ("master", "admin"):

            return render_template(
                "access_denied.html"
            ), 403

    return render_template(
        "flow_editor.html",
        company_id=company_id
    )


# =====================================================
# NODE REGISTRY API
# =====================================================

@app.route(
    "/node_registry"
)
@login_required
def node_registry():

    return jsonify(
        NODE_REGISTRY
    )


# =====================================================
# FLOW ROLES API
# =====================================================

@app.route(
    "/flow_roles"
)
@login_required
def flow_roles():

    company_id = request.args.get(
        "company_id",
        type=int
    )

    if company_id is None:

        return jsonify([])

    # Normal users cannot request
    # another company's roles.
    if not is_master():

        session_company = (
            get_session_company_id()
        )

        if (
            session_company is None
            or session_company != company_id
        ):

            return jsonify({
                "status": "error",
                "message": "Access denied"
            }), 403

    return jsonify(
        _get_flow_roles(
            company_id
        )
    )


# =====================================================
# EDGE CONFIG
# =====================================================

@app.route(
    "/api/edge/config"
)
def edge_config():

    try:

        plc_id = request.args.get(
            "PLC_ID",
            type=int
        )

        if not plc_id:

            return jsonify({

                "status":
                    "error",

                "message":
                    "PLC_ID is required"

            }), 400

        conn = get_connection()
        cursor = conn.cursor()

        # -------------------------------------------------
        # FIND COMPANY OWNING PLC
        # -------------------------------------------------

        cursor.execute(
            """
            SELECT CompanyID
            FROM PLCs
            WHERE PLC_ID = ?
            """,
            (
                plc_id,
            )
        )

        plc = cursor.fetchone()

        if not plc:

            cursor.close()
            conn.close()

            return jsonify({

                "status":
                    "error",

                "message":
                    "PLC not found"

            }), 404

        company_id = plc[
            "CompanyID"
        ]

        # -------------------------------------------------
        # LOAD COMPANY FLOW
        # -------------------------------------------------

        cursor.execute(
            """
            SELECT FlowJson
            FROM Flows
            WHERE CompanyID = ?
            ORDER BY FlowID DESC
            LIMIT 1
            """,
            (
                company_id,
            )
        )

        row = cursor.fetchone()

        cursor.close()
        conn.close()

        if not row:

            return jsonify({

                "status":
                    "error",

                "message":
                    "No flow configured for this company"

            }), 404

        flow = json.loads(
            row["FlowJson"]
        )

        return jsonify(
            flow
        )

    except Exception as e:

        import traceback

        traceback.print_exc()

        return jsonify({

            "status":
                "error",

            "message":
                str(e)

        }), 500


# =====================================================
# MASTER COMPANY MANAGEMENT
# =====================================================

# =====================================================
# MASTER COMPANY MANAGEMENT
# =====================================================

@app.route("/master/companies")
@login_required
def master_companies():

    if not is_master():

        return jsonify({
            "status": "error",
            "message": "Master access required"
        }), 403

    conn = None
    cursor = None

    try:

        conn = get_connection()
        cursor = conn.cursor()

        cursor.execute(
            """
            SELECT
                CompanyID,
                CompanyName
            FROM Companies
            ORDER BY CompanyName
            """
        )

        companies = [
            dict(row)
            for row in cursor.fetchall()
        ]

        return render_template(
            "master_companies.html",
            companies=companies
        )

    except Exception as e:

        import traceback
        traceback.print_exc()

        return jsonify({
            "status": "error",
            "message": str(e)
        }), 500

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
# SAVE FLOW
# =====================================================

@app.route(
    "/save_flow",
    methods=["POST"]
)
@login_required
def save_flow():

    conn = None
    cursor = None

    try:

        # =================================================
        # COMPANY
        # =================================================

        if is_master():

            # Master can save the currently selected company.
            company_id = request.args.get(
                "company_id",
                type=int
            )

            # If frontend does not send it in URL,
            # use the selected company stored in session.
            if company_id is None:

                company_id = session.get(
                    "selected_company_id"
                )

                try:

                    if company_id is not None:
                        company_id = int(company_id)

                except (TypeError, ValueError):

                    company_id = None

        else:

            # Normal users can only save their own company.
            company_id = get_session_company_id()

        # =================================================
        # COMPANY REQUIRED
        # =================================================

        if company_id is None:

            return jsonify({

                "status":
                    "error",

                "message":
                    "Company is required"

            }), 403

        # =================================================
        # ONLY MASTER OR ADMIN CAN SAVE
        # =================================================

        if not is_master():

            role = str(
                session.get(
                    "role",
                    ""
                )
            ).strip().lower()

            if role != "admin":

                return jsonify({

                    "status":
                        "error",

                    "message":
                        "Flow save access denied"

                }), 403

        # =================================================
        # DATA
        # =================================================

        data = request.get_json()

        if not data:

            return jsonify({

                "status":
                    "error",

                "message":
                    "No flow data received"

            }), 400

        flow_json = json.dumps(
            data,
            ensure_ascii=False
        )

        conn = get_connection()
        cursor = conn.cursor()

        # =================================================
        # SAVE FLOW
        # =================================================

        cursor.execute(
            """
            SELECT FlowID
            FROM Flows
            WHERE CompanyID = ?
            LIMIT 1
            """,
            (
                company_id,
            )
        )

        row = cursor.fetchone()

        if row:

            cursor.execute(
                """
                UPDATE Flows
                SET
                    FlowJson = ?,
                    LastModified =
                        datetime(
                            'now',
                            'localtime'
                        )
                WHERE CompanyID = ?
                """,
                (
                    flow_json,
                    company_id
                )
            )

        else:

            cursor.execute(
                """
                INSERT INTO Flows
                (
                    CompanyID,
                    FlowJson,
                    LastModified
                )
                VALUES
                (
                    ?,
                    ?,
                    datetime(
                        'now',
                        'localtime'
                    )
                )
                """,
                (
                    company_id,
                    flow_json
                )
            )

        # =================================================
        # SYNC ROLES -> USERS
        # =================================================

        roles = []

        nodes = (
            data
            .get("drawflow", {})
            .get("Home", {})
            .get("data", {})
        )

        for node_id, node in nodes.items():

            if node.get("name") != "Roles":
                continue

            node_data = node.get(
                "data",
                {}
            )

            role_list = node_data.get(
                "roles",
                []
            )

            if not isinstance(
                role_list,
                list
            ):
                continue

            for item in role_list:

                if not isinstance(
                    item,
                    dict
                ):
                    continue

                role = str(
                    item.get(
                        "role",
                        item.get(
                            "name",
                            ""
                        )
                    )
                ).strip()

                username = str(
                    item.get(
                        "username",
                        ""
                    )
                ).strip()

                password = str(
                    item.get(
                        "password",
                        ""
                    )
                )

                enabled = (
                    1
                    if item.get(
                        "enabled",
                        True
                    )
                    else 0
                )

                if (
                    not role
                    or not username
                ):
                    continue

                roles.append({

                    "role":
                        role,

                    "username":
                        username,

                    "password":
                        password,

                    "enabled":
                        enabled

                })

        # =================================================
        # SYNC USERS WITH ROLES BLOCK
        # =================================================

        # The Roles block is the source of truth.
        #
        # Any existing company user that is NOT present
        # in the Roles block will be disabled.
        #
        # Users that are present in the Roles block will
        # be created or updated.

        # -------------------------------------------------
        # DISABLE USERS REMOVED FROM ROLES BLOCK
        # -------------------------------------------------

        role_usernames = {
            str(item["username"]).strip().lower()
            for item in roles
            if item.get("username")
        }

        cursor.execute(
            """
            SELECT
                UserID,
                Username,
                Role,
                Enabled
            FROM Users
            WHERE CompanyID = ?
            """,
            (
                company_id,
            )
        )

        existing_users = cursor.fetchall()

        for existing_user in existing_users:

            username = str(
                existing_user["Username"]
            ).strip().lower()

            if username not in role_usernames:

                print(
                    "DISABLING REMOVED FLOW USER:",
                    existing_user["Username"]
                )

                cursor.execute(
                    """
                    UPDATE Users
                    SET Enabled = 0
                    WHERE UserID = ?
                    """,
                    (
                        existing_user["UserID"],
                    )
                )

        # -------------------------------------------------
        # CREATE / UPDATE USERS FROM ROLES BLOCK
        # -------------------------------------------------

        for item in roles:

            username = str(
                item["username"]
            ).strip()

            role = str(
                item["role"]
            ).strip()

            enabled = (
                1
                if item.get(
                    "enabled",
                    True
                )
                else 0
            )

            password = str(
                item.get(
                    "password",
                    ""
                )
            )

            cursor.execute(
                """
                SELECT
                    UserID,
                    PasswordHash
                FROM Users
                WHERE Username = ?
                AND CompanyID = ?
                LIMIT 1
                """,
                (
                    username,
                    company_id
                )
            )

            existing = cursor.fetchone()

            # -------------------------------------------------
            # EXISTING USER
            # -------------------------------------------------

            if existing:

                if password:

                    password_hash = (
                        generate_password_hash(
                            password
                        )
                    )

                    cursor.execute(
                        """
                        UPDATE Users
                        SET
                            PasswordHash = ?,
                            Role = ?,
                            Enabled = ?
                        WHERE UserID = ?
                        """,
                        (
                            password_hash,
                            role,
                            enabled,
                            existing["UserID"]
                        )
                    )

                else:

                    cursor.execute(
                        """
                        UPDATE Users
                        SET
                            Role = ?,
                            Enabled = ?
                        WHERE UserID = ?
                        """,
                        (
                            role,
                            enabled,
                            existing["UserID"]
                        )
                    )

                print(
                    "USER SYNCED:",
                    username,
                    role
                )

            # -------------------------------------------------
            # NEW USER
            # -------------------------------------------------

            else:

                password_hash = (
                    generate_password_hash(
                        password
                    )
                    if password
                    else ""
                )

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
                        ?
                    )
                    """,
                    (
                        username,
                        password_hash,
                        company_id,
                        role,
                        enabled
                    )
                )

                print(
                    "NEW FLOW USER CREATED:",
                    username,
                    role
                )

        conn.commit()

        print(
            "FLOW SAVED SUCCESSFULLY:",
            company_id
        )

        print(
            "ROLES SYNCED:",
            len(roles)
        )

        return jsonify({

            "status":
                "ok",

            "message":
                "Flow saved",

            "CompanyID":
                company_id,

            "roles_synced":
                len(roles)

        })

    except Exception as e:

        if conn is not None:

            try:
                conn.rollback()
            except Exception:
                pass

        import traceback

        traceback.print_exc()

        return jsonify({

            "status":
                "error",

            "message":
                str(e)

        }), 500

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
# TREND CLEANUP THREAD
# =====================================================

def trend_cleanup_worker():

    while True:

        try:

            cleanup_old_trend_data()

        except Exception as e:

            print(
                "TREND CLEANUP ERROR:",
                e
            )

        time.sleep(
            86400
        )


# =====================================================
# START APPLICATION
# =====================================================

if __name__ == "__main__":

    cleanup_thread = threading.Thread(
        target=trend_cleanup_worker,
        daemon=True
    )

    cleanup_thread.start()

    engine_thread = threading.Thread(
        target=start_flow_engine,
        daemon=True
    )

    engine_thread.start()

    print(
        "SCADA_FLOW STARTED"
    )

    socketio.run(

        app,

        host=os.environ.get(
            "SCADA_HOST",
            "0.0.0.0"
        ),

        port=int(
            os.environ.get(
                "SCADA_PORT",
                "5000"
            )
        ),

        allow_unsafe_werkzeug=True,

    )