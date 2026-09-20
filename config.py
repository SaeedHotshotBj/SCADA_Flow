# ======================================
# SCADA FLOW SERVER CONFIGURATION
# ======================================

FLASK_CONFIG = {
    "SECRET_KEY": "SCADA_FLOW_SECRET_KEY",
}

SOCKETIO_CONFIG = {
    "cors_allowed_origins": "*",
}

FLOW_CONFIG = {
    "flow_file": "flow.json",
}

DB_CONFIG = {
    "path": "data/scada_flow.db",
}

TREND_CONFIG = {
    "raw_retention_minutes": 5,
    "minute_retention_hours": 2,
    "hour_retention_days": 2,
    "day_retention_days": 3650,
    "worker_interval_seconds": 30,
}

# Background services are deliberately not started while config.py is imported.
# Process-level startup is explicit in services.runtime_bootstrap.


DB_MAINTENANCE_CONFIG = {
    "interval_seconds": 900,
    "trigger_retention_days": 3,
    "edge_ledger_retention_days": 3,
    "production_event_retention_days": 90,
    "delete_batch_size": 5000,
    "wal_checkpoint": "TRUNCATE",
}
