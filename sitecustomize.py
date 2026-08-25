"""SCADA_FLOW local runtime bootstrap."""

import builtins
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
        flow_data
        .get("drawflow", {})
        .get("Home", {})
        .get("data", {})
    )

    if not isinstance(nodes, dict):
        return None

    for node in nodes.values():
        if not isinstance(node, dict):
            continue

        node_type = node.get("class") or node.get("name")
        if node_type == "PLCReader":
            return node

    return None


def _sync_plc_reader_to_database(flow_data, company_id):
    """Create/update the PLC record strictly from the company's saved Flow."""

    if company_id is None:
        return False

    plc_reader = _extract_plc_reader(flow_data)
    if plc_reader is None:
        return False

    data = plc_reader.get("data", {}) or {}

    plc_ip = str(data.get("ip", "")).strip()
    if not plc_ip:
        return False

    try:
        plc_port = int(data.get("port", 502))
    except (TypeError, ValueError):
        plc_port = 502

    try:
        slave_id = int(data.get("slave", 1))
    except (TypeError, ValueError):
        slave_id = 1

    plc_name = str(
        data.get("name") or data.get("PLC_Name") or "PLC"
    ).strip()

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
                SET
                    PLC_Name = ?,
                    PLC_IP = ?,
                    PLC_Port = ?,
                    Slave_ID = ?
                WHERE PLC_ID = ?
                """,
                (
                    plc_name,
                    plc_ip,
                    plc_port,
                    slave_id,
                    plc_id,
                ),
            )
        else:
            cursor.execute(
                """
                INSERT INTO PLCs
                (
                    CompanyID,
                    PLC_Name,
                    PLC_IP,
                    PLC_Port,
                    Slave_ID
                )
                VALUES
                (?, ?, ?, ?, ?)
                """,
                (
                    company_id,
                    plc_name,
                    plc_ip,
                    plc_port,
                    slave_id,
                ),
            )
            plc_id = cursor.lastrowid

        conn.commit()

        _original_print(
            "PLC FLOW SYNC:",
            "CompanyID=",
            company_id,
            "PLC_ID=",
            plc_id,
            "IP=",
            plc_ip,
            "PORT=",
            plc_port,
            "SLAVE=",
            slave_id,
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

    for row in rows:
        try:
            company_id = int(row["CompanyID"])
            flow_data = __import__("json").loads(row["FlowJson"])
            _sync_plc_reader_to_database(flow_data, company_id)
        except Exception as exc:
            _original_print(
                "PLC FLOW STARTUP SYNC ERROR:",
                exc,
            )


def _disable_file_flow_fallback():
    """Prevent the legacy flow.json from becoming a company configuration source."""

    app_module = (
        sys.modules.get("app")
        or sys.modules.get("__main__")
    )

    if app_module is None:
        return False

    if hasattr(app_module, "_read_flow_file"):
        def _no_file_flow_fallback():
            return None

        app_module._read_flow_file = _no_file_flow_fallback
        _original_print("FLOW FILE FALLBACK DISABLED")
        return True

    return False


def _patch_save_flow():
    """Patch the existing Flask save_flow view to sync PLC from saved Flow."""

    app_module = (
        sys.modules.get("app")
        or sys.modules.get("__main__")
    )

    if app_module is None:
        return False

    flask_app = getattr(app_module, "app", None)
    if flask_app is None:
        return False

    view = flask_app.view_functions.get("save_flow")
    if view is None:
        return False

    if getattr(view, "_plc_flow_sync_patched", False):
        return True

    try:
        from flask import request, session
    except Exception:
        return False

    @wraps(view)
    def wrapped_save_flow(*args, **kwargs):
        try:
            flow_data = request.get_json(silent=True) or {}
        except Exception:
            flow_data = {}

        company_id = None

        try:
            role = str(session.get("role", "")).strip().lower()
            if role == "master":
                company_id = request.args.get("company_id", type=int)
                if company_id is None:
                    company_id = session.get("selected_company_id")
            else:
                company_id = session.get("company_id")

            if company_id is not None:
                company_id = int(company_id)
        except (TypeError, ValueError):
            company_id = None

        response = view(*args, **kwargs)

        if _get_response_status(response) < 400:
            _sync_plc_reader_to_database(flow_data, company_id)

        return response

    wrapped_save_flow._plc_flow_sync_patched = True
    flask_app.view_functions["save_flow"] = wrapped_save_flow

    _original_print("PLC FLOW SAVE SYNC ENABLED")
    return True


def _wait_for_app_and_patch():
    deadline = time.time() + 60
    startup_synced = False
    fallback_disabled = False

    while time.time() < deadline:
        try:
            if not fallback_disabled:
                fallback_disabled = _disable_file_flow_fallback()

            if not startup_synced:
                _sync_all_saved_flows_to_plcs()
                startup_synced = True

            if _patch_save_flow():
                return
        except Exception as exc:
            _original_print("PLC FLOW PATCH ERROR:", exc)

        time.sleep(0.2)

    _original_print("PLC FLOW PATCH: save_flow was not found")


if _is_scada_server_process():
    try:
        from services.trend_runtime_fix import start
        start()
    except Exception as exc:
        _original_print("TREND AGGREGATION START ERROR:", exc)

    threading.Thread(
        target=_wait_for_app_and_patch,
        daemon=True,
    ).start()
