"""Master-only Trend diagnostics endpoint."""

import json
import sys
import time

from flask import jsonify, redirect, request, session, url_for


def _is_master():
    return session.get("role", "").strip().lower() == "master"


def _write_log(company_id, plc_id, level, message):
    conn = None
    try:
        from database import get_connection
        conn = get_connection()
        conn.execute("""
            CREATE TABLE IF NOT EXISTS EdgeTimeoutDiagnosticLog (
                LogID INTEGER PRIMARY KEY AUTOINCREMENT,
                CompanyID INTEGER NOT NULL DEFAULT 0,
                PLC_ID INTEGER,
                Level TEXT NOT NULL DEFAULT 'INFO',
                Message TEXT NOT NULL DEFAULT '',
                Timestamp TEXT NOT NULL DEFAULT ''
            )
        """)
        conn.execute("""
            INSERT INTO EdgeTimeoutDiagnosticLog
            (CompanyID, PLC_ID, Level, Message, Timestamp)
            VALUES (?, ?, ?, ?, datetime('now','localtime'))
        """, (int(company_id or 0), plc_id, str(level).upper(), str(message)))
        conn.commit()
    except Exception:
        pass
    finally:
        if conn is not None:
            try: conn.close()
            except Exception: pass


def _extract_request_context():
    payload = request.get_json(silent=True) or {}
    tr = payload.get("TrendRequest", payload) if isinstance(payload, dict) else {}
    if not isinstance(tr, dict): tr = {}
    company_id = tr.get("CompanyID")
    if company_id is None: company_id = request.args.get("company_id", type=int)
    try: company_id = int(company_id) if company_id is not None else 0
    except (TypeError, ValueError): company_id = 0
    plc_id = tr.get("PLC_ID", tr.get("plc_id"))
    try: plc_id = int(plc_id) if plc_id not in (None, "") else None
    except (TypeError, ValueError): plc_id = None
    return company_id, plc_id, tr


def _install():
    for _ in range(120):
        app_module = sys.modules.get("app") or sys.modules.get("__main__")
        flask_app = getattr(app_module, "app", None) if app_module else None
        if flask_app is None:
            time.sleep(0.5); continue
        if getattr(flask_app, "_trend_debug_installed", False): return
        try:
            from database import get_connection

            @flask_app.before_request
            def _trend_debug_request_trace():
                if request.path != "/flow_trend" or request.method != "POST": return None
                try:
                    company_id, plc_id, tr = _extract_request_context()
                    _write_log(company_id, plc_id, "INFO", "TREND_HTTP_REQUEST " + json.dumps({"path":request.path,"company":company_id,"plc":plc_id,"tag":str(tr.get("Tag") or "").strip(),"start":tr.get("Start"),"end":tr.get("End"),"calendar":tr.get("Calendar")}, ensure_ascii=False, separators=(",",":")))
                except Exception as exc:
                    _write_log(0, None, "ERROR", f"TREND_HTTP_REQUEST_TRACE_ERROR error={exc!r}")
                return None

            @flask_app.after_request
            def _trend_debug_response_trace(response):
                if request.path == "/flow_trend" and request.method == "POST":
                    try:
                        company_id, plc_id, tr = _extract_request_context()
                        _write_log(company_id, plc_id, "INFO" if int(response.status_code) < 400 else "ERROR", f"TREND_HTTP_RESPONSE status={response.status_code} tag={str(tr.get('Tag') or '').strip()}")
                    except Exception as exc:
                        _write_log(0, None, "ERROR", f"TREND_HTTP_RESPONSE_TRACE_ERROR error={exc!r}")
                return response

            @flask_app.get("/master/trend-debug")
            def master_trend_debug():
                try:
                    if not _is_master(): return redirect(url_for("login", next=request.path))
                    company_id = request.args.get("company_id", type=int)
                    if company_id is None: company_id = session.get("selected_company_id")
                    company_id = int(company_id) if company_id is not None else None
                    if company_id is None: return jsonify({"status":"error","message":"Company is required"}), 400
                    conn = get_connection(); cursor = conn.cursor()
                    try:
                        cursor.execute("PRAGMA table_info(EdgeTimeoutDiagnosticLog)")
                        cols = [r["name"] for r in cursor.fetchall()]
                        if not cols: return jsonify({"status":"ok","company_id":company_id,"trend_logs":[],"recent_plc_data":[],"hint":"Diagnostic table does not exist yet."})
                        has_plc = "PLC_ID" in cols
                        log_sql = ("SELECT LogID, CompanyID, PLC_ID, Level, Message, Timestamp FROM EdgeTimeoutDiagnosticLog" if has_plc else "SELECT LogID, CompanyID, NULL AS PLC_ID, Level, Message, Timestamp FROM EdgeTimeoutDiagnosticLog")
                        cursor.execute(log_sql + " WHERE CompanyID = ? AND (Message LIKE 'TREND_%' OR Message LIKE '%TREND_DIRECT%' OR Message LIKE '%PLC_ID%') ORDER BY LogID DESC LIMIT 300", (company_id,))
                        rows = [dict(r) for r in cursor.fetchall()]
                        cursor.execute("PRAGMA table_info(PLC_Data)")
                        pcols = [r["name"] for r in cursor.fetchall()]
                        data_sql = ("SELECT ID, CompanyID, PLC_ID, TagName, Value, StorageType, Timestamp FROM PLC_Data" if "PLC_ID" in pcols else "SELECT ID, CompanyID, NULL AS PLC_ID, TagName, Value, StorageType, Timestamp FROM PLC_Data")
                        cursor.execute(data_sql + " WHERE CompanyID = ? ORDER BY ID DESC LIMIT 100", (company_id,))
                        plc_rows = [dict(r) for r in cursor.fetchall()]
                    finally:
                        cursor.close(); conn.close()
                    return jsonify({"status":"ok","company_id":company_id,"trend_logs":rows,"recent_plc_data":plc_rows,"hint":"TREND_HTTP_REQUEST/RESPONSE proves the HTTP path; TREND_DIRECT_* proves TrendOutput execution."})
                except Exception as exc:
                    return jsonify({"status":"error","error_type":type(exc).__name__,"error":repr(exc)}), 200

            flask_app._trend_debug_installed = True
            return
        except Exception as exc:
            print("TREND DEBUG INSTALL RETRY:", exc)
        time.sleep(0.5)
    print("TREND DEBUG INSTALL FAILED AFTER RETRIES")
