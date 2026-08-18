import time

from flask import redirect, request, session

MASTER_USERNAME = "master"
MASTER_PASSWORD = "1234"
MASTER_ROLE = "master"


def handle_master_login(app):
    @app.before_request
    def _hardcoded_master_login():
        if request.path != "/login" or request.method != "POST":
            return None

        username = request.form.get("username", "").strip().lower()
        password = request.form.get("password", "")

        if username != MASTER_USERNAME or password != MASTER_PASSWORD:
            return None

        session.clear()
        session["user_id"] = "hardcoded-master"
        session["username"] = MASTER_USERNAME
        session["role"] = MASTER_ROLE
        session["company_id"] = None
        session["auth_login_time"] = time.time()
        session.permanent = True

        print("HARDCODED MASTER LOGIN SUCCESS")
        return redirect("/master/companies")
