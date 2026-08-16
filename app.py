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
# DATABASE INITIALIZATION
# =====================================================

init_database()


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
    print("SESSION CHECK:", dict(session))
    return user_id is not None


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
        if not is_master() and get_session_company_id() is None:
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
        cursor.execute("""
            SELECT CompanyID, CompanyName
            FROM Companies
            ORDER BY CompanyName
        """)
        return [
            {
                "CompanyID": row["CompanyID"],
                "CompanyName": row["CompanyName"],
            }
            for row in cursor.fetchall()
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
        return flow.get("drawflow", {}).get("Home", {}).get("data", {})
    except Exception:
        return {}


def _get_flow_roles(company_id):
    nodes = _get_company_flow_nodes(company_id)
    roles = []
    for node_id, node in nodes.items():
        if node.get("name") != "Roles":
            continue
        data = node.get("data", {})
        role_list = data.get("roles", [])
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

    selected_roles = []

    for node_id, node in nodes.items():
        if node.get("name") != "RolesEngaged":
            continue

        connected = False
        outputs = node.get("outputs", {})
        if isinstance(outputs, dict):
            for output in outputs.values():
                if not isinstance(output, dict):
                    continue
                for connection in output.get("connections", []):
                    if str(connection.get("node", "")) in target_node_ids:
                        connected = True
                        break
                if connected:
                    break

        if not connected:
            continue

        roles = node.get("data", {}).get("roles", [])
        if isinstance(roles, str):
            roles = [roles]
        if not isinstance(roles, list):
            continue

        for role in roles:
            role_name = (
                str(role.get("role", "")).strip()
                if isinstance(role, dict)
                else str(role).strip()
            )
            if role_name and role_name not in selected_roles:
                selected_roles.append(role_name)

    print("FLOW ACCESS:", page_node_name, "Company:", company_id, "Allowed Roles:", selected_roles)
    return selected_roles


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

    return any(user_role.lower() == str(role).lower() for role in allowed_roles)


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
                return jsonify({"status": "error", "message": "No company assigned"}), 403

            if not _user_has_flow_access(company_id, page_node_name):
                return render_template("access_denied.html"), 403

            return view_func(*args, **kwargs)
        return wrapped
    return decorator


# =====================================================
# LOGIN
# =====================================================

@app.route("/login", methods=["GET", "POST"])
def login():
    if is_logged_in():
        if is_master():
            return redirect(url_for("master_companies"))
        return redirect(url_for("dashboard"))

    if request.method == "GET":
        return render_template("login.html", companies=_get_companies_for_login())

    company_id = request.form.get("company_id", type=int)
    username = request.form.get("username", "").strip()
    password = request.form.get("password", "")

    if not username or not password:
        return render_template("login.html", companies=_get_companies_for_login(), error="Username and password are required.")

    conn = None
    cursor = None
    try:
        conn = get_connection()
        cursor = conn.cursor()

        cursor.execute("""
            SELECT UserID, Username, PasswordHash, CompanyID, Role, Enabled
            FROM Users
            WHERE Username = ? AND CompanyID IS NULL
            LIMIT 1
        """, (username,))
        master_user = cursor.fetchone()

        if master_user and str(master_user["Role"]).strip().lower() == "master" and master_user["Enabled"]:
            password_hash = master_user["PasswordHash"]
            password_ok = False
            try:
                password_ok = check_password_hash(password_hash, password)
            except Exception:
                password_ok = False
            if not password_ok and password_hash == password:
                password_ok = True
                cursor.execute("UPDATE Users SET PasswordHash = ? WHERE UserID = ?", (generate_password_hash(password), master_user["UserID"]))
                conn.commit()
            if password_ok:
                session.clear()
                session["user_id"] = master_user["UserID"]
                session["username"] = master_user["Username"]
                session["role"] = master_user["Role"]
                session["company_id"] = None
                session.permanent = True
                next_url = request.form.get("next")
                if next_url and next_url.startswith("/"):
                    return redirect(next_url)
                return redirect(url_for("master_companies"))

        if company_id is None:
            return render_template("login.html", companies=_get_companies_for_login(), error="Please select a company.")

        cursor.execute("""
            SELECT UserID, Username, PasswordHash, CompanyID, Role, Enabled
            FROM Users
            WHERE Username = ? AND CompanyID = ?
            LIMIT 1
        """, (username, company_id))
        user = cursor.fetchone()

        if not user:
            return render_template("login.html", companies=_get_companies_for_login(), error="Invalid company, username, or password.")
        if not user["Enabled"]:
            return render_template("login.html", companies=_get_companies_for_login(), error="This user account is disabled.")

        password_hash = user["PasswordHash"]
        password_ok = False
        try:
            password_ok = check_password_hash(password_hash, password)
        except Exception:
            password_ok = False
        if not password_ok and password_hash == password:
            password_ok = True
            cursor.execute("UPDATE Users SET PasswordHash = ? WHERE UserID = ?", (generate_password_hash(password), user["UserID"]))
            conn.commit()

        if not password_ok:
            return render_template("login.html", companies=_get_companies_for_login(), error="Invalid company, username, or password.")

        session.clear()
        session["user_id"] = user["UserID"]
        session["username"] = user["Username"]
        session["role"] = user["Role"]
        session["company_id"] = user["CompanyID"]
        session.permanent = True

        next_url = request.form.get("next")
        if next_url and next_url.startswith("/"):
            return redirect(next_url)
        return redirect(url_for("dashboard"))

    except Exception as e:
        print("LOGIN ERROR:", e)
        return render_template("login.html", companies=_get_companies_for_login(), error="Login error.")
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


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


# =====================================================
# FLOW DATA HELPERS
# =====================================================

def _read_flow_file():
    path = FLOW_CONFIG.get("FLOW_FILE", "flow.json")
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    except Exception as e:
        print("FLOW FILE READ ERROR:", e)
        return None


def get_flow_data(company_id):
    flow_json = get_company_flow(company_id)
    if not flow_json:
        flow_json = _read_flow_file()
    if not flow_json:
        return None
    if isinstance(flow_json, dict):
        return flow_json
    try:
        return json.loads(flow_json)
    except Exception as e:
        print("FLOW JSON PARSE ERROR:", e)
        return None


def load_flow(company_id):
    try:
        flow = get_flow_data(company_id)
        if not flow:
            print("NO FLOW FOUND FOR COMPANY:", company_id)
            return None
        print("FLOW LOADED:", company_id)
        return flow
    except Exception as e:
        print("DATABASE FLOW LOAD ERROR:", e)
        return None


# =====================================================
# FLOW ENGINE THREAD
# =====================================================

def start_flow_engine():
    print("FLOW ENGINE STARTING")
    cleanup_old_trend_data()
    company_id = int(os.environ.get("SCADA_COMPANY_ID", "1"))
    flow = load_flow(company_id)
    if flow is None:
        print("NO FLOW LOADED")
        return

    global flow_runner_instance
    flow_runner_instance = FlowRunner(flow, company_id)
    flow_runner_instance.run()


# =====================================================
# SOCKET.IO EVENTS
# =====================================================

@socketio.on("connect")
def socket_connect():
    print("Dashboard Connected")


@socketio.on("disconnect")
def socket_disconnect():
    print("Dashboard Disconnected")


# =====================================================
# DATE FILTER
# =====================================================

@app.route("/date_filter")
@login_required
@flow_role_required("TrendOutput")
def date_filter_page():
    return render_template("date_filter.html")


# =====================================================
# TREND CONFIG
# =====================================================

@app.route("/trend_config")
@login_required
@flow_role_required("TrendOutput")
def trend_config():
    result = {"calendar": "Gregorian", "date_picker": "GregorianPicker", "tags": []}
    company_id = get_request_company_id()
    if company_id is None:
        return jsonify(result)

    flow_json = get_company_flow(company_id)
    if not flow_json:
        flow_json = _read_flow_file()
    if not flow_json:
        return jsonify(result)

    try:
        flow = json.loads(flow_json) if isinstance(flow_json, str) else flow_json
        nodes = flow.get("drawflow", {}).get("Home", {}).get("data", {})

        for node in nodes.values():
            if node.get("name") != "TrendOutput":
                continue
            config = node.get("data", {})
            result["date_picker"] = config.get("DatePicker", "GregorianPicker")
            result["calendar"] = "Jalali" if result["date_picker"] == "JalaliPicker" else "Gregorian"

        for node in nodes.values():
            if node.get("name") != "TagMapper":
                continue
            mappings = node.get("data", {}).get("mappings", [])
            if not isinstance(mappings, list):
                continue
            for item in mappings:
                if str(item.get("storage", "")).upper() != "TIME":
                    continue
                tag_name = item.get("name")
                if tag_name:
                    result["tags"].append({"tag": tag_name, "title": tag_name, "unit": item.get("unit", "")})
            break
    except Exception as e:
        print("TREND CONFIG ERROR:", e)

    return jsonify(result)


# =====================================================
# TREND TAGS
# =====================================================

@app.route("/trend_tags")
@login_required
@flow_role_required("TrendOutput")
def trend_tags():
    tags = []
    try:
        company_id = get_request_company_id()
        flow = get_flow_data(company_id)
        if not flow:
            return jsonify([])
        nodes = flow.get("drawflow", {}).get("Home", {}).get("data", {})
        for node in nodes.values():
            if node.get("name") != "TagMapper":
                continue
            mappings = node.get("data", {}).get("mappings", [])
            if not isinstance(mappings, list):
                continue
            for item in mappings:
                if str(item.get("storage", "")).upper() != "TIME":
                    continue
                name = item.get("name")
                if name:
                    tags.append({"tag": name, "title": name, "unit": item.get("unit", "")})
            break
        return jsonify(tags)
    except Exception as e:
        print("TREND TAG ERROR:", e)
        return jsonify([])


# =====================================================
# TREND PAGE
# =====================================================

@app.route("/trend")
@login_required
@flow_role_required("TrendOutput")
def trend():
    return render_template("trend.html")


# =====================================================
# FLOW TREND
# =====================================================

@app.route("/flow_trend", methods=["POST"])
@login_required
@flow_role_required("TrendOutput")
def flow_trend():
    try:
        company_id = get_request_company_id()
        if company_id is None:
            return jsonify({"datasets": []}), 403

        flow_json = get_company_flow(company_id)
        if not flow_json:
            return jsonify({"datasets": []})

        flow = json.loads(flow_json) if isinstance(flow_json, str) else flow_json
        runner = FlowRunner(flow, company_id)
        request_data = request.get_json() or {}
        result = runner.execute_request(request_data)
        return jsonify(result.get("ChartData", {"datasets": []}))
    except Exception as e:
        print("FLOW TREND ERROR:", e)
        return jsonify({"datasets": []})


# =====================================================
# TREND REQUEST
# =====================================================

@app.route("/trend_request", methods=["POST"])
@login_required
@flow_role_required("TrendOutput")
def trend_request():
    try:
        data = request.get_json(silent=True) or {}

        print("TREND REQUEST RECEIVED:")
        print(data)

        tag = data.get("tag")
        start = data.get("start")
        end = data.get("end")
        calendar = data.get("calendar", "Gregorian")
        timezone_offset = data.get("timezoneOffset")

        print("START RECEIVED =", start)
        print("END RECEIVED =", end)
        print("TIMEZONE OFFSET RECEIVED =", timezone_offset)

        if not tag:
            return jsonify({"datasets": []})

        company_id = get_request_company_id()
        if company_id is None:
            return jsonify({"datasets": []}), 403

        flow = get_flow_data(company_id)
        if not flow:
            return jsonify({"datasets": []})

        runner = FlowRunner(flow, company_id)

        request_data = {
            "TrendRequest": {
                "Tag": tag,
                "Tags": [tag],
                "Start": start,
                "End": end,
                "Calendar": calendar,
                "DatePicker": "JalaliPicker" if calendar == "Jalali" else "GregorianPicker",
                "CompanyID": company_id,
                "TimezoneOffset": timezone_offset,
            }
        }

        print("TREND REQUEST TO FLOW:", request_data)

        result = runner.execute_request(request_data)
        chart_data = result.get("ChartData", {"datasets": []})

        for ds in chart_data.get("datasets", []):
            points = ds.get("data", [])
            if len(points) > 2000:
                step = max(1, len(points) // 2000)
                ds["data"] = points[::step]

        print("FINAL CHART DATASETS:", len(chart_data.get("datasets", [])))
        for ds in chart_data.get("datasets", []):
            print(ds.get("tag"), len(ds.get("data", [])))

        return jsonify(chart_data)

    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500


# =====================================================
# DASHBOARD LATEST VALUES
# =====================================================

@app.route("/dashboard/latest")
@login_required
def dashboard_latest():
    company_id = get_request_company_id()
    if company_id is None:
        return jsonify({"Online": False, "Tags": {}, "Timestamps": {}})

    widgets = get_dashboard_widgets(company_id)
    tag_names = [widget.get("tag") for widget in widgets if widget.get("tag")]
    latest = get_latest_tag_values(company_id, tag_names)

    tags = {tag: item["value"] for tag, item in latest.items()}
    return jsonify({"Online": bool(tags), "Tags": tags, "Timestamps": {tag: item.get("timestamp") for tag, item in latest.items()}})


# =====================================================
# DASHBOARD
# =====================================================

@app.route("/")
@login_required
def index():
    return redirect(url_for("dashboard"))


@app.route("/dashboard")
@login_required
@flow_role_required("DashboardOutput")
def dashboard():
    company_id = get_request_company_id()
    return render_template("dashboard.html", company_id=company_id)


# =====================================================
# APP STARTUP
# =====================================================

if __name__ == "__main__":
    init_socketio(app)

    thread = threading.Thread(
        target=start_flow_engine,
        daemon=True
    )
    thread.start()

    socketio.run(
        app,
        host=FLASK_CONFIG.get("HOST", "127.0.0.1"),
        port=FLASK_CONFIG.get("PORT", 5000),
        debug=False,
        allow_unsafe_werkzeug=True,
    )
