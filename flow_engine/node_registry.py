# SCADA_FLOW EDITOR NODE REGISTRY WRAPPER
# Preserve the existing registry and extend it with the management node.

from flow_engine import node_registry_legacy as _legacy

NODE_REGISTRY = dict(_legacy.NODE_REGISTRY)
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
