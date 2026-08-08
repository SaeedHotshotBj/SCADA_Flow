# ======================================
# SCADA FLOW SERVER CONFIGURATION
# ======================================


# ======================================
# FLASK CONFIGURATION
# ======================================

FLASK_CONFIG = {

    "SECRET_KEY":
        "SCADA_FLOW_SECRET_KEY"

}


# ======================================
# SOCKET.IO CONFIGURATION
# ======================================

SOCKETIO_CONFIG = {

    "cors_allowed_origins":
        "*"

}


# ======================================
# FLOW ENGINE CONFIGURATION
# ======================================

FLOW_CONFIG = {

    "flow_file":
        "flow.json"

}


# ======================================
# DATABASE CONFIGURATION
# ======================================

DB_CONFIG = {

    "path":
        "data/scada_flow.db"

}