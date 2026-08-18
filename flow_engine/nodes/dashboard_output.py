# =====================================================
# SCADA_FLOW DASHBOARD OUTPUT NODE
# ROLE-AWARE LIVE DASHBOARD OUTPUT
# EDGE TIMEOUT CONFIGURATION
# =====================================================

from socket_manager import send_dashboard_data


class DashboardOutput:

    DEFAULT_TIMEOUT_SECONDS = 10

    def __init__(self, config):

        self.config = config or {}

        self.widgets = self.config.get(
            "widgets",
            []
        )

        try:
            self.timeout = float(
                self.config.get(
                    "timeout",
                    self.DEFAULT_TIMEOUT_SECONDS
                )
            )
        except (TypeError, ValueError):
            self.timeout = float(self.DEFAULT_TIMEOUT_SECONDS)

        if self.timeout < 0:
            self.timeout = 0.0

    # =================================================
    # EXECUTE
    # =================================================

    def execute(self, data=None):

        if data is None:
            data = {}

        tags = data.get(
            "Tags",
            {}
        )

        # =================================================
        # ROLE INFORMATION
        # =================================================

        engaged_roles = data.get(
            "EngagedRoles",
            []
        )

        # =================================================
        # BUILD OUTPUT
        # =================================================

        output = {

            "Online": True,

            "Tags": {},

            "Roles": engaged_roles,

            "EdgeTimeout": self.timeout

        }

        # =================================================
        # FILTER WIDGETS
        # =================================================

        if self.widgets:

            for widget in self.widgets:

                tag = widget.get(
                    "tag"
                )

                if tag in tags:

                    output["Tags"][tag] = tags[tag]

        else:

            output["Tags"] = tags

        # =================================================
        # SEND DASHBOARD DATA
        # =================================================

        try:

            send_dashboard_data(
                output
            )

            print()
            print(
                "DASHBOARD OUTPUT SENT"
            )

            print(
                output
            )

            print()

        except Exception as e:

            print(
                "DASHBOARD OUTPUT ERROR:",
                e
            )

        # =================================================
        # RETURN
        # =================================================

        data["DashboardData"] = output

        return data
