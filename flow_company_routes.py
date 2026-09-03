# SCADA_FLOW COMPANY ROUTES WRAPPER
# Keeps the existing company/report/database routes intact and adds the
# Flow-based management panel routes below.

from flow_company_routes_legacy import *  # noqa: F401,F403
from flow_company_routes_legacy import flow_company_bp, _report_company_id, _is_master

from flask import jsonify, request, session, render_template

from services.management_service import (
    ensure_management_tables,
    management_flow_allowed,
    get_config as get_management_config,
    get_management_data,
    save_contract,
    save_product,
    get_products,
)
from services.management_migration import ensure_management_schema
from services.management_crud import (
    get_contract_by_code,
    update_contract,
    delete_contract,
    update_product,
    delete_product,
)
from services.plc_write_service import (
    ensure_write_table,
    get_company_plcs,
    create_write_command,
    get_command_status,
    get_command_history,
    claim_next_command,
    complete_command,
)


def _management_company_id():
    return _report_company_id()


def _management_allowed(company_id):
    if not session.get("user_id"):
        return False
    return management_flow_allowed(
        company_id,
        session.get("role"),
        is_master=_is_master(),
    )


def _prepare_management_db():
    ensure_management_tables()
    ensure_management_schema()


@flow_company_bp.get("/management")
def management_page():
    company_id = _management_company_id()
    if company_id is None:
        return jsonify({"status": "error", "message": "Company is required"}), 403
    if not _management_allowed(company_id):
        return render_template("access_denied.html"), 403
    _prepare_management_db()

    html = render_template("management.html", company_id=company_id)
    dropdown_bootstrap = (
        '<script>window.SCADA_MANAGEMENT_COMPANY_ID = '
        + str(int(company_id))
        + ';</script>'
        '<script src="/static/management_dropdowns.js?v=20260902"></script>'
    )
    if "</body>" in html:
        html = html.replace("</body>", dropdown_bootstrap + "</body>", 1)
    else:
        html += dropdown_bootstrap
    return html


@flow_company_bp.get("/management/options")
def management_options():
    company_id = _management_company_id()
    if company_id is None or not _management_allowed(company_id):
        return jsonify({"status": "error", "message": "Access denied"}), 403

    _prepare_management_db()
    conn = None
    try:
        conn = __import__("database").get_connection()
        row = conn.execute(
            """
            SELECT
                (SELECT GROUP_CONCAT(ContractCode, char(10))
                   FROM (SELECT DISTINCT ContractCode
                           FROM Contracts
                          WHERE CompanyID=?
                            AND TRIM(COALESCE(ContractCode,''))<>''
                          ORDER BY ContractCode)) AS ContractCodes,
                (SELECT GROUP_CONCAT(ContractName, char(10))
                   FROM (SELECT DISTINCT ContractName
                           FROM Contracts
                          WHERE CompanyID=?
                            AND TRIM(COALESCE(ContractName,''))<>''
                          ORDER BY ContractName)) AS ContractNames,
                (SELECT GROUP_CONCAT(ProductCode, char(10))
                   FROM (SELECT DISTINCT ProductCode
                           FROM Products
                          WHERE CompanyID=?
                            AND TRIM(COALESCE(ProductCode,''))<>''
                          ORDER BY ProductCode)) AS ProductCodes,
                (SELECT GROUP_CONCAT(ProductName, char(10))
                   FROM (SELECT DISTINCT ProductName
                           FROM Products
                          WHERE CompanyID=?
                            AND TRIM(COALESCE(ProductName,''))<>''
                          ORDER BY ProductName)) AS ProductNames
            """,
            (company_id, company_id, company_id, company_id),
        ).fetchone()

        def split_group(value):
            if not value:
                return []
            return [item.strip() for item in str(value).split("\n") if item.strip()]

        return jsonify({
            "status": "ok",
            "CompanyID": int(company_id),
            "contract_codes": split_group(row["ContractCodes"]),
            "contract_names": split_group(row["ContractNames"]),
            "product_codes": split_group(row["ProductCodes"]),
            "product_names": split_group(row["ProductNames"]),
        })
    except Exception as exc:
        import traceback
        traceback.print_exc()
        return jsonify({"status": "error", "message": str(exc)}), 500
    finally:
        if conn is not None:
            conn.close()


@flow_company_bp.get("/management/config")
def management_config():
    company_id = _management_company_id()
    if company_id is None or not _management_allowed(company_id):
        return jsonify({"status": "error", "message": "Access denied"}), 403
    _prepare_management_db()
    return jsonify(get_management_config(company_id))


