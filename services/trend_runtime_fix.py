"""Compatibility wrapper for the canonical trend aggregation worker."""

from .trend_aggregation import aggregate_once, start_aggregation_worker


_started = False


def aggregate_once_local_time(force=False):
    """Compatibility entry point used by deployment diagnostics.

    The canonical aggregator already operates in the SCADA local timezone and
    is safe to call directly. ``force`` is kept for the old diagnostic API.
    """
    return aggregate_once()


def start():
    global _started
    if _started:
        return
    _started = True
    try:
        aggregate_once()
    except Exception as exc:
        print("TREND INITIAL AGGREGATION ERROR:", repr(exc))
    start_aggregation_worker()


__all__ = ["start", "aggregate_once_local_time"]
