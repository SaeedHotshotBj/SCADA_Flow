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
        try:
            if conn is not None:
                conn.rollback()
        except Exception:
            pass
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass


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

            @flask_app.before_request
            def _trend_debug_request_trace():
                if request.path != "/flow_trend" or request.method != "POST":
                    return None
                try:
                    payload = request.get_json(silent=True) or {}
                    tr = payload.get("TrendRequest", payload) if isinstance(payload, dict) else {}
                    if not isinstance(tr, dict):
                        tr = {}
                    company_id = tr.get("CompanyID")
                    if company_id is None:
                        company_id = request.args.get("company_id", type=int)
                    try:
                        company_id = int(company_id) if company_id is not None else 0
                    except (TypeError, ValueError):
                        company_id = 0
                    plc_id = tr.get("PLC_ID", tr.get("plc_id"))
                    try:
                        plc_id = int(plc_id) if plc_id not in (None, "") else None
                    except (TypeError, ValueError):
                        plc_id = None
                    _write_log(company_id, plc_id, "INFO", "TREND_HTTP_REQUEST " + json.dumps({
                        "path": request.path,
                        "company": company_id,
                        "plc": plc_id,
                        "tag": str(tr.get("Tag") or "").strip(),
                        "start": tr.get("Start"),
                        "end": tr.get("End"),
                        "calendar": tr.get("Calendar"),
                    }, ensure_ascii=False, separators=(",", ":")))
                except Exception as exc:
                    _write_log(0, None, "ERROR", f"TREND_HTTP_REQUEST_TRACE_ERROR error={exc!r}")
                return None

            @flask_app.after_request
            def _trend_debug_response_trace(response):
                if request.path == "/flow_trend" and request.method == "POST":
                    try:
                        payload = request.get_json(silent=True) or {}
                        tr = payload.get("TrendRequest", payload) if isinstance(payload, dict) else {}
                        if not isinstance(tr, dict):
                            tr = {}
                        try:
                            company_id = int(tr.get("CompanyID")) if tr.get("CompanyID") is not None else 0
                        except (TypeError, ValueError):
                            company_id = 0
                        plc_id = tr.get("PLC_ID", tr.get("plc_id"))
                        try:
                            plc_id = int(plc_id) if plc_id not in (None, "") else None
                        except (TypeError, ValueError):
                            plc_id = None
                        status = getattr(response, "status_code", 500)
                        _write_log(company_id, plc_id, "INFO" if int(status) < 400 else "ERROR",
                                    f"TREND_HTTP_RESPONSE status={status} content_type={response.headers.get('Content-Type', '')}")
                    except Exception as exc:
                        _write_log(0, None, "ERROR", f"TREND_HTTP_RESPONSE_TRACE_ERROR error={exc!r}")
                return response

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
                    cursor.execute("""
                        SELECT LogID, CompanyID, PLC_ID, Level, Message, Timestamp
                        FROM EdgeTimeoutDiagnosticLog
                        WHERE CompanyID = ?
                          AND (Message LIKE 'TREND_%' OR Message LIKE '%TREND_DIRECT%' OR Message LIKE '%PLC_ID%')
                        ORDER BY LogID DESC
                        LIMIT 300
                    """, (company_id,))
                    rows = [dict(r) for r in cursor.fetchall()]
                    cursor.execute("""
                        SELECT ID, CompanyID, PLC_ID, TagName, Value, StorageType, Timestamp
                        FROM PLC_Data WHERE CompanyID = ? ORDER BY ID DESC LIMIT 100
                    """, (company_id,))
                    plc_rows = [dict(r) for r in cursor.fetchall()]
                finally:
                    cursor.close()
                    conn.close()
                return jsonify({
                    "status": "ok",
                    "company_id": company_id,
                    "trend_logs": rows,
                    "recent_plc_data": plc_rows,
                    "hint": "TREND_HTTP_REQUEST/RESPONSE proves the HTTP path; TREND_DIRECT_* proves TrendOutput execution."
                })

            flask_app._trend_debug_installed = True
            print("TREND DEBUG MASTER ENDPOINT INSTALLED: /master/trend-debug")
            return
        except Exception as exc:
            print("TREND DEBUG INSTALL RETRY:", exc)
        time.sleep(0.5)
    print("TREND DEBUG INSTALL FAILED AFTER RETRIES")
