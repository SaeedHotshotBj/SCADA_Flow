# =====================================================
# SCADA_FLOW RUNTIME NODE REGISTRY
# =====================================================

from flow_engine.nodes.plc_reader import PLCReader
from flow_engine.nodes.tag_mapper import TagMapper
from flow_engine.nodes.expression_node import ExpressionNode
from flow_engine.nodes.sql_writer import SQLWriter
from flow_engine.nodes.dashboard_output import DashboardOutput
from flow_engine.nodes.machine_card import MachineCard
from flow_engine.nodes.alarm_node import AlarmNode
from flow_engine.nodes.edge_timeout import EdgeTimeout

from flow_engine.nodes.roles import Roles
from flow_engine.nodes.roles_engaged import RolesEngaged

from flow_engine.nodes.trend_reader import TrendReader
from flow_engine.nodes.trend_output import TrendOutput
from flow_engine.nodes.trend_database_reader import TrendDatabaseReader
from services.trend_aggregation import start_aggregation_worker

from flow_engine.nodes.report_output import ReportOutput
from flow_engine.nodes.date_converter import DateConverterNode
from services.edge_timeout_service import start_worker as start_edge_timeout_worker

from flow_engine.nodes.management_nodes import (
    ManagementRolesEngaged,
    ManagementInput,
    ContractRepository,
    ProductBOMRepository,
    ManagementCostCalculator,
    ManagementOutput,
    ManagementPanelOutput,
)

NODE_CLASSES = {
    "PLCReader": PLCReader,
    "TagMapper": TagMapper,
    "ExpressionNode": ExpressionNode,
    "SQLWriter": SQLWriter,
    "DashboardOutput": DashboardOutput,
    "MachineCard": MachineCard,
    "AlarmNode": AlarmNode,
    "EdgeTimeout": EdgeTimeout,
    "Roles": Roles,
    "RolesEngaged": RolesEngaged,
    "TrendReader": TrendReader,
    "TrendDatabaseReader": TrendDatabaseReader,
    "TrendOutput": TrendOutput,
    "ReportOutput": ReportOutput,
    "DateConverter": DateConverterNode,
    "ManagementRolesEngaged": ManagementRolesEngaged,
    "ManagementInput": ManagementInput,
    "ContractRepository": ContractRepository,
    "ProductBOMRepository": ProductBOMRepository,
    "ManagementCostCalculator": ManagementCostCalculator,
    "ManagementOutput": ManagementOutput,
    "ManagementPanelOutput": ManagementPanelOutput,
}

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


def get_node_class(name):
    node = NODE_CLASSES.get(name)
    if node is None:
        print("UNKNOWN NODE TYPE:", name)
        return None
    return node
