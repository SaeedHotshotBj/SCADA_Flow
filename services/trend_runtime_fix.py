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
        SELECT MAX(Timestamp) AS LatestTimestamp
        FROM PLC_Data
        WHERE (StorageType IS NULL OR UPPER(StorageType) IN ({allowed}))
        """,
        ta._ALLOWED_STORAGE,
    ).fetchone()
    if not row:
        return None
    return ta._parse_ts(row["LatestTimestamp"])


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
        written = ta._aggregate_raw_bucket(conn, minute_start, minute_end)

        hour_end = ta._hour_start(anchor)
        hour_start = hour_end - timedelta(hours=1)
        ta._aggregate_children(conn, "TrendMinute", "TrendHour", hour_start, hour_end)

        day_end = ta._day_start(anchor)
        day_start = day_end - timedelta(days=1)
        ta._aggregate_children(conn, "TrendHour", "TrendDay", day_start, day_end)

        raw_cutoff = ta._ts(anchor - timedelta(minutes=ta.RAW_RETENTION_MINUTES))
        conn.execute(
            "DELETE FROM PLC_Data WHERE Timestamp < ? AND (StorageType IS NULL OR UPPER(StorageType) IN ('EDGE','TIME'))",
            (raw_cutoff,),
        )

        history_cutoff = ta._ts(anchor - timedelta(hours=2))
        conn.execute("DELETE FROM TagHistory WHERE Timestamp < ?", (history_cutoff,))

        minute_cutoff = ta._ts(anchor - timedelta(hours=ta.MINUTE_RETENTION_HOURS))
        conn.execute("DELETE FROM TrendMinute WHERE PeriodStart < ?", (minute_cutoff,))

        hour_cutoff = ta._ts(anchor - timedelta(days=ta.HOUR_RETENTION_DAYS))
        conn.execute("DELETE FROM TrendHour WHERE PeriodStart < ?", (hour_cutoff,))

        day_cutoff = ta._ts(anchor - timedelta(days=ta.DAY_RETENTION_DAYS))
        conn.execute("DELETE FROM TrendDay WHERE PeriodStart < ?", (day_cutoff,))

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
