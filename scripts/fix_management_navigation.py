from pathlib import Path

p = Path("app.py")
t = p.read_text(encoding="utf-8")
old = '''        flow_json = get_company_flow(company_id)\n\n        if not flow_json:\n            flow_json = _read_flow_file()\n'''
new = '''        ensure_management_flow(company_id)\n        flow_json = get_company_flow(company_id)\n\n        if not flow_json:\n            flow_json = _read_flow_file()\n'''
if old not in t:
    raise SystemExit("flow.json anchor not found")
p.write_text(t.replace(old, new, 1), encoding="utf-8")
print("management navigation wired")
