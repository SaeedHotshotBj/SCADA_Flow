"""Accurate PLC historian aggregation and trend query service.

Raw PLC_Data is the authoritative source. TrendMinute/Hour/Day are independent
materialized views with exact bucket boundaries and step-hold weighted averages.
Higher resolutions never depend on shorter-lived aggregate tables.
"""

import os
import sqlite3
import threading
import time
from collections import defaultdict
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation, getcontext
from zoneinfo import ZoneInfo

from config import DB_CONFIG

getcontext().prec = 40

# Keep enough raw TIME history to rebuild the complete 600-day TrendDay view.
RAW_RETENTION_DAYS = int(os.environ.get("SCADA_TREND_RAW_RETENTION_DAYS", "605"))
MINUTE_RETENTION_HOURS = int(os.environ.get("SCADA_TREND_MINUTE_RETENTION_HOURS", "2"))
HOUR_RETENTION_DAYS = int(os.environ.get("SCADA_TREND_HOUR_RETENTION_DAYS", "2"))
DAY_RETENTION_DAYS = int(os.environ.get("SCADA_TREND_DAY_RETENTION_DAYS", "600"))
WORKER_INTERVAL_SECONDS = max(10, int(os.environ.get("SCADA_TREND_WORKER_INTERVAL_SECONDS", "30")))
MAX_MINUTE_BUCKETS_PER_RUN = max(1, int(os.environ.get("SCADA_TREND_MINUTE_BUCKETS_PER_RUN", "240")))
MAX_HOUR_BUCKETS_PER_RUN = max(1, int(os.environ.get("SCADA_TREND_HOUR_BUCKETS_PER_RUN", "48")))
MAX_DAY_BUCKETS_PER_RUN = max(1, int(os.environ.get("SCADA_TREND_DAY_BUCKETS_PER_RUN", "10")))
LEASE_SECONDS = max(WORKER_INTERVAL_SECONDS * 2, 90)
_ALLOWED_STORAGE = ("EDGE", "TIME")
SCADA_TIMEZONE = ZoneInfo("Asia/Tehran")
_worker_started = False
_worker_lock = threading.Lock()


def _db_path():
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base_dir, DB_CONFIG["path"])


