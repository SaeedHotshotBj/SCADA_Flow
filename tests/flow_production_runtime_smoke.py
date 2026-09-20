from pathlib import Path

import flow_engine.nodes.report_output as report_module
from flow_engine.nodes.expression_node import ExpressionNode
from flow_engine.nodes.management_panel import ManagementPanel
from flow_engine.nodes.tag_mapper import TagMapper
from flow_runner import FlowRunner
from services.production_event_service import trigger_definitions
from services.edge_ingest import _ordered_ingest_items


def node(name, config=None, outputs=None):
    return {
        "name": name,
        "data": {"config": config or {}},
        "outputs": outputs or {},
    }


def test_edge_batch_preserves_trigger_groups_in_queue_order():
    items = [
        {"PLC_ID": 1, "TagName": "ContractCode", "Value": 123},
        {"PLC_ID": 1, "TagName": "ProductCode", "Value": 456},
        {"PLC_ID": 1, "TagName": "__TRIGGER_REGISTER_118", "Value": 1},
        {"PLC_ID": 1, "TagName": "ContractCode", "Value": 223},
        {"PLC_ID": 1, "TagName": "ProductCode", "Value": 556},
        {"PLC_ID": 1, "TagName": "__TRIGGER_REGISTER_118", "Value": 0},
    ]
    ordered = _ordered_ingest_items(items)
    assert ordered == items


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


def test_saved_flow_sanitizer_repairs_ids_and_legacy_nodes():
    from services.runtime_bootstrap import _sanitize_company_flow

    flow = {
        "drawflow": {
            "Home": {
                "data": {
                    "10": {
                        "id": 99,
                        "name": "PLCReader",
                        "outputs": {
                            "output_1": {
                                "connections": [{"node": "11"}]
                            }
                        },
                    },
                    "11": {
                        "id": 11,
                        "name": "ManagementOutput",
                        "inputs": {
                            "input_1": {
                                "connections": [{"node": "10"}]
                            }
                        },
                    },
                    "12": {
                        "id": 12,
                        "name": "TagMapper",
                        "inputs": {
                            "input_1": {
                                "connections": [{"node": "10"}]
                            }
                        },
                    },
                }
            }
        }
    }

    cleaned, changed = _sanitize_company_flow(flow)
    nodes = cleaned["drawflow"]["Home"]["data"]

    assert changed is True
    assert set(nodes) == {"10", "12"}
    assert nodes["10"]["id"] == 10
    assert nodes["10"]["outputs"]["output_1"]["connections"] == []
    assert nodes["12"]["inputs"]["input_1"]["connections"] == [{"node": "10"}]


def test_shared_production_trigger_creates_one_event():
    import sqlite3
    import services.production_event_service as production_module

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    original_connection = production_module.get_connection
    production_module.get_connection = lambda: conn
    try:
        definitions = [
            {"name": "B1", "storage": "TRIGGER", "trigger_register": 118, "trigger_value": 1, "trigger_edge": "rise"},
            {"name": "B2", "storage": "TRIGGER", "trigger_register": 118, "trigger_value": 1, "trigger_edge": "rise"},
            {"name": "ProductCode", "storage": "TRIGGER", "trigger_register": 118, "trigger_value": 1, "trigger_edge": "rise"},
        ]

        first = production_module.process_trigger_signal(
            7, 1, 118, 0, "2026-09-16 12:00:00", 1,
            definitions, {"B1": 10, "B2": 20, "ProductCode": 30},
        )
        second = production_module.process_trigger_signal(
            7, 1, 118, 1, "2026-09-16 12:00:01", 2,
            definitions, {"B1": 11, "B2": 21, "ProductCode": 31},
        )

        assert first == []
        assert len(second) == 1, second
        assert second[0]["edge"] == "rise"
        count = conn.execute("SELECT COUNT(*) AS Count FROM ProductionEvents").fetchone()["Count"]
        assert count == 1
    finally:
        production_module.get_connection = original_connection
        conn.close()


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


