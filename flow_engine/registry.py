# =====================================================
# SCADA_FLOW RUNTIME NODE REGISTRY
# =====================================================

# =====================================================
# REAL TIME NODES
# =====================================================

from datetime import datetime

from flow_engine.nodes.plc_reader import PLCReader
from flow_engine.nodes.tag_mapper import TagMapper
from flow_engine.nodes.expression_node import ExpressionNode
from flow_engine.nodes.sql_writer import SQLWriter
from services.management_context import ManagementSQLWriter
from flow_engine.nodes.dashboard_output import DashboardOutput
from flow_engine.nodes.machine_card import MachineCard
from flow_engine.nodes.alarm_node import AlarmNode
from flow_engine.nodes.edge_timeout import EdgeTimeout
from flow_engine.nodes.pulse import Pulse

# =====================================================
# ROLE NODES
# =====================================================

from flow_engine.nodes.roles import Roles
from flow_engine.nodes.roles_engaged import RolesEngaged

# =====================================================
# TREND NODES
# =====================================================

from flow_engine.nodes.trend_reader import TrendReader
from flow_engine.nodes.trend_output import TrendOutput
from flow_engine.nodes.trend_database_reader import TrendDatabaseReader
from services.trend_aggregation import start_aggregation_worker

# =====================================================
# REPORT NODES
# =====================================================

from flow_engine.nodes.report_output import ReportOutput

# =====================================================
# DATE NODES
# =====================================================

from flow_engine.nodes.date_converter import DateConverterNode
from services.edge_timeout_service import start_worker as start_edge_timeout_worker


# =====================================================
# NODE CLASS MAP
# =====================================================

NODE_CLASSES = {

    # REAL TIME
    "PLCReader":
        PLCReader,

    "TagMapper":
        TagMapper,

    "ExpressionNode":
        ExpressionNode,

    "SQLWriter":
        ManagementSQLWriter,

    "DashboardOutput":
        DashboardOutput,

    "MachineCard":
        MachineCard,

    "AlarmNode":
        AlarmNode,

    "EdgeTimeout":
        EdgeTimeout,

    "Pulse":
        Pulse,

    # ROLES
    "Roles":
        Roles,

    "RolesEngaged":
        RolesEngaged,

    # TREND
    "TrendReader":
        TrendReader,

    "TrendDatabaseReader":
        TrendDatabaseReader,

    "TrendOutput":
        TrendOutput,

    # REPORT
    "ReportOutput":
        ReportOutput,

    # DATE
    "DateConverter":
        DateConverterNode
}


# =====================================================
# EDGE TRIGGER SOURCE BRIDGE
# =====================================================
#
# The Edge gateway persists trigger-fired tags in PLC_Data with
# StorageType=EDGE. ReportOutput must therefore build its upstream
# EdgeTriggerEvents from that table. The historical implementation queried
# TagHistory, which is not the authoritative Edge trigger stream and can be
# empty even while PLC_Data contains the correct EDGE rows.
#
# We patch the trigger module's lookup function here, without changing the
# Drawflow topology and without creating a second trigger state machine.

try:
    import flow_engine.trigger_edge_report_fix as _trigger_fix
    from database import get_connection as _trigger_get_connection

    def _latest_trigger_rows_from_plc_data(company_id, definitions):
        groups = {}

        for definition in definitions or []:
            if not isinstance(definition, dict):
                continue
            if str(definition.get("storage", "")).strip().upper() != "TRIGGER":
                continue

            name = str(definition.get("name", "")).strip()
            if not name:
                continue

            register = definition.get("trigger_register")
            if register in (None, ""):
                continue

            groups.setdefault(str(register), []).append(name)

        _trigger_fix._trace(
            "TRIGGER_PLC_DATA_QUERY_START",
            company_id=company_id,
            groups=groups,
        )

        if not groups:
            _trigger_fix._trace(
                "TRIGGER_PLC_DATA_QUERY_NO_GROUPS",
                company_id=company_id,
            )
            return {}

        conn = _trigger_get_connection()
        try:
            result = {}

            for register, names in groups.items():
                rows = {}
                for name in names:
                    row = conn.execute(
                        """
                        SELECT ID, TagName, Value, Timestamp
                        FROM PLC_Data
                        WHERE CompanyID = ?
                          AND UPPER(COALESCE(StorageType, '')) = 'EDGE'
                          AND LOWER(TagName) = LOWER(?)
                        ORDER BY ID DESC
                        LIMIT 1
                        """,
                        (int(company_id), name),
                    ).fetchone()

                    if row is not None:
                        rows[name] = row
                        _trigger_fix._trace(
                            "TRIGGER_PLC_DATA_ROW",
                            company_id=company_id,
                            register=register,
                            tag=name,
                            id=row["ID"],
                            value=row["Value"],
                            timestamp=row["Timestamp"],
                            age_seconds=_trigger_fix._timestamp_age(row["Timestamp"]),
                        )
                    else:
                        _trigger_fix._trace(
                            "TRIGGER_PLC_DATA_MISSING",
                            company_id=company_id,
                            register=register,
                            tag=name,
                        )

                if rows:
                    result[register] = rows

            _trigger_fix._trace(
                "TRIGGER_PLC_DATA_QUERY_END",
                company_id=company_id,
                result_summary={
                    register: {
                        name: {
                            "id": int(row["ID"]),
                            "value": row["Value"],
                            "timestamp": str(row["Timestamp"] or ""),
                        }
                        for name, row in rows.items()
                    }
                    for register, rows in result.items()
                },
            )

            return result
        finally:
            conn.close()

    _trigger_fix._latest_trigger_rows = _latest_trigger_rows_from_plc_data
    _trigger_fix._TRACE_VERSION = "2026-09-01-v3-PLC-DATA"
    print("EDGE TRIGGER SOURCE PATCH: PLC_Data/EDGE")

except Exception as exc:
    print("EDGE TRIGGER SOURCE PATCH ERROR:", repr(exc))


# =====================================================
# REPORT SNAPSHOT OWNERSHIP
# =====================================================
# SQLWriter remains a historian/trigger persistence node. ReportOutput owns
# ReportHistory snapshots because it is the node that contains the actual
# report product configuration. Trigger tag persistence itself remains active
# inside SQLWriter.

def _disable_sqlwriter_report_snapshots():
    return []


ManagementSQLWriter._get_report_products = (
    lambda self: _disable_sqlwriter_report_snapshots()
)


# =====================================================
# START BACKGROUND WORKERS
# =====================================================

try:
    start_aggregation_worker()
    print("TREND AGGREGATION WORKER STARTED FROM REGISTRY")
except Exception as exc:
    print("TREND AGGREGATION START ERROR:", exc)

try:
    start_edge_timeout_worker()
    print("EDGE TIMEOUT WORKER STARTED FROM REGISTRY")
except Exception as exc:
    print("EDGE TIMEOUT START ERROR:", exc)


# =====================================================
# GET NODE CLASS
# =====================================================

def get_node_class(name):

    node = NODE_CLASSES.get(name)

    if node is None:

        print(
            "UNKNOWN NODE TYPE:",
            name
        )

        return None

    return node
