"""SCADA_FLOW local runtime bootstrap."""

import builtins
import json
import os
import sys
import threading
import time
from functools import wraps

_original_print = builtins.print


def _quiet_print(*args, **kwargs):
    message = " ".join(str(arg) for arg in args)
    if "TIMEOUT" in message.upper():
        _original_print(*args, **kwargs)


builtins.print = _quiet_print


def _is_scada_server_process():
    executable = os.path.basename(sys.argv[0] or "").lower()
    args = [os.path.basename(str(item)).lower() for item in sys.argv[1:]]

    if executable == "app.py":
        return True
    if "app.py" in args:
        return True
    if "gunicorn" in executable:
        return True
    if executable in {"flask", "flask.exe"} and "run" in args:
        return True
    return False


def _get_response_status(response):
    try:
        return int(response.status_code)
    except Exception:
        pass
    if isinstance(response, tuple) and response:
        try:
            return int(response[0].status_code)
        except Exception:
            return 200
    return 200


def _extract_plc_reader(flow_data):
    if not isinstance(flow_data, dict):
        return None
    nodes = (
        flow_data.get("drawflow", {})
        .get("Home", {})
        .get("data", {})
    )
    if not isinstance(nodes, dict):
        return None
    for node in nodes.values():
        if not isinstance(node, dict):
            continue
        if (node.get("class") or node.get("name")) == "PLCReader":
            return node
    return None


def _resolve_company_id(request, session=None):
    company_id = request.args.get("company_id", type=int)
    if company_id is None:
        company_id = request.headers.get("X-Company-ID", type=int)
    if company_id is None and session is not None:
        try:
            company_id = session.get("selected_company_id")
        except Exception:
            company_id = None
        if company_id is None:
            try:
                company_id = session.get("company_id")
            except Exception:
                company_id = None
    try:
        return int(company_id) if company_id is not None else None
    except (TypeError, ValueError):
        return None


def _sync_plc_reader_to_database(flow_data, company_id):
    """Create/update PLC configuration strictly from a company's Flow."""
    if company_id is None:
        return False

    plc_reader = _extract_plc_reader(flow_data)
    if plc_reader is None:
        _original_print("PLC FLOW SYNC SKIPPED: NO PLCReader", "CompanyID=", company_id)
        return False

    data = plc_reader.get("data", {}) or {}
    plc_ip = str(data.get("ip", "")).strip()
    if not plc_ip:
        _original_print("PLC FLOW SYNC SKIPPED: PLCReader has no IP", "CompanyID=", company_id)
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

    from database import get_connection
    conn = None
    cursor = None
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT PLC_ID
            FROM PLCs
            WHERE CompanyID = ?
            ORDER BY PLC_ID
            LIMIT 1
            """,
            (company_id,),
        )
        existing = cursor.fetchone()
        if existing:
            plc_id = existing["PLC_ID"]
            cursor.execute(
                """
                UPDATE PLCs
                SET PLC_Name = ?, PLC_IP = ?, PLC_Port = ?, Slave_ID = ?
                WHERE PLC_ID = ?
                """,
                (plc_name, plc_ip, plc_port, slave_id, plc_id),
            )
        else:
            cursor.execute(
                """
                INSERT INTO PLCs
                (CompanyID, PLC_Name, PLC_IP, PLC_Port, Slave_ID)
                VALUES (?, ?, ?, ?, ?)
                """,
                (company_id, plc_name, plc_ip, plc_port, slave_id),
            )
            plc_id = cursor.lastrowid
        conn.commit()
        _original_print(
            "PLC FLOW SYNC:", "CompanyID=", company_id,
            "PLC_ID=", plc_id, "IP=", plc_ip,
            "PORT=", plc_port, "SLAVE=", slave_id,
        )
        return True
    except Exception as exc:
        if conn is not None:
            try:
                conn.rollback()
            except Exception:
                pass
        _original_print("PLC FLOW SYNC ERROR:", exc)
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


def _sync_all_saved_flows_to_plcs():
    """Build/update PLC configuration from every saved company Flow."""
    from database import get_connection
    conn = None
    cursor = None
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT CompanyID, FlowJson
            FROM Flows
            WHERE CompanyID IS NOT NULL
              AND FlowJson IS NOT NULL
              AND TRIM(FlowJson) <> ''
            ORDER BY FlowID
            """
        )
        rows = cursor.fetchall()
    except Exception as exc:
        _original_print("PLC FLOW STARTUP SYNC LOAD ERROR:", exc)
        return
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

    _original_print("PLC FLOW STARTUP SYNC FLOWS:", len(rows))
    for row in rows:
        try:
            company_id = int(row["CompanyID"])
            flow_data = json.loads(row["FlowJson"])
            _sync_plc_reader_to_database(flow_data, company_id)
        except Exception as exc:
            _original_print("PLC FLOW STARTUP SYNC ERROR:", exc)


