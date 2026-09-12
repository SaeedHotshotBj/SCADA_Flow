# =====================================================
# SCADA_FLOW RUNTIME NODE REGISTRY
# =====================================================

from flow_engine.node_registry import NODE_REGISTRY

from flow_engine.nodes.plc_reader import PLCReader
from flow_engine.nodes.tag_mapper import TagMapper
from flow_engine.nodes.expression_node import ExpressionNode
from services.management_sql_writer import ManagementSQLWriter
from flow_engine.nodes.dashboard_output import DashboardOutput
from flow_engine.nodes.machine_card import MachineCard
from flow_engine.nodes.alarm_node import AlarmNode
from flow_engine.nodes.edge_timeout import EdgeTimeout
from flow_engine.nodes.roles import Roles
from flow_engine.nodes.roles_engaged import RolesEngaged
from flow_engine.nodes.pulse import Pulse
from flow_engine.nodes.trend_reader import TrendReader
from flow_engine.nodes.trend_output import TrendOutput
from flow_engine.nodes.trend_database_reader import TrendDatabaseReader
from flow_engine.nodes.report_output import ReportOutput
from flow_engine.nodes.date_converter import DateConverterNode

NODE_CLASSES = {
    "PLCReader": PLCReader,
    "TagMapper": TagMapper,
    "ExpressionNode": ExpressionNode,
    "SQLWriter": ManagementSQLWriter,
    "DashboardOutput": DashboardOutput,
    "MachineCard": MachineCard,
    "AlarmNode": AlarmNode,
    "EdgeTimeout": EdgeTimeout,
    "Roles": Roles,
    "RolesEngaged": RolesEngaged,
    "Pulse": Pulse,
    "TrendReader": TrendReader,
    "TrendDatabaseReader": TrendDatabaseReader,
    "TrendOutput": TrendOutput,
    "ReportOutput": ReportOutput,
    "DateConverter": DateConverterNode,
}


def get_node_class(name):
    node_class = NODE_CLASSES.get(name)
    if node_class is None:
        print("UNKNOWN NODE TYPE:", name)
    return node_class


__all__ = ["NODE_REGISTRY", "NODE_CLASSES", "get_node_class"]