def _connect():
    conn = sqlite3.connect(_db_path(), timeout=60, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=15000")
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


def _parse_ts(value):
    if value is None:
        return None
    if isinstance(value, datetime):
        dt = value
    else:
        text = str(value).strip().replace("T", " ")
        try:
            dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except Exception:
            dt = None
            for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
                try:
                    dt = datetime.strptime(text, fmt)
                    break
                except ValueError:
                    pass
            if dt is None:
                return None
    if dt.tzinfo is not None:
        dt = dt.astimezone(SCADA_TIMEZONE).replace(tzinfo=None)
    return dt


def _ts(dt):
    return dt.strftime("%Y-%m-%d %H:%M:%S.%f").rstrip("0").rstrip(".")


def _minute_start(dt):
    return dt.replace(second=0, microsecond=0)


def _hour_start(dt):
    return dt.replace(minute=0, second=0, microsecond=0)


def _day_start(dt):
    return dt.replace(hour=0, minute=0, second=0, microsecond=0)


def _ensure_tables(conn=None):
    close = False
    if conn is None:
        conn = _connect()
        close = True
    try:
        for table in ("TrendMinute", "TrendHour", "TrendDay"):
            conn.execute(
                f"""
                CREATE TABLE IF NOT EXISTS {table} (
                    ID INTEGER PRIMARY KEY AUTOINCREMENT,
                    CompanyID INTEGER NOT NULL,
                    PLC_ID INTEGER,
                    TagName TEXT NOT NULL,
                    PeriodStart TEXT NOT NULL,
                    PeriodEnd TEXT NOT NULL,
                    FirstValue REAL,
                    LastValue REAL,
                    MinValue REAL,
                    MaxValue REAL,
                    WeightedAverage REAL,
                    DurationSeconds REAL NOT NULL DEFAULT 0,
                    SampleCount INTEGER NOT NULL DEFAULT 0
                )
                """
            )
            cols = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
            if "PLC_ID" not in cols:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN PLC_ID INTEGER")
            conn.execute(f"DROP INDEX IF EXISTS uq_trend_{table.replace('Trend', '').lower()}_company_tag_period")
            suffix = table.replace("Trend", "").lower()
            conn.execute(f"CREATE UNIQUE INDEX IF NOT EXISTS uq_trend_{suffix}_company_plc_tag_period ON {table}(CompanyID, PLC_ID, TagName, PeriodStart)")
            conn.execute(f"CREATE INDEX IF NOT EXISTS idx_trend_{suffix}_company_plc_tag_time ON {table}(CompanyID, PLC_ID, TagName, PeriodStart)")

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS TrendAggregationCursor (
                Resolution TEXT PRIMARY KEY,
                NextPeriodStart TEXT NOT NULL
            )
            """
        )
        conn.execute("CREATE TABLE IF NOT EXISTS TrendAggregationLock (LockID INTEGER PRIMARY KEY CHECK (LockID=1), LeaseUntil REAL NOT NULL)")
        if close:
            conn.commit()
    finally:
        if close:
            conn.close()


def _try_acquire_lease():
    now = time.time()
    lease_until = now + LEASE_SECONDS
    conn = _connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT LeaseUntil FROM TrendAggregationLock WHERE LockID=1").fetchone()
        if row is not None and float(row["LeaseUntil"]) > now:
            conn.rollback()
            return False
        conn.execute(
            "INSERT INTO TrendAggregationLock(LockID,LeaseUntil) VALUES(1,?) ON CONFLICT(LockID) DO UPDATE SET LeaseUntil=excluded.LeaseUntil",
            (lease_until,),
        )
        conn.commit()
        return True
    except Exception:
        conn.rollback()
        return False
    finally:
        conn.close()


def _get_anchor(conn):
    allowed = ",".join("?" for _ in _ALLOWED_STORAGE)
    row = conn.execute(
        f"""
        SELECT MIN(Timestamp) AS Oldest, MAX(Timestamp) AS Newest
        FROM PLC_Data
        WHERE (StorageType IS NULL OR UPPER(StorageType) IN ({allowed}))
        """,
        _ALLOWED_STORAGE,
    ).fetchone()
    oldest = _parse_ts(row["Oldest"]) if row and row["Oldest"] else None
    newest = _parse_ts(row["Newest"]) if row and row["Newest"] else None
    return oldest, newest


def _cursor_start(conn, resolution, earliest, latest, retention_start):
    row = conn.execute("SELECT NextPeriodStart FROM TrendAggregationCursor WHERE Resolution=?", (resolution,)).fetchone()
    if row:
        return _parse_ts(row["NextPeriodStart"])
    start = max(_period_start(earliest, resolution), _period_start(retention_start, resolution))
    conn.execute(
        "INSERT INTO TrendAggregationCursor(Resolution,NextPeriodStart) VALUES(?,?)",
        (resolution, _ts(start)),
    )
    return start


def _period_start(dt, resolution):
    if resolution == "minute":
        return _minute_start(dt)
    if resolution == "hour":
        return _hour_start(dt)
    return _day_start(dt)


def _period_end(start, resolution):
    if resolution == "minute":
        return start + timedelta(minutes=1)
    if resolution == "hour":
        return start + timedelta(hours=1)
    return start + timedelta(days=1)


def _initialize_step_state(conn, start):
    allowed = ",".join("?" for _ in _ALLOWED_STORAGE)
    rows = conn.execute(
        f"""
        SELECT CompanyID, PLC_ID, TagName, Timestamp, Value
        FROM (
            SELECT CompanyID, PLC_ID, TagName, Timestamp, Value,
                   ROW_NUMBER() OVER (
                       PARTITION BY CompanyID, PLC_ID, TagName
                       ORDER BY Timestamp DESC, ID DESC
                   ) AS rn
            FROM PLC_Data
            WHERE Timestamp < ?
              AND (StorageType IS NULL OR UPPER(StorageType) IN ({allowed}))
        )
        WHERE rn=1
        """,
        (_ts(start), *_ALLOWED_STORAGE),
    ).fetchall()
    state = {}
    for row in rows:
        dt = _parse_ts(row["Timestamp"])
        if dt is None:
            continue
        try:
            value = float(row["Value"])
        except (TypeError, ValueError):
            continue
        state[(int(row["CompanyID"]), row["PLC_ID"], row["TagName"])] = (dt, value)
    return state


def _fetch_bucket_rows(conn, start, end):
    allowed = ",".join("?" for _ in _ALLOWED_STORAGE)
    return conn.execute(
        f"""
        SELECT ID, CompanyID, PLC_ID, TagName, Timestamp, Value
        FROM PLC_Data
        WHERE Timestamp >= ? AND Timestamp < ?
          AND (StorageType IS NULL OR UPPER(StorageType) IN ({allowed}))
        ORDER BY CompanyID, PLC_ID, TagName, Timestamp, ID
        """,
        (_ts(start), _ts(end), *_ALLOWED_STORAGE),
    ).fetchall()


def _aggregate_group(rows, previous, start, end):
    points = []
    if previous is not None and previous[0] <= start:
        points.append((previous[0], previous[1]))
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
        return None, previous
    points.sort(key=lambda item: item[0])

    first_value = actual[0][1] if actual else points[0][1]
    last_value = actual[-1][1] if actual else points[-1][1]
    minimum = min(value for _, value in points)
    maximum = max(value for _, value in points)

    weighted_sum = Decimal("0")
    duration = Decimal("0")
    for index, (dt, value) in enumerate(points):
        seg_start = max(dt, start)
        next_dt = points[index + 1][0] if index + 1 < len(points) else end
        seg_end = min(next_dt, end)
        seconds = (seg_end - seg_start).total_seconds()
        if seconds <= 0:
            continue
        try:
            dec_value = Decimal(str(value))
        except InvalidOperation:
            continue
        dec_seconds = Decimal(str(seconds))
        weighted_sum += dec_value * dec_seconds
        duration += dec_seconds

    if duration <= 0:
        return None, (actual[-1][0], actual[-1][1]) if actual else previous

    weighted = weighted_sum / duration
    stats = {
        "first": first_value,
        "last": last_value,
        "min": minimum,
        "max": maximum,
        "weighted": float(weighted),
        "duration": float(duration),
        "count": len(actual),
    }
    next_state = (actual[-1][0], actual[-1][1]) if actual else previous
    return stats, next_state


def _write_aggregate(conn, table, company_id, plc_id, tag, start, end, stats):
    conn.execute(
        f"""
        INSERT INTO {table}
        (CompanyID, PLC_ID, TagName, PeriodStart, PeriodEnd,
         FirstValue, LastValue, MinValue, MaxValue, WeightedAverage,
         DurationSeconds, SampleCount)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(CompanyID, PLC_ID, TagName, PeriodStart) DO UPDATE SET
            PeriodEnd=excluded.PeriodEnd,
            FirstValue=excluded.FirstValue,
            LastValue=excluded.LastValue,
            MinValue=excluded.MinValue,
            MaxValue=excluded.MaxValue,
            WeightedAverage=excluded.WeightedAverage,
            DurationSeconds=excluded.DurationSeconds,
            SampleCount=excluded.SampleCount
        """,
        (
            int(company_id), plc_id, tag, _ts(start), _ts(end),
            stats["first"], stats["last"], stats["min"], stats["max"],
            stats["weighted"], stats["duration"], stats["count"],
        ),
    )


def _aggregate_resolution(conn, resolution, next_start, limit, completed_end):
    state = _initialize_step_state(conn, next_start)
    written = 0
    bucket = next_start
    while bucket < completed_end and written < limit:
        bucket_end = min(_period_end(bucket, resolution), completed_end)
        rows = _fetch_bucket_rows(conn, bucket, bucket_end)
        grouped = defaultdict(list)
        for row in rows:
            grouped[(int(row["CompanyID"]), row["PLC_ID"], row["TagName"])].append(row)

        keys = set(grouped) | set(state)
        for key in list(keys):
            company_id, plc_id, tag = key
            stats, next_state = _aggregate_group(grouped.get(key, []), state.get(key), bucket, bucket_end)
            if stats is not None:
                _write_aggregate(conn, f"Trend{resolution.title()}", company_id, plc_id, tag, bucket, bucket_end, stats)
                written += 1
            if next_state is not None:
                state[key] = next_state

        bucket = bucket_end

    conn.execute(
        "INSERT INTO TrendAggregationCursor(Resolution,NextPeriodStart) VALUES(?,?) ON CONFLICT(Resolution) DO UPDATE SET NextPeriodStart=excluded.NextPeriodStart",
        (resolution, _ts(bucket)),
    )
    return written, bucket


def _cleanup(conn, anchor):
    raw_cutoff = anchor - timedelta(days=RAW_RETENTION_DAYS)
    conn.execute(
        "DELETE FROM PLC_Data WHERE Timestamp < ? AND (StorageType IS NULL OR UPPER(StorageType) IN ('EDGE','TIME'))",
        (_ts(raw_cutoff),),
    )
    conn.execute("DELETE FROM TagHistory WHERE Timestamp < ?", (_ts(anchor - timedelta(hours=2)),))
    conn.execute("DELETE FROM TrendMinute WHERE PeriodStart < ?", (_ts(anchor - timedelta(hours=MINUTE_RETENTION_HOURS)),))
    conn.execute("DELETE FROM TrendHour WHERE PeriodStart < ?", (_ts(anchor - timedelta(days=HOUR_RETENTION_DAYS)),))
    conn.execute("DELETE FROM TrendDay WHERE PeriodStart < ?", (_ts(anchor - timedelta(days=DAY_RETENTION_DAYS)),))


def aggregate_once():
    _ensure_tables()
    if not _try_acquire_lease():
        return 0
    conn = _connect()
    try:
        oldest, newest = _get_anchor(conn)
        if oldest is None or newest is None:
            return 0
        now = datetime.now(SCADA_TIMEZONE).replace(tzinfo=None, microsecond=0)
        anchor = max(newest, now)
        minute_end = _minute_start(anchor)
        hour_end = _hour_start(anchor)
        day_end = _day_start(anchor)

        total = 0
        minute_start = max(oldest, anchor - timedelta(hours=MINUTE_RETENTION_HOURS))
        hour_start = max(oldest, anchor - timedelta(days=HOUR_RETENTION_DAYS))
        day_start = max(oldest, anchor - timedelta(days=DAY_RETENTION_DAYS))

        for resolution, retention_start, completed_end, limit in (
            ("minute", minute_start, minute_end, MAX_MINUTE_BUCKETS_PER_RUN),
            ("hour", hour_start, hour_end, MAX_HOUR_BUCKETS_PER_RUN),
            ("day", day_start, day_end, MAX_DAY_BUCKETS_PER_RUN),
        ):
            cursor = conn.execute("SELECT NextPeriodStart FROM TrendAggregationCursor WHERE Resolution=?", (resolution,)).fetchone()
            if cursor is None:
                start = max(_period_start(oldest, resolution), _period_start(retention_start, resolution))
                conn.execute("INSERT INTO TrendAggregationCursor(Resolution,NextPeriodStart) VALUES(?,?)", (resolution, _ts(start)))
            else:
                start = _parse_ts(cursor["NextPeriodStart"]) or _period_start(retention_start, resolution)
                if start < _period_start(retention_start, resolution):
                    start = _period_start(retention_start, resolution)
                    conn.execute("UPDATE TrendAggregationCursor SET NextPeriodStart=? WHERE Resolution=?", (_ts(start), resolution))

            if start < completed_end:
                written, _ = _aggregate_resolution(conn, resolution, start, limit, completed_end)
                total += written

        _cleanup(conn, anchor)
        conn.commit()
        return total
    finally:
        conn.close()


def get_resolution(start, end):
    if start is None or end is None:
        return "minute"
    seconds = max(0.0, (end - start).total_seconds())
    if seconds <= 2 * 3600:
        return "minute"
    if seconds <= 2 * 86400:
        return "hour"
    return "day"


def get_trend_series(company_id, plc_id, tag_name, start=None, end=None):
    if start is None or end is None:
        end = datetime.now(SCADA_TIMEZONE).replace(tzinfo=None)
        start = end - timedelta(hours=2)
    start = _parse_ts(start)
    end = _parse_ts(end)
    if start is None or end is None or start >= end:
        return "raw", []

    resolution = get_resolution(start, end)
    table = {"minute": "TrendMinute", "hour": "TrendHour", "day": "TrendDay"}[resolution]
    conn = _connect()
    try:
        rows = conn.execute(
            f"""
            SELECT PeriodStart AS Timestamp, WeightedAverage AS Value,
                   MinValue, MaxValue, WeightedAverage, DurationSeconds, SampleCount
            FROM {table}
            WHERE CompanyID=? AND PLC_ID=? AND LOWER(TagName)=LOWER(?)
              AND PeriodStart < ? AND PeriodEnd > ?
            ORDER BY PeriodStart
            """,
            (int(company_id), int(plc_id), tag_name, _ts(end), _ts(start)),
        ).fetchall()
        expected = int((end - start).total_seconds() // {"minute": 60, "hour": 3600, "day": 86400}[resolution])
        if rows and len(rows) >= max(1, min(expected, 1)):
            return resolution, rows

        raw = conn.execute(
            """
            SELECT Timestamp, Value
            FROM PLC_Data
            WHERE CompanyID=? AND PLC_ID=? AND LOWER(TagName)=LOWER(?)
              AND Timestamp>=? AND Timestamp<=?
            ORDER BY Timestamp, ID
            """,
            (int(company_id), int(plc_id), tag_name, _ts(start), _ts(end)),
        ).fetchall()
        return "raw", raw
    finally:
        conn.close()


def get_trend_stats(company_id, plc_id, tag_name, start=None, end=None):
    resolution, rows = get_trend_series(company_id, plc_id, tag_name, start, end)
    if not rows:
        return {"resolution": resolution, "min": None, "max": None, "weighted_average": None, "sample_count": 0}
    if resolution == "raw":
        start_dt = _parse_ts(start) if start is not None else None
        end_dt = _parse_ts(end) if end is not None else datetime.now(SCADA_TIMEZONE).replace(tzinfo=None)
        parsed_rows = []
        for row in rows:
            dt = _parse_ts(row["Timestamp"])
            try:
                value = float(row["Value"])
            except (TypeError, ValueError):
                continue
            if dt is not None:
                parsed_rows.append((dt, value))
        if not parsed_rows:
            return {"resolution": resolution, "min": None, "max": None, "weighted_average": None, "sample_count": 0}
        stats, _ = _aggregate_group([], parsed_rows[-1], start_dt or parsed_rows[0][0], end_dt)
        if stats is None:
            stats = {"min": min(v for _, v in parsed_rows), "max": max(v for _, v in parsed_rows), "weighted": float(sum(v for _, v in parsed_rows) / len(parsed_rows)), "count": len(parsed_rows)}
        return {"resolution": resolution, "min": stats.get("min"), "max": stats.get("max"), "weighted_average": stats.get("weighted"), "sample_count": len(parsed_rows)}

    minimum = min(float(row["MinValue"]) for row in rows if row["MinValue"] is not None)
    maximum = max(float(row["MaxValue"]) for row in rows if row["MaxValue"] is not None)
    duration = sum(float(row["DurationSeconds"] or 0) for row in rows)
    weighted_sum = sum(float(row["WeightedAverage"] or 0) * float(row["DurationSeconds"] or 0) for row in rows)
    return {
        "resolution": resolution,
        "min": minimum,
        "max": maximum,
        "weighted_average": weighted_sum / duration if duration > 0 else None,
        "sample_count": sum(int(row["SampleCount"] or 0) for row in rows),
    }


def start_aggregation_worker():
    global _worker_started
    with _worker_lock:
        if _worker_started:
            return
        _worker_started = True

    def worker():
        while True:
            try:
                written = aggregate_once()
                if written:
                    print("TREND AGGREGATION: wrote", written, "aggregate bucket/tag rows")
            except Exception as exc:
                print("TREND AGGREGATION ERROR:", repr(exc))
            time.sleep(WORKER_INTERVAL_SECONDS)

    threading.Thread(target=worker, name="SCADA-Trend-Aggregation", daemon=True).start()


__all__ = [
    "aggregate_once",
    "get_resolution",
    "get_trend_series",
    "get_trend_stats",
    "start_aggregation_worker",
]
