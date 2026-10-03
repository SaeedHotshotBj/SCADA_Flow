"""Runtime bootstrap for the Edge-precomputed Trend historian."""

_started = False


def _ensure_trend_schema():
    """Create the Trend tables used by Edge-precomputed aggregates."""
    from services.edge_ingest import ensure_edge_event_schema
    ensure_edge_event_schema()


def aggregate_once_local_time(force=False):
    """Compatibility entry point; server-side aggregate calculation is disabled."""
    _ensure_trend_schema()
    return 0


def start():
    global _started
    if _started:
        return
    _started = True
    try:
        _ensure_trend_schema()
        print("TREND RUNTIME: Edge-precomputed mode; server aggregation disabled")
    except Exception as exc:
        print("TREND RUNTIME SCHEMA ERROR:", repr(exc))


__all__ = ["start", "aggregate_once_local_time"]
