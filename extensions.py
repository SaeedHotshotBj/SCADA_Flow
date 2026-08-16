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
