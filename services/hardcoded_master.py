import time

from flask import session


MASTER_USERNAME = "master"
MASTER_PASSWORD = "1234"
MASTER_ROLE = "master"
MASTER_USER_ID = "hardcoded-master"


def is_hardcoded_master_credentials(username, password):
    return (
        str(username or "").strip().lower() == MASTER_USERNAME
        and str(password or "") == MASTER_PASSWORD
    )


def set_hardcoded_master_session():
    session.clear()
    session["user_id"] = MASTER_USER_ID
    session["username"] = MASTER_USERNAME
    session["role"] = MASTER_ROLE
    session["company_id"] = None
    session["auth_login_time"] = time.time()
    session.permanent = True


def is_hardcoded_master_session():
    return (
        session.get("user_id") == MASTER_USER_ID
        and str(session.get("username", "")).strip().lower() == MASTER_USERNAME
        and str(session.get("role", "")).strip().lower() == MASTER_ROLE
        and session.get("company_id") is None
    )
