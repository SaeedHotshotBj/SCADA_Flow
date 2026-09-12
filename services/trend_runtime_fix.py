"""Compatibility wrapper for the canonical trend aggregation worker."""

from .trend_aggregation import start_aggregation_worker


_started = False


def start():
    global _started
    if _started:
        return
    _started = True
    start_aggregation_worker()


__all__ = ["start"]
