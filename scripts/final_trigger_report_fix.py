from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]


def replace_once(path, old, new):
    text = path.read_text(encoding="utf-8")
    if old not in text:
        raise SystemExit(f"PATCH TARGET NOT FOUND: {path}: {old[:120]!r}")
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


# 1) SQLWriter is the single owner of Trigger -> ReportHistory.
sql = ROOT / "flow_engine" / "nodes" / "sql_writer.py"
replace_once(
    sql,
    '''            report_id = save_report_snapshot(\n                self.company_id,\n                tags,\n                report_products,\n                timestamp=report_timestamp,\n            )''',
    '''            report_id = save_report_snapshot(\n                self.company_id,\n                tags,\n                report_products,\n                timestamp=report_timestamp,\n                trigger_tag=trigger_events[0] if trigger_events else None,\n                trigger_register=next(\n                    (definition.get("trigger_register")\n                     for definition in definitions\n                     if isinstance(definition, dict)\n                     and str(definition.get("name", "")).strip() in trigger_events\n                     and definition.get("trigger_register") not in (None, "")),\n                    None,\n                ),\n                trigger_value=event_target,\n            )''',
)

# 2) The old Edge-trigger wrapper must enrich data only; it must not create
# another ReportHistory snapshot after SQLWriter already did it.
trigger = ROOT / "flow_engine" / "trigger_edge_report_fix.py"
text = trigger.read_text(encoding="utf-8")
pattern = re.compile(
    r"def save_edge_trigger_reports\(original_execute, writer, data\):.*?\n\ndef install\(\):",
    re.S,
)
replacement = '''def save_edge_trigger_reports(original_execute, writer, data):\n    """Run the normal SQLWriter only.\n\n    SQLWriter is the single owner of Trigger -> ReportHistory persistence.\n    This compatibility wrapper remains so EdgeTriggerEvents can still be\n    attached to the runtime payload for diagnostics, but it never creates a\n    second report snapshot.\n    """\n    trace_id = str(uuid.uuid4())\n    _trace(\n        "REPORT_SAVE_START",\n        trace_id=trace_id,\n        company_id=getattr(writer, "company_id", None),\n        input_tags=(data or {}).get("Tags", {}) if isinstance(data, dict) else {},\n        input_events=(data or {}).get("EdgeTriggerEvents", []) if isinstance(data, dict) else [],\n        mode="SQLWRITER_ONLY",\n    )\n\n    try:\n        result = original_execute(writer, data)\n    except Exception as exc:\n        _trace(\n            "SQLWRITER_EXECUTE_EXCEPTION",\n            trace_id=trace_id,\n            error=repr(exc),\n            traceback=traceback.format_exc(),\n        )\n        raise\n\n    if result is None:\n        result = data or {}\n\n    _trace(\n        "REPORT_SAVE_DELEGATED_TO_SQLWRITER",\n        trace_id=trace_id,\n        company_id=getattr(writer, "company_id", None),\n        result_keys=sorted(str(key) for key in result.keys()),\n        result_tags=result.get("Tags", {}),\n        events=result.get("EdgeTriggerEvents", []),\n        report_written=result.get("Report_Written"),\n    )\n    return result\n\n\ndef install():'''
new_text, count = pattern.subn(replacement, text, count=1)
if count != 1:
    raise SystemExit("PATCH TARGET NOT FOUND: trigger_edge_report_fix.py save_edge_trigger_reports")
trigger.write_text(new_text, encoding="utf-8")

# 3) Legacy ReportRuntime must not create a second Trigger snapshot. TIME reports
# continue to use their existing runtime path.
runtime = ROOT / "services" / "report_runtime.py"
rtext = runtime.read_text(encoding="utf-8")
old = '''            elif incoming_mode == "TRIGGER":\n                pass\n            else:\n                continue'''
new = '''            elif incoming_mode == "TRIGGER":\n                # Trigger ReportHistory persistence is owned by SQLWriter.\n                # Do not create a duplicate snapshot here.\n                continue\n            else:\n                continue'''
if old not in rtext:
    raise SystemExit("PATCH TARGET NOT FOUND: report_runtime.py trigger branch")
runtime.write_text(rtext.replace(old, new, 1), encoding="utf-8")

print("FINAL TRIGGER REPORT FIX APPLIED")
print("SQLWriter: owns Trigger -> ReportHistory")
print("trigger_edge_report_fix: enrichment/diagnostic only")
print("ReportRuntime: TIME only for report snapshots")
