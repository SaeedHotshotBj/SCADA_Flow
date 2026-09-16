from pathlib import Path

import flow_engine.nodes.report_output as report_module
from flow_engine.nodes.expression_node import ExpressionNode
from flow_engine.nodes.management_panel import ManagementPanel
from flow_engine.nodes.tag_mapper import TagMapper
from flow_runner import FlowRunner
from services.production_event_service import trigger_definitions


def node(name, config=None, outputs=None):
    return {
        "name": name,
        "data": {"config": config or {}},
        "outputs": outputs or {},
    }


def test_calculation_context_and_security():
    payload = {
        "Tags": {"Voltage": 400, "H": 30},
        "ProductionEvent": {"duration_seconds": 120},
    }
    payload = ExpressionNode({
        "expressions": [{"name": "Hour", "expression": "40-H"}],
    }).execute(payload)
    assert payload["Tags"]["Hour"] == 10.0

    payload = ManagementPanel({
        "calculations": [{
            "name": "Energy",
            "expression": "Voltage * Hour + duration_seconds",
        }],
    }).execute(payload)
    assert payload["Tags"]["Energy"] == 4120.0

    payload = ExpressionNode({
        "expressions": [{"name": "Bad", "expression": "__import__('os').system('x')"}],
    }).execute({"Tags": {}})
    assert "Bad" not in payload["Tags"]


def test_tag_mapper_plc_inference():
    mapper = TagMapper({
        "mappings": [
            {"register": 100, "name": "Batch"},
            {"register": 101, "name": "Voltage", "plc_id": 2},
        ]
    })

    payload = mapper.execute({
        "CompanyID": 7,
        "PLC_ID": 1,
        "Registers": {"100": 55, "101": 400},
    })
    assert payload["Tags"] == {"Batch": 55.0}
    assert payload["TagDefinitions"][0]["plc_id"] == 1


def test_shared_dag_executes_once_and_reaches_two_reports():
    saved = []
    original_save = report_module.save_report_snapshot

    def fake_save(company_id, tags, products, **kwargs):
        saved.append({
            "company_id": company_id,
            "tags": dict(tags),
            "products": list(products),
            **kwargs,
        })
        return len(saved)

    report_module.save_report_snapshot = fake_save
    try:
        flow = {
            "drawflow": {
                "Home": {
                    "data": {
                        "1": node(
                            "ExpressionNode",
                            {"expressions": [{"name": "Hour", "expression": "40-H"}]},
                            {"output_1": {"connections": [{"node": "3"}]}},
                        ),
                        "2": node(
                            "ExpressionNode",
                            {"expressions": [{"name": "Power2", "expression": "Power * 2"}]},
                            {"output_1": {"connections": [{"node": "3"}]}},
                        ),
                        "3": node(
                            "ManagementPanel",
                            {"calculations": [{"name": "Energy", "expression": "Voltage * Hour + Power2"}]},
                            {
                                "output_1": {
                                    "connections": [
                                        {"node": "4"},
                                        {"node": "5"},
                                    ]
                                }
                            },
                        ),
                        "4": node(
                            "ReportOutput",
                            {"products": [{"name": "Energy", "tag": "Energy", "plc_id": 1}]},
                        ),
                        "5": node(
                            "ReportOutput",
                            {"products": [{"name": "Energy", "tag": "Energy", "plc_id": 1}]},
                        ),
                        "6": node(
                            "ReportOutput",
                            {"products": [{"name": "Energy", "tag": "Energy", "plc_id": 1}]},
                        ),
                    }
                }
            }
        }

        runner = FlowRunner(flow, 7)
        result = runner.execute_production_event({
            "event_id": "evt-shared-1",
            "PLC_ID": 1,
            "tags": {"H": 30, "Voltage": 400, "Power": 25},
            "duration_seconds": 120,
            "timestamp": "2026-09-16 12:00:00",
        })

        assert result is not None
        assert len(saved) == 2, saved
        assert {row["report_node_id"] for row in saved} == {"4", "5"}
        assert all(row["tags"]["Hour"] == 10.0 for row in saved)
        assert all(row["tags"]["Power2"] == 50.0 for row in saved)
        assert all(row["tags"]["Energy"] == 4050.0 for row in saved)
    finally:
        report_module.save_report_snapshot = original_save


def test_disconnected_report_is_not_executed():
    saved = []
    original_save = report_module.save_report_snapshot

    def fake_save(company_id, tags, products, **kwargs):
        saved.append(kwargs["report_node_id"])
        return len(saved)

    report_module.save_report_snapshot = fake_save
    try:
        flow = {
            "drawflow": {
                "Home": {
                    "data": {
                        "1": node(
                            "ExpressionNode",
                            {"expressions": [{"name": "Energy", "expression": "Voltage * 2"}]},
                            {"output_1": {"connections": [{"node": "2"}]}},
                        ),
                        "2": node(
                            "ReportOutput",
                            {"products": [{"name": "Energy", "tag": "Energy", "plc_id": 1}]},
                        ),
                        "3": node(
                            "ReportOutput",
                            {"products": [{"name": "Energy", "tag": "Energy", "plc_id": 1}]},
                        ),
                    }
                }
            }
        }
        FlowRunner(flow, 7).execute_production_event({
            "event_id": "evt-disconnected-1",
            "PLC_ID": 1,
            "tags": {"Voltage": 400},
            "timestamp": "2026-09-16 12:00:00",
        })
        assert saved == ["2"], saved
    finally:
        report_module.save_report_snapshot = original_save


def test_trigger_edge_configuration():
    definitions = [
        {
            "name": "Start",
            "storage": "TRIGGER",
            "trigger_register": 118,
            "trigger_value": 1,
            "trigger_edge": "rise",
        },
        {
            "name": "Stop",
            "storage": "TRIGGER",
            "trigger_register": 119,
            "trigger_value": 0,
            "trigger_edge": "fall",
        },
    ]
    assert trigger_definitions(definitions, 118)[0][1] == "rise"
    assert trigger_definitions(definitions, 119)[0][1] == "fall"


def test_report_persistence_contains_no_calculation_engine():
    report_plc = Path("services/report_plc.py").read_text(encoding="utf-8")
    snapshot_runtime = Path("services/report_snapshot_runtime.py").read_text(encoding="utf-8")
    assert "def _safe_eval" not in report_plc
    assert "_all_management_calculations" not in report_plc
    assert "eval(" not in report_plc
    assert "def safe_flow_eval" not in snapshot_runtime
    assert "eval(" not in snapshot_runtime


def run():
    tests = [
        test_calculation_context_and_security,
        test_tag_mapper_plc_inference,
        test_shared_dag_executes_once_and_reaches_two_reports,
        test_disconnected_report_is_not_executed,
        test_trigger_edge_configuration,
        test_report_persistence_contains_no_calculation_engine,
    ]
    for test in tests:
        test()
        print(f"PASS: {test.__name__}")
    print("FLOW PRODUCTION RUNTIME SMOKE OK")


if __name__ == "__main__":
    run()
