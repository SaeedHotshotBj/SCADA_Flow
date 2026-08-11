# =====================================================
# SCADA_FLOW FLOW DESIGNER COMPANY MANAGEMENT
# =====================================================

from flask import Blueprint, jsonify, request, session

from database import (
    create_company,
    get_companies,
    get_company,
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


def register_flow_company_routes(app):

    if "flow_company.flow_companies" not in app.view_functions:
        app.register_blueprint(
            flow_company_bp
        )
