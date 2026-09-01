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
        return jsonify({"status": "error", "message": str(exc)}), 400


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
