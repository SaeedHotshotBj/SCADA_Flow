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

EVENT_PASSTHROUGH_NODE_TYPES = {
    "PLCReader",
    "TagMapper",
    "SQLWriter",
    "DashboardOutput",
    "AlarmNode",
    "MachineCard",
    "Pulse",
    "EdgeTimeout",
    "Roles",
    "RolesEngaged",
    "TrendReader",
    "TrendDatabaseReader",
    "TrendOutput",
}

EVENT_TERMINAL_NODE_TYPES = {
    "ReportOutput",
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

    def get_node_config(self, node, node_id=None):
        data = node.get("data", {}) or {}
        raw = data.get("config", data)
        config = dict(raw) if isinstance(raw, dict) else {}
        config["company_id"] = self.company_id
        if node_id is not None:
            config["node_id"] = str(node_id)
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
            config = self.get_node_config(node, node_id)
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
        for info in self.nodes.values():
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

    def _reverse_connections(self):
        reverse = {node_id: set() for node_id in self.nodes}
        for source_id, children in self.connections.items():
            for child_id in children:
                if child_id in reverse and source_id in self.nodes:
                    reverse[child_id].add(str(source_id))
        return reverse

    def _ancestor_nodes(self, node_id):
        reverse = self._reverse_connections()
        result = set()
        stack = [str(node_id)]
        while stack:
            current = stack.pop()
            for parent in reverse.get(current, set()):
                if parent in result:
                    continue
                result.add(parent)
                stack.append(parent)
        return result

    def _has_eligible_ancestor(self, node_id, eligible, reverse):
        """Find any executable ancestor across passthrough nodes."""
        stack = list(reverse.get(str(node_id), set()))
        seen = set()
        while stack:
            current = str(stack.pop())
            if current in seen:
                continue
            seen.add(current)
            if current in eligible:
                return True
            stack.extend(reverse.get(current, set()))
        return False

    def _execute_production_branch(self, node_id, data, visited):
        node_id = str(node_id)
        if node_id in visited or node_id not in self.nodes:
            return data
        visited = set(visited)
        visited.add(node_id)
        info = self.nodes[node_id]
        payload = self._prepare_payload(data)
        if info["type"] == "ReportOutput":
            payload["_CurrentReportNodeID"] = node_id

        if info["type"] in EVENT_PASSTHROUGH_NODE_TYPES:
            result = payload
        else:
            try:
                result = info["instance"].execute(payload)
                if result is None:
                    result = payload
                if not isinstance(result, dict):
                    raise TypeError(
                        f"Node {node_id} ({info['type']}) must return a dict or None"
                    )
                result.setdefault("CompanyID", self.company_id)
                flow_status.node_ok(node_id)
            except Exception as exc:
                flow_status.node_error(node_id, exc)
                print(
                    "PRODUCTION FLOW NODE ERROR:",
                    "Node=", node_id,
                    "Type=", info["type"],
                    "Error=", repr(exc),
                )
                return payload

        # ReportOutput is the terminal persistence node for production events.
        # Never continue through a ReportOutput into another branch or node.
        if info["type"] in EVENT_TERMINAL_NODE_TYPES:
            return copy.deepcopy(result)

        branch_results = []
        for child_id in self.next_nodes(node_id):
            child_result = self._execute_production_branch(
                child_id,
                copy.deepcopy(result),
                visited,
            )
            branch_results.append({"node_id": str(child_id), "data": child_result})
        result = copy.deepcopy(result)
        result["_BranchResults"] = branch_results
        return result

    def execute_production_event(self, event):
        """Run a production event through connected ReportOutput branches."""
        if not isinstance(event, dict):
            return None
        report_nodes = [
            node_id
            for node_id, info in self.nodes.items()
            if info["type"] == "ReportOutput"
        ]
        if not report_nodes:
            return None

        event_tags = dict(event.get("tags") or {})
        duration = float(event.get("duration_seconds", 0.0) or 0.0)
        event_tags.setdefault("DurationSeconds", duration)
        event_tags.setdefault("WorkTimeSeconds", duration)
        event_tags.setdefault("WorkTimeMinutes", duration / 60.0)
        event_tags.setdefault("WorkTimeHours", duration / 3600.0)
        payload = {
            "CompanyID": self.company_id,
            "PLC_ID": event.get("PLC_ID"),
            "Tags": event_tags,
            "ProductionEvent": copy.deepcopy(event),
            "Timestamp": event.get("timestamp"),
        }

        reverse = self._reverse_connections()
        results = []
        for report_node_id in report_nodes:
            ancestors = self._ancestor_nodes(report_node_id)
            if not ancestors:
                # A root ReportOutput is not connected to the production event.
                continue

            # A ReportOutput is a terminal target, never an executable upstream
            # source for another production report branch.
            eligible = {
                node_id
                for node_id in ancestors
                if self.nodes.get(node_id, {}).get("type") not in EVENT_PASSTHROUGH_NODE_TYPES
                and self.nodes.get(node_id, {}).get("type") not in EVENT_TERMINAL_NODE_TYPES
            }
            starts = sorted(
                node_id
                for node_id in eligible
                if not self._has_eligible_ancestor(node_id, eligible, reverse)
            )
            if not starts:
                # Valid direct paths such as TagMapper -> ReportOutput contain
                # only passthrough ancestors, so the event starts at ReportOutput.
                starts = [str(report_node_id)]
            for start_id in starts:
                result = self._execute_production_branch(
                    start_id,
                    copy.deepcopy(payload),
                    set(),
                )
                results.append(result)

        if not results:
            return None
        return results[0] if len(results) == 1 else {
            "CompanyID": self.company_id,
            "ProductionEvent": copy.deepcopy(event),
            "_BranchResults": results,
        }

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
