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


def _register_to_tag(nodes):
    """Build register -> real TagMapper name lookup for dashboard machine parameters."""
    lookup = {}

    for node in nodes.values():
        if not isinstance(node, dict) or node.get("name") != "TagMapper":
            continue

        mappings = node.get("data", {}).get("mappings", [])
        if not isinstance(mappings, list):
            continue

        for item in mappings:
            if not isinstance(item, dict):
                continue

            name = str(item.get("name", "")).strip()
            register = item.get("register")

            if not name or register in (None, ""):
                continue

            try:
                lookup[str(int(register))] = name
            except (TypeError, ValueError):
                lookup[str(register).strip()] = name

    return lookup


def _resolve_machine_tag(raw_tag, register_lookup):
    """Resolve a MachineCard tag that may be a PLC register to its PLC_Data TagName."""
    tag = str(raw_tag or "").strip()
    if not tag:
        return ""

    return register_lookup.get(tag, tag)


# =====================================================
# DASHBOARD CONFIGURATION
# =====================================================

def get_dashboard_widgets(company_id):
    """
    Return DashboardOutput widgets followed by MachineCard definitions.

    MachineCard parameters are normalized through TagMapper so a parameter
    configured as register 144/145 can read PLC_Data rows stored as
    Motor1V1/Motor1S.
    """

    widgets = []
    machines = []
    icon_library = []

    try:
        nodes = _get_nodes(company_id)
        register_lookup = _register_to_tag(nodes)

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

                                raw_tag = str(parameter.get("tag", "")).strip()
                                resolved_tag = _resolve_machine_tag(raw_tag, register_lookup)
                                if not resolved_tag:
                                    continue

                                label = str(parameter.get("label", "")).strip() or resolved_tag
                                unit = str(parameter.get("unit", "")).strip()

                                normalized["parameters"].append({
                                    "label": label,
                                    "tag": resolved_tag,
                                    "configured_tag": raw_tag,
                                    "unit": unit,
                                })

                                widgets.append({
                                    "tag": resolved_tag,
                                    "title": normalized["name"] + " / " + label,
                                    "unit": unit,
                                    "_dashboard_type": "machine_parameter",
                                })

                        machines.append(normalized)

                if isinstance(configured_icons, list):
                    icon_library.extend(
                        item for item in configured_icons
                        if isinstance(item, dict)
                    )

        # Keep all DashboardOutput widgets first. Machine cards are appended
        # after them so the dashboard always renders machine cards below the
        # normal DashboardOutput cards.
        for machine in machines:
            widgets.append({
                "_dashboard_type": "machine",
                "machine": machine,
                "icon_library": icon_library,
            })

    except Exception as e:
        print("Dashboard widget error:", e)

    return widgets


# =====================================================
# START EDGE TIMEOUT WORKER
# =====================================================

try:
    from services.edge_timeout_service import start_worker as start_edge_timeout_worker

    start_edge_timeout_worker()
    print("EDGE TIMEOUT WORKER STARTED FROM DASHBOARD SERVICE")
except Exception as exc:
    print("EDGE TIMEOUT START ERROR:", exc)
