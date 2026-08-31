"""Remove accidentally persisted management nodes from saved company flows.

This script changes ONLY the Flows.FlowJson column. PLC_Data, TagHistory,
ReportHistory, ReportValues, TrendMinute and all other tables are untouched.
"""

import json

from database import get_connection


LEGACY_MANAGEMENT_NODE_TYPES = {
    "ManagementRolesEngaged",
    "ManagementPanelOutput",
    "ManagementInput",
    "ContractRepository",
    "ProductBOMRepository",
    "ManagementCostCalculator",
    "ManagementOutput",
}


def clean_flow(flow):
    if not isinstance(flow, dict):
        return flow, False

    nodes = (
        flow.get("drawflow", {})
        .get("Home", {})
        .get("data", {})
    )
    if not isinstance(nodes, dict):
        return flow, False

    remove_ids = {
        str(node_id)
        for node_id, node in nodes.items()
        if isinstance(node, dict)
        and str(node.get("name", "")).strip() in LEGACY_MANAGEMENT_NODE_TYPES
    }
    if not remove_ids:
        return flow, False

    cleaned = json.loads(json.dumps(flow, ensure_ascii=False))
    nodes = (
        cleaned.get("drawflow", {})
        .get("Home", {})
        .get("data", {})
    )

    for node_id in list(nodes.keys()):
        if str(node_id) in remove_ids:
            del nodes[node_id]

    for node in nodes.values():
        if not isinstance(node, dict):
            continue

        for output in (node.get("outputs", {}) or {}).values():
            if not isinstance(output, dict):
                continue
            output["connections"] = [
                connection
                for connection in (output.get("connections", []) or [])
                if str(connection.get("node", "")) not in remove_ids
            ]

        for input_node in (node.get("inputs", {}) or {}).values():
            if not isinstance(input_node, dict):
                continue
            input_node["connections"] = [
                connection
                for connection in (input_node.get("connections", []) or [])
                if str(connection.get("node", "")) not in remove_ids
            ]

    return cleaned, True


def main():
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT FlowID, CompanyID, FlowJson FROM Flows ORDER BY FlowID"
        ).fetchall()

        changed = 0
        for row in rows:
            try:
                flow = json.loads(row["FlowJson"] or "{}")
            except Exception as exc:
                print(f"SKIP FlowID={row['FlowID']}: invalid JSON: {exc}")
                continue

            cleaned, was_changed = clean_flow(flow)
            if not was_changed:
                continue

            conn.execute(
                """
                UPDATE Flows
                SET FlowJson = ?,
                    LastModified = datetime('now', 'localtime')
                WHERE FlowID = ?
                """,
                (json.dumps(cleaned, ensure_ascii=False), row["FlowID"]),
            )
            print(
                f"REPAIRED FlowID={row['FlowID']} CompanyID={row['CompanyID']}"
            )
            changed += 1

        conn.commit()
        print(f"DONE: repaired {changed} flow(s). Other database tables were not modified.")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
