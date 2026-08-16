# =====================================================
# SCADA_FLOW TREND QUERY NODE
# =====================================================

from datetime import datetime, timedelta

from database import get_connection, get_trend_data, row_value


class TrendQuery:

    def __init__(self, config):
        self.config = config or {}
        self._historian_integrity_ready = False

    # =================================================
    # HISTORIAN INTEGRITY
    # =================================================

    def _ensure_historian_integrity(self):
        """
        Keep PLC_Data safe for one historian value per second.

        Existing data is normalized to second precision and duplicate rows
        for the same Company/Tag/second are collapsed. A BEFORE INSERT
        trigger then prevents future duplicate samples without requiring
        changes to the Edge/API sender.
        """

        if self._historian_integrity_ready:
            return

        conn = None
        cursor = None

        try:
            conn = get_connection()
            cursor = conn.cursor()

            # -------------------------------------------------
            # NORMALIZE EXISTING TIMESTAMPS TO ONE SECOND
            # -------------------------------------------------
            cursor.execute(
                """
                UPDATE PLC_Data
                SET Timestamp = substr(Timestamp, 1, 19)
                WHERE Timestamp IS NOT NULL
                  AND length(Timestamp) > 19
                """
            )

            # -------------------------------------------------
            # REMOVE EXISTING DUPLICATES
            # Keep the first stored row for each exact
            # Company + Tag + second.
            # -------------------------------------------------
            cursor.execute(
                """
                DELETE FROM PLC_Data
                WHERE ID NOT IN
                (
                    SELECT MIN(ID)
                    FROM PLC_Data
                    WHERE Timestamp IS NOT NULL
                    GROUP BY
                        CompanyID,
                        LOWER(TagName),
                        Timestamp
                )
                AND Timestamp IS NOT NULL
                """
            )

            # -------------------------------------------------
            # BLOCK FUTURE DUPLICATES.
            # The trigger uses second precision, so a sender
            # timestamp containing milliseconds cannot create
            # a second point for the same historian second.
            # -------------------------------------------------
            cursor.execute(
                """
                CREATE TRIGGER IF NOT EXISTS
                    trg_plc_data_one_value_per_second
                BEFORE INSERT ON PLC_Data
                FOR EACH ROW
                WHEN NEW.Timestamp IS NOT NULL
                 AND EXISTS
                 (
                    SELECT 1
                    FROM PLC_Data
                    WHERE CompanyID = NEW.CompanyID
                      AND LOWER(TagName) = LOWER(NEW.TagName)
                      AND substr(Timestamp, 1, 19)
                          = substr(NEW.Timestamp, 1, 19)
                 )
                BEGIN
                    SELECT RAISE(IGNORE);
                END
                """
            )

            conn.commit()
            self._historian_integrity_ready = True

        except Exception as exc:
            if conn is not None:
                try:
                    conn.rollback()
                except Exception:
                    pass

            print(
                "TREND HISTORIAN INTEGRITY ERROR:",
                exc
            )

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

    # =================================================
    # TIMESTAMP HELPERS
    # =================================================

    @staticmethod
    def _to_second(value):
        if value is None:
            return None

        if isinstance(value, datetime):
            return value.replace(microsecond=0)

        text = str(value).strip().replace("T", " ")

        for fmt in (
            "%Y-%m-%d %H:%M:%S.%f",
            "%Y-%m-%d %H:%M:%S",
            "%Y-%m-%d %H:%M",
        ):
            try:
                return datetime.strptime(text, fmt).replace(microsecond=0)
            except Exception:
                pass

        return None

    def _format_output_timestamp(self, value):
        dt = self._to_second(value)
        if dt is None:
            return str(value)
        return dt.strftime("%Y-%m-%d %H:%M:%S")

    def _add_zero_gap_points(
        self,
        values,
        start,
        end
    ):
        """
        Represent periods where no Edge value exists as zero.

        We do not generate one database row per missing second. Only the
        transition points are added, which keeps long offline periods small
        while making the chart unambiguously zero during the gap.
        """

        normalized = []

        for item in values:
            timestamp = self._to_second(item["Timestamp"])
            if timestamp is None:
                continue

            normalized.append({
                "Timestamp": timestamp,
                "Value": float(item["Value"])
            })

        normalized.sort(
            key=lambda item: item["Timestamp"]
        )

        # -------------------------------------------------
        # ONE POINT PER SECOND
        # -------------------------------------------------
        unique = {}
        for item in normalized:
            # If legacy duplicates still exist, keep the first
            # historian value for that second.
            unique.setdefault(
                item["Timestamp"],
                item["Value"]
            )

        actual = [
            {
                "Timestamp": timestamp,
                "Value": value
            }
            for timestamp, value
            in sorted(unique.items())
        ]

        start_dt = self._to_second(start)
        end_dt = self._to_second(end)

        if start_dt is None and actual:
            start_dt = actual[0]["Timestamp"]

        if end_dt is None and actual:
            end_dt = actual[-1]["Timestamp"]

        if start_dt is None or end_dt is None:
            return actual

        if end_dt < start_dt:
            start_dt, end_dt = end_dt, start_dt

        result = []

        # -------------------------------------------------
        # OFFLINE BEFORE FIRST RECEIVED VALUE
        # -------------------------------------------------
        if not actual:
            return [
                {
                    "Timestamp": start_dt,
                    "Value": 0.0
                },
                {
                    "Timestamp": end_dt,
                    "Value": 0.0
                }
            ] if start_dt != end_dt else [
                {
                    "Timestamp": start_dt,
                    "Value": 0.0
                }
            ]

        first = actual[0]["Timestamp"]

        if start_dt < first:
            result.append({
                "Timestamp": start_dt,
                "Value": 0.0
            })

            before_first = first - timedelta(seconds=1)

            if before_first >= start_dt:
                result.append({
                    "Timestamp": before_first,
                    "Value": 0.0
                })

        # -------------------------------------------------
        # ACTUAL VALUES + OFFLINE GAPS
        # -------------------------------------------------
        for index, current in enumerate(actual):

            timestamp = current["Timestamp"]

            if timestamp < start_dt or timestamp > end_dt:
                continue

            result.append(current)

            if index >= len(actual) - 1:
                continue

            next_item = actual[index + 1]
            next_timestamp = next_item["Timestamp"]

            gap_seconds = int(
                (next_timestamp - timestamp).total_seconds()
            )

            if gap_seconds > 1:
                gap_start = timestamp + timedelta(seconds=1)
                gap_end = next_timestamp - timedelta(seconds=1)

                if gap_start <= end_dt and gap_end >= start_dt:
                    zero_start = max(
                        gap_start,
                        start_dt
                    )
                    zero_end = min(
                        gap_end,
                        end_dt
                    )

                    result.append({
                        "Timestamp": zero_start,
                        "Value": 0.0
                    })

                    if zero_end != zero_start:
                        result.append({
                            "Timestamp": zero_end,
                            "Value": 0.0
                        })

        # -------------------------------------------------
        # OFFLINE AFTER LAST RECEIVED VALUE
        # -------------------------------------------------
        last = actual[-1]["Timestamp"]

        if last < end_dt:
            after_last = last + timedelta(seconds=1)

            if after_last <= end_dt:
                result.append({
                    "Timestamp": after_last,
                    "Value": 0.0
                })

            if end_dt != after_last:
                result.append({
                    "Timestamp": end_dt,
                    "Value": 0.0
                })

        # -------------------------------------------------
        # FINAL SORT + ABSOLUTE DEDUPLICATION
        # -------------------------------------------------
        result.sort(
            key=lambda item: item["Timestamp"]
        )

        final = []
        seen = set()

        for item in result:
            timestamp = item["Timestamp"]

            if timestamp in seen:
                continue

            seen.add(timestamp)

            final.append({
                "Timestamp": self._format_output_timestamp(
                    timestamp
                ),
                "Value": float(item["Value"])
            })

        return final

    # =================================================
    # EXECUTE
    # =================================================

    def execute(self, data=None):

        if data is None:
            data = {}

        self._ensure_historian_integrity()

        request = data.get("TrendRequest", {}) or {}

        # DateConverterNode(J2G) converts dates directly inside
        # TrendRequest. Older flow versions used ConvertedDate, so keep
        # that as a fallback for compatibility.
        dates = data.get("ConvertedDate", {}) or {}

        start = request.get("Start")
        end = request.get("End")

        if start is None:
            start = dates.get("Start")

        if end is None:
            end = dates.get("End")

        company_id = self.config.get("company_id", 1)

        tags = request.get("Tags", []) or []

        # Normalize the single-tag form used by the Trend page.
        if not tags and request.get("Tag"):
            tags = [request.get("Tag")]

        result = {}

        for tag in tags:

            if not tag:
                continue

            rows = get_trend_data(
                company_id,
                tag,
                start,
                end
            )

            values = []

            for row in rows:

                raw_value = row_value(
                    row,
                    "Value",
                    1
                )

                try:
                    numeric_value = float(raw_value)
                except (TypeError, ValueError):
                    continue

                values.append(
                    {
                        "Timestamp": row_value(
                            row,
                            "Timestamp",
                            0
                        ),
                        "Value": numeric_value
                    }
                )

            result[tag] = self._add_zero_gap_points(
                values,
                start,
                end
            )

        data["TrendResult"] = result

        print()
        print("==============================")
        print("TREND QUERY")
        print("Company:", company_id)
        print("Start:", start)
        print("End:", end)
        print("Tags:", tags)
        print(result)
        print("==============================")
        print()

        return data
