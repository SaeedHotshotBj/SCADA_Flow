from pathlib import Path

p = Path("flow_runner.py")
t = p.read_text(encoding="utf-8")

old1 = '''REALTIME_SKIP_NODE_TYPES = {\n    "TrendReader",\n    "TrendDatabaseReader",\n    "TrendOutput",\n}\n'''
new1 = '''REALTIME_SKIP_NODE_TYPES = {\n    "TrendReader",\n    "TrendDatabaseReader",\n    "TrendOutput",\n    "ManagementRolesEngaged",\n    "ManagementPanelOutput",\n    "ManagementInput",\n    "ContractRepository",\n    "ProductBOMRepository",\n    "ManagementCostCalculator",\n    "ManagementOutput",\n}\n'''
if old1 not in t:
    raise SystemExit("REALTIME_SKIP_NODE_TYPES anchor not found")
t = t.replace(old1, new1, 1)

old2 = '''        ignored_types = {"Roles", "RolesEngaged"}\n'''
new2 = '''        ignored_types = {\n            "Roles",\n            "RolesEngaged",\n            "ManagementRolesEngaged",\n            "ManagementPanelOutput",\n            "ManagementInput",\n            "ContractRepository",\n            "ProductBOMRepository",\n            "ManagementCostCalculator",\n            "ManagementOutput",\n        }\n'''
if old2 not in t:
    raise SystemExit("get_start_nodes anchor not found")
t = t.replace(old2, new2, 1)

p.write_text(t, encoding="utf-8")
print("FlowRunner management nodes isolated")
