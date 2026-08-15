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
)


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


@flow_company_bp.get("/flow/companies")
def flow_companies():

    if not _is_master():
        return jsonify({
            "status": "error",
            "message": "Access denied",
        }), 403

    return jsonify([
        dict(row)
        for row in get_companies()
    ])


@flow_company_bp.post("/flow/company/create")
def flow_company_create():

    if not _is_master():
        return jsonify({
            "status": "error",
            "message": "Access denied",
        }), 403

    data = request.get_json(
        silent=True
    ) or {}

    company_name = str(
        data.get(
            "company_name",
            "",
        )
    ).strip()

    if not company_name:
        return jsonify({
            "status": "error",
            "message": "Company name is required",
        }), 400

    existing = get_companies()

    if any(
        str(row["CompanyName"]).strip().lower()
        == company_name.lower()
        for row in existing
    ):
        return jsonify({
            "status": "error",
            "message": "Company already exists",
        }), 409

    company_id = create_company(
        company_name
    )

    company = get_company(
        company_id
    )

    return jsonify({
        "status": "ok",
        "company": dict(company),
    }), 201


@flow_company_bp.get("/master/database")
def master_database():
    """
    Read-only browser for the production SQLite database.

    This is intentionally restricted to Master users and only exposes
    table/row data through the existing SCADA web application. It does
    not expose the SQLite file itself.
    """

    if not _is_master():
        return jsonify({
            "status": "error",
            "message": "Access denied",
        }), 403

    conn = None

    try:
        conn = get_connection()
        cursor = conn.cursor()

        cursor.execute(
            """
            SELECT name
            FROM sqlite_master
            WHERE type = 'table'
              AND name NOT LIKE 'sqlite_%'
            ORDER BY name
            """
        )

        tables = [
            row["name"]
            for row in cursor.fetchall()
        ]

        selected_table = str(
            request.args.get(
                "table",
                ""
            )
        ).strip()

        if selected_table not in tables:
            selected_table = tables[0] if tables else ""

        rows = []
        columns = []
        total_rows = 0

        try:
            limit = int(
                request.args.get(
                    "limit",
                    100
                )
            )
        except (TypeError, ValueError):
            limit = 100

        limit = max(
            10,
            min(limit, 500)
        )

        try:
            offset = int(
                request.args.get(
                    "offset",
                    0
                )
            )
        except (TypeError, ValueError):
            offset = 0

        offset = max(
            0,
            offset
        )

        if selected_table:
            safe_table = selected_table.replace(
                '"',
                '""'
            )

            cursor.execute(
                f'SELECT COUNT(*) AS row_count FROM "{safe_table}"'
            )

            total_rows = int(
                cursor.fetchone()["row_count"]
            )

            cursor.execute(
                f'SELECT * FROM "{safe_table}" LIMIT ? OFFSET ?',
                (
                    limit,
                    offset,
                )
            )

            fetched = cursor.fetchall()

            if fetched:
                columns = [
                    description[0]
                    for description in cursor.description
                ]

                for row in fetched:
                    display_row = []

                    for column in columns:
                        value = row[column]

                        # Never display password hashes or similar
                        # credential material in the browser.
                        if any(
                            token in column.lower()
                            for token in (
                                "passwordhash",
                                "password_hash",
                                "token",
                                "secret",
                            )
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
        return jsonify({
            "status": "error",
            "message": str(e),
        }), 500

    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass


def register_flow_company_routes(app):

    if "flow_company.flow_companies" not in app.view_functions:
        app.register_blueprint(
            flow_company_bp
        )
