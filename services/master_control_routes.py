"""Master control and Edge transport API routes."""

from flask import Blueprint, jsonify, request, session

from services.edge_ingest import ingest_items, ensure_edge_event_schema
from services.plc_write_service import (
    claim_next_command,
    complete_command,
    ensure_plc_write_schema,
    get_command,
    queue_write,
)


bp = Blueprint("master_control", __name__)


def _is_master():
    return str(session.get("role", "")).strip().lower() == "master"


def _requested_company_id():
    value = request.args.get("company_id", type=int)
    if value is None:
        payload = request.get_json(silent=True) or {}
        value = payload.get("CompanyID")
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


@bp.get("/master/plc_write/plcs")
def master_plc_write_plcs():
    if not _is_master():
        return jsonify({"status": "error", "message": "Master access required"}), 403
    company_id = _requested_company_id()
    if company_id is None:
        return jsonify({"status": "error", "message": "CompanyID is required"}), 400

    from database import get_connection
    conn = get_connection()
    try:
        rows = conn.execute(
            """
            SELECT PLC_ID, PLC_Name, PLC_IP, PLC_Port, Slave_ID
            FROM PLCs
            WHERE CompanyID=?
            ORDER BY PLC_ID
            """,
            (company_id,),
        ).fetchall()
        return jsonify([dict(row) for row in rows])
    finally:
        conn.close()


@bp.post("/master/plc_write")
def master_plc_write():
    if not _is_master():
        return jsonify({"status": "error", "message": "Master access required"}), 403
    payload = request.get_json(silent=True) or {}
    try:
        company_id = int(payload.get("CompanyID"))
        plc_id = int(payload.get("PLC_ID"))
        command = queue_write(company_id, plc_id, payload.get("Register"), payload.get("Value"))
        return jsonify({"status": "ok", "command": command}), 201
    except Exception as exc:
        return jsonify({"status": "error", "message": str(exc)}), 400


@bp.get("/master/plc_write/<int:command_id>")
def master_plc_write_status(command_id):
    if not _is_master():
        return jsonify({"status": "error", "message": "Master access required"}), 403
    company_id = _requested_company_id()
    if company_id is None:
        return jsonify({"status": "error", "message": "CompanyID is required"}), 400
    command = get_command(company_id, command_id)
    if command is None:
        return jsonify({"status": "error", "message": "Command not found"}), 404
    return jsonify({"status": "ok", "command": command})


@bp.get("/api/edge/write_command")
def edge_write_command():
    plc_id = request.args.get("PLC_ID", type=int)
    if plc_id is None or plc_id <= 0:
        return jsonify({"status": "error", "message": "PLC_ID is required"}), 400
    try:
        ensure_plc_write_schema()
        from database import get_connection
        conn = get_connection()
        try:
            plc = conn.execute("SELECT PLC_ID FROM PLCs WHERE PLC_ID=?", (plc_id,)).fetchone()
        finally:
            conn.close()
        if plc is None:
            return jsonify({"status": "error", "message": "PLC not found"}), 404
        command = claim_next_command(plc_id)
        return jsonify({"status": "ok", "command": command})
    except Exception as exc:
        return jsonify({"status": "error", "message": str(exc)}), 500


@bp.post("/api/edge/write_result")
def edge_write_result():
    payload = request.get_json(silent=True) or {}
    try:
        command_id = int(payload.get("CommandID"))
        plc_id = int(payload.get("PLC_ID"))
        success = bool(payload.get("Success"))
    except (TypeError, ValueError):
        return jsonify({"status": "error", "message": "CommandID, PLC_ID and Success are required"}), 400
    try:
        ensure_plc_write_schema()
        command = complete_command(command_id, plc_id, success, payload.get("ErrorMessage"))
        if command is None:
            return jsonify({"status": "error", "message": "Command not found"}), 404
        return jsonify({"status": "ok", "command": command})
    except Exception as exc:
        return jsonify({"status": "error", "message": str(exc)}), 500


@bp.post("/api/store_forward")
def edge_store_forward():
    try:
        payload = request.get_json(silent=True) or {}
        result = ingest_items(payload.get("items"))
        ensure_edge_event_schema()
        return jsonify({
            "status": "ok",
            "acks": result["acks"],
            "errors": [
                {"EventID": item.get("EventID"), "Error": item.get("Error")}
                for item in result["errors"]
            ],
            "inserted": result["inserted"],
        })
    except Exception as exc:
        return jsonify({"status": "error", "message": str(exc)}), 500


__all__ = ["bp"]
