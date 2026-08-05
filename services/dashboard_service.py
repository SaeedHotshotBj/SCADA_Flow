import json
import os

from database import get_company_flow


def _read_flow_file():

    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    flow_path = os.path.join(base_dir, "flow.json")

    if not os.path.exists(flow_path):
        return None

    with open(flow_path, encoding="utf-8") as f:
        return f.read()


def get_dashboard_widgets(company_id=1):

    widgets = []

    try:

        flow_json = get_company_flow(company_id)

        if not flow_json:
            flow_json = _read_flow_file()

        if not flow_json:
            return widgets

        flow = json.loads(flow_json)

        nodes = (
            flow
            .get("drawflow", {})
            .get("Home", {})
            .get("data", {})
        )

        for node in nodes.values():

            if node.get("name") == "DashboardOutput":

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
