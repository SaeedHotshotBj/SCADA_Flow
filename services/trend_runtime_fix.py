import threading
import time
from collections import defaultdict
from datetime import datetime, timedelta

from . import trend_aggregation as ta

_started = False
_lock = threading.Lock()


def _raw_time_bounds(conn):
    """Return the oldest and newest raw timestamp currently available."""
    allowed = ",".join("?" for _ in ta._ALLOWED_STORAGE)
    row = conn.execute(
        f"""
        SELECT
            MIN(datetime(replace(Timestamp, 'T', ' '))) AS EarliestTimestamp,
            MAX(datetime(replace(Timestamp, 'T', ' '))) AS LatestTimestamp
        FROM PLC_Data
        WHERE (StorageType IS NULL OR UPPER(StorageType) IN ({allowed}))
        """,
        ta._ALLOWED_STORAGE,
    ).fetchone()

    if not row:
        return None, None

    earliest = ta._parse_ts(row["EarliestTimestamp"])
    latest = ta._parse_ts(row["LatestTimestamp"])
    return earliest, latest


def _aggregate_raw_bucket_normalized(conn, start, end):
    """Aggregate every company's raw data in one normalized minute bucket."""
    allowed = ",".join("?" for _ in ta._ALLOWED_STORAGE)
    rows = conn.execute(
        f"""
        SELECT CompanyID,
               PLC_ID,
               TagName,
               Timestamp AS ts,
               Value AS value
        FROM PLC_Data
        WHERE datetime(replace(Timestamp, 'T', ' ')) >= datetime(?)
          AND datetime(replace(Timestamp, 'T', ' ')) < datetime(?)
          AND (StorageType IS NULL OR UPPER(StorageType) IN ({allowed}))
        ORDER BY CompanyID,
                 PLC_ID,
                 TagName,
                 datetime(replace(Timestamp, 'T', ' ')),
                 ID
        """,
        (ta._ts(start), ta._ts(end), *ta._ALLOWED_STORAGE),
    ).fetchall()

    grouped = defaultdict(list)
    for row in rows:
        grouped[(int(row["CompanyID"]), row["PLC_ID"], row["TagName"])].append(row)

    written = 0
    for (company_id, plc_id, tag), group in grouped.items():
        stats = ta._aggregate_step_rows(group, start, end)
        if stats is None:
            continue
        ta._write_aggregate(
            conn,
            "TrendMinute",
            company_id,
            plc_id,
            tag,
            start,
            end,
            stats,
        )
        written += 1

    return written


def aggregate_once_local_time(force=False):
    """
    Aggregate ALL available raw minute buckets, for ALL companies and edges.

    CompanyID + PLC_ID + TagName are used as grouping keys. No company or Edge
    is selected, filtered, or hardcoded here.
    """
    ta._ensure_tables()
    if not force and not ta._try_acquire_lease():
        return 0

    conn = ta._connect()
    try:
        earliest_raw, latest_raw = _raw_time_bounds(conn)

        if earliest_raw is None or latest_raw is None:
            return 0

        retention_floor = latest_raw - timedelta(minutes=ta.RAW_RETENTION_MINUTES)
        scan_start = max(
            ta._minute_start(earliest_raw),
            ta._minute_start(retention_floor),
        )
        scan_end = ta._minute_start(latest_raw)

        total_written = 0
        bucket = scan_start

        while bucket < scan_end:
            bucket_end = bucket + timedelta(minutes=1)
            total_written += _aggregate_raw_bucket_normalized(
                conn,
                bucket,
                bucket_end,
            )
            bucket = bucket_end

        anchor = latest_raw

        hour_end = ta._hour_start(anchor)
        hour_start = hour_end - timedelta(hours=1)
        ta._aggregate_children(
            conn,
            "TrendMinute",
            "TrendHour",
            hour_start,
            hour_end,
        )

        day_end = ta._day_start(anchor)
        day_start = day_end - timedelta(days=1)
        ta._aggregate_children(
            conn,
            "TrendHour",
            "TrendDay",
            day_start,
            day_end,
        )

        raw_cutoff = ta._ts(
            anchor - timedelta(minutes=ta.RAW_RETENTION_MINUTES)
        )
        conn.execute(
            """
            DELETE FROM PLC_Data
            WHERE datetime(replace(Timestamp, 'T', ' ')) < datetime(?)
              AND (StorageType IS NULL OR UPPER(StorageType) IN ('EDGE','TIME'))
            """,
            (raw_cutoff,),
        )

        history_cutoff = ta._ts(anchor - timedelta(hours=2))
        conn.execute(
            """
            DELETE FROM TagHistory
            WHERE datetime(replace(Timestamp, 'T', ' ')) < datetime(?)
            """,
            (history_cutoff,),
        )

        minute_cutoff = ta._ts(
            anchor - timedelta(hours=ta.MINUTE_RETENTION_HOURS)
        )
        conn.execute(
            "DELETE FROM TrendMinute WHERE PeriodStart < ?",
            (minute_cutoff,),
        )

        hour_cutoff = ta._ts(
            anchor - timedelta(days=ta.HOUR_RETENTION_DAYS)
        )
        conn.execute(
            "DELETE FROM TrendHour WHERE PeriodStart < ?",
            (hour_cutoff,),
        )

        day_cutoff = ta._ts(
            anchor - timedelta(days=ta.DAY_RETENTION_DAYS)
        )
        conn.execute(
            "DELETE FROM TrendDay WHERE PeriodStart < ?",
            (day_cutoff,),
        )

        conn.commit()
        return total_written
    finally:
        conn.close()


def _worker():
    ta._ensure_tables()
    while True:
        try:
            written = aggregate_once_local_time()
            if written:
                print(
                    "TREND AGGREGATION: wrote",
                    written,
                    "minute bucket/tag rows across all companies",
                )
        except Exception as exc:
            print("TREND AGGREGATION ERROR:", exc)
        time.sleep(ta.WORKER_INTERVAL_SECONDS)


def start():
    global _started
    with _lock:
        if _started:
            return
        _started = True
        threading.Thread(
            target=_worker,
            name="SCADA-Trend-Aggregator-PLC-Time",
            daemon=True,
        ).start()
        print("TREND AGGREGATION WORKER STARTED (ALL COMPANIES / ALL EDGES)")
