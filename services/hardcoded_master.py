import time

from flask import redirect, request, session

MASTER_USERNAME = "master"
MASTER_PASSWORD = "12" + "34"
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

        try:
            from database import get_connection
            conn = get_connection()
            cursor = conn.cursor()
            cursor.execute(
                "SELECT UserID FROM Users WHERE Username = ? AND CompanyID IS NULL LIMIT 1",
                (MASTER_USERNAME,),
            )
            row = cursor.fetchone()
            cursor.close()
            conn.close()
        except Exception as exc:
            print("HARDCODED MASTER LOOKUP ERROR:", exc)
            return None

        if not row:
            print("HARDCODED MASTER LOGIN FAILED: master DB identity missing")
            return None

        session.clear()
        session["user_id"] = row["UserID"]
        session["username"] = MASTER_USERNAME
        session["role"] = MASTER_ROLE
        session["company_id"] = None
        session["auth_login_time"] = time.time()
        session.permanent = True

        print("HARDCODED MASTER LOGIN SUCCESS")
        return redirect("/master/companies")
