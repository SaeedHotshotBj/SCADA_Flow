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


@flow_company_bp.get("/management")
def management_page():
    company_id = _management_company_id()
    if company_id is None:
        return jsonify({"status": "error", "message": "Company is required"}), 403
    if not _management_allowed(company_id):
        return render_template("access_denied.html"), 403
    ensure_management_tables()
    return render_template("management.html", company_id=company_id)


@flow_company_bp.get("/management/config")
def management_config():
    company_id = _management_company_id()
    if company_id is None or not _management_allowed(company_id):
        return jsonify({"status": "error", "message": "Access denied"}), 403
    return jsonify(get_management_config(company_id))


@flow_company_bp.post("/management/contracts")
def management_contract_create():
    company_id = _management_company_id()
    if company_id is None or not _management_allowed(company_id):
        return jsonify({"status": "error", "message": "Access denied"}), 403
    try:
        payload = request.get_json(silent=True) or {}
        contract_id = save_contract(company_id, payload)
        return jsonify({"status": "ok", "ContractID": contract_id}), 201
    except Exception as exc:
        return jsonify({"status": "error", "message": str(exc)}), 400


@flow_company_bp.post("/management/products")
def management_product_save():
    company_id = _management_company_id()
    if company_id is None or not _management_allowed(company_id):
        return jsonify({"status": "error", "message": "Access denied"}), 403
    try:
        payload = request.get_json(silent=True) or {}
        product_id = save_product(company_id, payload)
        return jsonify({"status": "ok", "ProductID": product_id}), 201
    except Exception as exc:
        return jsonify({"status": "error", "message": str(exc)}), 400


@flow_company_bp.get("/management/products")
def management_products():
    company_id = _management_company_id()
    if company_id is None or not _management_allowed(company_id):
        return jsonify({"status": "error", "message": "Access denied"}), 403
    return jsonify(get_products(company_id))


@flow_company_bp.get("/management/data")
def management_data():
    company_id = _management_company_id()
    if company_id is None or not _management_allowed(company_id):
        return jsonify({"status": "error", "message": "Access denied"}), 403
    try:
        return jsonify(get_management_data(company_id, request.args.to_dict(flat=True)))
    except Exception as exc:
        return jsonify({"status": "error", "message": str(exc)}), 400
