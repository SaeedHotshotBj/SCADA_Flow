# =====================================================
# SCADA_FLOW DASHBOARD DATA
# FLOW BASED TAG READER
# MULTI-COMPANY
# =====================================================

import json

from database import get_company_flow


# =====================================================
# GET TAGS FROM COMPANY FLOW
# =====================================================

def get_flow_tags(company_id):

    tags = []

    try:

        flow_json = get_company_flow(
            company_id
        )

        if not flow_json:
            return tags

        flow = json.loads(
            flow_json
        )

        nodes = (
            flow
            .get("drawflow", {})
            .get("Home", {})
            .get("data", {})
        )

        for node in nodes.values():

            if node.get("name") != "TagMapper":
                continue

            mappings = (
                node
                .get("data", {})
                .get("mappings", [])
            )

            for item in mappings:

                name = item.get("name")

                if not name:
                    continue

                tags.append({
                    "tag": name,
                    "title": item.get(
                        "title",
                        name
                    ),
                    "unit": item.get(
                        "unit",
                        ""
                    )
                })

            break

    except Exception as e:

        print(
            "FLOW TAG ERROR:",
            e
        )

    return tags





    # =====================================================
    # FLOW ROLES
    # =====================================================

    def get_flow_roles(company_id):

        import json

        roles = []

        try:

            flow_json = get_company_flow(
                company_id
            )

            if not flow_json:

                return roles

            flow = json.loads(
                flow_json
            )

            nodes = (
                flow
                .get("drawflow", {})
                .get("Home", {})
                .get("data", {})
            )

            for node in nodes.values():

                if node.get("name") != "Roles":
                    continue

                node_roles = (
                    node
                    .get("data", {})
                    .get("roles", [])
                )

                for item in node_roles:

                    role = str(
                        item.get(
                            "role",
                            ""
                        )
                    ).strip()

                    username = str(
                        item.get(
                            "username",
                            ""
                        )
                    ).strip()

                    if not role or not username:
                        continue

                    roles.append({
                        "role": role,
                        "username": username
                    })

        except Exception as e:

            print(
                "FLOW ROLE ERROR:",
                e
            )

        return roles


    # =====================================================
    # FLOW ROLE NAMES
    # =====================================================

    def get_flow_role_names(company_id):

        roles = get_flow_roles(
            company_id
        )

        result = []

        for item in roles:

            role = item["role"]

            if role not in result:

                result.append(role)

        return result


# =====================================================
# START EDGE TIMEOUT WORKER
# =====================================================
# dashboard_data.py is imported directly by app.py during module startup.
# Starting the worker here guarantees that it does not depend on a secondary
# service import path. The worker itself is guarded against duplicate starts.
try:
    from services.edge_timeout_service import start_worker as start_edge_timeout_worker
    start_edge_timeout_worker()
    print("EDGE TIMEOUT WORKER STARTED FROM DASHBOARD DATA")
except Exception as exc:
    print("EDGE TIMEOUT START ERROR FROM DASHBOARD DATA:", exc)
