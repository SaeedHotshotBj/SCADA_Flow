"""Historical Trend query over Edge-precomputed aggregate values."""

from datetime import datetime
from zoneinfo import ZoneInfo

from services.trend_aggregation import get_resolution
from database import get_connection

TZ = ZoneInfo("Asia/Tehran")
STORAGE_BY_RESOLUTION = {
    "minute": "CALCULATED_MINUTE",
    "hour": "CALCULATED_HOUR",
    "day": "CALCULATED_DAY",
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


def get_trend_series(company_id, plc_id, tag_name, start, end):
    start = _parse_ts(start)
    end = _parse_ts(end)
    if start is None or end is None or start >= end:
        return "calculated", []

    resolution = get_resolution(start, end)
    storage_type = STORAGE_BY_RESOLUTION.get(resolution)
    if storage_type is None:
        return resolution, []

    conn = get_connection()
    try:
        rows = conn.execute(
            """
            SELECT Timestamp, Value
            FROM PLC_Data
            WHERE CompanyID=?
              AND PLC_ID=?
              AND LOWER(TagName)=LOWER(?)
              AND StorageType=?
              AND Timestamp >= ?
              AND Timestamp < ?
            ORDER BY Timestamp ASC, ID ASC
            """,
            (
                int(company_id),
                int(plc_id),
                str(tag_name),
                storage_type,
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

    values = []
    for row in rows:
        try:
            values.append(float(row["Value"]))
        except (TypeError, ValueError):
            pass

    if not values:
        return {
            "resolution": resolution,
            "min": None,
            "max": None,
            "weighted_average": None,
            "sample_count": 0,
        }

    # Values are already averages calculated on Edge. VPS only reads those
    # stored values; it does not recompute the underlying PLC samples.
    return {
        "resolution": resolution,
        "min": min(values),
        "max": max(values),
        # These are already Edge-computed bucket averages. The displayed
        # average is the arithmetic mean of the stored aggregate points.
        "weighted_average": sum(values) / len(values),
        "sample_count": len(values),
    }


__all__ = ["get_trend_series", "get_trend_stats"]
