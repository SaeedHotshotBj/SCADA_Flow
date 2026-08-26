# =====================================================
# SCADA_FLOW DASHBOARD OUTPUT NODE
# ROLE-AWARE LIVE DASHBOARD OUTPUT
# EDGE TIMEOUT CONFIGURATION
# =====================================================

from datetime import datetime

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

        if not isinstance(tags, dict):
            tags = {}

        engaged_roles = data.get(
            "EngagedRoles",
            []
        )

        company_id = data.get("CompanyID")
        if company_id is None:
            company_id = self.config.get("company_id")

        timestamp = data.get(
            "Timestamp",
            datetime.now().isoformat()
        )

        output = {

            "Online": True,

            "Tags": {},

            "Roles": engaged_roles,

            "EdgeTimeout": self.timeout,

            "CompanyID": company_id,

            "Timestamp": timestamp

        }

        machine_cards = data.get("MachineCards", [])
        if isinstance(machine_cards, list):
            output["MachineCards"] = machine_cards
            output["MachineIconLibrary"] = data.get(
                "MachineIconLibrary",
                []
            )

        if self.widgets:

            for widget in self.widgets:

                tag = widget.get(
                    "tag"
                )

                if tag in tags:
                    output["Tags"][tag] = tags[tag]

            # MachineCard parameters are dashboard data too. They are added
            # automatically so the editor does not require duplicate tag rows
            # in DashboardOutput just to make a machine card live.
            if isinstance(machine_cards, list):
                for machine in machine_cards:
                    for parameter in machine.get("parameters", []):
                        tag = parameter.get("tag")
                        if tag in tags:
                            output["Tags"][tag] = tags[tag]

        else:

            output["Tags"] = tags

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

        data["DashboardData"] = output

        return data
