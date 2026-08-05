import json
import os

from database import get_connection


def sync_flow_file(company_id=1, flow_file="flow.json"):

    base_dir = os.path.dirname(os.path.abspath(__file__))
    flow_path = os.path.join(base_dir, flow_file)

    with open(flow_path, encoding="utf-8") as f:
        flow_json = f.read()

    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute(
        "SELECT FlowID FROM Flows WHERE CompanyID = ?",
        (company_id,),
    )
    row = cursor.fetchone()

    if row:
        cursor.execute(
            """
            UPDATE Flows
            SET FlowJson = ?, LastModified = datetime('now', 'localtime')
            WHERE CompanyID = ?
            """,
            (flow_json, company_id),
        )
    else:
        cursor.execute(
            """
            INSERT INTO Flows (CompanyID, FlowJson, LastModified)
            VALUES (?, ?, datetime('now', 'localtime'))
            """,
            (company_id, flow_json),
        )

    conn.commit()
    conn.close()

    data = json.loads(flow_json)
    node_count = len(data["drawflow"]["Home"]["data"])
    print(f"Flow synced to database ({node_count} nodes)")


if __name__ == "__main__":
    sync_flow_file()
