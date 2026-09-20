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


def _extract_plc_readers(flow_data):
    nodes = (flow_data or {}).get("drawflow", {}).get("Home", {}).get("data", {})
    if not isinstance(nodes, dict):
        return []
    readers = []
    for node_id, node in nodes.items():
        if isinstance(node, dict) and node.get("name") == "PLCReader":
            readers.append((str(node_id), node))
    return readers


def _node_config(node):
    data = node.get("data", {}) or {}
    config = data.get("config", data)
    if isinstance(config, dict):
        merged = dict(config)
        merged.update({key: value for key, value in data.items() if key != "config"})
        return merged
    return {}


def _sync_flow_plc(flow_data, company_id):
    readers = _extract_plc_readers(flow_data)
    if company_id is None or not readers:
        return False

    from database import get_connection

    conn = cursor = None
    changed_any = False
    try:
        conn = get_connection()
        cursor = conn.cursor()
        company_id = int(company_id)

        existing_rows = cursor.execute(
            "SELECT PLC_ID FROM PLCs WHERE CompanyID = ? ORDER BY PLC_ID",
            (company_id,),
        ).fetchall()
        unused_ids = [int(row["PLC_ID"]) for row in existing_rows]
        used_ids = set()

        for node_id, node in readers:
            data = _node_config(node)
            ip = str(data.get("ip", "")).strip()
            if not ip:
                print(
                    "PLC FLOW SYNC ERROR:",
                    "PLCReader", node_id,
                    "requires an IP address",
                )
                continue

            port_value = data.get("port")
            slave_value = data.get("slave")
            if port_value in (None, "") or slave_value in (None, ""):
                print(
                    "PLC FLOW SYNC ERROR:",
                    "PLCReader", node_id,
                    "requires port and slave in Flow configuration",
                )
                continue

            try:
                port = int(port_value)
                slave = int(slave_value)
            except (TypeError, ValueError):
                print(
                    "PLC FLOW SYNC ERROR:",
                    "PLCReader", node_id,
                    "port/slave must be numeric",
                )
                continue

            if port <= 0 or slave < 0:
                print(
                    "PLC FLOW SYNC ERROR:",
                    "PLCReader", node_id,
                    "contains invalid numeric settings",
                )
                continue

            name = str(data.get("name") or data.get("PLC_Name") or "PLC").strip() or "PLC"

            explicit_value = data.get("plc_id", data.get("PLC_ID"))
            explicit_plc_id = None
            if explicit_value not in (None, ""):
                try:
                    explicit_plc_id = int(explicit_value)
                except (TypeError, ValueError):
                    print(
                        "PLC FLOW SYNC ERROR:",
                        "PLCReader", node_id,
                        "PLC_ID must be numeric",
                    )
                    continue
                if explicit_plc_id <= 0:
                    print(
                        "PLC FLOW SYNC ERROR:",
                        "PLCReader", node_id,
                        "PLC_ID must be positive",
                    )
                    continue

            if explicit_plc_id is not None:
                plc_id = explicit_plc_id
                row = cursor.execute(
                    "SELECT PLC_ID, CompanyID FROM PLCs WHERE PLC_ID = ? LIMIT 1",
                    (plc_id,),
                ).fetchone()

                if row is not None and int(row["CompanyID"]) != company_id:
                    print(
                        "PLC FLOW SYNC ERROR:",
                        "PLCReader", node_id,
                        "PLC_ID belongs to another company",
                    )
                    continue

                if plc_id in used_ids:
                    print(
                        "PLC FLOW SYNC ERROR:",
                        "duplicate PLC_ID", plc_id,
                        "in Flow node", node_id,
                    )
                    continue
            else:
                available = [item for item in unused_ids if item not in used_ids]
                plc_id = available[0] if available else None

            if plc_id is None:
                cursor.execute(
                    """
                    INSERT INTO PLCs
                    (CompanyID, PLC_Name, PLC_IP, PLC_Port, Slave_ID)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (company_id, name, ip, port, slave),
                )
                plc_id = int(cursor.lastrowid)
            else:
                row = cursor.execute(
                    "SELECT PLC_ID FROM PLCs WHERE PLC_ID = ? AND CompanyID = ? LIMIT 1",
                    (plc_id, company_id),
                ).fetchone()
                if row:
                    cursor.execute(
                        """
                        UPDATE PLCs
                        SET PLC_Name=?, PLC_IP=?, PLC_Port=?, Slave_ID=?
                        WHERE PLC_ID=?
                        """,
                        (name, ip, port, slave, plc_id),
                    )
                else:
                    cursor.execute(
                        """
                        INSERT INTO PLCs
                        (PLC_ID, CompanyID, PLC_Name, PLC_IP, PLC_Port, Slave_ID)
                        VALUES (?, ?, ?, ?, ?, ?)
                        """,
                        (plc_id, company_id, name, ip, port, slave),
                    )

            used_ids.add(plc_id)
            changed_any = True

        conn.commit()
        return changed_any
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
            "SELECT FlowID, CompanyID, FlowJson FROM Flows "
            "WHERE CompanyID IS NOT NULL AND FlowJson IS NOT NULL "
            "AND TRIM(FlowJson) <> '' ORDER BY FlowID"
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
            _sync_flow_plc(getattr(g, "_flow_plc_payload", None), company_id)
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


def _database_ready():
    from database import get_connection
    conn = None
    try:
        conn = get_connection()
        required = {"Companies", "Users", "PLCs", "Flows", "PLC_Data", "TagHistory"}
        rows = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        existing = {str(row["name"]) for row in rows}
        missing = required - existing
        if missing:
            print("APPLICATION SERVICES DATABASE NOT READY:", sorted(missing))
            return False
        return True
    except Exception as exc:
        print("APPLICATION SERVICES DATABASE CHECK ERROR:", exc)
        return False
    finally:
        if conn is not None:
            conn.close()


def register_flow_company_blueprint(app):
    try:
        from flow_company_routes import flow_company_bp
        if "flow_company" not in app.blueprints:
            app.register_blueprint(flow_company_bp)
    except Exception as exc:
        print("FLOW COMPANY BLUEPRINT REGISTER ERROR:", exc)


def register_master_control_blueprint(app):
    try:
        from services.master_control_routes import bp
        if "master_control" not in app.blueprints:
            app.register_blueprint(bp)
    except Exception as exc:
        print("MASTER CONTROL BLUEPRINT REGISTER ERROR:", exc)


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


def start_database_maintenance_worker():
    try:
        from services.database_maintenance import start
        start()
    except Exception as exc:
        print("DATABASE MAINTENANCE WORKER BOOTSTRAP ERROR:", exc)


def bootstrap(app):
    install_save_flow_sync(app)
    install_flow_json_guard(app)
    register_flow_company_blueprint(app)
    register_master_control_blueprint(app)
    if not _database_ready():
        print("APPLICATION SERVICES WORKERS SKIPPED: database is not ready")
        return False
    try:
        from services.plc_write_service import ensure_plc_write_schema
        ensure_plc_write_schema()
    except Exception as exc:
        print("PLC WRITE SCHEMA BOOTSTRAP ERROR:", exc)
    try:
        from services.edge_ingest import ensure_edge_event_schema
        ensure_edge_event_schema()
    except Exception as exc:
        print("EDGE EVENT SCHEMA BOOTSTRAP ERROR:", exc)
    try:
        from services.edge_ingest_policy import install as install_edge_ingest_policy
        install_edge_ingest_policy()
    except Exception as exc:
        print("EDGE INGEST POLICY BOOTSTRAP ERROR:", exc)
    sync_all_saved_flows()
    load_master_logs()
    start_edge_timeout_worker()
    start_trend_runtime_worker()
    start_report_runtime_worker()
    return True


__all__ = ["bootstrap"]