def test_converging_calculation_outputs_are_preserved():
    merged = FlowRunner._merge_production_payloads([
        {
            "Tags": {"Voltage": 400, "Hour": 10},
            "ReportCalculations": [{"name": "Energy", "tag": "Energy", "unit": "kWh"}],
        },
        {
            "Tags": {"Power": 50},
            "ReportCalculations": [{"name": "Cost", "tag": "Cost", "unit": "USD"}],
        },
    ])

    assert merged["Tags"] == {"Voltage": 400, "Hour": 10, "Power": 50}
    assert [item["tag"] for item in merged["ReportCalculations"]] == ["Energy", "Cost"]


def test_shared_trigger_register_stores_all_tags():
    import services.historian_service as historian_module

    saved = []
    original_schema = historian_module.ensure_plc_identity_schema

    historian_module.ensure_plc_identity_schema = lambda: None
    try:
        historian = historian_module.HistorianService()
        historian.trigger_memory[(7, 1, "118")] = 0

        def fake_insert(company_id, plc_id, name, value, storage_type, timestamp=None):
            saved.append((name, value, storage_type))
            return True

        historian._insert_changed = fake_insert
        definitions = [
            {"name": "B1", "storage": "TRIGGER", "trigger_register": 118, "trigger_value": 1},
            {"name": "B2", "storage": "TRIGGER", "trigger_register": 118, "trigger_value": 1},
            {"name": "ContractCode", "storage": "TRIGGER", "trigger_register": 118, "trigger_value": 1},
        ]

        written = historian.process(
            7,
            1,
            {"B1": 10, "B2": 20, "ContractCode": 30},
            definitions,
            {"118": 1},
        )

        assert written == 3, saved
        assert saved == [
            ("B1", 10, "TRIGGER"),
            ("B2", 20, "TRIGGER"),
            ("ContractCode", 30, "TRIGGER"),
        ], saved
    finally:
        historian_module.ensure_plc_identity_schema = original_schema


def test_high_volume_ingest_guardrails():
    ingest_source = Path("services/edge_ingest.py").read_text(encoding="utf-8")
    maintenance_source = Path("services/database_maintenance.py").read_text(encoding="utf-8")

    # EventID idempotency is now backed directly by the indexed PLC_Data.EventID
    # field instead of a second hot-path ledger write.
    assert "SELECT ID FROM PLC_Data WHERE EventID=? LIMIT 1" in ingest_source
    assert "INSERT OR IGNORE INTO EdgeEventLedger" not in ingest_source

    # Trigger signals are persisted only when their value changes, and
    # TRIGGER samples cannot exceed the Flow-defined interval.
    assert "def _trigger_signal_is_redundant" in ingest_source
    assert "def _trigger_sample_is_due" in ingest_source

    # High-volume operational history has explicit retention and WAL checkpointing.
    assert "TRIGGER_SIGNAL" in maintenance_source
    assert "EdgeEventLedger" in maintenance_source
    assert "wal_checkpoint" in maintenance_source


def test_report_persistence_contains_no_calculation_engine():
    report_plc = Path("services/report_plc.py").read_text(encoding="utf-8")
    snapshot_runtime = Path("services/report_snapshot_runtime.py").read_text(encoding="utf-8")
    assert "def _safe_eval" not in report_plc
    assert "_all_management_calculations" not in report_plc
    assert "eval(" not in report_plc
    assert "def safe_flow_eval" not in snapshot_runtime
    assert "eval(" not in snapshot_runtime

    assert "[(report_id, name, value) for name, value in values]" in report_plc


def run():
    tests = [
        test_edge_batch_preserves_trigger_groups_in_queue_order,
        test_calculation_context_and_security,
        test_tag_mapper_plc_inference,
        test_shared_dag_executes_once_and_reaches_two_reports,
        test_disconnected_report_is_not_executed,
        test_converging_calculation_outputs_are_preserved,
        test_shared_trigger_register_stores_all_tags,
        test_saved_flow_sanitizer_repairs_ids_and_legacy_nodes,
        test_trigger_edge_configuration,
        test_shared_production_trigger_creates_one_event,
        test_high_volume_ingest_guardrails,
        test_report_persistence_contains_no_calculation_engine,
    ]
    for test in tests:
        test()
        print(f"PASS: {test.__name__}")
    print("FLOW PRODUCTION RUNTIME SMOKE OK")


if __name__ == "__main__":
    run()
