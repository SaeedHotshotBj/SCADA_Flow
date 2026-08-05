# =====================================================
# Seed ~10 days of historian test data into SQLite
# Usage: python seed_test_data.py
# =====================================================

import math
import random
from datetime import datetime, timedelta

from database import get_connection, init_database


COMPANY_ID = 1
DAYS = 10
INTERVAL_MINUTES = 15


TAG_PROFILES = {
    "Voltage12": {"base": 398.0, "amplitude": 8.0, "noise": 1.5, "min": 370.0, "max": 410.0},
    "Voltage13": {"base": 399.0, "amplitude": 7.5, "noise": 1.5, "min": 370.0, "max": 410.0},
    "Voltage23": {"base": 397.0, "amplitude": 8.5, "noise": 1.5, "min": 370.0, "max": 410.0},
    "Voltage1": {"base": 230.0, "amplitude": 5.0, "noise": 1.0, "min": 210.0, "max": 245.0},
    "Voltage2": {"base": 231.0, "amplitude": 5.0, "noise": 1.0, "min": 210.0, "max": 245.0},
    "Voltage3": {"base": 229.5, "amplitude": 5.0, "noise": 1.0, "min": 210.0, "max": 245.0},
    "Current1": {"base": 42.0, "amplitude": 18.0, "noise": 2.0, "min": 5.0, "max": 85.0},
    "Current2": {"base": 40.0, "amplitude": 17.0, "noise": 2.0, "min": 5.0, "max": 85.0},
    "Current3": {"base": 41.5, "amplitude": 18.5, "noise": 2.0, "min": 5.0, "max": 85.0},
}


def _load_tag_names(cursor):

    cursor.execute(
        "SELECT TagName FROM Tags WHERE CompanyID = ? ORDER BY TagName",
        (COMPANY_ID,),
    )
    return [row[0] for row in cursor.fetchall()]


def _generate_value(tag_name, timestamp, day_index):

    profile = TAG_PROFILES.get(
        tag_name,
        {"base": 100.0, "amplitude": 10.0, "noise": 1.0, "min": 0.0, "max": 200.0},
    )

    hour = timestamp.hour + timestamp.minute / 60.0

    # Daily load curve: lower at night, peak around midday
    daily_factor = 0.55 + 0.45 * math.sin((hour - 6) * math.pi / 12)
    daily_factor = max(0.2, daily_factor)

    # Slow drift across the 10-day window
    trend = math.sin(day_index * 0.7) * 0.08

    value = profile["base"] + profile["amplitude"] * daily_factor
    value += profile["base"] * trend
    value += random.uniform(-profile["noise"], profile["noise"])

    return round(max(profile["min"], min(profile["max"], value)), 2)


def seed_historian_data(days=DAYS, interval_minutes=INTERVAL_MINUTES):

    init_database()

    conn = get_connection()
    cursor = conn.cursor()

    tag_names = _load_tag_names(cursor)
    if not tag_names:
        conn.close()
        raise RuntimeError("No tags found in database. Run init_database first.")

    end_time = datetime.now().replace(second=0, microsecond=0)
    start_time = end_time - timedelta(days=days)

    rows = []
    current = start_time
    day_index = 0
    last_day = current.date()

    while current <= end_time:
        if current.date() != last_day:
            day_index += 1
            last_day = current.date()

        timestamp = current.strftime("%Y-%m-%d %H:%M:%S")

        for tag_name in tag_names:
            rows.append(
                (
                    COMPANY_ID,
                    tag_name,
                    _generate_value(tag_name, current, day_index),
                    "TIME",
                    timestamp,
                )
            )

        current += timedelta(minutes=interval_minutes)

    cursor.executemany(
        """
        INSERT INTO PLC_Data (CompanyID, TagName, Value, StorageType, Timestamp)
        VALUES (?, ?, ?, ?, ?)
        """,
        rows,
    )

    conn.commit()

    cursor.execute("SELECT COUNT(*) FROM PLC_Data WHERE StorageType = 'TIME'")
    total_rows = cursor.fetchone()[0]

    cursor.execute(
        """
        SELECT MIN(Timestamp), MAX(Timestamp)
        FROM PLC_Data
        WHERE StorageType = 'TIME'
        """
    )
    min_ts, max_ts = cursor.fetchone()

    conn.close()

    print(f"Inserted {len(rows)} test records")
    print(f"Tags: {len(tag_names)} | Interval: {interval_minutes} min | Days: {days}")
    print(f"Time range: {min_ts} -> {max_ts}")
    print(f"Total TIME records in PLC_Data: {total_rows}")


if __name__ == "__main__":
    random.seed(42)
    seed_historian_data()
