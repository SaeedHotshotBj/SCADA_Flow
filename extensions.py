from flask_socketio import SocketIO


class SCADAFlowSocketIO(SocketIO):

    def run(self, app, *args, **kwargs):
        """
        Register Flow Designer company-management routes
        after the Flask application has been created and
        immediately before the server starts.
        """

        try:
            from flow_company_routes import (
                register_flow_company_routes
            )

            register_flow_company_routes(app)

            print(
                "FLOW COMPANY ROUTES REGISTERED"
            )

        except Exception as exc:

            print(
                "FLOW COMPANY ROUTE REGISTRATION ERROR:",
                exc
            )

        return super().run(
            app,
            *args,
            **kwargs
        )


socketio = SCADAFlowSocketIO()


# =====================================================
# EDGE OFFLINE WATCHDOG
# =====================================================
# Isolated from the Trend page, Trend Query, and Trend
# database reader. It only adds zero historian records when
# the existing Edge samples stop arriving.
try:
    from services.edge_watchdog import start_edge_watchdog

    start_edge_watchdog()

except Exception as exc:
    print(
        "EDGE WATCHDOG START ERROR:",
        exc
    )
