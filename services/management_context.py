"""Backward-compatible management context helpers.

Runtime behavior now lives in services.management_sql_writer.ManagementSQLWriter.
"""


def set_context(company_id, registers):
    return None


def clear_context():
    return None


def current_context():
    return {}


from services.management_sql_writer import ManagementSQLWriter

__all__ = ["ManagementSQLWriter", "set_context", "clear_context", "current_context"]
