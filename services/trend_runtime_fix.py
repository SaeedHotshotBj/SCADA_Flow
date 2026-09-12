"""Compatibility wrapper for the canonical trend aggregation worker."""

from .trend_aggregation import _connect, _ensure_tables, aggregate_once, start_aggregation_worker


_started = False


def _ensure_trend_schema_compat():
    """Repair legacy Trend* uniqueness so the canonical UPSERT can always match it."""
    _ensure_tables()
    conn = _connect()
    try:
        for resolution in ("minute", "hour", "day"):
            table = f"Trend{resolution.title()}"
            suffix = resolution
            canonical = f"uq_trend_{suffix}_company_plc_tag_period"
            legacy = f"uq_trend_{suffix}_company_tag_period"

            # Older deployments used a PLC-unaware unique key. Remove both
            # legacy and possibly stale canonical definitions before rebuilding
            # the exact key required by the current aggregator.
            conn.execute(f"DROP INDEX IF EXISTS {legacy}")
            conn.execute(f"DROP INDEX IF EXISTS {canonical}")

            # A legacy database may already contain duplicate aggregate rows.
            # Keep one row for each canonical identity before creating the
            # unique index; this does not remove distinct periods/tags/PLCs.
            conn.execute(
                f"""
                DELETE FROM {table}
                WHERE ID NOT IN (
                    SELECT MIN(ID)
                    FROM {table}
                    GROUP BY CompanyID, PLC_ID, TagName, PeriodStart
                )
                """
            )

            conn.execute(
                f"CREATE UNIQUE INDEX {canonical} "
                f"ON {table}(CompanyID, PLC_ID, TagName, PeriodStart)"
            )
            conn.execute(
                f"CREATE INDEX IF NOT EXISTS idx_trend_{suffix}_company_plc_tag_time "
                f"ON {table}(CompanyID, PLC_ID, TagName, PeriodStart)"
            )

        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def aggregate_once_local_time(force=False):
    """Compatibility entry point used by deployment diagnostics.

    The canonical aggregator already operates in the SCADA local timezone.
    ``force`` is retained for the existing diagnostic API.
    """
    _ensure_trend_schema_compat()
    return aggregate_once()


def start():
    global _started
    if _started:
        return
    _started = True
    try:
        _ensure_trend_schema_compat()
        aggregate_once()
    except Exception as exc:
        print("TREND INITIAL AGGREGATION ERROR:", repr(exc))
    start_aggregation_worker()


__all__ = ["start", "aggregate_once_local_time"]
