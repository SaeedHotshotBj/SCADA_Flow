"""Explicit application-service bootstrap.

The services package is import-safe. Process-level startup happens here only,
after Flask and SQLite initialization.
"""

import json


LEGACY_MANAGEMENT_NODE_NAMES = {
    "ManagementRolesEngaged",
    "ManagementPanelOutput",
    "ManagementInput",
    "ContractRepository",
    "ProductBOMRepository",
    "ManagementCostCalculator",
    "ManagementOutput",
}


def _extract_plc_reader(flow_data):
    nodes = (flow_data or {}).get("drawflow", {}).get("Home", {}).get("data", {})
    if not isinstance(nodes, dict):
        return None
    for node in nodes.values():
        if isinstance(node, dict) and node.get("name") == "PLCReader":
            return node
    return None


def _sanitize_company_flow(flow_data):
    """Normalize Drawflow IDs and remove only known accidental legacy nodes."""
    if not isinstance(flow_data, dict):
        return flow_data, False

    home = (flow_data.get("drawflow", {}) or {}).get("Home", {})
    nodes = home.get("data") if isinstance(home, dict) else None
    if not isinstance(nodes, dict):
        return flow_data, False

    cleaned = json.loads(json.dumps(flow_data, ensure_ascii=False))
    cleaned_nodes = cleaned.get("drawflow", {}).get("Home", {}).get("data", {})
    changed = False

    remove_ids = {
        str(node_id)
        for node_id, node in cleaned_nodes.items()
        if isinstance(node, dict)
        and str(node.get("name", "")).strip() in LEGACY_MANAGEMENT_NODE_NAMES
    }

    for node_id in list(cleaned_nodes.keys()):
        if str(node_id) in remove_ids:
            del cleaned_nodes[node_id]
            changed = True

    key_id_map = {}
    for node_id, node in cleaned_nodes.items():
        if not isinstance(node, dict):
            continue
        old_id = str(node.get("id", node_id))
        new_id = str(node_id)
        key_id_map[old_id] = new_id
        if old_id != new_id:
            node["id"] = int(new_id) if new_id.isdigit() else new_id
            changed = True

    for node in cleaned_nodes.values():
        if not isinstance(node, dict):
            continue
        for output in (node.get("outputs", {}) or {}).values():
            if not isinstance(output, dict):
                continue
            connections = output.get("connections", [])
            if not isinstance(connections, list):
                continue
            repaired = []
            for connection in connections:
                if not isinstance(connection, dict):
                    continue
                target = str(connection.get("node", ""))
                if target in remove_ids:
                    changed = True
                    continue
                mapped = key_id_map.get(target, target)
                if mapped != target:
                    connection["node"] = mapped
                    changed = True
                repaired.append(connection)
            output["connections"] = repaired

        for input_item in (node.get("inputs", {}) or {}).values():
            if not isinstance(input_item, dict):
                continue
            connections = input_item.get("connections", [])
            if not isinstance(connections, list):
                continue
            repaired = []
            for connection in connections:
                if not isinstance(connection, dict):
                    continue
                source = str(connection.get("node", ""))
                if source in remove_ids:
                    changed = True
                    continue
                mapped = key_id_map.get(source, source)
                if mapped != source:
                    connection["node"] = mapped
                    changed = True
                repaired.append(connection)
            input_item["connections"] = repaired

    return cleaned, changed


def _sanitize_saved_flow(conn, row):
    raw = row["FlowJson"]
    try:
        flow = json.loads(raw or "{}")
    except Exception as exc:
        print("FLOW JSON REPAIR PARSE ERROR:", row["FlowID"], exc)
        return None

    cleaned, changed = _sanitize_company_flow(flow)
    if not changed:
        return cleaned

    conn.execute(
        """
        UPDATE Flows
        SET FlowJson = ?,
            LastModified = datetime('now', 'localtime')
        WHERE FlowID = ?
        """,
        (json.dumps(cleaned, ensure_ascii=False), row["FlowID"]),
    )
    return cleaned


def _sync_flow_plc(flow_data, company_id):
    plc_reader = _extract_plc_reader(flow_data)
    if company_id is None or plc_reader is None:
        return False
    data = plc_reader.get("data", {}) or {}
    ip = str(data.get("ip", "")).strip()
    if not ip:
        return False
    try:
        port = int(data.get("port", 502))
    except (TypeError, ValueError):
        port = 502
    try:
        slave = int(data.get("slave", 1))
    except (TypeError, ValueError):
        slave = 1
    name = str(data.get("name") or data.get("PLC_Name") or "PLC").strip()

    from database import get_connection
    conn = cursor = None
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT PLC_ID FROM PLCs WHERE CompanyID = ? ORDER BY PLC_ID LIMIT 1",
            (int(company_id),),
        )
        row = cursor.fetchone()
        if row:
            cursor.execute(
                "UPDATE PLCs SET PLC_Name=?, PLC_IP=?, PLC_Port=?, Slave_ID=? WHERE PLC_ID=?",
                (name, ip, port, slave, int(row["PLC_ID"])),
            )
        else:
            cursor.execute(
                "INSERT INTO PLCs (CompanyID, PLC_Name, PLC_IP, PLC_Port, Slave_ID) VALUES (?, ?, ?, ?, ?)",
                (int(company_id), name, ip, port, slave),
            )
        conn.commit()
        return True
    except Exception as exc:
        if conn is not None:
            conn.rollback()
        print("PLC FLOW SYNC ERROR:", exc)
        return False
    finally:
        if cursor is not None:
            cursor.close()
        if conn is not None:
            conn.close()


