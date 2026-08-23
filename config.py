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


# ======================================
# TREND AGGREGATION
# ======================================

TREND_CONFIG = {
    "raw_retention_minutes": 5,
    "minute_retention_hours": 2,
    "hour_retention_days": 2,
    "day_retention_days": 3650,
    "worker_interval_seconds": 30,
}


# Start the low-frequency aggregation worker after all
# configuration values are defined. The worker uses SQLite
# directly and never participates in realtime Flow execution.
try:
    from services.trend_aggregation import start_aggregation_worker
    start_aggregation_worker()
except Exception as _trend_worker_error:
    print("TREND AGGREGATION START ERROR:", _trend_worker_error)
