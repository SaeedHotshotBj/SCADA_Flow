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
    """Use the PLCReader inside a company's saved Flow as PLC source of truth."""

    if company_id is None:
        return False

    plc_reader = _extract_plc_reader(flow_data)
    if plc_reader is None:
        return False

    data = plc_reader.get("data", {}) or {}
    plc_ip = str(data.get("ip", "")).strip()

    if not plc_ip:
        return False

    plc_port = data.get("port")
    slave_id = data.get("slave")

    plc_name = str(
        data.get("name")
        or data.get("PLC_Name")
        or "PLC"
    ).strip()

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
            (company_id,),
        )

        row = cursor.fetchone()

        if row:
            plc_id = row["PLC_ID"]

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
        cursor.close()
        conn.close()

        print(
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
        print(
            "PLC FLOW SYNC ERROR:",
            exc,
        )
        return False


def _sync_all_saved_flows():
    """Synchronize PLC records from every Flow already stored in Flows."""

    try:
        from database import get_connection

        conn = get_connection()
        cursor = conn.cursor()

        cursor.execute(
            """
            SELECT
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

        cursor.close()
        conn.close()

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
                    "PLC FLOW STARTUP SYNC ERROR:",
                    exc,
                )

    except Exception as exc:
        print(
            "PLC FLOW STARTUP SYNC LOAD ERROR:",
            exc,
        )


def _install_save_flow_sync():
    """Install a direct Flask request hook before the server accepts requests."""

    app_module = (
        sys.modules.get("app")
        or sys.modules.get("__main__")
    )

    if app_module is None:
        return False

    flask_app = getattr(app_module, "app", None)

    if flask_app is None:
        return False

    if getattr(
        flask_app,
        "_flow_plc_sync_installed",
        False,
    ):
        return True

    try:
        from flask import request, session, g
    except Exception:
        return False

    @flask_app.before_request
    def _capture_save_flow_payload():
        if (
            request.path != "/save_flow"
            or request.method != "POST"
        ):
            return None

        try:
            g._flow_plc_payload = (
                request.get_json(
                    silent=True
                )
                or {}
            )
        except Exception:
            g._flow_plc_payload = {}

        company_id = request.args.get(
            "company_id",
            type=int,
        )

        if company_id is None:
            company_id = request.headers.get(
                "X-Company-ID",
                type=int,
            )

        if company_id is None:
            try:
                company_id = session.get(
                    "selected_company_id"
                )
            except Exception:
                company_id = None

        if company_id is None:
            try:
                company_id = session.get(
                    "company_id"
                )
            except Exception:
                company_id = None

        try:
            g._flow_plc_company_id = (
                int(company_id)
                if company_id is not None
                else None
            )
        except (TypeError, ValueError):
            g._flow_plc_company_id = None

        return None

    @flask_app.after_request
    def _sync_saved_flow_to_plc(response):
        if (
            request.path != "/save_flow"
            or request.method != "POST"
        ):
            return response

        try:
            status_code = int(
                response.status_code
            )
        except Exception:
            status_code = 200

        if status_code >= 400:
            return response

        _sync_flow_plc(
            getattr(
                g,
                "_flow_plc_payload",
                None,
            ),
            getattr(
                g,
                "_flow_plc_company_id",
                None,
            ),
        )

        return response

    flask_app._flow_plc_sync_installed = True

    print(
        "PLC FLOW SAVE SYNC HOOK INSTALLED"
    )

    return True


# Install the Save Flow hook immediately while Flask is still in its setup phase.
_install_save_flow_sync()


def _bootstrap_flow_plc_sync():
    # Existing Flow records may already be present in the database.
    # Wait until database initialization has completed, then synchronize them.
    time.sleep(2)
    _sync_all_saved_flows()


threading.Thread(
    target=_bootstrap_flow_plc_sync,
    name="SCADA-Flow-PLC-Sync",
    daemon=True,
).start()
