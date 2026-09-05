# SCADA_FLOW EDITOR NODE REGISTRY WRAPPER
# Preserve the existing registry and extend it with management configuration.

from copy import deepcopy
from flow_engine import node_registry_legacy as _legacy

NODE_REGISTRY = deepcopy(_legacy.NODE_REGISTRY)

report = NODE_REGISTRY.get("ReportOutput")
if isinstance(report, dict):
    for definition in report.get("config", []):
        if definition.get("name") != "products":
            continue
        columns = definition.setdefault("columns", [])
        if not any(column.get("name") == "context_role" for column in columns):
            columns.append({
                "name": "context_role",
                "label": "Context Role",
                "type": "select",
                "options": ["", "contract_code", "product_code"],
            })

NODE_REGISTRY["ManagementPanel"] = {
    "config": [
        {
            "name": "DatePicker",
            "label": "Date Type",
            "type": "select",
            "options": ["GregorianPicker", "JalaliPicker"],
            "default": "JalaliPicker",
        },
        {
            "name": "contract_code_register",
            "label": "Contract Code PLC Register",
            "type": "number",
            "default": "",
        },
        {
            "name": "product_code_register",
            "label": "Product Code PLC Register",
            "type": "number",
            "default": "",
        },
        {
            "name": "calculations",
            "label": "Management Calculations",
            "type": "table",
            "columns": [
                {"name": "name", "label": "Result Name", "type": "text"},
                {"name": "label", "label": "Column Label", "type": "text"},
                {"name": "expression", "label": "Expression", "type": "text"},
                {"name": "unit", "label": "Unit", "type": "text"},
            ],
        },
    ]
}

NODE_REGISTRY["Pulse"] = {
    "config": [
        {
            "name": "interval",
            "label": "Pulse Interval (sec)",
            "type": "number",
            "default": 1,
        },
        {
            "name": "pulse_width",
            "label": "Pulse Width (sec)",
            "type": "number",
            "default": 0.1,
        },
        {
            "name": "plc_id",
            "label": "PLC ID",
            "type": "number",
        },
        {
            "name": "register",
            "label": "Register",
            "type": "number",
        },
    ]
}
