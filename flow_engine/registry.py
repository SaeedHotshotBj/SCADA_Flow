# =====================================================
# SCADA_FLOW RUNTIME NODE REGISTRY
# =====================================================

# =====================================================
# REAL TIME NODES
# =====================================================

from flow_engine.nodes.plc_reader import PLCReader
from flow_engine.nodes.tag_mapper import TagMapper
from flow_engine.nodes.expression_node import ExpressionNode
from flow_engine.nodes.sql_writer import SQLWriter
from services.management_context import ManagementSQLWriter
from flow_engine.nodes.dashboard_output import DashboardOutput
from flow_engine.nodes.machine_card import MachineCard
from flow_engine.nodes.alarm_node import AlarmNode
from flow_engine.nodes.edge_timeout import EdgeTimeout

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
# REPORT SNAPSHOT OWNERSHIP
# =====================================================
# SQLWriter is a historian/trigger persistence node. ReportOutput owns
# ReportHistory snapshots because it is the node that contains the actual
# report product configuration. Returning no report products here prevents
# multiple SQLWriter branches from creating duplicate or empty snapshots.
# Trigger tag persistence itself remains active inside SQLWriter.

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
