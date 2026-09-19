"""Compatibility alias for the Flow SQLWriter node.

Management/report behavior is implemented by actual Flow nodes. No implicit
context or report backfill is performed by this service.
"""

from flow_engine.nodes.sql_writer import SQLWriter


class ManagementSQLWriter(SQLWriter):
    pass


__all__ = ["ManagementSQLWriter"]
