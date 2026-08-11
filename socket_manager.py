# =====================================================
# SCADA_FLOW SOCKET MANAGER
# DASHBOARD REALTIME DATA
# =====================================================

socketio_instance = None


# =====================================================
# INITIALIZE
# =====================================================

def init_socketio(socketio):

    global socketio_instance

    # Keep the SocketIO instance for realtime emits.
    # Flask routes are registered by SCADAFlowSocketIO.run()
    # after the real Flask app object exists.
    socketio_instance = socketio


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
