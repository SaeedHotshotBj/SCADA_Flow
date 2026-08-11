import time
import traceback

from flow_engine.registry import get_node_class
from flow_status import flow_status


class FlowRunner:

    # =====================================================
    # INIT
    # =====================================================

    def __init__(self, flow_data, company_id):

        if company_id is None:
            raise ValueError(
                "FlowRunner requires company_id"
            )

        self.nodes = {}
        self.connections = {}

        self.running = True

        self.flow_data = flow_data

        self.company_id = int(
            company_id
        )

        self.load_flow()

    # =====================================================
    # CONFIG
    # =====================================================

    def get_node_config(self, node):

        data = node.get(
            "data",
            {}
        )

        if "config" in data:

            config = dict(
                data["config"]
            )

        else:

            config = dict(
                data
            )

        config["company_id"] = self.company_id

        return config

    # =====================================================
    # LOAD FLOW
    # =====================================================

    def load_flow(self):

        flow = self.flow_data

        if not flow or "drawflow" not in flow:

            print(
                "Invalid flow format"
            )

            return

        print(
            "Loading Drawflow format"
        )

        home = (
            flow
            .get("drawflow", {})
            .get("Home", {})
            .get("data", {})
        )

        # =================================================
        # LOAD NODES
        # =================================================

        for node_id, node in home.items():

            node_type = node.get(
                "name"
            )

            config = self.get_node_config(
                node
            )

            node_class = get_node_class(
                node_type
            )

            if not node_class:

                print(
                    "UNKNOWN NODE:",
                    node_type
                )

                continue

            self.nodes[str(node_id)] = {

                "instance":
                    node_class(config),

                "type":
                    node_type,

                "config":
                    config

            }

            print(
                "Loaded Node:",
                node_type,
                "CompanyID:",
                self.company_id
            )

        # =================================================
        # CONNECT ROLE DEFINITIONS TO ROLES ENGAGED
        # =================================================

        role_definitions = []

        for node_id, node in home.items():

            if node.get("name") != "Roles":
                continue

            role_config = self.get_node_config(
                node
            )

            node_roles = role_config.get(
                "roles",
                []
            )

            if not isinstance(
                node_roles,
                list
            ):
                continue

            for role in node_roles:

                if not isinstance(
                    role,
                    dict
                ):
                    continue

                role_name = str(
                    role.get(
                        "role",
                        ""
                    )
                ).strip()

                if not role_name:
                    continue

                role_definitions.append(
                    role
                )

        # Remove duplicate roles
        unique_roles = []

        seen_roles = set()

        for role in role_definitions:

            role_name = str(
                role.get(
                    "role",
                    ""
                )
            ).strip()

            key = role_name.lower()

            if key in seen_roles:
                continue

            seen_roles.add(key)

            unique_roles.append(
                role
            )

        role_definitions = unique_roles

        print(
            "FLOW ROLE DEFINITIONS:",
            role_definitions
        )

        # =================================================
        # APPLY ROLES TO ALL ROLES ENGAGED NODES
        # =================================================

        for node_id, node in home.items():

            if node.get("name") != "RolesEngaged":
                continue

            engaged_id = str(
                node_id
            )

            if engaged_id not in self.nodes:
                continue

            engaged_config = self.nodes[
                engaged_id
            ]["config"]

            engaged_config["roles"] = (
                role_definitions
            )

            instance = self.nodes[
                engaged_id
            ]["instance"]

            instance.roles = (
                role_definitions
            )

            print(
                "ROLES CONNECTED:",
                engaged_id,
                role_definitions
            )

        # =================================================
        # LOAD CONNECTIONS
        # =================================================

        for node_id, node in home.items():

            self.connections[
                str(node_id)
            ] = []

            outputs = node.get(
                "outputs",
                {}
            )

            for output in outputs.values():

                for connection in output.get(
                    "connections",
                    []
                ):

                    target = connection.get(
                        "node"
                    )

                    if target is not None:

                        self.connections[
                            str(node_id)
                        ].append(
                            str(target)
                        )

    # =====================================================
    # START NODES
    # =====================================================

    def get_start_nodes(self):

        ignored_types = {
            "Roles",
            "RolesEngaged"
        }

        all_nodes = {
            node_id
            for node_id, node in self.nodes.items()
            if node["type"] not in ignored_types
        }

        targets = set()

        for source_id, nodes in self.connections.items():

            source_node = self.nodes.get(
                str(source_id)
            )

            if not source_node:
                continue

            if source_node["type"] in ignored_types:
                continue

            for node in nodes:

                if node in all_nodes:

                    targets.add(
                        node
                    )

        return list(
            all_nodes - targets
        )

    # =====================================================
    # NEXT
    # =====================================================

    def next_nodes(self, node_id):

        return self.connections.get(
            str(node_id),
            []
        )

    # =====================================================
    # EXECUTE NODE
    # =====================================================

    def execute_node(
        self,
        node_id,
        data,
        visited=None
    ):

        if visited is None:

            visited = set()

        node_id = str(
            node_id
        )

        if node_id in visited:

            return data

        visited.add(
            node_id
        )

        if node_id not in self.nodes:

            return data

        node = self.nodes[
            node_id
        ]["instance"]

        try:

            result = node.execute(
                data
            )

            if result is not None:

                data = result

            flow_status.node_ok(
                node_id
            )

        except Exception as e:

            print()

            print(
                "NODE ERROR:",
                self.nodes[node_id]["type"]
            )

            print(
                "CompanyID:",
                self.company_id
            )

            print(
                e
            )

            traceback.print_exc()

            flow_status.node_error(
                node_id,
                e
            )

        children = self.next_nodes(
            node_id
        )

        for child in children:

            data = self.execute_node(
                child,
                data,
                visited.copy()
            )

        return data

    # =====================================================
    # REALTIME ENGINE
    # =====================================================

    def run(self):

        print()

        print(
            "FLOW ENGINE RUNNING"
        )

        print(
            "COMPANY ID:",
            self.company_id
        )

        print()

        flow_status.start()

        start_nodes = self.get_start_nodes()

        print(
            "START NODES:",
            start_nodes
        )

        while self.running:

            try:

                flow_status.update_scan()

                for node in start_nodes:

                    self.execute_node(
                        node,
                        {},
                        set()
                    )

            except Exception as e:

                print(
                    "FLOW ENGINE ERROR:",
                    e
                )

                traceback.print_exc()

            time.sleep(1)

    # =====================================================
    # TREND REQUEST ENGINE
    # =====================================================

    def execute_request(
        self,
        request
    ):

        print()

        print(
            "TREND REQUEST:",
            request
        )

        print(
            "COMPANY ID:",
            self.company_id
        )

        print()

        result = request

        start_nodes = self.get_start_nodes()

        for node in start_nodes:

            result = self.execute_node(
                node,
                result,
                set()
            )

        return result

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

            if hasattr(
                instance,
                "get_roles"
            ):

                node_roles = instance.get_roles()

                if isinstance(
                    node_roles,
                    list
                ):

                    roles.extend(
                        node_roles
                    )

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

            if not hasattr(
                instance,
                "get_allowed_roles"
            ):
                continue

            roles = instance.get_allowed_roles()

            if not isinstance(
                roles,
                list
            ):
                roles = []

            roles = [
                str(role).strip()
                for role in roles
                if str(role).strip()
            ]

            # ---------------------------------------------
            # RolesEngaged -> target page
            # ---------------------------------------------

            for target in self.connections.get(
                str(node_id),
                []
            ):

                target = str(
                    target
                )

                if target not in access:

                    access[target] = []

                for role in roles:

                    if role not in access[target]:

                        access[target].append(
                            role
                        )

        return access

    # =====================================================
    # CHECK PAGE ACCESS
    # =====================================================

    def can_access_page(
        self,
        node_id,
        user_role
    ):

        access = self.get_page_access()

        node_id = str(
            node_id
        )

        # Page has no RolesEngaged.
        # Keep existing behavior.
        if node_id not in access:

            return True

        return (
            str(user_role).strip().lower()
            in [
                str(role).strip().lower()
                for role in access[node_id]
            ]
        )