"""Compatibility wrapper for the single server-side EdgeTimeout worker."""

import os

from .edge_timeout_runtime import check_once as _check_once
from .edge_timeout_runtime import start_worker as _start_worker


def check_once():
    return _check_once()


def start_worker():
    """Start the EdgeTimeout worker unless the process is a diagnostic runner."""
    if os.environ.get("SCADA_SKIP_EDGE_TIMEOUT_WORKER", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }:
        return None

    return _start_worker()


__all__ = [
    "check_once",
    "start_worker",
]
