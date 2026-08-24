import threading
import time
from datetime import datetime, timedelta

from . import trend_aggregation as ta

_started = False
_lock = threading.Lock()


def _latest_raw_timestamp(conn):
    allowed = ",".join("?" for _ in ta._ALLOWED_STORAGE)
    row = conn.execute(
        f"""
        SELECT MAX(datetime(replace(Timestamp, 'T', ' '))) AS LatestTimestamp
        FROM PLC_Data
        WHERE (StorageType IS NULL OR UPPER(StorageType) IN ({allowed}))
        """,
        ta._ALLOWED_STORAGE,
    ).fetchone()
    if not row:
        return None
    return ta._parse_ts(row["LatestTimestamp"])


def _aggregate_raw_bucket_normalized(conn, start, end):
    allowed = ",".join("?" for _ in ta._ALLOWED_STORAGE)
    rows = conn.execute(
        f"""
        SELECT CompanyID,
               TagName,
               Timestamp AS ts,
               Value AS value
        FROM PLC_Data
        WHERE datetime(replace(Timestamp, 'T', ' ')) >= datetime(?)
          AND datetime(replace(Timestamp, 'T', ' ')) < datetime(?)
          AND (StorageType IS NULL OR UPPER(StorageType) IN ({allowed}))
        ORDER BY CompanyID,
                 TagName,
                 datetime(replace(Timestamp, 'T', ' ')),
                 ID
        """,
        (ta._ts(start), ta._ts(end), *_ALLOWED_STORAGE),
    ).fetchall()

    from collections import defaultdict

    grouped = defaultdict(list)
    for row in rows:
        grouped[(int(row["CompanyID"]), row["TagName"])].append(row)

    written = 0
    for (company_id, tag), group in grouped.items():
        stats = ta._aggregate_step_rows(group, start, end)
        if stats is None:
            continue
        ta._write_aggregate(
            conn,
            "TrendMinute",
            company_id,
            tag,
            start,
            end,
            stats,
        )
        written += 1

    return written


def aggregate_once_local_time():
    ta._ensure_tables()
    if not ta._try_acquire_lease():
        return 0

    conn = ta._connect()
    try:
        latest_raw = _latest_raw_timestamp(conn)
        anchor = latest_raw or datetime.now().replace(microsecond=0)

        minute_end = ta._minute_start(anchor)
        minute_start = minute_end - timedelta(minutes=1)
        written = _aggregate_raw_bucket_normalized(
            conn,
            minute_start,
            minute_end,
        )

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

        conn.commit()
        return written
    finally:
        conn.close()


def _worker():
    ta._ensure_tables()
    while True:
        try:
            written = aggregate_once_local_time()
            if written:
                print("TREND AGGREGATION: wrote", written, "minute bucket(s)")
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
        print("TREND AGGREGATION WORKER STARTED (PLC TIMESTAMP ANCHOR)")
