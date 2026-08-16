import json
from datetime import datetime, timedelta

import jdatetime

# =====================================================
# SCADA_FLOW FLOW DESIGNER COMPANY MANAGEMENT
# =====================================================

from flask import (
    Blueprint,
    jsonify,
    request,
    session,
    render_template,
)

from database import (
    create_company,
    get_companies,
    get_company,
    get_connection,
    get_company_flow,
)

from flow_runner import FlowRunner


flow_company_bp = Blueprint(
    "flow_company",
    __name__,
)


def _is_master():
    return (
        str(session.get("role", "")).strip().lower() == "master"
    )


def _report_company_id():
    if _is_master():
        return request.args.get("company_id", type=int) or session.get("selected_company_id")

    company_id = session.get("company_id")
    try:
        return int(company_id) if company_id is not None else None
    except (TypeError, ValueError):
        return None


@flow_company_bp.get("/flow/companies")
def flow_companies():
    if not _is_master():
        return jsonify({"status": "error", "message": "Access denied"}), 403
    return jsonify([dict(row) for row in get_companies()])


@flow_company_bp.post("/flow/company/create")
def flow_company_create():
    if not _is_master():
        return jsonify({"status": "error", "message": "Access denied"}), 403

    data = request.get_json(silent=True) or {}
    company_name = str(data.get("company_name", "")).strip()

    if not company_name:
        return jsonify({"status": "error", "message": "Company name is required"}), 400

    existing = get_companies()
    if any(str(row["CompanyName"]).strip().lower() == company_name.lower() for row in existing):
        return jsonify({"status": "error", "message": "Company already exists"}), 409

    company_id = create_company(company_name)
    company = get_company(company_id)
    return jsonify({"status": "ok", "company": dict(company)}), 201


@flow_company_bp.route("/report")
def report_page():
    if not session.get("user_id"):
        return jsonify({"status": "error", "message": "Login required"}), 401
    if _report_company_id() is None:
        return jsonify({"status": "error", "message": "Company is required"}), 403
    return render_template("date_filter.html")


@flow_company_bp.post("/flow_report")
def flow_report():
    if not session.get("user_id"):
        return jsonify({"status": "error", "message": "Login required"}), 401

    company_id = _report_company_id()
    if company_id is None:
        return jsonify({"status": "error", "message": "Company is required"}), 403

    try:
        flow_json = get_company_flow(company_id)
        if not flow_json:
            return jsonify({"status": "error", "message": "No flow configured for this company"}), 404

        flow = json.loads(flow_json) if isinstance(flow_json, str) else flow_json
        payload = request.get_json(silent=True) or {}
        report_request = payload.get("ReportRequest", {}) or {}
        report_request["CompanyID"] = company_id
        payload["ReportRequest"] = report_request

        runner = FlowRunner(flow, company_id)
        result = runner.execute_request(payload)
        chart_data = result.get("ChartData", {}) or {}
        report = chart_data.get("report", result.get("ReportData", {})) or {}

        return jsonify({
            "calendar": chart_data.get("calendar", report_request.get("Calendar", "Gregorian")),
            "date_picker": chart_data.get("date_picker", "GregorianPicker"),
            "report": report,
            "labels": chart_data.get("labels", []),
            "datasets": chart_data.get("datasets", []),
        })

    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({"status": "error", "message": str(e)}), 500


@flow_company_bp.get("/master/database")
def master_database():
    """Read-only Master database browser with newest records first."""
    if not _is_master():
        return jsonify({"status": "error", "message": "Access denied"}), 403

    conn = None

    try:
        conn = get_connection()
        cursor = conn.cursor()

        cursor.execute("""
            SELECT name
            FROM sqlite_master
            WHERE type = 'table'
              AND name NOT LIKE 'sqlite_%'
            ORDER BY name
        """)

        tables = [row["name"] for row in cursor.fetchall()]

        selected_table = str(request.args.get("table", "")).strip()
        if selected_table not in tables:
            selected_table = tables[0] if tables else ""

        rows = []
        columns = []
        total_rows = 0

        try:
            limit = int(request.args.get("limit", 100))
        except (TypeError, ValueError):
            limit = 100
        limit = max(10, min(limit, 500))

        try:
            offset = int(request.args.get("offset", 0))
        except (TypeError, ValueError):
            offset = 0
        offset = max(0, offset)

        if selected_table:
            safe_table = selected_table.replace('"', '""')

            cursor.execute(f'SELECT COUNT(*) AS row_count FROM "{safe_table}"')
            total_rows = int(cursor.fetchone()["row_count"])

            # Database Viewer must show newest records first.
            # Timestamp is preferred; ID is used as a deterministic tie-breaker.
            cursor.execute(f'PRAGMA table_info("{safe_table}")')
            table_info = cursor.fetchall()
            column_names = [row["name"] for row in table_info]

            if "Timestamp" in column_names:
                order_sql = 'ORDER BY "Timestamp" DESC, "ID" DESC'
            elif "CreatedTime" in column_names:
                order_sql = 'ORDER BY "CreatedTime" DESC, "ID" DESC'
            elif "LastModified" in column_names:
                order_sql = 'ORDER BY "LastModified" DESC, "ID" DESC'
            elif "ID" in column_names:
                order_sql = 'ORDER BY "ID" DESC'
            else:
                order_sql = ""

            cursor.execute(
                f'SELECT * FROM "{safe_table}" {order_sql} LIMIT ? OFFSET ?',
                (limit, offset),
            )

            fetched = cursor.fetchall()

            if fetched:
                columns = [description[0] for description in cursor.description]

                for row in fetched:
                    display_row = []

                    for column in columns:
                        value = row[column]

                        if any(token in column.lower() for token in ("passwordhash", "password_hash", "token", "secret")):
                            value = "••••••••"
                        elif isinstance(value, str) and len(value) > 300:
                            value = value[:300] + " …"

                        display_row.append(value)

                    rows.append(display_row)

        return render_template(
            "database_viewer.html",
            tables=tables,
            selected_table=selected_table,
            columns=columns,
            rows=rows,
            total_rows=total_rows,
            limit=limit,
            offset=offset,
        )

    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass


# =====================================================
# DIRECT DATABASE TREND
# =====================================================


def _direct_trend_company_id():
    company_id = session.get("company_id")
    if company_id is None and _is_master():
        company_id = session.get("selected_company_id")
    try:
        return int(company_id) if company_id is not None else None
    except (TypeError, ValueError):
        return None


def _parse_utc(value):
    if not value:
        return None
    text = str(value).strip().replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(text)
        if dt.tzinfo is not None:
            dt = dt.astimezone(__import__("datetime").timezone.utc).replace(tzinfo=None)
        return dt
    except Exception:
        return None


def _parse_jalali(value):
    if not value:
        return None
    try:
        text = str(value).strip().replace("-", "/").replace("T", " ")
        return jdatetime.datetime.strptime(text, "%Y/%m/%d %H:%M").togregorian()
    except Exception:
        return None


@flow_company_bp.get("/trend_db_config")
def trend_db_config():
    if not session.get("user_id"):
        return jsonify({"status": "error", "message": "Login required"}), 401

    company_id = _direct_trend_company_id()
    if company_id is None:
        return jsonify({"status": "error", "message": "Company is required"}), 403

    conn = get_connection()
    try:
        rows = conn.execute(
            """
            SELECT TagName, MAX(Timestamp) AS LastTimestamp
            FROM PLC_Data
            WHERE CompanyID = ?
              AND (StorageType IS NULL OR UPPER(StorageType) = 'TIME')
            GROUP BY TagName
            ORDER BY TagName
            """,
            (company_id,),
        ).fetchall()

        tags = [{"tag": row["TagName"], "title": row["TagName"], "last": row["LastTimestamp"]} for row in rows if row["TagName"]]
        return jsonify({"calendar": "Jalali", "tags": tags})
    finally:
        conn.close()


@flow_company_bp.post("/trend_db_data")
def trend_db_data():
    if not session.get("user_id"):
        return jsonify({"status": "error", "message": "Login required"}), 401

    company_id = _direct_trend_company_id()
    if company_id is None:
        return jsonify({"status": "error", "message": "Company is required"}), 403

    payload = request.get_json(silent=True) or {}
    tag = str(payload.get("tag", "")).strip()
    if not tag:
        return jsonify({"status": "error", "message": "Tag is required"}), 400

    start = _parse_utc(payload.get("startUtc"))
    end = _parse_utc(payload.get("endUtc"))

    live = start is None and end is None
    if live:
        end = datetime.now()
        start = end - timedelta(minutes=10)

    if (start is None) != (end is None):
        return jsonify({"status": "error", "message": "Both start and end are required"}), 400

    if start > end:
        start, end = end, start

    start_text = start.strftime("%Y-%m-%d %H:%M:%S")
    end_text = end.strftime("%Y-%m-%d %H:%M:%S")

    conn = get_connection()
    try:
        rows = conn.execute(
            """
            SELECT Timestamp, Value
            FROM PLC_Data
            WHERE CompanyID = ?
              AND LOWER(TagName) = LOWER(?)
              AND Timestamp >= ?
              AND Timestamp <= ?
            ORDER BY Timestamp ASC, ID ASC
            """,
            (company_id, tag, start_text, end_text),
        ).fetchall()

        points = []
        for row in rows:
            ts = row["Timestamp"]
            if ts is None:
                continue
            try:
                dt = datetime.fromisoformat(str(ts).replace("Z", ""))
                x = int(dt.timestamp() * 1000)
                y = float(row["Value"])
            except (TypeError, ValueError, OverflowError):
                continue
            points.append({"x": x, "y": y})

        return jsonify({"tag": tag, "live": live, "start": start_text, "end": end_text, "count": len(points), "points": points})
    finally:
        conn.close()


def register_flow_company_routes(app):
    if "flow_company.flow_companies" not in app.view_functions:
        app.register_blueprint(flow_company_bp)