def _normalize_drawflow_node_ids(flow_data):
    """Normalize persisted Drawflow node IDs to their object keys.

    Drawflow stores each node under a dictionary key and also stores an internal
    node.id. These must agree for imports/connections to work. Older saved flows
    may contain mismatched values (for example key '32' with id 1).
    """
    if not isinstance(flow_data, dict):
        return flow_data, False

    drawflow = flow_data.get("drawflow")
    if not isinstance(drawflow, dict):
        return flow_data, False

    home = drawflow.get("Home")
    if not isinstance(home, dict):
        return flow_data, False

    nodes = home.get("data")
    if not isinstance(nodes, dict):
        return flow_data, False

    normalized = json.loads(json.dumps(flow_data, ensure_ascii=False))
    normalized_nodes = (
        normalized.get("drawflow", {})
        .get("Home", {})
        .get("data", {})
    )

    changed = False
    for node_key, node in normalized_nodes.items():
        if not isinstance(node, dict):
            continue
        key_id = str(node_key)
        if str(node.get("id", "")) != key_id:
            node["id"] = int(key_id) if key_id.isdigit() else key_id
            changed = True

    return normalized, changed


def _install_drawflow_id_normalizer():
    """Normalize company flows before the Flask app consumes/imports them."""
    app_module = sys.modules.get("app") or sys.modules.get("__main__")
    if app_module is None:
        return False
    flask_app = getattr(app_module, "app", None)
    original_get_flow_data = getattr(app_module, "get_flow_data", None)
    if flask_app is None or original_get_flow_data is None:
        return False
    if getattr(flask_app, "_drawflow_id_normalizer_installed", False):
        return True

    def _safe_get_flow_data(company_id):
        flow = original_get_flow_data(company_id)
        normalized, changed = _normalize_drawflow_node_ids(flow)
        if not changed:
            return flow

        try:
            from database import get_connection
            conn = get_connection()
            try:
                conn.execute(
                    """
                    UPDATE Flows
                    SET FlowJson = ?,
                        LastModified = datetime('now', 'localtime')
                    WHERE CompanyID = ?
                    """,
                    (json.dumps(normalized, ensure_ascii=False), int(company_id)),
                )
                conn.commit()
                _original_print(
                    "DRAWFLOW ID NORMALIZED:",
                    "CompanyID=", company_id,
                )
            finally:
                conn.close()
        except Exception as exc:
            _original_print("DRAWFLOW ID NORMALIZE SAVE ERROR:", exc)

        return normalized

    app_module.get_flow_data = _safe_get_flow_data
    flask_app._drawflow_id_normalizer_installed = True
    _original_print("DRAWFLOW ID NORMALIZER ENABLED")
    return True


def _disable_file_flow_fallback():
    """Prevent legacy flow.json from becoming a company configuration source."""
    app_module = sys.modules.get("app") or sys.modules.get("__main__")
    if app_module is None:
        return False
    if hasattr(app_module, "_read_flow_file"):
        def _no_file_flow_fallback():
            return None
        app_module._read_flow_file = _no_file_flow_fallback
        _original_print("FLOW FILE FALLBACK DISABLED")
        return True
    return False


def _install_save_flow_hooks():
    """Install Flask-level hooks so PLC sync does not depend on route wrapping."""
    app_module = sys.modules.get("app") or sys.modules.get("__main__")
    if app_module is None:
        return False
    flask_app = getattr(app_module, "app", None)
    if flask_app is None:
        return False
    if getattr(flask_app, "_plc_flow_sync_hooks_installed", False):
        return True
    try:
        from flask import request, session, g
    except Exception:
        return False

    @flask_app.before_request
    def _capture_flow_for_plc_sync():
        if request.path != "/save_flow" or request.method != "POST":
            return None
        try:
            g._plc_flow_sync_data = request.get_json(silent=True) or {}
        except Exception:
            g._plc_flow_sync_data = {}
        g._plc_flow_sync_company_id = _resolve_company_id(request, session)
        return None

    @flask_app.after_request
    def _sync_flow_plc_after_save(response):
        if request.path != "/save_flow" or request.method != "POST":
            return response
        if _get_response_status(response) >= 400:
            return response
        flow_data = getattr(g, "_plc_flow_sync_data", None)
        company_id = getattr(g, "_plc_flow_sync_company_id", None)
        if not _sync_plc_reader_to_database(flow_data, company_id):
            _original_print("PLC FLOW SAVE SYNC FAILED:", "CompanyID=", company_id)
        return response

    flask_app._plc_flow_sync_hooks_installed = True
    _original_print("PLC FLOW SAVE SYNC HOOKS ENABLED")
    return True


def _wait_for_app_and_patch():
    deadline = time.time() + 60
    fallback_disabled = False
    hooks_installed = False

    for delay in (0.5, 2.0, 5.0, 10.0):
        time.sleep(delay)
        try:
            if not fallback_disabled:
                fallback_disabled = _disable_file_flow_fallback()
            if not hooks_installed:
                hooks_installed = _install_save_flow_hooks()
            _install_drawflow_id_normalizer()
            _sync_all_saved_flows_to_plcs()
            if hooks_installed and fallback_disabled:
                break
        except Exception as exc:
            _original_print("PLC FLOW PATCH ERROR:", exc)

    while time.time() < deadline:
        try:
            if not fallback_disabled:
                fallback_disabled = _disable_file_flow_fallback()
            if not hooks_installed:
                hooks_installed = _install_save_flow_hooks()
            _install_drawflow_id_normalizer()
            if hooks_installed:
                return
        except Exception as exc:
            _original_print("PLC FLOW PATCH ERROR:", exc)
        time.sleep(0.5)

    _original_print("PLC FLOW PATCH: Flask hooks were not installed")


if _is_scada_server_process():
    try:
        from services.trend_runtime_fix import start
        start()
    except Exception as exc:
        _original_print("TREND AGGREGATION START ERROR:", exc)

    threading.Thread(target=_wait_for_app_and_patch, daemon=True).start()
