# =============================================================
# SCADA_FLOW MANAGEMENT FLOW RUNNER
# Executes management requests strictly through saved Drawflow topology.
# =============================================================

import copy

from flow_engine.registry import get_node_class


IGNORED_START_TYPES = {"Roles", "RolesEngaged"}


def _load_nodes(flow, company_id):
    home = (
        flow.get("drawflow", {})
        .get("Home", {})
        .get("data", {})
    ) if isinstance(flow, dict) else {}

    nodes = {}
    connections = {}

    for node_id, node in home.items():
        if not isinstance(node, dict):
            continue
        node_type = node.get("name")
        cls = get_node_class(node_type)
        if not cls:
            continue
        data = node.get("data", {}) or {}
        config = dict(data.get("config", data) or {})
        config["company_id"] = company_id
        nodes[str(node_id)] = {
            "type": node_type,
            "instance": cls(config),
        }

    for node_id, node in home.items():
        out = []
        for output in (node.get("outputs", {}) or {}).values():
            for connection in output.get("connections", []) or []:
                target = connection.get("node")
                if target is not None:
                    out.append(str(target))
        connections[str(node_id)] = out

    return nodes, connections


def _start_nodes(nodes, connections):
    all_nodes = {
        node_id
        for node_id, info in nodes.items()
        if info["type"] not in IGNORED_START_TYPES
    }
    targets = set()
    for source_id, children in connections.items():
        if source_id not in nodes or nodes[source_id]["type"] in IGNORED_START_TYPES:
            continue
        for child in children:
            if child in all_nodes:
                targets.add(child)
    return sorted(all_nodes - targets)


def _walk(nodes, connections, node_id, data, visited):
    node_id = str(node_id)
    if node_id in visited or node_id not in nodes:
        return None
    visited = set(visited)
    visited.add(node_id)

    try:
        result = nodes[node_id]["instance"].execute(data)
        if result is not None:
            data = result
    except Exception as exc:
        return {
            "status": "error",
            "message": str(exc),
            "node_id": node_id,
            "node_type": nodes[node_id]["type"],
        }

    response = data.get("ManagementResponse") if isinstance(data, dict) else None
    if isinstance(response, dict) and response.get("status") in ("ok", "error"):
        return response

    for child in connections.get(node_id, []):
        child_data = copy.deepcopy(data)
        result = _walk(nodes, connections, child, child_data, visited)
        if isinstance(result, dict) and result.get("status") in ("ok", "error"):
            return result

    return None


def execute_management_flow(flow, company_id, request_data):
    nodes, connections = _load_nodes(flow, company_id)
    starts = _start_nodes(nodes, connections)
    for node_id in starts:
        result = _walk(nodes, connections, node_id, copy.deepcopy(request_data), set())
        if isinstance(result, dict) and result.get("status") in ("ok", "error"):
            return result
    return {"status": "error", "message": "No ManagementResponse produced by the configured Flow"}