def sync_all_saved_flows():
    from database import get_connection
    conn = cursor = None
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT FlowID, CompanyID, FlowJson FROM Flows WHERE CompanyID IS NOT NULL AND FlowJson IS NOT NULL AND TRIM(FlowJson) <> '' ORDER BY FlowID"
        )
        rows = cursor.fetchall()
        for row in rows:
            try:
                flow = _sanitize_saved_flow(conn, row)
                if flow is not None:
                    _sync_flow_plc(flow, int(row["CompanyID"]))
            except Exception as exc:
                print("PLC FLOW STARTUP FLOW ERROR:", row["FlowID"], exc)
        conn.commit()
        return True
    except Exception as exc:
        if conn is not None:
            conn.rollback()
        print("PLC FLOW STARTUP SYNC ERROR:", exc)
        return False
    finally:
        if cursor is not None:
            cursor.close()
        if conn is not None:
            conn.close()


def _resolve_company_id(request, session):
    company_id = request.args.get("company_id", type=int)
    if company_id is None:
        company_id = request.headers.get("X-Company-ID", type=int)
    if company_id is None:
        company_id = session.get("selected_company_id") or session.get("company_id")
    try:
        return int(company_id) if company_id is not None else None
    except (TypeError, ValueError):
        return None


def install_save_flow_sync(app):
    if getattr(app, "_flow_plc_sync_installed", False):
        return
    from flask import g, request, session

    @app.before_request
    def _capture_save_flow_payload():
        if request.path != "/save_flow" or request.method != "POST":
            return None
        g._flow_plc_payload = request.get_json(silent=True) or {}
        g._flow_plc_company_id = _resolve_company_id(request, session)

    @app.after_request
    def _sync_saved_flow_to_plc(response):
        if request.path == "/save_flow" and request.method == "POST" and int(response.status_code) < 400:
            company_id = getattr(g, "_flow_plc_company_id", None)
            flow_data = getattr(g, "_flow_plc_payload", None)
            _sync_flow_plc(flow_data, company_id)
            _sanitize_saved_flow_for_company(company_id)
        return response

    app._flow_plc_sync_installed = True


def _sanitize_saved_flow_for_company(company_id):
    if company_id is None:
        return False
    from database import get_connection
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT FlowID, CompanyID, FlowJson FROM Flows WHERE CompanyID = ? ORDER BY FlowID DESC LIMIT 1",
            (int(company_id),),
        ).fetchone()
        if not row:
            return False
        _sanitize_saved_flow(conn, row)
        conn.commit()
        return True
    except Exception as exc:
        conn.rollback()
        print("FLOW SAVE SANITIZE ERROR:", exc)
        return False
    finally:
        conn.close()


def install_flow_json_guard(app):
    if getattr(app, "_company_flow_json_guard_installed", False):
        return
    from flask import jsonify, request, session
    from database import get_connection

    @app.before_request
    def _company_flow_json_guard():
        if request.path != "/flow.json" or request.method != "GET":
            return None
        company_id = _resolve_company_id(request, session)
        if company_id is None:
            return jsonify({"error": "Company not selected"}), 403

        try:
            conn = get_connection()
            try:
                row = conn.execute(
                    "SELECT FlowID, CompanyID, FlowJson FROM Flows WHERE CompanyID = ? ORDER BY FlowID DESC LIMIT 1",
                    (int(company_id),),
                ).fetchone()
                if not row:
                    flow = {"drawflow": {"Home": {"data": {}}}}
                else:
                    flow = _sanitize_saved_flow(conn, row)
                    conn.commit()
            finally:
                conn.close()
            return jsonify(flow or {"drawflow": {"Home": {"data": {}}}})
        except Exception as exc:
            print("COMPANY FLOW JSON GUARD ERROR:", exc)
            return jsonify({"status": "error", "message": str(exc)}), 500

    app._company_flow_json_guard_installed = True


def register_flow_company_blueprint(app):
    try:
        from flow_company_routes import flow_company_bp
        if "flow_company" not in app.blueprints:
            app.register_blueprint(flow_company_bp)
    except Exception as exc:
        print("FLOW COMPANY BLUEPRINT REGISTER ERROR:", exc)


def load_master_logs():
    try:
        import services.master_logs  # noqa: F401
    except Exception as exc:
        print("MASTER LOGS LOAD ERROR:", exc)


def start_edge_timeout_worker():
    try:
        from services.edge_timeout_service import start_worker
        start_worker()
    except Exception as exc:
        print("EDGE TIMEOUT WORKER BOOTSTRAP ERROR:", exc)


def start_trend_runtime_worker():
    try:
        from services.trend_runtime_fix import start
        start()
    except Exception as exc:
        print("TREND RUNTIME WORKER BOOTSTRAP ERROR:", exc)


def start_report_runtime_worker():
    try:
        from services.report_runtime import start
        start()
    except Exception as exc:
        print("REPORT RUNTIME WORKER BOOTSTRAP ERROR:", exc)


def bootstrap(app):
    install_save_flow_sync(app)
    install_flow_json_guard(app)
    register_flow_company_blueprint(app)
    sync_all_saved_flows()
    load_master_logs()
    start_edge_timeout_worker()
    start_trend_runtime_worker()
    start_report_runtime_worker()
    return True


__all__ = ["bootstrap"]
