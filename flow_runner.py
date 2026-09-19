import copy
import time
import traceback
from collections import deque

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
                    role
                    for role in roles
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
            node_id
            for node_id, node in self.nodes.items()
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
            print(
                "FLOW NODE ERROR:",
                "Node=",
                node_id,
                "Type=",
                info["type"],
                "Error=",
                repr(exc),
            )
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

    @staticmethod
    def _merge_production_payloads(payloads):
        merged = {}
        merged_tags = {}
        merged_event = None
        merged_branch_results = []
        merged_report_calculations = []

        for payload in payloads:
            if not isinstance(payload, dict):
                continue
            for key, value in payload.items():
                if key == "Tags" and isinstance(value, dict):
                    merged_tags.update(copy.deepcopy(value))
                    continue
                if key == "ProductionEvent" and merged_event is None:
                    merged_event = copy.deepcopy(value)
                    continue
                if key == "_BranchResults":
                    if isinstance(value, list):
                        merged_branch_results.extend(copy.deepcopy(value))
                    continue
                if key == "ReportCalculations":
                    if isinstance(value, list):
                        merged_report_calculations.extend(copy.deepcopy(value))
                    continue
                if key not in merged:
                    merged[key] = copy.deepcopy(value)
                elif isinstance(merged[key], dict) and isinstance(value, dict):
                    nested = copy.deepcopy(merged[key])
                    nested.update(copy.deepcopy(value))
                    merged[key] = nested
                else:
                    merged[key] = copy.deepcopy(value)

        if merged_tags:
            merged["Tags"] = merged_tags
        if merged_event is not None:
            merged["ProductionEvent"] = merged_event
        if merged_branch_results:
            merged["_BranchResults"] = merged_branch_results
        if merged_report_calculations:
            merged["ReportCalculations"] = merged_report_calculations
        return merged

    def _production_report_targets(self):
        targets = []
        for node_id, info in self.nodes.items():
            if info["type"] != "ReportOutput":
                continue
            ancestors = self._ancestor_nodes(node_id)
            if not ancestors:
                continue
            # ReportOutput is terminal. A report node must never be an
            # upstream calculation/source for another production report.
            if any(
                self.nodes.get(ancestor, {}).get("type") == "ReportOutput"
                for ancestor in ancestors
            ):
                continue
            targets.append(str(node_id))
        return sorted(targets)

    def _execute_production_graph(self, target_reports, base_payload):
        reverse = self._reverse_connections()
        relevant = set(target_reports)
        for report_id in target_reports:
            relevant.update(self._ancestor_nodes(report_id))

        # Production ReportOutput nodes are terminals. Exclude any report node
        # that was not selected as a valid terminal target from the graph.
        relevant = {
            node_id
            for node_id in relevant
            if self.nodes.get(node_id, {}).get("type") != "ReportOutput"
            or node_id in target_reports
        }

        indegree = {node_id: 0 for node_id in relevant}
        children = {node_id: [] for node_id in relevant}
        for source_id in relevant:
            for child_id in self.next_nodes(source_id):
                if child_id not in relevant:
                    continue
                # Never route a production event through a ReportOutput as an
                # upstream node. Only the selected terminal target is executed.
                if (
                    self.nodes.get(child_id, {}).get("type") == "ReportOutput"
                    and child_id not in target_reports
                ):
                    continue
                children[source_id].append(child_id)
                indegree[child_id] += 1

        ready = deque(sorted(node_id for node_id, degree in indegree.items() if degree == 0))
        pending = {}
        results = []
        processed = set()

        while ready:
            node_id = ready.popleft()
            processed.add(node_id)
            incoming = pending.pop(node_id, [])
            payload = self._merge_production_payloads(incoming or [base_payload])
            info = self.nodes[node_id]

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
                        "Node=",
                        node_id,
                        "Type=",
                        info["type"],
                        "Error=",
                        repr(exc),
                    )
                    return None

            if info["type"] in EVENT_TERMINAL_NODE_TYPES:
                results.append(copy.deepcopy(result))
                continue

            for child_id in children.get(node_id, []):
                pending.setdefault(child_id, []).append(copy.deepcopy(result))
                indegree[child_id] -= 1
                if indegree[child_id] == 0:
                    ready.append(child_id)

        if len(processed) != len(relevant):
            raise ValueError("Production Flow contains a cycle or unresolved dependency")

        return results

    def execute_production_event(self, event):
        """Run a production event through the connected Flow graph exactly once."""
        if not isinstance(event, dict):
            return None

        report_nodes = self._production_report_targets()
        if not report_nodes:
            return None

        payload = {
            "CompanyID": self.company_id,
            "PLC_ID": event.get("PLC_ID"),
            "Tags": dict(event.get("tags") or {}),
            "ProductionEvent": copy.deepcopy(event),
            "Timestamp": event.get("timestamp"),
        }

        results = self._execute_production_graph(report_nodes, payload)
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
            print(
                "TREND FLOW NODE ERROR:",
                "Node=",
                node_id,
                "Type=",
                info["type"],
                "Error=",
                repr(exc),
            )
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
        print(
            "TREND FLOW START:",
            "Company=",
            self.company_id,
            "Tag=",
            requested_tag,
            "StartNodes=",
            start_nodes,
        )

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
