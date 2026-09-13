"""Master Database Viewer historical cleanup routes.

Only handles date-based deletion from non-configuration tables in the Master
Database Viewer. Existing application data flow and database behavior remain
unchanged.
"""

from flask import jsonify, request

from database import get_connection
from data_cleanup import (
    count_rows_before,
    date_range,
    list_tables,
    parse_cutoff,
    temporal_columns,
)
from flow_company_routes_legacy import flow_company_bp, _is_master


PROTECTED_TABLES = {
    "Companies",
    "Users",
    "PLCs",
    "Tags",
    "Flows",
    "AuthSessionRevocations",
}


def _validate_table(conn, table):
    table = str(table or "").strip()
    if not table:
        raise ValueError("Table is required")

    tables = set(list_tables(conn))
    if table not in tables:
        raise ValueError("Invalid table")

    if table in PROTECTED_TABLES:
        raise PermissionError("This table is protected from historical deletion.")

    return table


def _validate_column(conn, table, column):
    column = str(column or "").strip()
    available = {item["name"] for item in temporal_columns(conn, table)}
    if column not in available:
        raise ValueError("Invalid date/time column for this table")
    return column


def _payload(payload):
    return {
        "table": str(payload.get("table", "")).strip(),
        "column": str(payload.get("column", "")).strip(),
        "before": str(payload.get("before", "")).strip(),
    }


@flow_company_bp.get("/master/database/cleanup/schema")
def master_database_cleanup_schema():
    if not _is_master():
        return jsonify({"status": "error", "message": "Access denied"}), 403

    conn = get_connection()
    try:
        table = str(request.args.get("table", "")).strip()
        if not table:
            return jsonify({"status": "error", "message": "Table is required"}), 400

        if table not in set(list_tables(conn)):
            return jsonify({"status": "error", "message": "Invalid table"}), 400

        protected = table in PROTECTED_TABLES
        columns = temporal_columns(conn, table)

        return jsonify({
            "status": "ok",
            "table": table,
            "protected": protected,
            "columns": [
                {"name": item["name"], "type": item["type"]}
                for item in columns
            ],
        })
    except Exception as exc:
        return jsonify({"status": "error", "message": str(exc)}), 500
    finally:
        conn.close()


@flow_company_bp.post("/master/database/cleanup/preview")
def master_database_cleanup_preview():
    if not _is_master():
        return jsonify({"status": "error", "message": "Access denied"}), 403

    data = _payload(request.get_json(silent=True) or {})
    conn = get_connection()
    try:
        table = _validate_table(conn, data["table"])
        column = _validate_column(conn, table, data["column"])
        cutoff = parse_cutoff(data["before"])
        count = count_rows_before(conn, table, column, cutoff)
        minimum, maximum = date_range(conn, table, column)

        return jsonify({
            "status": "ok",
            "table": table,
            "column": column,
            "before": data["before"],
            "rows_to_delete": count,
            "minimum": minimum,
            "maximum": maximum,
        })
    except PermissionError as exc:
        return jsonify({"status": "error", "message": str(exc)}), 403
    except Exception as exc:
        return jsonify({"status": "error", "message": str(exc)}), 400
    finally:
        conn.close()


@flow_company_bp.post("/master/database/cleanup/delete")
def master_database_cleanup_delete():
    if not _is_master():
        return jsonify({"status": "error", "message": "Access denied"}), 403

    payload = request.get_json(silent=True) or {}
    data = _payload(payload)
    confirmation = str(payload.get("confirmation", "")).strip()

    conn = get_connection()
    try:
        table = _validate_table(conn, data["table"])
        column = _validate_column(conn, table, data["column"])
        cutoff = parse_cutoff(data["before"])

        expected = f"DELETE BEFORE {table}"
        if confirmation != expected:
            return jsonify({
                "status": "error",
                "message": f"Confirmation required: {expected}",
            }), 400

        before_count = count_rows_before(conn, table, column, cutoff)
        if before_count == 0:
            return jsonify({
                "status": "ok",
                "table": table,
                "column": column,
                "deleted": 0,
                "remaining_before_cutoff": 0,
                "message": "No rows matched the selected cutoff.",
            })

        from data_cleanup import delete_rows

        conn.execute("BEGIN")
        try:
            deleted = delete_rows(conn, table, column, cutoff)
            conn.commit()
        except Exception:
            conn.rollback()
            raise

        remaining = count_rows_before(conn, table, column, cutoff)

        return jsonify({
            "status": "ok",
            "table": table,
            "column": column,
            "deleted": deleted,
            "remaining_before_cutoff": remaining,
            "message": f"{deleted} historical rows deleted from {table}.",
        })
    except PermissionError as exc:
        return jsonify({"status": "error", "message": str(exc)}), 403
    except Exception as exc:
        try:
            conn.rollback()
        except Exception:
            pass
        return jsonify({"status": "error", "message": str(exc)}), 400
    finally:
        conn.close()