@flow_company_bp.post("/management/contracts")
def management_contract_create():
    company_id = _management_company_id()
    if company_id is None or not _management_allowed(company_id):
        return jsonify({"status": "error", "message": "Access denied"}), 403
    try:
        payload = request.get_json(silent=True) or {}
        _prepare_management_db()
        contract_id = save_contract(company_id, payload)
        return jsonify({"status": "ok", "ContractID": contract_id}), 201
    except Exception as exc:
        return jsonify({"status": "error", "message": str(exc)}), 400


@flow_company_bp.get("/management/contracts/detail")
def management_contract_detail():
    company_id = _management_company_id()
    if company_id is None or not _management_allowed(company_id):
        return jsonify({"status": "error", "message": "Access denied"}), 403
    try:
        _prepare_management_db()
        contract_code = request.args.get("contract_code", "")
        return jsonify(get_contract_by_code(company_id, contract_code))
    except Exception as exc:
        return jsonify({"status": "error", "message": str(exc)}), 404


@flow_company_bp.put("/management/contracts/detail")
def management_contract_update():
    company_id = _management_company_id()
    if company_id is None or not _management_allowed(company_id):
        return jsonify({"status": "error", "message": "Access denied"}), 403
    try:
        payload = request.get_json(silent=True) or {}
        original_code = str(payload.pop("original_contract_code", "")).strip()
        _prepare_management_db()
        contract_id = update_contract(company_id, original_code, payload)
        return jsonify({"status": "ok", "ContractID": contract_id})
    except Exception as exc:
        return jsonify({"status": "error", "message": str(exc)}), 400


@flow_company_bp.delete("/management/contracts/detail")
def management_contract_delete():
    company_id = _management_company_id()
    if company_id is None or not _management_allowed(company_id):
        return jsonify({"status": "error", "message": "Access denied"}), 403
    try:
        _prepare_management_db()
        contract_code = request.args.get("contract_code", "")
        contract_id = delete_contract(company_id, contract_code)
        return jsonify({"status": "ok", "ContractID": contract_id})
    except Exception as exc:
        return jsonify({"status": "error", "message": str(exc)}), 409


@flow_company_bp.post("/management/products")
def management_product_save():
    company_id = _management_company_id()
    if company_id is None or not _management_allowed(company_id):
        return jsonify({"status": "error", "message": "Access denied"}), 403
    try:
        payload = request.get_json(silent=True) or {}
        _prepare_management_db()
        product_id = save_product(company_id, payload)
        return jsonify({"status": "ok", "ProductID": product_id}), 201
    except Exception as exc:
        return jsonify({"status": "error", "message": str(exc)}), 400


@flow_company_bp.put("/management/products")
def management_product_update():
    company_id = _management_company_id()
    if company_id is None or not _management_allowed(company_id):
        return jsonify({"status": "error", "message": "Access denied"}), 403
    try:
        payload = request.get_json(silent=True) or {}
        product_id = payload.pop("product_id", None)
        _prepare_management_db()
        product_id = update_product(company_id, product_id, payload)
        return jsonify({"status": "ok", "ProductID": product_id})
    except Exception as exc:
        return jsonify({"status": "error", "message": str(exc)}), 400


@flow_company_bp.delete("/management/products")
def management_product_delete():
    company_id = _management_company_id()
    if company_id is None or not _management_allowed(company_id):
        return jsonify({"status": "error", "message": "Access denied"}), 403
    try:
        product_id = request.args.get("product_id", type=int)
        _prepare_management_db()
        deleted_id = delete_product(company_id, product_id)
        return jsonify({"status": "ok", "ProductID": deleted_id})
    except Exception as exc:
        return jsonify({"status": "error", "message": str(exc)}), 409


@flow_company_bp.get("/management/products")
def management_products():
    company_id = _management_company_id()
    if company_id is None or not _management_allowed(company_id):
        return jsonify({"status": "error", "message": "Access denied"}), 403
    _prepare_management_db()
    return jsonify(get_products(company_id))


@flow_company_bp.get("/management/data")
def management_data():
    company_id = _management_company_id()
    if company_id is None or not _management_allowed(company_id):
        return jsonify({"status": "error", "message": "Access denied"}), 403
    try:
        _prepare_management_db()
        return jsonify(get_management_data(company_id, request.args.to_dict(flat=True)))
    except Exception as exc:
        return jsonify({"status": "error", "message": str(exc)}), 400


