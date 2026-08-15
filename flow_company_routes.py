import json

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
        str(
            session.get("role", "")
        ).strip().lower()
        == "master"
    )


def _report_company_id():
    """Return the company available to the current session."""
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

    if any(
        str(row["CompanyName"]).strip().lower() == company_name.lower()
        for row in existing
    ):
        return jsonify({"status": "error", "message": "Company already exists"}), 409

    company_id = create_company(company_name)
    company = get_company(company_id)

    return jsonify({
        "status": "ok",
        "company": dict(company),
    }), 201


@flow_company_bp.route("/report")
def report_page():
    """Production report page driven by the company's ReportOutput node."""
    if not session.get("user_id"):
        return jsonify({"status": "error", "message": "Login required"}), 401

    if _report_company_id() is None:
        return jsonify({"status": "error", "message": "Company is required"}), 403

    return render_template("date_filter.html")


@flow_company_bp.post("/flow_report")
def flow_report():
    """Execute the saved flow and return ReportOutput ChartData."""
    if not session.get("user_id"):
        return jsonify({"status": "error", "message": "Login required"}), 401

    company_id = _report_company_id()
    if company_id is None:
        return jsonify({"status": "error", "message": "Company is required"}), 403

    try:
        flow_json = get_company_flow(company_id)
        if not flow_json:
            return jsonify({
                "status": "error",
                "message": "No flow configured for this company",
            }), 404

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
    """
    Read-only browser for the production SQLite database.

    This is intentionally restricted to Master users and only exposes
    table/row data through the existing SCADA web application. It does
    not expose the SQLite file itself.
    """
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

            cursor.execute(
                f'SELECT * FROM "{safe_table}" LIMIT ? OFFSET ?',
                (limit, offset),
            )

            fetched = cursor.fetchall()

            if fetched:
                columns = [description[0] for description in cursor.description]

                for row in fetched:
                    display_row = []

                    for column in columns:
                        value = row[column]

                        if any(
                            token in column.lower()
                            for token in ("passwordhash", "password_hash", "token", "secret")
                        ):
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


def register_flow_company_routes(app):
    if "flow_company.flow_companies" not in app.view_functions:
        app.register_blueprint(flow_company_bp)
