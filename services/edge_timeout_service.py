"""Compatibility wrapper for the single server-side EdgeTimeout worker."""

from .edge_timeout_runtime import check_once, start_worker


__all__ = [
    "check_once",
    "start_worker",
]
