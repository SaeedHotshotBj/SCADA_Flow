"""SCADA_FLOW services package bootstrap."""

import json
import threading
import time
import sys

try:
    from .trend_runtime_fix import start as _start_trend_aggregation
    _start_trend_aggregation()
except Exception as _trend_exc:
    print("TREND AGGREGATION START ERROR:", _trend_exc)


def _extract_plc_reader(flow_data):
    if not isinstance(flow_data, dict):
        return None
    nodes = flow_data.get("drawflow", {}).get("Home", {}).get("data", {})
    if not isinstance(nodes, dict):
        return None
    for node in nodes.values():
        if isinstance(node, dict) and (node.get("class") or node.get("name")) == "PLCReader":
            return node
    return None


def _sync_flow_plc(flow_data, company_id):
    if company_id is None:
        return False
    plc_reader = _extract_plc_reader(flow_data)
    if plc_reader is None:
        return False
    data = plc_reader.get("data", {}) or {}
    plc_ip = str(data.get("ip", "")).strip()
    if not plc_ip:
        print("PLC FLOW SYNC SKIPPED: PLCReader has no IP", company_id)
        return False
    try:
        plc_port = int(data.get("port", 502))
    except (TypeError, ValueError):
        plc_port = 502
    try:
        slave_id = int(data.get("slave", 1))
    except (TypeError, ValueError):
        slave_id = 1
    plc_name = str(data.get("name") or data.get("PLC_Name") or "PLC").strip()
    conn = cursor = None
    try:
        from database import get_connection
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT PLC_ID FROM PLCs WHERE CompanyID = ? ORDER BY PLC_ID LIMIT 1", (int(company_id),))
        row = cursor.fetchone()
        if row:
            plc_id = int(row["PLC_ID"])
            cursor.execute("UPDATE PLCs SET PLC_Name = ?, PLC_IP = ?, PLC_Port = ?, Slave_ID = ? WHERE PLC_ID = ?", (plc_name, plc_ip, plc_port, slave_id, plc_id))
        else:
            cursor.execute("INSERT INTO PLCs (CompanyID, PLC_Name, PLC_IP, PLC_Port, Slave_ID) VALUES (?, ?, ?, ?, ?)", (int(company_id), plc_name, plc_ip, plc_port, slave_id))
            plc_id = cursor.lastrowid
        conn.commit()
        print("PLC FLOW SYNC OK:", "CompanyID=", company_id, "PLC_ID=", plc_id, "IP=", plc_ip, "PORT=", plc_port, "SLAVE=", slave_id)
        return True
    except Exception as exc:
        if conn is not None:
            try:
                conn.rollback()
            except Exception:
                pass
        print("PLC FLOW SYNC ERROR:", "CompanyID=", company_id, "ERROR=", exc)
        return False
    finally:
        if cursor is not None:
            try:
                cursor.close()
            except Exception:
                pass
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass


def _sync_all_saved_flows():
    conn = cursor = None
    try:
        from database import get_connection
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT FlowID, CompanyID, FlowJson FROM Flows WHERE CompanyID IS NOT NULL AND FlowJson IS NOT NULL AND TRIM(FlowJson) <> '' ORDER BY FlowID")
        rows = cursor.fetchall()
        print("PLC FLOW STARTUP SYNC FLOWS:", len(rows))
        for row in rows:
            try:
                _sync_flow_plc(json.loads(row["FlowJson"]), int(row["CompanyID"]))
            except Exception as exc:
                print("PLC FLOW STARTUP FLOW ERROR:", "FlowID=", row["FlowID"], "ERROR=", exc)
        return True
    except Exception as exc:
        print("PLC FLOW STARTUP SYNC LOAD ERROR:", exc)
        return False
    finally:
        if cursor is not None:
            try:
                cursor.close()
            except Exception:
                pass
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass


def _install_save_flow_sync():
    app_module = sys.modules.get("app") or sys.modules.get("__main__")
    flask_app = getattr(app_module, "app", None) if app_module else None
    if flask_app is None or getattr(flask_app, "_flow_plc_sync_installed", False):
        return bool(flask_app)
    try:
        from flask import request, session, g
    except Exception as exc:
        print("PLC FLOW HOOK IMPORT ERROR:", exc)
        return False

    @flask_app.before_request
    def _capture_save_flow_payload():
        if request.path != "/save_flow" or request.method != "POST":
            return None
        g._flow_plc_payload = request.get_json(silent=True) or {}
        company_id = request.args.get("company_id", type=int)
        if company_id is None:
            company_id = request.headers.get("X-Company-ID", type=int)
        if company_id is None:
            company_id = session.get("selected_company_id")
        if company_id is None:
            company_id = session.get("company_id")
        try:
            g._flow_plc_company_id = int(company_id) if company_id is not None else None
        except (TypeError, ValueError):
            g._flow_plc_company_id = None
        return None

    @flask_app.after_request
    def _sync_saved_flow_to_plc(response):
        if request.path != "/save_flow" or request.method != "POST":
            return response
        if int(getattr(response, "status_code", 200)) >= 400:
            return response
        _sync_flow_plc(getattr(g, "_flow_plc_payload", None), getattr(g, "_flow_plc_company_id", None))
        return response

    flask_app._flow_plc_sync_installed = True
    print("PLC FLOW SAVE SYNC HOOK INSTALLED")
    return True


