from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def patch_file(path, replacements):
    text = path.read_text(encoding="utf-8")
    original = text
    for old, new in replacements:
        if old not in text:
            raise RuntimeError(f"Patch anchor not found in {path}: {old[:100]!r}")
        text = text.replace(old, new, 1)
    if text != original:
        path.write_text(text, encoding="utf-8")
        print(f"UPDATED {path}")


# app.py: wire the UI to the topology-driven management runner.
app = ROOT / "app.py"
patch_file(app, [
    (
        "from services.dashboard_service import get_dashboard_widgets\n",
        "from services.dashboard_service import get_dashboard_widgets\n\nfrom services.management_service import (\n    ensure_management_flow,\n    get_management_config,\n    init_management_database,\n)\nfrom services.management_runner import execute_management_flow\nfrom services.management_access import allowed as management_access_allowed\n",
    ),
    (
        "init_database()\n\n\n# =====================================================\n# EDGE TIMEOUT WORKER",
        "init_database()\ninit_management_database()\n\n\n# =====================================================\n# EDGE TIMEOUT WORKER",
    ),
    (
        "# =====================================================\n# SAVE FLOW\n# =====================================================\n",
        """# =====================================================\n# MANAGEMENT PANEL\n# =====================================================\n\n@app.route(\"/management\")\n@login_required\ndef management_page():\n    company_id = get_request_company_id()\n    if company_id is None:\n        return jsonify({\"status\": \"error\", \"message\": \"Company is required\"}), 403\n\n    ensure_management_flow(company_id)\n    if not management_access_allowed(company_id, session.get(\"role\")):\n        return render_template(\"access_denied.html\"), 403\n\n    return render_template(\"management.html\")\n\n\n@app.route(\"/management/config\")\n@login_required\ndef management_config_api():\n    company_id = get_request_company_id()\n    if company_id is None:\n        return jsonify({\"status\": \"error\", \"message\": \"Company is required\"}), 403\n\n    ensure_management_flow(company_id)\n    if not management_access_allowed(company_id, session.get(\"role\")):\n        return jsonify({\"status\": \"error\", \"message\": \"Access denied\"}), 403\n\n    config = get_management_config(company_id)\n    return jsonify(config)\n\n\n@app.route(\"/management/request\", methods=[\"POST\"])\n@login_required\ndef management_request_api():\n    company_id = get_request_company_id()\n    if company_id is None:\n        return jsonify({\"status\": \"error\", \"message\": \"Company is required\"}), 403\n\n    ensure_management_flow(company_id)\n    if not management_access_allowed(company_id, session.get(\"role\")):\n        return jsonify({\"status\": \"error\", \"message\": \"Access denied\"}), 403\n\n    flow = get_flow_data(company_id)\n    if not flow:\n        return jsonify({\"status\": \"error\", \"message\": \"Management Flow not configured\"}), 409\n\n    payload = request.get_json() or {}\n    result = execute_management_flow(flow, company_id, {\"ManagementRequest\": payload})\n    status_code = 200 if result.get(\"status\") == \"ok\" else 400\n    return jsonify(result), status_code\n\n\n# =====================================================\n# SAVE FLOW\n# =====================================================\n""",
    ),
])


# nav.html: expose the page using the same Flow permission model.
nav = ROOT / "templates" / "nav.html"
patch_file(nav, [
    (
        '<a class="flow-nav-link" data-flow-page="ReportOutput" href="/report">Report</a>\n',
        '<a class="flow-nav-link" data-flow-page="ReportOutput" href="/report">Report</a>\n    <a class="flow-nav-link" data-flow-page="ManagementPanelOutput" href="/management">Management</a>\n',
    ),
    (
        'if (!node || node.name !== "RolesEngaged") return;\n',
        'if (!node || (node.name !== "RolesEngaged" && node.name !== "ManagementRolesEngaged")) return;\n',
    ),
])
