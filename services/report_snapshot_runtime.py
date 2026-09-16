"""Compatibility facade for Flow-defined report persistence.

Production/report calculations are executed by the actual Drawflow nodes.
Persistence is implemented once in services.report_plc and consumes only the
values supplied by ReportOutput.
"""

from services.report_plc import ensure_report_tables, save_report_snapshot


def safe_flow_eval(*_args, **_kwargs):
    raise RuntimeError("Report expression evaluation must occur in a Flow calculation node")


__all__ = ["safe_flow_eval", "ensure_report_tables", "save_report_snapshot"]
