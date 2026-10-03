"""Historical Trend query over Edge-precomputed aggregate values."""

from datetime import datetime
from zoneinfo import ZoneInfo

from services.trend_aggregation import get_resolution
from services.edge_ingest import ensure_edge_event_schema, get_flow_history_resolution
from database import get_connection

TZ = ZoneInfo("Asia/Tehran")
TABLE_BY_RESOLUTION = {
    "minute": "TrendMinute",
    "hour": "TrendHour",
    "day": "TrendDay",
    "month": "TrendMonth",
}


def _parse_ts(value):
    if isinstance(value, datetime):
        dt = value
    else:
        text = str(value or "").strip().replace("T", " ")
        try:
            dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except Exception:
            dt = None
            for fmt in (
                "%Y-%m-%d %H:%M:%S.%f",
                "%Y-%m-%d %H:%M:%S",
                "%Y-%m-%d %H:%M",
                "%Y/%m/%d %H:%M:%S",
                "%Y/%m/%d %H:%M",
            ):
                try:
                    dt = datetime.strptime(text, fmt)
                    break
                except ValueError:
                    pass
        if dt is None:
            return None

    if dt.tzinfo is not None:
        dt = dt.astimezone(TZ).replace(tzinfo=None)
    return dt


def _ts(dt):
    return dt.strftime("%Y-%m-%d %H:%M:%S.%f").rstrip("0").rstrip(".")


def _resolution_for_tag(company_id, plc_id, start, end, tag_name):
    flow_resolution = get_flow_history_resolution(company_id, plc_id, tag_name)
    if flow_resolution == "NONE":
        return "none"
    if flow_resolution in TABLE_BY_RESOLUTION:
        return flow_resolution
    # ALL/legacy Flow mappings keep the existing range-based Trend selection.
    return get_resolution(start, end)


def get_trend_series(company_id, plc_id, tag_name, start, end):
    start = _parse_ts(start)
    end = _parse_ts(end)
    if start is None or end is None or start >= end:
        return "calculated", []

    resolution = _resolution_for_tag(company_id, plc_id, start, end, tag_name)
    table = TABLE_BY_RESOLUTION.get(resolution)
    if table is None:
        return resolution, []

    # Trend tables are the server historian for Edge-precomputed aggregates.
    # The server never rebuilds these values from raw PLC samples.
    ensure_edge_event_schema()
    conn = get_connection()
    try:
        rows = conn.execute(
            f"""
            SELECT PeriodStart AS Timestamp,
                   WeightedAverage AS Value,
                   MinValue,
                   MaxValue,
                   WeightedAverage,
                   DurationSeconds,
                   SampleCount
            FROM {table}
            WHERE CompanyID=?
              AND PLC_ID=?
              AND LOWER(TagName)=LOWER(?)
              AND PeriodStart >= ?
              AND PeriodEnd <= ?
            ORDER BY PeriodStart ASC
            """,
            (
                int(company_id),
                int(plc_id),
                str(tag_name),
                _ts(start),
                _ts(end),
            ),
        ).fetchall()
        return resolution, rows
    finally:
        conn.close()


def get_trend_stats(company_id, plc_id, tag_name, start, end):
    resolution, rows = get_trend_series(
        company_id,
        plc_id,
        tag_name,
        start,
        end,
    )
    if not rows:
        return {
            "resolution": resolution,
            "min": None,
            "max": None,
            "weighted_average": None,
            "sample_count": 0,
        }

    minimum_values = []
    maximum_values = []
    weighted_sum = 0.0
    duration = 0.0
    sample_count = 0

    for row in rows:
        try:
            value = float(row["Value"])
        except (TypeError, ValueError):
            continue

        try:
            minimum_values.append(
                float(row["MinValue"])
                if row["MinValue"] is not None
                else value
            )
        except (TypeError, ValueError):
            minimum_values.append(value)

        try:
            maximum_values.append(
                float(row["MaxValue"])
                if row["MaxValue"] is not None
                else value
            )
        except (TypeError, ValueError):
            maximum_values.append(value)

        try:
            row_duration = float(row["DurationSeconds"] or 0)
        except (TypeError, ValueError):
            row_duration = 0.0
        if row_duration > 0:
            weighted_sum += value * row_duration
            duration += row_duration

        try:
            sample_count += int(row["SampleCount"] or 0)
        except (TypeError, ValueError):
            sample_count += 1

    if not minimum_values:
        return {
            "resolution": resolution,
            "min": None,
            "max": None,
            "weighted_average": None,
            "sample_count": 0,
        }

    if duration <= 0:
        averages = []
        for row in rows:
            try:
                averages.append(float(row["Value"]))
            except (TypeError, ValueError):
                pass
        weighted_average = sum(averages) / len(averages) if averages else None
    else:
        weighted_average = weighted_sum / duration

    return {
        "resolution": resolution,
        "min": min(minimum_values),
        "max": max(maximum_values),
        "weighted_average": weighted_average,
        "sample_count": sample_count,
    }

__all__ = ["get_trend_series", "get_trend_stats"]
