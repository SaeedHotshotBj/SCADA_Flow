import base64
import os
import tempfile

import database
from services.edge_management import (
    ensure_edge_management_schema,
    generate_pairing_token,
    get_command_status,
    get_company_edges,
    poll_command,
    queue_command,
    record_command_result,
    register_or_heartbeat,
)


def main():
    with tempfile.TemporaryDirectory() as temp_dir:
        database.DB_CONFIG["path"] = os.path.join(temp_dir, "edge_management_test.db")
        database.init_database()
        conn = database.get_connection()
        conn.execute("INSERT INTO Companies (CompanyName) VALUES (?)", ("Test Company",))
        conn.commit()
        conn.close()

        ensure_edge_management_schema()

        pairing = generate_pairing_token(1)
        token = pairing["token"]

        register_or_heartbeat(
            {
                "edge_id": "EDGE-TEST-01",
                "company_id": 1,
                "token": token,
                "hostname": "TEST-PC",
                "platform": "Windows Test",
                "agent_version": "1.0.0",
                "root_path": r"C:\SCADA_FLOW_EDGE",
                "app_running": False,
                "app_pid": None,
            }
        )

        edges = get_company_edges(1)
        assert len(edges) == 1
        assert edges[0]["edge_id"] == "EDGE-TEST-01"
        assert edges[0]["status"] == "ONLINE"

        content = base64.b64encode(b"print('hello')\n").decode("ascii")
        command_id = queue_command(
            1,
            "EDGE-TEST-01",
            "WRITE_FILE",
            "modules/test.py",
            {"content_b64": content},
        )

        command = poll_command("EDGE-TEST-01", token)
        assert command is not None
        assert command["command_id"] == command_id
        assert command["command"] == "WRITE_FILE"
        assert command["path"] == "modules/test.py"

        record_command_result(
            "EDGE-TEST-01",
            token,
            command_id,
            True,
            {"ok": True, "message": "saved"},
        )

        status = get_command_status(1, command_id)
        assert status["status"] == "SUCCESS"
        assert status["result"]["ok"] is True

        try:
            poll_command("EDGE-TEST-01", "wrong-token")
        except PermissionError:
            pass
        else:
            raise AssertionError("Invalid token was accepted")

        try:
            queue_command(
                1,
                "EDGE-TEST-01",
                "DELETE_FILE",
                "../outside.txt",
            )
        except ValueError:
            pass
        else:
            raise AssertionError("Path traversal was accepted")

        print("EDGE MANAGEMENT SERVER TEST OK")


if __name__ == "__main__":
    main()
