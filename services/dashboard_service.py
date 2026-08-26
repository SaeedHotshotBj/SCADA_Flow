import json

from database import get_company_flow


# =====================================================
# READ COMPANY FLOW NODES
# =====================================================

def _get_nodes(company_id):
    if company_id is None:
        return {}

    flow_json = get_company_flow(company_id)
    if not flow_json:
        return {}

    try:
        flow = json.loads(flow_json)
    except Exception as exc:
        print("Dashboard flow JSON error:", exc)
        return {}

    return (
        flow.get("drawflow", {})
        .get("Home", {})
        .get("data", {})
    )


# =====================================================
# DASHBOARD CONFIGURATION
# =====================================================

def get_dashboard_widgets(company_id):
    """
    Return the existing parameter widgets plus the information required by
    MachineCard. Machine parameter tags are included as hidden lookup entries
    so /dashboard/latest can fetch their current values without changing the
    existing dashboard API shape.
    """

    widgets = []
    machines = []
    icon_library = []

    try:
        nodes = _get_nodes(company_id)

        for node in nodes.values():
            if not isinstance(node, dict):
                continue

            if node.get("name") == "DashboardOutput":
                configured = node.get("data", {}).get("widgets", [])
                if isinstance(configured, list):
                    widgets.extend(configured)

            elif node.get("name") == "MachineCard":
                data = node.get("data", {})
                configured_machines = data.get("machines", [])
                configured_icons = data.get("icon_library", [])

                if isinstance(configured_machines, list):
                    for machine in configured_machines:
                        if not isinstance(machine, dict):
                            continue

                        normalized = {
                            "id": str(machine.get("id", "")).strip(),
                            "name": str(machine.get("name", "")).strip(),
                            "icon": machine.get("icon", "builtin:factory"),
                            "layout": machine.get("layout", "auto"),
                            "parameters": [],
                        }

                        if not normalized["id"]:
                            normalized["id"] = "machine_%s" % (len(machines) + 1)
                        if not normalized["name"]:
                            normalized["name"] = normalized["id"]

                        parameters = machine.get("parameters", [])
                        if isinstance(parameters, list):
                            for parameter in parameters:
                                if not isinstance(parameter, dict):
                                    continue

                                tag = str(parameter.get("tag", "")).strip()
                                if not tag:
                                    continue

                                normalized["parameters"].append({
                                    "label": str(parameter.get("label", "")).strip() or tag,
                                    "tag": tag,
                                    "unit": str(parameter.get("unit", "")).strip(),
                                })

                                widgets.append({
                                    "tag": tag,
                                    "title": normalized["name"] + " / " + (
                                        str(parameter.get("label", "")).strip() or tag
                                    ),
                                    "unit": str(parameter.get("unit", "")).strip(),
                                    "_dashboard_type": "machine_parameter",
                                })

                        machines.append(normalized)

                if isinstance(configured_icons, list):
                    icon_library.extend(
                        item for item in configured_icons
                        if isinstance(item, dict)
                    )

        # Machine definitions are passed through the same list because the
        # existing Flask dashboard route already supplies `widgets` to the
        # template. No unrelated route/API contract is changed.
        for machine in machines:
            widgets.append({
                "_dashboard_type": "machine",
                "machine": machine,
                "icon_library": icon_library,
            })

    except Exception as e:
        print("Dashboard widget error:", e)

    return widgets
