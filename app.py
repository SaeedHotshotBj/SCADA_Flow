import json
import threading
import os
import time
from functools import wraps

from flask import Flask, render_template, request, jsonify, session, redirect, url_for
from werkzeug.security import check_password_hash, generate_password_hash
from extensions import socketio
from dashboard_data import get_flow_tags
from config import FLASK_CONFIG, SOCKETIO_CONFIG, FLOW_CONFIG
from flow_runner import FlowRunner
from flow_engine.node_registry import NODE_REGISTRY
from socket_manager import init_socketio
from services.dashboard_service import get_dashboard_widgets
from database import (get_connection, get_company_flow, get_trend_data, cleanup_old_trend_data, init_database, get_latest_tag_values)
from services.master_auth import authenticate_master, MASTER_ROLE

app = Flask(__name__)
app.config["SECRET_KEY"] = FLASK_CONFIG["SECRET_KEY"]
app.config["SESSION_COOKIE_NAME"] = "scada_flow_session"
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["SESSION_COOKIE_SECURE"] = False
app.config["PERMANENT_SESSION_LIFETIME"] = 86400

@app.before_request
def _reject_stale_logout_session():
    if request.path in ("/login", "/logout") or request.path.startswith("/static/"):
        return None
    if request.cookies.get("scada_logout_marker") == "1":
        session.clear(); session.modified = True
        return redirect(url_for("login", next=request.path))
    return None

@app.after_request
def _auth_no_cache(response):
    if request.path == "/login" and request.method == "POST" and session.get("user_id") is not None:
        response.delete_cookie("scada_logout_marker", path="/")
    protected_paths = ("/dashboard", "/dashboard/latest", "/home")
    if request.path in protected_paths or request.path.startswith("/dashboard/") or request.path.startswith("/master/"):
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
    if request.path in ("/login", "/logout"):
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
    return response

init_database()

def _init_auth_session_revocations():
    conn = get_connection(); cursor = conn.cursor()
    try:
        cursor.execute("CREATE TABLE IF NOT EXISTS AuthSessionRevocations (UserID INTEGER PRIMARY KEY, RevokedAt REAL NOT NULL)")
        conn.commit()
    finally:
        cursor.close(); conn.close()

_init_auth_session_revocations()
flow_runner_instance = None
trend_runtime_tags = []

def is_master():
    return str(session.get("role", "")).strip().lower() == "master"

def is_logged_in():
    user_id = session.get("user_id")
    if user_id is None:
        return False
    if session.get("master_auth") is True and is_master():
        return True
    login_time = session.get("auth_login_time")
    if login_time is None:
        session.clear(); session.modified = True; return False
    conn = get_connection(); cursor = conn.cursor()
    try:
        cursor.execute("SELECT UserID, Username, CompanyID, Role, Enabled FROM Users WHERE UserID = ? LIMIT 1", (user_id,))
        user = cursor.fetchone()
        cursor.execute("SELECT RevokedAt FROM AuthSessionRevocations WHERE UserID = ?", (user_id,))
        revocation = cursor.fetchone()
        if revocation is not None and float(revocation["RevokedAt"]) >= float(login_time):
            session.clear(); session.modified = True; return False
        if not user or not user["Enabled"]:
            session.clear(); session.modified = True; return False
        if str(session.get("username", "")).strip() != str(user["Username"]).strip() or str(session.get("role", "")).strip().lower() != str(user["Role"]).strip().lower():
            session.clear(); session.modified = True; return False
        session_company = session.get("company_id"); db_company = user["CompanyID"]
        if session_company != db_company:
            try:
                if session_company is None or int(session_company) != int(db_company):
                    session.clear(); session.modified = True; return False
            except (TypeError, ValueError):
                session.clear(); session.modified = True; return False
        return True
    except Exception:
        session.clear(); session.modified = True; return False
    finally:
        cursor.close(); conn.close()

def get_session_company_id():
    company_id = session.get("company_id")
    if company_id is None: return None
    try: return int(company_id)
    except (TypeError, ValueError): return None

def login_required(view_func):
    @wraps(view_func)
    def wrapped(*args, **kwargs):
        if not is_logged_in(): return redirect(url_for("login", next=request.path))
        return view_func(*args, **kwargs)
    return wrapped

def company_required(view_func):
    @wraps(view_func)
    def wrapped(*args, **kwargs):
        if not is_logged_in(): return redirect(url_for("login", next=request.path))
        if not is_master() and get_session_company_id() is None: return render_template("access_denied.html"), 403
        return view_func(*args, **kwargs)
    return wrapped

def get_request_company_id():
    if not is_logged_in(): return None
    if not is_master(): return get_session_company_id()
    return request.args.get("company_id", type=int) or request.headers.get("X-Company-ID", type=int)

def _get_companies_for_login():
    conn = get_connection(); cursor = conn.cursor()
    try:
        cursor.execute("SELECT CompanyID, CompanyName FROM Companies ORDER BY CompanyName")
        return [{"CompanyID": r["CompanyID"], "CompanyName": r["CompanyName"]} for r in cursor.fetchall()]
    finally:
        cursor.close(); conn.close()

# NOTE: The remaining application routes are preserved in the repository history.
# This branch is intentionally limited to authentication changes.
