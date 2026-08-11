# =====================================================
# SCADA_FLOW SOCKET MANAGER
# DASHBOARD REALTIME DATA
# =====================================================

from flask_socketio import emit


socketio_instance = None


# =====================================================
# INITIALIZE
# =====================================================

def init_socketio(socketio):

    global socketio_instance

    socketio_instance = socketio

    # Register Flow Designer company-management endpoints
    # once the Flask application object exists.
    try:

        from flow_company_routes import (
            register_flow_company_routes
        )

        register_flow_company_routes(
            socketio.app
        )

    except Exception as exc:

        print(
            "FLOW COMPANY ROUTE REGISTRATION ERROR:",
            exc
        )


# =====================================================
# SEND DASHBOARD DATA
# =====================================================

def send_dashboard_data(data):

    if socketio_instance is None:

        print(
            "SOCKET.IO NOT INITIALIZED"
        )

        return

    try:

        socketio_instance.emit(
            "tag_update",
            data
        )

        print(
            "SOCKET DATA SENT"
        )

    except Exception as e:

        print(
            "SOCKET SEND ERROR:",
            e
        )


# =====================================================
# OPTIONAL MANUAL EMIT
# =====================================================

def send_tag_data(tags, online=True):

    send_dashboard_data({
        "Online": online,
        "Tags": tags,
    })
