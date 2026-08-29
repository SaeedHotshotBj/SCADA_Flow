"""
SCADA_FLOW MASTER LOGS / DEBUG PAGE

Read-only diagnostics for one company at a time.
No runtime behavior is changed by this module.
"""

import json
import sys
import threading
import time

from flask import render_template, request, redirect, url_for


def _install():
    for _ in range(120):
        app_module = sys.modules.get("app") or sys.modules.get("__main__")
        flask_app = getattr(app_module, "app", None) if app_module else None
        if flask_app is None:
            time.sleep(0.5)
            continue

        if getattr(flask_app, "_master_logs_installed", False):
            return

        try:
            from database import get_connection
        except Exception:
            time.sleep(0.5)
            continue

        def ensure_edge_timeout_worker():
            """Guarantee the EdgeTimeout worker is started in this Flask process."""
            try:
                from .edge_timeout_service import start_worker
                start_worker()
                return True
            except Exception as exc:
                print("EDGE TIMEOUT MASTER LOGS START ERROR:", exc)
                return False

        def diagnostics(company_id):
            # Start EdgeTimeout before reading its state/log tables. This is
            # intentionally tied to the existing Master Logs diagnostic page
            # so the page itself can prove whether the worker is alive.
            ensure_edge_timeout_worker()

            conn = get_connection()
            cursor = conn.cursor()
            out = {
                "company": None,
                "plcs": [],
                "flow": {"exists": False, "flow_id": None, "last_modified": None, "nodes": []},
                "report_outputs": [],
                "tag_mappers": [],
                "plc_data": [],
                "report_history": [],
                "report_values": [],
                "trend_minute": [],
                "edge_timeout": [],
                "edge_timeout_count": 0,
                "counts": {},
                "errors": [],
            }

            try:
                cursor.execute(
                    "SELECT CompanyID, CompanyName FROM Companies WHERE CompanyID = ? LIMIT 1",
                    (company_id,),
                )
                row = cursor.fetchone()
                if row:
                    out["company"] = {"CompanyID": row["CompanyID"], "CompanyName": row["CompanyName"]}

                cursor.execute(
                    """
                    SELECT PLC_ID, CompanyID, PLC_Name, PLC_IP, PLC_Port, Slave_ID
                    FROM PLCs
                    WHERE CompanyID = ?
                    ORDER BY PLC_ID
                    """,
                    (company_id,),
                )
                out["plcs"] = [dict(r) for r in cursor.fetchall()]

                cursor.execute(
                    """
                    SELECT FlowID, CompanyID, FlowJson, LastModified
                    FROM Flows
                    WHERE CompanyID = ?
                    ORDER BY FlowID DESC
                    LIMIT 1
                    """,
                    (company_id,),
                )
                flow_row = cursor.fetchone()
                if flow_row:
                    out["flow"]["exists"] = True
                    out["flow"]["flow_id"] = flow_row["FlowID"]
                    out["flow"]["last_modified"] = flow_row["LastModified"]
                    try:
                        flow = json.loads(flow_row["FlowJson"] or "{}")
                        nodes = flow.get("drawflow", {}).get("Home", {}).get("data", {})
                        for node_id, node in nodes.items():
                            if not isinstance(node, dict):
                                continue
                            out["flow"]["nodes"].append({
                                "id": str(node_id),
                                "name": node.get("name"),
                                "class": node.get("class"),
                                "data": node.get("data", {}) or {},
                                "inputs": node.get("inputs", {}) or {},
                                "outputs": node.get("outputs", {}) or {},
                            })

                            if node.get("name") == "ReportOutput":
                                out["report_outputs"].append({
                                    "id": str(node_id),
                                    "data": node.get("data", {}) or {},
                                    "inputs": node.get("inputs", {}) or {},
                                })

                            if node.get("name") == "TagMapper":
                                out["tag_mappers"].append({
                                    "id": str(node_id),
                                    "mappings": (node.get("data", {}) or {}).get("mappings", []),
                                })
                    except Exception as exc:
                        out["errors"].append(f"Flow JSON: {exc}")

                cursor.execute(
                    """
                    SELECT ID, CompanyID, TagName, Value, StorageType, Timestamp
                    FROM PLC_Data
                    WHERE CompanyID = ?
                    ORDER BY ID DESC
                    LIMIT 30
                    """,
                    (company_id,),
                )
                out["plc_data"] = [dict(r) for r in cursor.fetchall()]

                cursor.execute(
                    """
                    SELECT ReportID, CompanyID, Timestamp
                    FROM ReportHistory
                    WHERE CompanyID = ?
                    ORDER BY ReportID DESC
                    LIMIT 30
                    """,
                    (company_id,),
                )
                out["report_history"] = [dict(r) for r in cursor.fetchall()]

                cursor.execute(
                    """
                    SELECT v.ReportValueID, v.ReportID, v.TagName, v.Value, h.Timestamp
                    FROM ReportValues v
                    INNER JOIN ReportHistory h ON h.ReportID = v.ReportID
                    WHERE h.CompanyID = ?
                    ORDER BY v.ReportValueID DESC
                    LIMIT 60
                    """,
                    (company_id,),
                )
                out["report_values"] = [dict(r) for r in cursor.fetchall()]

                # TrendMinute schema varies across historical deployments.
                # Discover the company/timestamp columns before querying.
                cursor.execute("PRAGMA table_info(TrendMinute)")
                trend_columns = [r["name"] for r in cursor.fetchall()]

                company_column = None
                for candidate in ("CompanyID", "Company", "company_id", "company"):
                    if candidate in trend_columns:
                        company_column = candidate
                        break

                time_column = None
                for candidate in ("EndTime", "Timestamp", "Time", "StartTime"):
                    if candidate in trend_columns:
                        time_column = candidate
                        break

                if company_column:
                    safe_company = company_column.replace('"', '""')
                    if time_column:
                        safe_time = time_column.replace('"', '""')
                        cursor.execute(
                            f'SELECT * FROM "TrendMinute" WHERE "{safe_company}" = ? ORDER BY "{safe_time}" DESC LIMIT 30',
                            (company_id,),
                        )
                    else:
                        cursor.execute(
                            f'SELECT * FROM "TrendMinute" WHERE "{safe_company}" = ? LIMIT 30',
                            (company_id,),
                        )
                else:
                    if time_column:
                        safe_time = time_column.replace('"', '""')
                        cursor.execute(
                            f'SELECT * FROM "TrendMinute" ORDER BY "{safe_time}" DESC LIMIT 30'
                        )
                    else:
                        cursor.execute('SELECT * FROM "TrendMinute" LIMIT 30')

                out["trend_minute"] = [dict(r) for r in cursor.fetchall()]

                # EdgeTimeout diagnostic log is optional so older databases
                # continue to render the Master Logs page normally.
                try:
                    cursor.execute("PRAGMA table_info(EdgeTimeoutDiagnosticLog)")
                    edge_log_columns = [r["name"] for r in cursor.fetchall()]
                    if edge_log_columns:
                        cursor.execute(
                            """
                            SELECT LogID, CompanyID, Level, Message, Timestamp
                            FROM EdgeTimeoutDiagnosticLog
                            WHERE CompanyID = ?
                            ORDER BY LogID DESC
                            LIMIT 100
                            """,
                            (company_id,),
                        )
                        out["edge_timeout"] = [dict(r) for r in cursor.fetchall()]
                        out["edge_timeout_count"] = len(out["edge_timeout"])
                except Exception as exc:
                    out["errors"].append(f"EdgeTimeout logs: {exc}")

                for table, key in (
                    ("PLC_Data", "plc_data_count"),
                    ("ReportHistory", "report_history_count"),
                    ("ReportValues", "report_values_count"),
                ):
                    cursor.execute(f"SELECT COUNT(*) AS C FROM {table} WHERE CompanyID = ?", (company_id,))
                    out["counts"][key] = int(cursor.fetchone()["C"])

                if company_column:
                    cursor.execute(
                        f'SELECT COUNT(*) AS C FROM "TrendMinute" WHERE "{company_column.replace(chr(34), chr(34)+chr(34))}" = ?',
                        (company_id,),
                    )
                    out["counts"]["trend_minute_count"] = int(cursor.fetchone()["C"])
                else:
                    cursor.execute('SELECT COUNT(*) AS C FROM "TrendMinute"')
                    out["counts"]["trend_minute_count"] = int(cursor.fetchone()["C"])

            except Exception as exc:
                out["errors"].append(str(exc))
            finally:
                cursor.close()
                conn.close()

            return out

        @flask_app.route("/master/logs", methods=["GET"])
        def master_logs():
            from flask import session as flask_session
            if flask_session.get("role", "").strip().lower() != "master":
                return redirect(url_for("login", next=request.path))

            conn = get_connection()
            cursor = conn.cursor()
            try:
                cursor.execute("SELECT CompanyID, CompanyName FROM Companies ORDER BY CompanyID")
                companies = [dict(r) for r in cursor.fetchall()]
            finally:
                cursor.close()
                conn.close()

            selected = request.args.get("company_id", type=int)
            if selected is None and companies:
                selected = int(companies[0]["CompanyID"])

            data = diagnostics(selected) if selected is not None else None
            return render_template(
                "master_logs.html",
                companies=companies,
                selected_company_id=selected,
                diagnostics=data,
            )

        # FLOW-DRIVEN REPORT DATA ENDPOINT
        @flask_app.post("/flow_report")
        def flow_report_runtime_endpoint():
            from flask import jsonify, session as flask_session
            from datetime import datetime
            import jdatetime
            from database import get_company_flow
            from services.report_service import get_report_data

            if not flask_session.get("user_id"):
                return jsonify({"status": "error", "message": "Login required"}), 401

            if flask_session.get("role", "").strip().lower() == "master":
                company_id = request.args.get("company_id", type=int)
                if company_id is None:
                    company_id = flask_session.get("selected_company_id")
            else:
                company_id = flask_session.get("company_id")

            try:
                company_id = int(company_id)
            except (TypeError, ValueError):
                company_id = None

            if company_id is None:
                return jsonify({"status": "error", "message": "Company is required"}), 403

            flow_json = get_company_flow(company_id)
            if not flow_json:
                return jsonify({"status": "error", "message": "No flow configured for this company"}), 404

            flow = json.loads(flow_json) if isinstance(flow_json, str) else flow_json
            nodes = flow.get("drawflow", {}).get("Home", {}).get("data", {})

            products = []
            for node in nodes.values():
                if not isinstance(node, dict) or node.get("name") != "ReportOutput":
                    continue
                config = node.get("data", {}) or {}
                configured = (config.get("config", config) or {}).get("products", [])
                if not isinstance(configured, list):
                    continue
                for item in configured:
                    if not isinstance(item, dict):
                        continue
                    tag = str(item.get("tag", "")).strip()
                    if not tag:
                        continue
                    products.append({
                        "name": str(item.get("name", tag)).strip() or tag,
                        "tag": tag,
                        "unit": str(item.get("unit", "")).strip(),
                    })
                if products:
                    break

            if not products:
                return redirect(url_for("login", next=request.path)) if not flask_session.get("user_id") else (
                    {"status": "error", "message": "ReportOutput has no products configured"},
                    400,
                )

            payload = request.get_json(silent=True) or {}
            report_request = payload.get("ReportRequest", {}) or {}
            calendar = report_request.get("Calendar")
            if calendar not in ("Jalali", "Gregorian"):
                calendar = "Jalali"

            def normalize(value):
                if not value:
                    return None
                text = str(value).strip().replace("T", " ")
                text = text.translate(str.maketrans("۰۱۲۳۴۵۶۷۸۹", "0123456789"))
                if calendar == "Jalali":
                    text = text.replace("-", "/")
                    for fmt in ("%Y/%m/%d %H:%M:%S", "%Y/%m/%d %H:%M"):
                        try:
                            return jdatetime.datetime.strptime(text, fmt).togregorian()
                        except Exception:
                            pass
                else:
                    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
                        try:
                            return datetime.strptime(text, fmt)
                        except Exception:
                            pass
                return None

            start = normalize(report_request.get("Start"))
            end = normalize(report_request.get("End"))
            if start is None or end is None:
                return jsonify({"status": "error", "message": "Invalid report date/time range"}), 400
            if end < start:
                return jsonify({"status": "error", "message": "Report end time is before start time"}), 400

            report = get_report_data(company_id, start, end)
            report["columns"] = products

            return jsonify({
                "calendar": calendar,
                "date_picker": "JalaliPicker" if calendar == "Jalali" else "GregorianPicker",
                "report": report,
                "labels": [item["name"] for item in products],
                "datasets": [{"label": "مجموع گزارش", "data": report.get("totals", [])}],
            })

        flask_app._master_logs_installed = True
        print("MASTER LOGS PAGE INSTALLED: /master/logs")
        print("FLOW REPORT DATA ENDPOINT INSTALLED: /flow_report")
        return

    print("MASTER LOGS INSTALL FAILED")


threading.Thread(target=_install, name="SCADA-Master-Logs", daemon=True).start()
