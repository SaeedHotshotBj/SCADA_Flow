"""Compatibility export for Flow-owned report persistence.

Production/report values are calculated by executable Flow nodes and persisted
by ReportOutput. This module intentionally contains no evaluator, Flow scan,
or report-calculation fallback.
"""

from services.report_plc import ensure_report_tables, save_report_snapshot


__all__ = ["ensure_report_tables", "save_report_snapshot"]
