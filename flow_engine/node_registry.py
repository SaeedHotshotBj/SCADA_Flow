# =====================================================
# SCADA_FLOW EDITOR NODE REGISTRY
# PROPERTY DEFINITIONS
# =====================================================

NODE_REGISTRY = {

    "PLCReader": {
        "config": [
            {"name": "ip", "label": "PLC IP", "type": "text"},
            {"name": "port", "label": "PLC Port", "type": "number"},
            {"name": "slave", "label": "Slave ID", "type": "number"},
            {"name": "register", "label": "Start Register", "type": "number"},
            {"name": "count", "label": "Register Count", "type": "number"}
        ]
    },

    "TagMapper": {
        "config": [
            {
                "name": "mappings",
                "label": "Tag Definitions",
                "type": "table",
                "columns": [
                    {"name": "register", "label": "Register", "type": "number"},
                    {"name": "name", "label": "Tag Name", "type": "text"},
                    {"name": "datatype", "label": "Data Type", "type": "select", "options": ["FLOAT", "INT", "BOOL"]},
                    {"name": "scale", "label": "Scale", "type": "number"},
                    {"name": "storage", "label": "Storage", "type": "select", "options": ["TIME", "TRIGGER"]},
                    {"name": "interval", "label": "Time Interval (sec)", "type": "number"},
                    {"name": "trigger_register", "label": "Trigger Register", "type": "number"},
                    {"name": "trigger_value", "label": "Trigger Value", "type": "number"}
                ]
            }
        ]
    },

    "ExpressionNode": {
        "config": [
            {
                "name": "expressions",
                "label": "Expressions",
                "type": "table",
                "columns": [
                    {"name": "name", "label": "Result Name", "type": "text"},
                    {"name": "expression", "label": "Expression", "type": "text"}
                ]
            }
        ]
    },

    "SQLWriter": {
        "config": [
            {"name": "company_id", "label": "Company ID", "type": "number", "default": ""}
        ]
    },

    "Roles": {
        "config": [
            {
                "name": "roles",
                "label": "Company Roles",
                "type": "table",
                "columns": [
                    {"name": "role", "label": "Role Name", "type": "text"},
                    {"name": "username", "label": "Username", "type": "text"},
                    {"name": "password", "label": "Password", "type": "password"}
                ]
            }
        ]
    },

    "RolesEngaged": {
        "config": [
            {
                "name": "roles",
                "label": "Allowed Roles",
                "type": "table",
                "columns": [
                    {"name": "role", "label": "Role", "type": "select", "options": []}
                ]
            }
        ]
    },

    "DashboardOutput": {
        "config": [
            {
                "name": "widgets",
                "label": "Dashboard Widgets",
                "type": "table",
                "columns": [
                    {"name": "tag", "label": "Tag", "type": "text"},
                    {"name": "title", "label": "Title", "type": "text"},
                    {"name": "unit", "label": "Unit", "type": "text"}
                ]
            }
        ]
    },

    "AlarmNode": {
        "config": [
            {
                "name": "alarms",
                "label": "Alarm Rules",
                "type": "table",
                "columns": [
                    {"name": "tag", "label": "Tag", "type": "text"},
                    {"name": "condition", "label": "Condition", "type": "select", "options": [">", "<", "=="]},
                    {"name": "limit", "label": "Limit", "type": "number"},
                    {"name": "message", "label": "Message", "type": "text"}
                ]
            }
        ]
    },

    "TrendReader": {
        "config": [
            {"name": "company_id", "label": "Company ID", "type": "number", "default": ""}
        ]
    },

    "TrendDatabaseReader": {
        "config": [
            {"name": "company_id", "label": "Company ID", "type": "number", "default": ""}
        ]
    },

    "TrendOutput": {
        "config": [
            {
                "name": "DatePicker",
                "label": "Date Type",
                "type": "select",
                "options": ["GregorianPicker", "JalaliPicker"],
                "default": "JalaliPicker"
            }
        ]
    },

    "ReportOutput": {
        "config": [
            {
                "name": "DatePicker",
                "label": "Date Type",
                "type": "select",
                "options": ["GregorianPicker", "JalaliPicker"],
                "default": "JalaliPicker"
            },
            {
                "name": "products",
                "label": "Report Tags",
                "type": "table",
                "columns": [
                    {"name": "name", "label": "Column Name", "type": "text"},
                    {"name": "tag", "label": "Tag From TagMapper", "type": "text"},
                    {"name": "unit", "label": "Unit", "type": "text"}
                ]
            }
        ]
    },

    "DateConverter": {
        "config": [
            {
                "name": "direction",
                "label": "Direction",
                "type": "select",
                "options": ["J2G", "G2J"],
                "default": "G2J"
            }
        ]
    }
}
