# =============================================================
# MANAGEMENT FLOW ACCESS
# Access rules are read from ManagementRolesEngaged in the saved Flow.
# =============================================================

import json

from database import get_company_flow


def allowed(company_id, user_role):
    if str(user_role or "").strip().lower() == "master":
        return True
    if company_id is None or not str(user_role or "").strip():
        return False

    flow_json = get_company_flow(company_id)
    if not flow_json:
        return False
    try:
        flow = json.loads(flow_json) if isinstance(flow_json, str) else flow_json
    except Exception:
        return False

    nodes = flow.get("drawflow", {}).get("Home", {}).get("data", {})
    panel_ids = {
        str(node_id)
        for node_id, node in nodes.items()
        if isinstance(node, dict) and node.get("name") == "ManagementPanelOutput"
    }
    if not panel_ids:
        return False

    role_names = []
    found_gate = False
    for node in nodes.values():
        if not isinstance(node, dict) or node.get("name") != "ManagementRolesEngaged":
            continue
        outputs = node.get("outputs", {}) or {}
        connected = any(
            str(connection.get("node")) in panel_ids
            for output in outputs.values()
            if isinstance(output, dict)
            for connection in (output.get("connections", []) or [])
            if isinstance(connection, dict)
        )
        if not connected:
            continue
        found_gate = True
        roles = (node.get("data", {}) or {}).get("roles", [])
        if isinstance(roles, str):
            roles = [roles]
        if isinstance(roles, list):
            for item in roles:
                value = item.get("role") if isinstance(item, dict) else item
                value = str(value or "").strip()
                if value:
                    role_names.append(value.lower())

    if not found_gate or not role_names:
        return True
    return str(user_role).strip().lower() in set(role_names)
