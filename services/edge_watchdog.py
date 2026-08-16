# =====================================================
# SCADA_FLOW EDGE WATCHDOG
# =====================================================
#
# This service is intentionally isolated from the Trend
# reader/query/output code.
#
# It watches the existing EDGE records written by /api/data.
# If an EDGE tag has not received a fresh sample for more than
# two seconds, it writes one zero value per second to PLC_Data.
# When EDGE data resumes, the real value is written normally by
# the existing /api/data route.
# =====================================================

import threading
import time
from datetime import datetime

from database import get_connection


EDGE_OFFLINE_TIMEOUT = 2.0
WATCHDOG_INTERVAL = 1.0

_started = False
_start_lock = threading.Lock()


def _parse_timestamp(value):
    if value is None:
        return None

    if isinstance(value, datetime):
        return value.replace(tzinfo=None)

    text = str(value).strip().replace("T", " ")

    # Remove a trailing Z when present.
    if text.endswith("Z"):
        text = text[:-1].strip()

    # Remove timezone suffix for the existing local-time historian
    # format. The current SCADA_FLOW database stores local timestamps.
    if len(text) >= 6 and (text[-6] in "+-") and text[-3] == ":":
        text = text[:-6].strip()

    for fmt in (
        "%Y-%m-%d %H:%M:%S.%f",
        "%Y-%m-%d %H:%M:%S",
    ):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            pass

    return None


def _same_second(a, b):
    pa = _parse_timestamp(a)
    pb = _parse_timestamp(b)

    if pa is None or pb is None:
        return False

    return pa.replace(microsecond=0) == pb.replace(microsecond=0)


def _watch_once():
    conn = None
    cursor = None

    try:
        conn = get_connection()
        cursor = conn.cursor()

        # The existing Edge receiver stores every received sample
        # with StorageType='EDGE'. Find the newest Edge sample for
        # every company/tag without touching Trend queries.
        cursor.execute(
            """
            SELECT
                CompanyID,
                TagName,
                MAX(Timestamp) AS LastEdgeTimestamp
            FROM PLC_Data
            WHERE StorageType = 'EDGE'
            GROUP BY CompanyID, TagName
            """
        )

        edge_rows = cursor.fetchall()
        now = datetime.now()
        now_text = now.strftime("%Y-%m-%d %H:%M:%S")

        for row in edge_rows:
            company_id = row["CompanyID"]
            tag_name = row["TagName"]
            last_edge = _parse_timestamp(row["LastEdgeTimestamp"])

            if not tag_name or last_edge is None:
                continue

            age = (now - last_edge).total_seconds()

            if age <= EDGE_OFFLINE_TIMEOUT:
                continue

            # Do not create more than one offline point for the
            # same second. If the latest stored row is a real EDGE
            # sample, this inserts the first zero. If it is already
            # an offline zero from this second, nothing is inserted.
            cursor.execute(
                """
                SELECT
                    StorageType,
                    Timestamp
                FROM PLC_Data
                WHERE CompanyID = ?
                  AND TagName = ?
                ORDER BY Timestamp DESC, ID DESC
                LIMIT 1
                """,
                (company_id, tag_name)
            )

            latest = cursor.fetchone()

            if latest is not None:
                latest_type = str(
                    latest["StorageType"] or ""
                ).upper()

                latest_timestamp = latest["Timestamp"]

                if (
                    latest_type == "EDGE_OFFLINE"
                    and _same_second(latest_timestamp, now_text)
                ):
                    continue

            cursor.execute(
                """
                INSERT INTO PLC_Data
                (
                    CompanyID,
                    TagName,
                    Value,
                    StorageType,
                    Timestamp
                )
                VALUES
                (?, ?, 0, 'EDGE_OFFLINE', ?)
                """,
                (
                    company_id,
                    tag_name,
                    now_text
                )
            )

            print(
                "EDGE WATCHDOG: OFFLINE",
                "Company:", company_id,
                "Tag:", tag_name,
                "Value: 0",
                "TIME:", now_text
            )

        conn.commit()

    except Exception as exc:
        if conn is not None:
            try:
                conn.rollback()
            except Exception:
                pass

        print("EDGE WATCHDOG ERROR:", exc)

    finally:
        if cursor is not None:
            try:
                cursor.close()
            except Exception:
                pass

        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass


def _watch_loop():
    # Give Flask/database initialization time to complete.
    time.sleep(2)

    while True:
        _watch_once()
        time.sleep(WATCHDOG_INTERVAL)


def start_edge_watchdog():
    global _started

    with _start_lock:
        if _started:
            return

        _started = True

        thread = threading.Thread(
            target=_watch_loop,
            name="SCADA_FLOW_EDGE_WATCHDOG",
            daemon=True
        )

        thread.start()

        print(
            "EDGE WATCHDOG STARTED - TIMEOUT:",
            EDGE_OFFLINE_TIMEOUT,
            "SECONDS"
        )
