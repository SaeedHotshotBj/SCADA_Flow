# =====================================================
# SCADA_FLOW CONFIGURATION
# =====================================================


# =====================================================
# DATABASE CONFIGURATION (SQLite)
# =====================================================


DB_CONFIG = {

    # SQLite database file path (relative to project root)

    "path":

        "data/scada_flow.db",

}


# =====================================================
# FLASK CONFIGURATION
# =====================================================


FLASK_CONFIG = {


    "SECRET_KEY":

        "SCADA_FLOW_SECRET_KEY",



    "DEBUG":

        False

}







# =====================================================
# SOCKETIO CONFIGURATION
# =====================================================


SOCKETIO_CONFIG = {


    "cors_allowed_origins":

        "*"

}







# =====================================================
# FLOW ENGINE CONFIGURATION
# =====================================================


FLOW_CONFIG = {


    # Main flow definition file

    "flow_file":

        "flow.json",



    # Automatically start engine

    "auto_start":

        True,



    # Scan cycle milliseconds

    "cycle_time":

        1000

}







# =====================================================
# DEFAULT PLC CONFIGURATION
# =====================================================


PLC_CONFIG = {


    "default_port":

        502,



    "default_slave":

        1,



    "timeout":

        3

}







# =====================================================
# SYSTEM PATHS
# =====================================================


PATH_CONFIG = {


    "templates":

        "templates",



    "static":

        "static"

}







# =====================================================
# APPLICATION INFORMATION
# =====================================================


APP_INFO = {


    "name":

        "SCADA_FLOW",



    "version":

        "1.0.0"

}
