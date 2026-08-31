"""Remove accidentally persisted Management nodes from saved company flows.

This is a one-time repair utility. It only edits Flows.FlowJson and leaves
PLC/Tag/Report/Trend/Historian tables untouched.
"""

import argparse
import json

from database import get_connection


LEGACY_MANAGEMENT_NODE_NAMES = {
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
        return flow, 0

    data = (
        flow.get("drawflow", {})
        .get("Home", {})
        .get("data", {})
    )
    if not isinstance(data, dict):
        return flow, 0

    remove_ids = {
        str(node_id)
        for node_id, node in data.items()
        if isinstance(node, dict)
        and str(node.get("name", "")).strip() in LEGACY_MANAGEMENT_NODE_NAMES
    }

    if not remove_ids:
        return flow, 0

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
            connections = output.get("connections", [])
            if isinstance(connections, list):
                output["connections"] = [
                    connection
                    for connection in connections
                    if str(connection.get("node", "")) not in remove_ids
                ]

        for input_node in (node.get("inputs", {}) or {}).values():
            if not isinstance(input_node, dict):
                continue
            connections = input_node.get("connections", [])
            if isinstance(connections, list):
                input_node["connections"] = [
                    connection
                    for connection in connections
                    if str(connection.get("node", "")) not in remove_ids
                ]

    return cleaned, len(remove_ids)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--company-id", type=int, default=None)
    args = parser.parse_args()

    conn = get_connection()
    try:
        if args.company_id is None:
            rows = conn.execute(
                "SELECT FlowID, CompanyID, FlowJson FROM Flows ORDER BY FlowID"
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT FlowID, CompanyID, FlowJson FROM Flows WHERE CompanyID = ? ORDER BY FlowID",
                (args.company_id,),
            ).fetchall()

        changed = 0
        removed_nodes = 0

        for row in rows:
            try:
                flow = json.loads(row["FlowJson"] or "{}")
            except Exception as exc:
                print(f"SKIP FlowID={row['FlowID']}: invalid JSON: {exc}")
                continue

            cleaned, removed = clean_flow(flow)
            if removed == 0:
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
            changed += 1
            removed_nodes += removed
            print(
                f"CLEANED FlowID={row['FlowID']} CompanyID={row['CompanyID']} "
                f"removed_nodes={removed}"
            )

        conn.commit()
        print(
            f"DONE: cleaned_flows={changed}, removed_management_nodes={removed_nodes}."
        )
        print("No PLC_Data, TagHistory, ReportHistory, ReportValues or TrendMinute rows were modified.")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
