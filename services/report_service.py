# SCADA_FLOW PLC-aware report service facade.
# Keep the public API stable while using PLC-aware storage.
from services.report_plc import (
    ensure_report_tables,
    get_report_products,
    get_report_data,
)
from services.report_snapshot_runtime import save_report_snapshot

__all__ = [
    "ensure_report_tables",
    "get_report_products",
    "save_report_snapshot",
    "get_report_data",
]
