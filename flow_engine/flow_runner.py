import time
import traceback
import copy

from flow_engine.registry import get_node_class
from flow_status import flow_status


REALTIME_SKIP_NODE_TYPES = {
    "TrendReader",
    "TrendDatabaseReader",
    "TrendOutput",
}


class FlowRunner:

    # =====================================================
    # INIT
    # =====================================================

    def __init__(self, flow_data, company_id):
        if company_id is None:
            raise ValueError("FlowRunner requires company_id")

        self.nodes = {}
        self.connections = {}
        self.running = True
        self.flow_data = flow_data
        self.company_id = int(company_id)
        self.load_flow()

    # =====================================================
    # CONFIG
    # =====================================================

    def get_node_config(self, node):
        data = node.get("data", {})
        if "config" in data:
            config = dict(data["config"])
        else:
            config = dict(data)
        config["company_id"] = self.company_id
        return config

    # =====================================================
    # LOAD FLOW
    # =====================================================

    def load_flow(self):
        flow = self.flow_data
        if not flow or "drawflow" not in flow:
            return

        home = (
            flow.get("drawflow", {})
            .get("Home", {})
            .get("data", {})
        )

        for node_id, node in home.items():
            node_type = node.get("name")
            config = self.get_node_config(node)
            config["_node_id"] = str(node_id)
            node_class = get_node_class(node_type)
            if not node_class:
                continue

            self.nodes[str(node_id)] = {
                "instance": node_class(config),
                "type": node_type,
                "config": config,
            }

        role_definitions = []
        for node_id, node in home.items():
            if node.get("name") != "Roles":
                continue

            role_config = self.get_node_config(node)
            node_roles = role_config.get("roles", [])
            if not isinstance(node_roles, list):
                continue

            for role in node_roles:
                if not isinstance(role, dict):
                    continue
                role_name = str(role.get("role", "")).strip()
                if role_name:
                    role_definitions.append(role)

        unique_roles = []
        seen_roles = set()
        for role in role_definitions:
            role_name = str(role.get("role", "")).strip()
            key = role_name.lower()
            if key in seen_roles:
                continue
            seen_roles.add(key)
            unique_roles.append(role)

        role_definitions = unique_roles

        for node_id, node in home.items():
            if node.get("name") != "RolesEngaged":
                continue

            engaged_id = str(node_id)
            if engaged_id not in self.nodes:
                continue

            engaged_config = self.nodes[engaged_id]["config"]
            engaged_config["roles"] = role_definitions
            self.nodes[engaged_id]["instance"].roles = role_definitions

        for node_id, node in home.items():
            source_id = str(node_id)
            self.connections[source_id] = []

            outputs = node.get("outputs", {})
            for output in outputs.values():
                for connection in output.get("connections", []):
                    target = connection.get("node")
                    if target is not None:
                        self.connections[source_id].append(str(target))

    # =====================================================
    # START NODES
    # =====================================================

    def get_start_nodes(self, realtime=False):
        ignored_types = {"Roles", "RolesEngaged"}

        all_nodes = {
            node_id
            for node_id, node in self.nodes.items()
            if node["type"] not in ignored_types
            and not (
                realtime
                and node["type"] in REALTIME_SKIP_NODE_TYPES
            )
        }

        targets = set()
        for source_id, nodes in self.connections.items():
            source_node = self.nodes.get(str(source_id))
            if not source_node:
                continue
            if source_node["type"] in ignored_types:
                continue
            if realtime and source_node["type"] in REALTIME_SKIP_NODE_TYPES:
                continue

            for node in nodes:
                if node in all_nodes:
                    targets.add(node)

        return sorted(all_nodes - targets)

    def next_nodes(self, node_id):
        return self.connections.get(str(node_id), [])

    # =====================================================
    # NORMAL FLOW EXECUTION
    # =====================================================

    def execute_node(self, node_id, data, visited=None, realtime=False):
        if visited is None:
            visited = set()

        node_id = str(node_id)
        if node_id in visited:
            return data
        visited.add(node_id)

        if node_id not in self.nodes:
            return data

        node_info = self.nodes[node_id]

        if realtime and node_info["type"] in REALTIME_SKIP_NODE_TYPES:
            return data

        node = node_info["instance"]

        try:
            result = node.execute(data)
            if result is not None:
                data = result
            flow_status.node_ok(node_id)
        except Exception as exc:
            flow_status.node_error(node_id, exc)

        children = self.next_nodes(node_id)

        if not children:
            return data

        branch_results = []

        for child in children:
            if (
                realtime
                and self.nodes.get(str(child), {}).get("type")
                in REALTIME_SKIP_NODE_TYPES
            ):
                continue

            child_data = copy.deepcopy(data)
            child_result = self.execute_node(
                child,
                child_data,
                visited.copy(),
                realtime=realtime,
            )
            branch_results.append(child_result)

        return data

    # =====================================================
    # HISTORICAL TREND BRANCH EXECUTION
    # =====================================================

    def execute_trend_branch(self, node_id, data, visited=None):
        """Execute exactly the connected branch graph and return the first
        descendant result that contains ChartData.

        Connection topology remains the only routing mechanism. Each branch
        gets an isolated data snapshot, so Dashboard/Report branches cannot
        corrupt the Trend request state.
        """
        if visited is None:
            visited = set()

        node_id = str(node_id)
        if node_id in visited:
            return None
        visited.add(node_id)

        if node_id not in self.nodes:
            return None

        node = self.nodes[node_id]["instance"]

        try:
            result = node.execute(data)
            if result is not None:
                data = result
            flow_status.node_ok(node_id)
        except Exception as exc:
            flow_status.node_error(node_id, exc)
            print(
                "TREND FLOW NODE ERROR:",
                "Node=", node_id,
                "Type=", self.nodes[node_id]["type"],
                "Error=", repr(exc),
            )

        if isinstance(data, dict):
            chart = data.get("ChartData")
            if isinstance(chart, dict):
                return data

        for child in self.next_nodes(node_id):
            child_info = self.nodes.get(str(child))
            if not child_info:
                continue

            child_data = copy.deepcopy(data)
            result = self.execute_trend_branch(
                child,
                child_data,
                visited.copy(),
            )

            if isinstance(result, dict) and isinstance(
                result.get("ChartData"), dict
            ):
                return result

        return None

    # =====================================================
    # REALTIME ENGINE
    # =====================================================

    def run(self):
        flow_status.start()

        start_nodes = self.get_start_nodes(realtime=True)

        try:
            scan_interval = max(
                0.25,
                float(self.flow_data.get("scan_interval", 1)),
            )
        except (TypeError, ValueError):
            scan_interval = 1.0

        # Pulse nodes need a scan rate high enough to observe both edges of
        # their configured pulse width. Without this, a 1-second scan can
        # repeatedly sample a 100-ms pulse only at its ON phase.
        pulse_resolution = None
        for node_info in self.nodes.values():
            if node_info.get("type") != "Pulse":
                continue

            config = node_info.get("config", {})
            try:
                pulse_width = float(config.get("pulse_width", 0.1))
            except (TypeError, ValueError):
                continue

            if pulse_width <= 0:
                continue

            resolution = max(0.01, pulse_width / 2.0)
            if pulse_resolution is None or resolution < pulse_resolution:
                pulse_resolution = resolution

        if pulse_resolution is not None:
            scan_interval = min(scan_interval, pulse_resolution)

        while self.running:
            scan_started = time.monotonic()

            try:
                flow_status.update_scan()
                for node in start_nodes:
                    self.execute_node(
                        node,
                        {},
                        set(),
                        realtime=True,
                    )
            except Exception as exc:
                flow_status.node_error("__engine__", exc)

            elapsed = time.monotonic() - scan_started
            remaining = scan_interval - elapsed
            if remaining > 0:
                time.sleep(remaining)
            else:
                time.sleep(0.01)

    # =====================================================
    # TREND REQUEST ENGINE
    # =====================================================

    def execute_request(self, request):
        start_nodes = self.get_start_nodes(realtime=False)
        requested_tag = (
            request.get("TrendRequest", {}).get("Tag")
            if isinstance(request, dict)
            else None
        )

        print(
            "TREND FLOW START:",
            "Company=", self.company_id,
            "Tag=", requested_tag,
            "StartNodes=", start_nodes,
        )

        for node_id in start_nodes:
            branch_request = copy.deepcopy(request)
            result = self.execute_trend_branch(
                node_id,
                branch_request,
                set(),
            )

            if not isinstance(result, dict):
                continue

            chart_data = result.get("ChartData")
            if not isinstance(chart_data, dict):
                continue

            datasets = chart_data.get("datasets", [])
            if not isinstance(datasets, list):
                continue

            print(
                "TREND FLOW RESULT:",
                "Node=", node_id,
                "Datasets=", len(datasets),
                "Points=", sum(
                    len(ds.get("data", []))
                    for ds in datasets
                    if isinstance(ds, dict)
                ),
            )

            if not requested_tag:
                return result

            requested_key = str(requested_tag).strip().lower()
            for dataset in datasets:
                if not isinstance(dataset, dict):
                    continue

                dataset_tag = (
                    dataset.get("tag")
                    or dataset.get("title")
                    or dataset.get("name")
                )
                if str(dataset_tag).strip().lower() == requested_key:
                    return result

        return request

    # =====================================================
    # STOP
    # =====================================================

    def stop(self):
        self.running = False
        flow_status.stop()

    # =====================================================
    # ROLE ACCESS
    # =====================================================

    def get_flow_roles(self):
        roles = []
        for node_id, node in self.nodes.items():
            if node["type"] != "Roles":
                continue

            instance = node["instance"]
            if hasattr(instance, "get_roles"):
                node_roles = instance.get_roles()
                if isinstance(node_roles, list):
                    roles.extend(node_roles)

        return roles

    # =====================================================
    # PAGE ACCESS
    # =====================================================

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

            roles = [
                str(role).strip()
                for role in roles
                if str(role).strip()
            ]

            for target in self.connections.get(str(node_id), []):
                target = str(target)
                access.setdefault(target, [])
                for role in roles:
                    if role not in access[target]:
                        access[target].append(role)

        return access

    # =====================================================
    # CHECK PAGE ACCESS
    # =====================================================

    def can_access_page(self, node_id, user_role):
        access = self.get_page_access()
        node_id = str(node_id)

        if node_id not in access:
            return True

        return (
            str(user_role).strip().lower()
            in [
                str(role).strip().lower()
                for role in access[node_id]
            ]
        )
