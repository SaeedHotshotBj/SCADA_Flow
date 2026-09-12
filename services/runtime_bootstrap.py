"""Explicit application-service bootstrap.

The services package is import-safe. Process-level startup happens here only,
after Flask and SQLite initialization.
"""

import json


def _extract_plc_reader(flow_data):
    nodes = (flow_data or {}).get("drawflow", {}).get("Home", {}).get("data", {})
    if not isinstance(nodes, dict):
        return None
    for node in nodes.values():
        if isinstance(node, dict) and node.get("name") == "PLCReader":
            return node
    return None


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
        cursor.execute("SELECT PLC_ID FROM PLCs WHERE CompanyID = ? ORDER BY PLC_ID LIMIT 1", (int(company_id),))
        row = cursor.fetchone()
        if row:
            cursor.execute("UPDATE PLCs SET PLC_Name=?, PLC_IP=?, PLC_Port=?, Slave_ID=? WHERE PLC_ID=?", (name, ip, port, slave, int(row["PLC_ID"])))
        else:
            cursor.execute("INSERT INTO PLCs (CompanyID, PLC_Name, PLC_IP, PLC_Port, Slave_ID) VALUES (?, ?, ?, ?, ?)", (int(company_id), name, ip, port, slave))
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
        cursor.execute("SELECT FlowID, CompanyID, FlowJson FROM Flows WHERE CompanyID IS NOT NULL AND FlowJson IS NOT NULL AND TRIM(FlowJson) <> '' ORDER BY FlowID")
        for row in cursor.fetchall():
            try:
                _sync_flow_plc(json.loads(row["FlowJson"]), int(row["CompanyID"]))
            except Exception as exc:
                print("PLC FLOW STARTUP FLOW ERROR:", row["FlowID"], exc)
        return True
    except Exception as exc:
        print("PLC FLOW STARTUP SYNC ERROR:", exc)
        return False
    finally:
        if cursor is not None:
            cursor.close()
        if conn is not None:
            conn.close()


def install_save_flow_sync(app):
    if getattr(app, "_flow_plc_sync_installed", False):
        return
    from flask import g, request, session

    @app.before_request
    def _capture_save_flow_payload():
        if request.path != "/save_flow" or request.method != "POST":
            return None
        g._flow_plc_payload = request.get_json(silent=True) or {}
        company_id = request.args.get("company_id", type=int) or request.headers.get("X-Company-ID", type=int)
        if company_id is None:
            company_id = session.get("selected_company_id") or session.get("company_id")
        try:
            g._flow_plc_company_id = int(company_id) if company_id is not None else None
        except (TypeError, ValueError):
            g._flow_plc_company_id = None

    @app.after_request
    def _sync_saved_flow_to_plc(response):
        if request.path == "/save_flow" and request.method == "POST" and int(response.status_code) < 400:
            _sync_flow_plc(getattr(g, "_flow_plc_payload", None), getattr(g, "_flow_plc_company_id", None))
        return response

    app._flow_plc_sync_installed = True


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


def bootstrap(app):
    install_save_flow_sync(app)
    register_flow_company_blueprint(app)
    sync_all_saved_flows()
    load_master_logs()
    start_edge_timeout_worker()
    start_trend_runtime_worker()
    return True


__all__ = ["bootstrap"]
