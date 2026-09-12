import copy
import time
import traceback

from flow_engine.registry import get_node_class
from flow_status import flow_status
from services.edge_trigger_service import EdgeTriggerService


REALTIME_SKIP_NODE_TYPES = {
    "TrendReader",
    "TrendDatabaseReader",
    "TrendOutput",
}


class FlowRunner:
    """Execute a Drawflow graph with isolated branch payloads."""

    def __init__(self, flow_data, company_id):
        if company_id is None:
            raise ValueError("FlowRunner requires company_id")
        self.nodes = {}
        self.connections = {}
        self.running = True
        self.flow_data = flow_data or {}
        self.company_id = int(company_id)
        self.edge_trigger_service = EdgeTriggerService()
        self.load_flow()

    def get_node_config(self, node):
        data = node.get("data", {}) or {}
        raw = data.get("config", data)
        config = dict(raw) if isinstance(raw, dict) else {}
        config["company_id"] = self.company_id
        return config

    def load_flow(self):
        home = self.flow_data.get("drawflow", {}).get("Home", {}).get("data", {})
        if not isinstance(home, dict):
            return

        for node_id, node in home.items():
            if not isinstance(node, dict):
                continue
            node_type = node.get("name")
            node_class = get_node_class(node_type)
            if not node_class:
                continue
            config = self.get_node_config(node)
            self.nodes[str(node_id)] = {
                "instance": node_class(config),
                "type": node_type,
                "config": config,
            }

        role_definitions = []
        for node in home.values():
            if not isinstance(node, dict) or node.get("name") != "Roles":
                continue
            roles = self.get_node_config(node).get("roles", [])
            if isinstance(roles, list):
                role_definitions.extend(
                    role for role in roles
                    if isinstance(role, dict) and str(role.get("role", "")).strip()
                )

        unique_roles = []
        seen_roles = set()
        for role in role_definitions:
            key = str(role.get("role", "")).strip().lower()
            if key and key not in seen_roles:
                seen_roles.add(key)
                unique_roles.append(role)

        for node_id, info in self.nodes.items():
            if info["type"] == "RolesEngaged":
                info["config"]["roles"] = unique_roles
                info["instance"].roles = unique_roles

        for node_id, node in home.items():
            if not isinstance(node, dict):
                continue
            connections = []
            for output in (node.get("outputs", {}) or {}).values():
                if not isinstance(output, dict):
                    continue
                for connection in output.get("connections", []) or []:
                    if isinstance(connection, dict) and connection.get("node") is not None:
                        connections.append(str(connection["node"]))
            self.connections[str(node_id)] = connections

    def get_start_nodes(self, realtime=False):
        ignored = {"Roles", "RolesEngaged"}
        all_nodes = {
            node_id for node_id, node in self.nodes.items()
            if node["type"] not in ignored
            and not (realtime and node["type"] in REALTIME_SKIP_NODE_TYPES)
        }
        targets = set()
        for source_id, children in self.connections.items():
            source = self.nodes.get(str(source_id))
            if not source or source["type"] in ignored:
                continue
            if realtime and source["type"] in REALTIME_SKIP_NODE_TYPES:
                continue
            targets.update(child for child in children if child in all_nodes)
        return sorted(all_nodes - targets)

    def next_nodes(self, node_id):
        return self.connections.get(str(node_id), [])

    def _prepare_payload(self, data):
        payload = copy.deepcopy(data) if isinstance(data, dict) else {}
        payload.setdefault("CompanyID", self.company_id)
        return payload

    def _execute_single(self, node_id, data):
        info = self.nodes.get(str(node_id))
        if not info:
            raise KeyError(f"Unknown node id: {node_id}")
        result = info["instance"].execute(data)
        if result is None:
            result = data
        if not isinstance(result, dict):
            raise TypeError(f"Node {node_id} ({info['type']}) must return a dict or None")
        if info["type"] == "PLCReader":
            result = self.edge_trigger_service.enrich(result)
        result.setdefault("CompanyID", self.company_id)
        return result

    def execute_node(self, node_id, data, visited=None, realtime=False):
        if visited is None:
            visited = set()
        node_id = str(node_id)
        if node_id in visited or node_id not in self.nodes:
            return data
        visited.add(node_id)
        info = self.nodes[node_id]
        if realtime and info["type"] in REALTIME_SKIP_NODE_TYPES:
            return data

        payload = self._prepare_payload(data)
        try:
            payload = self._execute_single(node_id, payload)
            flow_status.node_ok(node_id)
        except Exception as exc:
            flow_status.node_error(node_id, exc)
            print("FLOW NODE ERROR:", "Node=", node_id, "Type=", info["type"], "Error=", repr(exc))
            return payload

        children = self.next_nodes(node_id)
        if not children:
            return payload

        branch_results = []
        for child in children:
            child_id = str(child)
            child_info = self.nodes.get(child_id)
            if not child_info:
                continue
            if realtime and child_info["type"] in REALTIME_SKIP_NODE_TYPES:
                continue
            child_result = self.execute_node(
                child_id,
                copy.deepcopy(payload),
                visited.copy(),
                realtime=realtime,
            )
            branch_results.append({"node_id": child_id, "data": child_result})

        result = copy.deepcopy(payload)
        result["_BranchResults"] = branch_results
        return result

    def execute_trend_branch(self, node_id, data, visited=None):
        if visited is None:
            visited = set()
        node_id = str(node_id)
        if node_id in visited or node_id not in self.nodes:
            return None
        visited.add(node_id)

        payload = self._prepare_payload(data)
        info = self.nodes[node_id]
        try:
            payload = self._execute_single(node_id, payload)
            flow_status.node_ok(node_id)
        except Exception as exc:
            flow_status.node_error(node_id, exc)
            print("TREND FLOW NODE ERROR:", "Node=", node_id, "Type=", info["type"], "Error=", repr(exc))
            return None

        if isinstance(payload.get("ChartData"), dict):
            return payload

        for child in self.next_nodes(node_id):
            result = self.execute_trend_branch(child, copy.deepcopy(payload), visited.copy())
            if isinstance(result, dict) and isinstance(result.get("ChartData"), dict):
                return result
        return None

    def run(self):
        flow_status.start()
        start_nodes = self.get_start_nodes(realtime=True)
        try:
            scan_interval = max(0.25, float(self.flow_data.get("scan_interval", 1)))
        except (TypeError, ValueError):
            scan_interval = 1.0

        while self.running:
            started = time.monotonic()
            previous_errors = flow_status.error_count
            flow_status.update_scan()
            for node_id in start_nodes:
                try:
                    self.execute_node(node_id, {}, set(), realtime=True)
                except Exception as exc:
                    flow_status.node_error("__engine__", exc)
                    traceback.print_exc()
            if flow_status.error_count == previous_errors:
                flow_status.clear_health_error()
            remaining = scan_interval - (time.monotonic() - started)
            time.sleep(remaining if remaining > 0 else 0.01)

    def execute_request(self, request):
        start_nodes = self.get_start_nodes(realtime=False)
        requested_tag = request.get("TrendRequest", {}).get("Tag") if isinstance(request, dict) else None
        print("TREND FLOW START:", "Company=", self.company_id, "Tag=", requested_tag, "StartNodes=", start_nodes)

        for node_id in start_nodes:
            result = self.execute_trend_branch(node_id, copy.deepcopy(request), set())
            if not isinstance(result, dict):
                continue
            chart_data = result.get("ChartData")
            if not isinstance(chart_data, dict):
                continue
            datasets = chart_data.get("datasets", [])
            if not isinstance(datasets, list):
                continue
            if not requested_tag:
                return result
            wanted = str(requested_tag).strip().lower()
            for dataset in datasets:
                if not isinstance(dataset, dict):
                    continue
                dataset_tag = dataset.get("tag") or dataset.get("title") or dataset.get("name")
                if str(dataset_tag).strip().lower() == wanted:
                    return result
        return request

    def stop(self):
        self.running = False
        flow_status.stop()

    def get_flow_roles(self):
        roles = []
        for node in self.nodes.values():
            if node["type"] != "Roles":
                continue
            instance = node["instance"]
            if hasattr(instance, "get_roles"):
                values = instance.get_roles()
                if isinstance(values, list):
                    roles.extend(values)
        return roles

    def get_page_access(self):
        access = {}
        for node_id, node in self.nodes.items():
            if node["type"] != "RolesEngaged":
                continue
            instance = node["instance"]
            if not hasattr(instance, "get_allowed_roles"):
                continue
            roles = instance.get_allowed_roles()
            if not isinstance(roles, list):
                roles = []
            roles = [str(role).strip() for role in roles if str(role).strip()]
            for target in self.connections.get(str(node_id), []):
                access.setdefault(str(target), [])
                for role in roles:
                    if role not in access[str(target)]:
                        access[str(target)].append(role)
        return access

    def can_access_page(self, node_id, user_role):
        access = self.get_page_access()
        node_id = str(node_id)
        if node_id not in access:
            return True
        allowed = {str(role).strip().lower() for role in access[node_id]}
        return str(user_role).strip().lower() in allowed
