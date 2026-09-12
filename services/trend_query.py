"""Single query layer shared by Trend nodes and Dashboard historical charts."""

from datetime import datetime
from decimal import Decimal, InvalidOperation, getcontext
from zoneinfo import ZoneInfo

from services.trend_aggregation import get_resolution
from database import get_connection

getcontext().prec = 40
TZ = ZoneInfo("Asia/Tehran")
TABLE_BY_RESOLUTION = {"minute": "TrendMinute", "hour": "TrendHour", "day": "TrendDay"}


def _parse_ts(value):
    if isinstance(value, datetime):
        dt = value
    else:
        text = str(value or "").strip().replace("T", " ")
        try:
            dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except Exception:
            dt = None
            for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y/%m/%d %H:%M:%S", "%Y/%m/%d %H:%M"):
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


def _raw_rows(conn, company_id, plc_id, tag, start, end):
    return conn.execute(
        """
        SELECT ID, Timestamp, Value
        FROM PLC_Data
        WHERE CompanyID=? AND PLC_ID=? AND LOWER(TagName)=LOWER(?)
          AND Timestamp >= ? AND Timestamp < ?
          AND (StorageType IS NULL OR UPPER(StorageType) IN ('TIME','EDGE'))
        ORDER BY Timestamp ASC, ID ASC
        """,
        (int(company_id), int(plc_id), tag, _ts(start), _ts(end)),
    ).fetchall()


def _raw_predecessor(conn, company_id, plc_id, tag, start):
    return conn.execute(
        """
        SELECT Timestamp, Value
        FROM PLC_Data
        WHERE CompanyID=? AND PLC_ID=? AND LOWER(TagName)=LOWER(?)
          AND Timestamp < ?
          AND (StorageType IS NULL OR UPPER(StorageType) IN ('TIME','EDGE'))
        ORDER BY Timestamp DESC, ID DESC
        LIMIT 1
        """,
        (int(company_id), int(plc_id), tag, _ts(start)),
    ).fetchone()


def _aggregate_raw(rows, predecessor, start, end):
    points = []
    if predecessor is not None:
        prev_ts = _parse_ts(predecessor["Timestamp"])
        try:
            prev_value = float(predecessor["Value"])
        except (TypeError, ValueError):
            prev_value = None
        if prev_ts is not None and prev_value is not None and prev_ts <= start:
            points.append((prev_ts, prev_value))

    actual = []
    for row in rows:
        dt = _parse_ts(row["Timestamp"])
        try:
            value = float(row["Value"])
        except (TypeError, ValueError):
            continue
        if dt is None:
            continue
        actual.append((dt, value))
        points.append((dt, value))

    if not points:
        return None
    points.sort(key=lambda item: item[0])

    weighted_sum = Decimal("0")
    duration = Decimal("0")
    for i, (dt, value) in enumerate(points):
        seg_start = max(dt, start)
        next_dt = points[i + 1][0] if i + 1 < len(points) else end
        seg_end = min(next_dt, end)
        seconds = (seg_end - seg_start).total_seconds()
        if seconds <= 0:
            continue
        try:
            weighted_sum += Decimal(str(value)) * Decimal(str(seconds))
            duration += Decimal(str(seconds))
        except InvalidOperation:
            continue

    if duration <= 0:
        return None
    return {
        "first": actual[0][1] if actual else points[0][1],
        "last": actual[-1][1] if actual else points[-1][1],
        "min": min(value for _, value in points),
        "max": max(value for _, value in points),
        "weighted_average": float(weighted_sum / duration),
        "duration": float(duration),
        "sample_count": len(actual),
    }


def get_trend_series(company_id, plc_id, tag_name, start, end):
    start = _parse_ts(start)
    end = _parse_ts(end)
    if start is None or end is None or start >= end:
        return "raw", []

    resolution = get_resolution(start, end)
    table = TABLE_BY_RESOLUTION[resolution]
    conn = get_connection()
    try:
        rows = conn.execute(
            f"""
            SELECT PeriodStart AS Timestamp, WeightedAverage AS Value,
                   MinValue, MaxValue, WeightedAverage, DurationSeconds, SampleCount
            FROM {table}
            WHERE CompanyID=? AND PLC_ID=? AND LOWER(TagName)=LOWER(?)
              AND PeriodStart < ? AND PeriodEnd > ?
            ORDER BY PeriodStart ASC
            """,
            (int(company_id), int(plc_id), tag_name, _ts(end), _ts(start)),
        ).fetchall()
        if rows:
            return resolution, rows

        return "raw", _raw_rows(conn, company_id, plc_id, tag_name, start, end)
    finally:
        conn.close()


def get_trend_stats(company_id, plc_id, tag_name, start, end):
    resolution, rows = get_trend_series(company_id, plc_id, tag_name, start, end)
    if not rows:
        return {"resolution": resolution, "min": None, "max": None, "weighted_average": None, "sample_count": 0}

    conn = get_connection()
    try:
        if resolution != "raw":
            minimums = [float(row["MinValue"]) for row in rows if row["MinValue"] is not None]
            maximums = [float(row["MaxValue"]) for row in rows if row["MaxValue"] is not None]
            durations = [float(row["DurationSeconds"] or 0) for row in rows]
            weighted = [float(row["WeightedAverage"] or 0) for row in rows]
            duration = sum(durations)
            weighted_average = sum(v * d for v, d in zip(weighted, durations)) / duration if duration > 0 else None
            return {
                "resolution": resolution,
                "min": min(minimums) if minimums else None,
                "max": max(maximums) if maximums else None,
                "weighted_average": weighted_average,
                "sample_count": sum(int(row["SampleCount"] or 0) for row in rows),
            }

        start_dt = _parse_ts(start)
        end_dt = _parse_ts(end)
        predecessor = _raw_predecessor(conn, company_id, plc_id, tag_name, start_dt)
        normalized_rows = _raw_rows(conn, company_id, plc_id, tag_name, start_dt, end_dt)
        stats = _aggregate_raw(normalized_rows, predecessor, start_dt, end_dt)
        if stats is None:
            return {"resolution": "raw", "min": None, "max": None, "weighted_average": None, "sample_count": 0}
        return {
            "resolution": "raw",
            "min": stats["min"],
            "max": stats["max"],
            "weighted_average": stats["weighted_average"],
            "sample_count": stats["sample_count"],
        }
    finally:
        conn.close()


__all__ = ["get_trend_series", "get_trend_stats"]