def _install_save_flow_sync_retry():
    for _ in range(120):
        try:
            if _install_save_flow_sync():
                return
        except Exception as exc:
            print("PLC FLOW HOOK RETRY ERROR:", exc)
        time.sleep(0.5)
    print("PLC FLOW HOOK INSTALL FAILED: app object was not detected")


def _startup_sync_retry():
    for _ in range(60):
        if _sync_all_saved_flows():
            return
        time.sleep(1)
    print("PLC FLOW STARTUP SYNC FAILED AFTER RETRIES")


threading.Thread(target=_install_save_flow_sync_retry, name="SCADA-Flow-PLC-Hook", daemon=True).start()
threading.Thread(target=_startup_sync_retry, name="SCADA-Flow-PLC-Startup-Sync", daemon=True).start()


def _load_master_logs_after_app_startup():
    for attempt in range(120):
        try:
            app_module = sys.modules.get("app") or sys.modules.get("__main__")
            flask_app = getattr(app_module, "app", None) if app_module else None
            if flask_app is not None:
                module = sys.modules.get("services.master_logs")
                if module is None:
                    from . import master_logs as module
                installer = getattr(module, "_install", None)
                if callable(installer):
                    installer()
                debug_module = sys.modules.get("services.trend_debug")
                if debug_module is None:
                    from . import trend_debug as debug_module
                debug_installer = getattr(debug_module, "_install", None)
                if callable(debug_installer):
                    debug_installer()
                print("MASTER LOGS MODULES LOADED/INSTALLED AFTER APP STARTUP")
                return
        except Exception as exc:
            print("MASTER LOGS LOAD RETRY:", attempt + 1, exc)
        time.sleep(0.5)
    print("MASTER LOGS LOAD FAILED AFTER RETRIES")


threading.Thread(target=_load_master_logs_after_app_startup, name="SCADA-Master-Logs-Loader", daemon=True).start()


def _install_flow_company_blueprint():
    app_module = sys.modules.get("app") or sys.modules.get("__main__")
    flask_app = getattr(app_module, "app", None) if app_module else None
    if flask_app is None or getattr(flask_app, "_flow_company_blueprint_registered", False):
        return bool(flask_app)
    try:
        from flow_company_routes import flow_company_bp
        if "flow_company" not in flask_app.blueprints:
            flask_app.register_blueprint(flow_company_bp)
        flask_app._flow_company_blueprint_registered = True
        print("FLOW COMPANY BLUEPRINT REGISTERED")
        print("FLOW COMPANY ROUTES:", len(flask_app.url_map._rules))
        return True
    except Exception as exc:
        print("FLOW COMPANY BLUEPRINT REGISTER ERROR:", exc)
        return False


def _install_flow_company_blueprint_retry():
    for _ in range(120):
        try:
            if _install_flow_company_blueprint():
                return
        except Exception as exc:
            print("FLOW COMPANY BLUEPRINT RETRY ERROR:", exc)
        time.sleep(0.5)
    print("FLOW COMPANY BLUEPRINT INSTALL FAILED")


threading.Thread(target=_install_flow_company_blueprint_retry, name="SCADA-Flow-Company-Routes", daemon=True).start()


def _start_edge_timeout_worker_retry():
    for attempt in range(120):
        try:
            from .edge_timeout_service import start_worker
            start_worker()
            print("EDGE TIMEOUT BOOTSTRAP OK:", "attempt=", attempt + 1)
            return
        except Exception as exc:
            print("EDGE TIMEOUT BOOTSTRAP RETRY:", attempt + 1, "ERROR=", exc)
        time.sleep(1)
    print("EDGE TIMEOUT BOOTSTRAP FAILED AFTER 120 ATTEMPTS")


threading.Thread(target=_start_edge_timeout_worker_retry, name="SCADA-Edge-Timeout-Bootstrap", daemon=True).start()


# =====================================================
# REPORT CONTEXT WIRING
# =====================================================


def _install_report_context_wiring():
    """Prefer ContractCode/ProductCode already produced by TagMapper."""
    for _ in range(120):
        try:
            module = sys.modules.get("flow_engine.nodes.report_output")
            if module is None:
                time.sleep(0.5)
                continue

            report_output = getattr(module, "ReportOutput", None)
            if report_output is None:
                time.sleep(0.5)
                continue

            if getattr(report_output, "_management_context_wiring_installed", False):
                return

            original = report_output._management_context_tags

            def _wired_management_context(self, data):
                result = {}
                tags = data.get("Tags", {}) if isinstance(data, dict) else {}
                if isinstance(tags, dict):
                    lookup = {
                        str(name).strip().lower(): value
                        for name, value in tags.items()
                    }
                    for result_key, aliases in {
                        "ContractCode": ("contractcode", "contract_code", "contract code"),
                        "ProductCode": ("productcode", "product_code", "product code"),
                    }.items():
                        for alias in aliases:
                            value = lookup.get(alias)
                            if value not in (None, ""):
                                result[result_key] = str(value).strip()
                                break

                if result:
                    return result
                return original(self, data)

            report_output._management_context_tags = _wired_management_context
            report_output._management_context_wiring_installed = True
            print("REPORT MANAGEMENT CONTEXT WIRING INSTALLED")
            return
        except Exception as exc:
            print("REPORT MANAGEMENT CONTEXT WIRING RETRY:", exc)
        time.sleep(0.5)

    print("REPORT MANAGEMENT CONTEXT WIRING FAILED")


threading.Thread(target=_install_report_context_wiring, name="SCADA-Report-Management-Context-Wiring", daemon=True).start()
