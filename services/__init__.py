"""SCADA_FLOW services package bootstrap."""

import json
import threading
import time
import sys


# =====================================================
# TREND AGGREGATION
# =====================================================

try:
    from .trend_runtime_fix import start as _start_trend_aggregation

    _start_trend_aggregation()
except Exception as _trend_exc:
    print("TREND AGGREGATION START ERROR:", _trend_exc)


# =====================================================
# PLC CONFIGURATION FROM SAVED FLOW
# =====================================================


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

        if (node.get("class") or node.get("name")) == "PLCReader":
            return node

    return None


def _sync_flow_plc(flow_data, company_id):
    """Create/update the PLC record from the PLCReader in the saved Flow."""

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

    plc_name = str(
        data.get("name")
        or data.get("PLC_Name")
        or "PLC"
    ).strip()

    conn = None
    cursor = None

    try:
        from database import get_connection

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
            (int(company_id),),
        )

        row = cursor.fetchone()

        if row:
            plc_id = int(row["PLC_ID"])

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
                    int(company_id),
                    plc_name,
                    plc_ip,
                    plc_port,
                    slave_id,
                ),
            )

            plc_id = cursor.lastrowid

        conn.commit()

        print(
            "PLC FLOW SYNC OK:",
            "CompanyID=", company_id,
            "PLC_ID=", plc_id,
            "IP=", plc_ip,
            "PORT=", plc_port,
            "SLAVE=", slave_id,
        )

        return True

    except Exception as exc:
        if conn is not None:
            try:
                conn.rollback()
            except Exception:
                pass

        print(
            "PLC FLOW SYNC ERROR:",
            "CompanyID=", company_id,
            "ERROR=", exc,
        )
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
    """Synchronize PLC records from every Flow already stored in Flows."""

    conn = None
    cursor = None

    try:
        from database import get_connection

        conn = get_connection()
        cursor = conn.cursor()

        cursor.execute(
            """
            SELECT
                FlowID,
                CompanyID,
                FlowJson
            FROM Flows
            WHERE CompanyID IS NOT NULL
              AND FlowJson IS NOT NULL
              AND TRIM(FlowJson) <> ''
            ORDER BY FlowID
            """
        )

        rows = cursor.fetchall()

        print(
            "PLC FLOW STARTUP SYNC FLOWS:",
            len(rows),
        )

        for row in rows:
            try:
                flow_data = json.loads(row["FlowJson"])
                _sync_flow_plc(
                    flow_data,
                    int(row["CompanyID"]),
                )
            except Exception as exc:
                print(
                    "PLC FLOW STARTUP FLOW ERROR:",
                    "FlowID=", row["FlowID"],
                    "ERROR=", exc,
                )

        return True

    except Exception as exc:
        print(
            "PLC FLOW STARTUP SYNC LOAD ERROR:",
            exc,
        )
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
    """Install the /save_flow hook after Flask app creation, even though this package loads earlier."""

    app_module = (
        sys.modules.get("app")
        or sys.modules.get("__main__")
    )

    if app_module is None:
        return False

    flask_app = getattr(app_module, "app", None)

    if flask_app is None:
        return False

    if getattr(flask_app, "_flow_plc_sync_installed", False):
        return True

    try:
        from flask import request, session, g
    except Exception as exc:
        print("PLC FLOW HOOK IMPORT ERROR:", exc)
        return False

    @flask_app.before_request
    def _capture_save_flow_payload():
        if request.path != "/save_flow" or request.method != "POST":
            return None

        try:
            payload = request.get_json(silent=True) or {}
        except Exception:
            payload = {}

        g._flow_plc_payload = payload

        company_id = request.args.get("company_id", type=int)

        if company_id is None:
            company_id = request.headers.get("X-Company-ID", type=int)

        if company_id is None:
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
            g._flow_plc_company_id = (
                int(company_id) if company_id is not None else None
            )
        except (TypeError, ValueError):
            g._flow_plc_company_id = None

        return None

    @flask_app.after_request
    def _sync_saved_flow_to_plc(response):
        if request.path != "/save_flow" or request.method != "POST":
            return response

        if int(getattr(response, "status_code", 200)) >= 400:
            return response

        _sync_flow_plc(
            getattr(g, "_flow_plc_payload", None),
            getattr(g, "_flow_plc_company_id", None),
        )

        return response

    flask_app._flow_plc_sync_installed = True

    print("PLC FLOW SAVE SYNC HOOK INSTALLED")
    return True


def _install_save_flow_sync_retry():
    """Retry Hook installation because services/__init__.py is imported before app = Flask(...)."""

    for attempt in range(120):
        try:
            if _install_save_flow_sync():
                return
        except Exception as exc:
            print("PLC FLOW HOOK RETRY ERROR:", exc)

        time.sleep(0.5)

    print("PLC FLOW HOOK INSTALL FAILED: app object was not detected")


def _startup_sync_retry():
    """Retry startup synchronization until database initialization has completed."""

    for attempt in range(60):
        if _sync_all_saved_flows():
            return

        time.sleep(1)

    print("PLC FLOW STARTUP SYNC FAILED AFTER RETRIES")


# Both jobs run asynchronously because this package is imported before the
# Flask application and database initialization in app.py.
threading.Thread(
    target=_install_save_flow_sync_retry,
    name="SCADA-Flow-PLC-Hook",
    daemon=True,
).start()

threading.Thread(
    target=_startup_sync_retry,
    name="SCADA-Flow-PLC-Startup-Sync",
    daemon=True,
).start()

# =====================================================
# MASTER LOGS / DEBUG PAGE
# =====================================================
try:
    from . import master_logs  # noqa: F401
except Exception as _master_logs_exc:
    print("MASTER LOGS LOAD ERROR:", _master_logs_exc)
