from flask_socketio import SocketIO
import builtins


# SCADA_FLOW uses Flask logging for server diagnostics.
# Disable legacy print-based debug output across the application.
builtins.print = lambda *args, **kwargs: None


class SCADAFlowSocketIO(SocketIO):

    def run(self, app, *args, **kwargs):
        try:
            from flow_company_routes import register_flow_company_routes
            register_flow_company_routes(app)
        except Exception:
            pass

        return super().run(
            app,
            *args,
            **kwargs
        )


socketio = SCADAFlowSocketIO()
