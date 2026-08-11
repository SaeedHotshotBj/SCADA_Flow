import json

from database import get_company_flow


def get_dashboard_widgets(company_id):

    widgets = []

    try:

        flow_json = get_company_flow(
            company_id
        )

        if not flow_json:
            return widgets

        flow = json.loads(
            flow_json
        )

        nodes = (
            flow
            .get("drawflow", {})
            .get("Home", {})
            .get("data", {})
        )

        for node in nodes.values():

            if node.get("name") != "DashboardOutput":
                continue

            widgets = (
                node
                .get("data", {})
                .get("widgets", [])
            )

            break

    except Exception as e:

        print(
            "Dashboard widget error:",
            e
        )

    return widgets