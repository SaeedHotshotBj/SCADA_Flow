# =====================================================
# SCADA_FLOW DASHBOARD OUTPUT NODE
# ROLE-AWARE LIVE DASHBOARD OUTPUT
# =====================================================

from socket_manager import send_dashboard_data


class DashboardOutput:

    def __init__(self, config):

        self.config = config or {}

        self.widgets = self.config.get(
            "widgets",
            []
        )

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

            "Roles": engaged_roles

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