import json
import threading
import time

from database import get_company_flow


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
    return flow.get("drawflow", {}).get("Home", {}).get("data", {})


def _to_plc_id(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _register_to_tag(nodes):
    """Build (PLC_ID, register) -> TagMapper name lookup."""
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
            plc_id = _to_plc_id(item.get("plc_id", item.get("PLC_ID")))
            if not name or register in (None, ""):
                continue
            try:
                register = str(int(register))
            except (TypeError, ValueError):
                register = str(register).strip()
            lookup[(plc_id, register)] = name
    return lookup


def _resolve_machine_tag(raw_tag, plc_id, register_lookup):
    tag = str(raw_tag or "").strip()
    if not tag:
        return ""
    try:
        key_register = str(int(float(tag)))
    except (TypeError, ValueError):
        key_register = tag
    return register_lookup.get((plc_id, key_register), tag)


def get_dashboard_widgets(company_id):
    """Return DashboardOutput widgets and MachineCards with explicit PLC identity."""
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
                    for widget in configured:
                        if not isinstance(widget, dict):
                            continue
                        item = dict(widget)
                        item["plc_id"] = _to_plc_id(item.get("plc_id", item.get("PLC_ID")))
                        widgets.append(item)

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
                                plc_id = _to_plc_id(parameter.get("plc_id", parameter.get("PLC_ID")))
                                raw_tag = str(parameter.get("tag", "")).strip()
                                resolved_tag = _resolve_machine_tag(raw_tag, plc_id, register_lookup)
                                if not resolved_tag:
                                    continue
                                label = str(parameter.get("label", "")).strip() or resolved_tag
                                unit = str(parameter.get("unit", "")).strip()
                                normalized["parameters"].append({
                                    "label": label,
                                    "tag": resolved_tag,
                                    "configured_tag": raw_tag,
                                    "plc_id": plc_id,
                                    "unit": unit,
                                })
                                widgets.append({
                                    "tag": resolved_tag,
                                    "plc_id": plc_id,
                                    "title": normalized["name"] + " / " + label,
                                    "unit": unit,
                                    "_dashboard_type": "machine_parameter",
                                })
                        machines.append(normalized)

                if isinstance(configured_icons, list):
                    icon_library.extend(item for item in configured_icons if isinstance(item, dict))

        for machine in machines:
            widgets.append({"_dashboard_type": "machine", "machine": machine, "icon_library": icon_library})

    except Exception as exc:
        print("Dashboard widget error:", exc)

    return widgets


def _start_edge_timeout_with_retry():
    for attempt in range(12):
        try:
            from services.edge_timeout_service import start_worker
            start_worker()
            print("EDGE TIMEOUT WORKER STARTED FROM DASHBOARD SERVICE RETRY", attempt + 1)
            return
        except Exception as exc:
            print("EDGE TIMEOUT RETRY START ERROR:", attempt + 1, exc)
        time.sleep(2)
    print("EDGE TIMEOUT WORKER RETRY START FAILED")


threading.Thread(
    target=_start_edge_timeout_with_retry,
    name="SCADA-Edge-Timeout-Bootstrap",
    daemon=True,
).start()
