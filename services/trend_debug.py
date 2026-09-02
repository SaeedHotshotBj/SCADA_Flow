"""Master-only Trend diagnostics endpoint.

This endpoint deliberately reads the existing EdgeTimeoutDiagnosticLog table,
but filters only TREND_* messages so frequent timeout checks do not hide them.
"""

import json
import sys
import time

from flask import jsonify, redirect, request, session, url_for


def _is_master():
    return session.get("role", "").strip().lower() == "master"


def _install():
    for _ in range(120):
        app_module = sys.modules.get("app") or sys.modules.get("__main__")
        flask_app = getattr(app_module, "app", None) if app_module else None
        if flask_app is None:
            time.sleep(0.5)
            continue

        if getattr(flask_app, "_trend_debug_installed", False):
            return

        try:
            from database import get_connection

            @flask_app.get("/master/trend-debug")
            def master_trend_debug():
                if not _is_master():
                    return redirect(url_for("login", next=request.path))

                company_id = request.args.get("company_id", type=int)
                if company_id is None:
                    company_id = session.get("selected_company_id")
                try:
                    company_id = int(company_id)
                except (TypeError, ValueError):
                    company_id = None

                if company_id is None:
                    return jsonify({"status": "error", "message": "Company is required"}), 400

                conn = get_connection()
                cursor = conn.cursor()
                try:
                    cursor.execute("PRAGMA table_info(EdgeTimeoutDiagnosticLog)")
                    columns = [r["name"] for r in cursor.fetchall()]
                    if not columns:
                        return jsonify({"status": "error", "message": "EdgeTimeoutDiagnosticLog table not found"}), 500

                    cursor.execute(
                        """
                        SELECT LogID, CompanyID, Level, Message, Timestamp
                        FROM EdgeTimeoutDiagnosticLog
                        WHERE CompanyID = ?
                          AND (
                              Message LIKE 'TREND_%'
                              OR Message LIKE '%TREND_DIRECT%'
                              OR Message LIKE '%PLC_ID%'
                          )
                        ORDER BY LogID DESC
                        LIMIT 300
                        """,
                        (company_id,),
                    )
                    rows = [dict(r) for r in cursor.fetchall()]

                    cursor.execute(
                        """
                        SELECT ID, CompanyID, PLC_ID, TagName, Value, StorageType, Timestamp
                        FROM PLC_Data
                        WHERE CompanyID = ?
                        ORDER BY ID DESC
                        LIMIT 100
                        """,
                        (company_id,),
                    )
                    plc_rows = [dict(r) for r in cursor.fetchall()]
                finally:
                    cursor.close()
                    conn.close()

                return jsonify({
                    "status": "ok",
                    "company_id": company_id,
                    "trend_logs": rows,
                    "recent_plc_data": plc_rows,
                    "hint": "Open this page immediately after requesting a Trend; TREND_DIRECT_START/RESULT/ERROR entries show the exact path.",
                })

            flask_app._trend_debug_installed = True
            print("TREND DEBUG MASTER ENDPOINT INSTALLED: /master/trend-debug")
            return
        except Exception as exc:
            print("TREND DEBUG INSTALL RETRY:", exc)
        time.sleep(0.5)

    print("TREND DEBUG INSTALL FAILED AFTER RETRIES")
