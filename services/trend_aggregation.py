# =====================================================
# SCADA_FLOW TREND AGGREGATION SERVICE
# Raw PLC_Data -> TrendMinute -> TrendHour -> TrendDay
# =====================================================

import os
import sqlite3
import threading
import time
from collections import defaultdict
from datetime import datetime, timedelta

from config import DB_CONFIG

RAW_RETENTION_MINUTES = int(os.environ.get("SCADA_TREND_RAW_RETENTION_MINUTES", "5"))
MINUTE_RETENTION_HOURS = int(os.environ.get("SCADA_TREND_MINUTE_RETENTION_HOURS", "2"))
HOUR_RETENTION_DAYS = int(os.environ.get("SCADA_TREND_HOUR_RETENTION_DAYS", "2"))
DAY_RETENTION_DAYS = int(os.environ.get("SCADA_TREND_DAY_RETENTION_DAYS", "3650"))
WORKER_INTERVAL_SECONDS = 30
_ALLOWED_STORAGE = ("EDGE", "TIME")
_worker_started = False
_worker_lock = threading.Lock()


def _db_path():
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base_dir, DB_CONFIG["path"])


def _connect():
    conn = sqlite3.connect(_db_path(), timeout=30, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def _parse_ts(value):
    if value is None:
        return None
    text = str(value).strip().replace("T", " ")
    if text.endswith("Z"):
        text = text[:-1]
    try:
        return datetime.fromisoformat(text)
    except Exception:
        for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
            try:
                return datetime.strptime(text, fmt)
            except ValueError:
                pass
    return None


def _ts(dt):
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def _minute_start(dt):
    return dt.replace(second=0, microsecond=0)


def _hour_start(dt):
    return dt.replace(minute=0, second=0, microsecond=0)


def _day_start(dt):
    return dt.replace(hour=0, minute=0, second=0, microsecond=0)


def _ensure_tables():
    conn = _connect()
    try:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS TrendMinute (
            ID INTEGER PRIMARY KEY AUTOINCREMENT,
            CompanyID INTEGER NOT NULL,
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
        );
        CREATE UNIQUE INDEX IF NOT EXISTS uq_trend_minute_company_tag_period
        ON TrendMinute(CompanyID, TagName, PeriodStart);
        CREATE INDEX IF NOT EXISTS idx_trend_minute_company_tag_time
        ON TrendMinute(CompanyID, TagName, PeriodStart);

        CREATE TABLE IF NOT EXISTS TrendHour (
            ID INTEGER PRIMARY KEY AUTOINCREMENT,
            CompanyID INTEGER NOT NULL,
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
        );
        CREATE UNIQUE INDEX IF NOT EXISTS uq_trend_hour_company_tag_period
        ON TrendHour(CompanyID, TagName, PeriodStart);
        CREATE INDEX IF NOT EXISTS idx_trend_hour_company_tag_time
        ON TrendHour(CompanyID, TagName, PeriodStart);

        CREATE TABLE IF NOT EXISTS TrendDay (
            ID INTEGER PRIMARY KEY AUTOINCREMENT,
            CompanyID INTEGER NOT NULL,
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
        );
        CREATE UNIQUE INDEX IF NOT EXISTS uq_trend_day_company_tag_period
        ON TrendDay(CompanyID, TagName, PeriodStart);
        CREATE INDEX IF NOT EXISTS idx_trend_day_company_tag_time
        ON TrendDay(CompanyID, TagName, PeriodStart);
        """)
        conn.commit()
    finally:
        conn.close()


def _aggregate_step_rows(rows, start, end):
    parsed = []
    for row in rows:
        dt = _parse_ts(row["ts"])
        if dt is None:
            continue
        try:
            value = float(row["value"])
        except (TypeError, ValueError):
            continue
        parsed.append((dt, value))

    parsed.sort(key=lambda item: item[0])
    if not parsed:
        return None

    weighted_sum = 0.0
    duration = 0.0
    values = []

    for index, (dt, value) in enumerate(parsed):
        next_dt = parsed[index + 1][0] if index + 1 < len(parsed) else end
        seg_start = max(dt, start)
        seg_end = min(next_dt, end)
        seconds = (seg_end - seg_start).total_seconds()
        if seconds <= 0:
            continue
        weighted_sum += value * seconds
        duration += seconds
        values.append(value)

    if duration <= 0 or not values:
        return None

    return {
        "first": values[0],
        "last": values[-1],
        "min": min(values),
        "max": max(values),
        "weighted": weighted_sum / duration,
        "duration": duration,
        "count": len(parsed),
    }


def _write_aggregate(conn, table, company_id, tag, start, end, stats):
    conn.execute(f"""
        INSERT INTO {table}
        (CompanyID, TagName, PeriodStart, PeriodEnd,
         FirstValue, LastValue, MinValue, MaxValue,
         WeightedAverage, DurationSeconds, SampleCount)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(CompanyID, TagName, PeriodStart)
        DO UPDATE SET
            PeriodEnd=excluded.PeriodEnd,
            FirstValue=excluded.FirstValue,
            LastValue=excluded.LastValue,
            MinValue=excluded.MinValue,
            MaxValue=excluded.MaxValue,
            WeightedAverage=excluded.WeightedAverage,
            DurationSeconds=excluded.DurationSeconds,
            SampleCount=excluded.SampleCount
    """, (
        company_id, tag, _ts(start), _ts(end),
        stats["first"], stats["last"], stats["min"], stats["max"],
        stats["weighted"], stats["duration"], stats["count"],
    ))


def _aggregate_raw_bucket(conn, start, end):
    allowed = ",".join("?" for _ in _ALLOWED_STORAGE)
    rows = conn.execute(f"""
        SELECT CompanyID, TagName, Timestamp AS ts, Value AS value
        FROM PLC_Data
        WHERE Timestamp >= ?
          AND Timestamp < ?
          AND (StorageType IS NULL OR UPPER(StorageType) IN ({allowed}))
        ORDER BY CompanyID, TagName, Timestamp, ID
    """, (_ts(start), _ts(end), *_ALLOWED_STORAGE)).fetchall()

    grouped = defaultdict(list)
    for row in rows:
        grouped[(int(row["CompanyID"]), row["TagName"])].append(row)

    written = 0
    for (company_id, tag), group in grouped.items():
        stats = _aggregate_step_rows(group, start, end)
        if stats is None:
            continue
        _write_aggregate(conn, "TrendMinute", company_id, tag, start, end, stats)
        written += 1
    return written


def _aggregate_children(conn, source, target, start, end):
    rows = conn.execute(f"""
        SELECT CompanyID, TagName, PeriodStart AS ts,
               WeightedAverage AS value, DurationSeconds
        FROM {source}
        WHERE PeriodStart >= ? AND PeriodStart < ?
        ORDER BY CompanyID, TagName, PeriodStart
    """, (_ts(start), _ts(end))).fetchall()

    grouped = defaultdict(list)
    for row in rows:
        grouped[(int(row["CompanyID"]), row["TagName"])].append(row)

    written = 0
    for (company_id, tag), group in grouped.items():
        total_duration = sum(float(row["DurationSeconds"] or 0) for row in group)
        if total_duration <= 0:
            continue

        weighted_sum = sum(
            float(row["WeightedAverage"] or 0) * float(row["DurationSeconds"] or 0)
            for row in group
        )
        first = float(group[0]["value"])
        last = float(group[-1]["value"])
        minimum = min(float(row["value"]) for row in group)
        maximum = max(float(row["value"]) for row in group)

        stats = {
            "first": first,
            "last": last,
            "min": minimum,
            "max": maximum,
            "weighted": weighted_sum / total_duration,
            "duration": total_duration,
            "count": sum(int(row["SampleCount"] or 0) for row in group) if "SampleCount" in group[0].keys() else len(group),
        }
        _write_aggregate(conn, target, company_id, tag, start, end, stats)
        written += 1
    return written


def aggregate_once():
    _ensure_tables()
    conn = _connect()
    try:
        now = datetime.now().replace(microsecond=0)

        # Complete minute -> TrendMinute.
        minute_end = _minute_start(now)
        minute_start = minute_end - timedelta(minutes=1)
        _aggregate_raw_bucket(conn, minute_start, minute_end)

        # Complete hour -> TrendHour.
        hour_end = _hour_start(now)
        hour_start = hour_end - timedelta(hours=1)
        _aggregate_children(conn, "TrendMinute", "TrendHour", hour_start, hour_end)

        # Complete day -> TrendDay.
        day_end = _day_start(now)
        day_start = day_end - timedelta(days=1)
        _aggregate_children(conn, "TrendHour", "TrendDay", day_start, day_end)

        # Delete raw seconds only after they have had time to be aggregated.
        raw_cutoff = _ts(now - timedelta(minutes=RAW_RETENTION_MINUTES))
        conn.execute(
            "DELETE FROM PLC_Data WHERE Timestamp < ? AND (StorageType IS NULL OR UPPER(StorageType) IN ('EDGE','TIME'))",
            (raw_cutoff,),
        )

        # TagHistory is a runtime buffer used by PLCReader; keep a short safety window.
        history_cutoff = _ts(now - timedelta(hours=2))
        conn.execute("DELETE FROM TagHistory WHERE Timestamp < ?", (history_cutoff,))

        # Minute/hour grace windows prevent boundary holes in recent trends.
        minute_cutoff = _ts(now - timedelta(hours=MINUTE_RETENTION_HOURS))
        conn.execute("DELETE FROM TrendMinute WHERE PeriodStart < ?", (minute_cutoff,))

        hour_cutoff = _ts(now - timedelta(days=HOUR_RETENTION_DAYS))
        conn.execute("DELETE FROM TrendHour WHERE PeriodStart < ?", (hour_cutoff,))

        day_cutoff = _ts(now - timedelta(days=DAY_RETENTION_DAYS))
        conn.execute("DELETE FROM TrendDay WHERE PeriodStart < ?", (day_cutoff,))

        conn.commit()
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


def get_trend_series(company_id, tag_name, start=None, end=None):
    if start is None or end is None:
        end = datetime.now()
        start = end - timedelta(hours=2)

    resolution = get_resolution(start, end)
    table = {
        "minute": "TrendMinute",
        "hour": "TrendHour",
        "day": "TrendDay",
    }[resolution]

    conn = _connect()
    try:
        rows = conn.execute(f"""
            SELECT PeriodStart AS Timestamp,
                   WeightedAverage AS Value,
                   MinValue,
                   MaxValue,
                   WeightedAverage,
                   DurationSeconds,
                   SampleCount
            FROM {table}
            WHERE CompanyID = ?
              AND LOWER(TagName) = LOWER(?)
              AND PeriodStart < ?
              AND PeriodEnd > ?
            ORDER BY PeriodStart ASC
        """, (int(company_id), tag_name, _ts(end), _ts(start))).fetchall()

        if rows:
            return resolution, rows

        # New database / very recent fallback: read raw without writing anything.
        raw = conn.execute("""
            SELECT Timestamp, Value
            FROM PLC_Data
            WHERE CompanyID = ?
              AND LOWER(TagName) = LOWER(?)
              AND Timestamp >= ? AND Timestamp <= ?
            ORDER BY Timestamp ASC, ID ASC
        """, (int(company_id), tag_name, _ts(start), _ts(end))).fetchall()
        return "raw", raw
    finally:
        conn.close()


def get_trend_stats(company_id, tag_name, start=None, end=None):
    resolution, rows = get_trend_series(company_id, tag_name, start, end)
    if not rows:
        return {
            "resolution": resolution,
            "min": None,
            "max": None,
            "weighted_average": None,
            "sample_count": 0,
        }

    if resolution == "raw":
        parsed = [{"ts": row["Timestamp"], "value": row["Value"]} for row in rows]
        stats = _aggregate_step_rows(parsed, start or datetime.min, end or datetime.now())
        if stats is None:
            return {
                "resolution": resolution,
                "min": None,
                "max": None,
                "weighted_average": None,
                "sample_count": 0,
            }
        return {
            "resolution": resolution,
            "min": stats["min"],
            "max": stats["max"],
            "weighted_average": stats["weighted"],
            "sample_count": stats["count"],
        }

    minimum = min(float(row["MinValue"]) for row in rows if row["MinValue"] is not None)
    maximum = max(float(row["MaxValue"]) for row in rows if row["MaxValue"] is not None)
    duration = sum(float(row["DurationSeconds"] or 0) for row in rows)
    weighted_sum = sum(
        float(row["WeightedAverage"] or 0) * float(row["DurationSeconds"] or 0)
        for row in rows
    )

    return {
        "resolution": resolution,
        "min": minimum,
        "max": maximum,
        "weighted_average": weighted_sum / duration if duration else None,
        "sample_count": sum(int(row["SampleCount"] or 0) for row in rows),
    }


def _worker():
    _ensure_tables()
    while True:
        try:
            aggregate_once()
        except Exception as exc:
            print("TREND AGGREGATION ERROR:", exc)
        time.sleep(WORKER_INTERVAL_SECONDS)


def start_aggregation_worker():
    global _worker_started
    with _worker_lock:
        if _worker_started:
            return
        _worker_started = True
        threading.Thread(
            target=_worker,
            name="SCADA-Trend-Aggregator",
            daemon=True,
        ).start()
