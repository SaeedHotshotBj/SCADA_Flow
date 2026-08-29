"""Compatibility wrapper for the Edge timeout worker.

Historically SCADA_FLOW had two timeout implementations. Keeping two workers
could race with each other and made outage zero insertion unreliable. The
single authoritative implementation now lives in edge_timeout_service.py.
"""

from services.edge_timeout_service import check_once, start_worker


__all__ = ["check_once", "start_worker"]