# =====================================================
# MASTER PLC WRITE CONTROL
# =====================================================


def _master_company_id_for_write():
    if not _is_master() or not session.get("user_id"):
        return None

    company_id = request.args.get("company_id", type=int)
    if company_id is None:
        company_id = session.get("selected_company_id")

    try:
        return int(company_id) if company_id is not None else None
    except (TypeError, ValueError):
        return None


@flow_company_bp.get("/master/plc_write/plcs")
def master_plc_write_plcs():
    company_id = _master_company_id_for_write()
    if company_id is None:
        return jsonify({"status": "error", "message": "Master access and company are required"}), 403

    ensure_write_table()
    return jsonify({
        "status": "ok",
        "CompanyID": company_id,
        "plcs": get_company_plcs(company_id),
    })


@flow_company_bp.post("/master/plc_write")
def master_plc_write_create():
    company_id = _master_company_id_for_write()
    if company_id is None:
        return jsonify({"status": "error", "message": "Master access and company are required"}), 403

    payload = request.get_json(silent=True) or {}

    try:
        plc_id = int(payload.get("PLC_ID"))
        register = int(payload.get("Register"))
        value = int(payload.get("Value"))
    except (TypeError, ValueError):
        return jsonify({"status": "error", "message": "PLC_ID, Register and Value must be integers"}), 400

    if plc_id <= 0:
        return jsonify({"status": "error", "message": "Invalid PLC_ID"}), 400

    if not 0 <= register <= 65535:
        return jsonify({"status": "error", "message": "Register must be between 0 and 65535"}), 400

    if not 0 <= value <= 65535:
        return jsonify({"status": "error", "message": "Value must be between 0 and 65535"}), 400

    ensure_write_table()

    try:
        command_id = create_write_command(
            company_id,
            plc_id,
            register,
            value,
        )
    except ValueError as exc:
        return jsonify({"status": "error", "message": str(exc)}), 400

    return jsonify({
        "status": "ok",
        "message": "Write command queued",
        "CommandID": command_id,
        "CompanyID": company_id,
        "PLC_ID": plc_id,
        "Register": register,
        "Value": value,
    }), 201


@flow_company_bp.get("/master/plc_write/status/<int:command_id>")
def master_plc_write_status(command_id):
    company_id = _master_company_id_for_write()
    if company_id is None:
        return jsonify({"status": "error", "message": "Master access and company are required"}), 403

    ensure_write_table()
    row = get_command_status(company_id, command_id)
    if row is None:
        return jsonify({"status": "error", "message": "Command not found"}), 404

    return jsonify({"status": "ok", "command": row})


@flow_company_bp.get("/master/plc_write/history")
def master_plc_write_history():
    company_id = _master_company_id_for_write()
    if company_id is None:
        return jsonify({"status": "error", "message": "Master access and company are required"}), 403

    ensure_write_table()
    return jsonify({
        "status": "ok",
        "commands": get_command_history(company_id),
    })


# =====================================================
# EDGE PLC WRITE QUEUE
# =====================================================


@flow_company_bp.get("/api/edge/write_command")
def edge_get_write_command():
    plc_id = request.args.get("PLC_ID", type=int)
    if plc_id is None or plc_id <= 0:
        return jsonify({"status": "error", "message": "PLC_ID is required"}), 400

    ensure_write_table()
    command = claim_next_command(plc_id)

    if command is None:
        return jsonify({"status": "empty"})

    return jsonify({
        "status": "ok",
        "command": command,
    })


@flow_company_bp.post("/api/edge/write_result")
def edge_write_result():
    payload = request.get_json(silent=True) or {}

    try:
        command_id = int(payload.get("CommandID"))
        plc_id = int(payload.get("PLC_ID"))
    except (TypeError, ValueError):
        return jsonify({"status": "error", "message": "CommandID and PLC_ID are required"}), 400

    success = payload.get("Success") is True
    error_message = payload.get("ErrorMessage")
    if error_message is not None:
        error_message = str(error_message)[:1000]

    ensure_write_table()

    updated = complete_command(
        command_id,
        plc_id,
        success,
        error_message,
    )

    if not updated:
        return jsonify({"status": "error", "message": "Command not found"}), 404

    return jsonify({
        "status": "ok",
        "CommandID": command_id,
        "Success": success,
    })
