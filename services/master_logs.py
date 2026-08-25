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
            from database import get_connection, get_company_flow
        except Exception:
            time.sleep(0.5)
            continue

        def diagnostics(company_id):
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

                cursor.execute(
                    """
                    SELECT CompanyID, TagName, StartTime, EndTime, MinValue, MaxValue,
                           AvgValue, FirstValue, LastValue, SampleCount
                    FROM TrendMinute
                    WHERE CompanyID = ?
                    ORDER BY EndTime DESC
                    LIMIT 30
                    """,
                    (company_id,),
                )
                out["trend_minute"] = [dict(r) for r in cursor.fetchall()]

                for table, key in (
                    ("PLC_Data", "plc_data_count"),
                    ("ReportHistory", "report_history_count"),
                    ("ReportValues", "report_values_count"),
                    ("TrendMinute", "trend_minute_count"),
                ):
                    cursor.execute(f"SELECT COUNT(*) AS C FROM {table} WHERE CompanyID = ?", (company_id,))
                    out["counts"][key] = int(cursor.fetchone()["C"])

            except Exception as exc:
                out["errors"].append(str(exc))
            finally:
                cursor.close()
                conn.close()

            return out

        @flask_app.route("/master/logs", methods=["GET"])
        def master_logs():
            # Master-only, read-only diagnostics.
            session = getattr(request, "session", None)
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

        flask_app._master_logs_installed = True
        print("MASTER LOGS PAGE INSTALLED: /master/logs")
        return

    print("MASTER LOGS INSTALL FAILED")


threading.Thread(target=_install, name="SCADA-Master-Logs", daemon=True).start()
