# =====================================================
# SCADA_FLOW HARD-CODED MASTER AUTHENTICATION
# =====================================================

MASTER_USERNAME = "master"
MASTER_PASSWORD = "1234"
MASTER_ROLE = "Master"


def authenticate_master(username, password):
    """Authenticate the fixed SCADA FLOW master account.

    The master account is intentionally independent of Companies, Users,
    Roles nodes, and company flows.
    """
    return (
        str(username or "").strip().lower() == MASTER_USERNAME
        and str(password or "") == MASTER_PASSWORD
    )
