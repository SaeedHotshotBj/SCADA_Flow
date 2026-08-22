# =====================================================
# SCADA_FLOW TREND DATABASE READER NODE
# =====================================================

from datetime import datetime
import jdatetime

from database import get_trend_data, row_value


class TrendDatabaseReader:

    def __init__(self, config=None):
        self.config = config or {}
        self.company_id = self.config.get("company_id")

    def normalize_date(self, value, calendar, timezone_offset=None):
        """
        Convert the user's date/time to the same naive wall-clock time used
        by the historian database.

        IMPORTANT:
        The historian stores timestamps without timezone information, so the
        browser timezone offset must NOT be added or subtracted here.
        """
        if not value:
            return None

        try:
            if calendar == "Jalali":
                text = str(value).strip().replace("-", "/").replace("T", " ")
                jalali = jdatetime.datetime.strptime(
                    text,
                    "%Y/%m/%d %H:%M"
                )
                return jalali.togregorian()

            text = str(value).strip().replace("T", " ")
            return datetime.strptime(
                text,
                "%Y-%m-%d %H:%M"
            )

        except Exception:
            return None

    def execute(self, data=None):
        if data is None:
            data = {}

        request = data.get("TrendRequest", {}) or {}

        selected_tag = request.get("Tag")
        tags = request.get("Tags", []) or []

        if selected_tag:
            tags = [selected_tag]
        elif len(tags) == 1:
            selected_tag = tags[0]

        # Kept only for compatibility with older callers.
        # It is intentionally NOT used for timestamp arithmetic.
        timezone_offset = request.get("TimezoneOffset")
        try:
            timezone_offset = (
                float(timezone_offset)
                if timezone_offset is not None
                else None
            )
        except (TypeError, ValueError):
            timezone_offset = None

        start = self.normalize_date(
            request.get("Start"),
            request.get("Calendar", "Gregorian"),
            timezone_offset
        )
        end = self.normalize_date(
            request.get("End"),
            request.get("Calendar", "Gregorian"),
            timezone_offset
        )

        company_id = data.get("CompanyID")
        if company_id is None:
            company_id = request.get("CompanyID")
        if company_id is None:
            company_id = self.company_id

        try:
            company_id = int(company_id)
        except (TypeError, ValueError):
            data["TrendData"] = []
            return data

        trend = []

        for tag in tags:
            if not tag:
                continue

            try:
                rows = get_trend_data(
                    company_id,
                    tag,
                    start,
                    end
                )

                for row in rows:
                    trend.append({
                        "Tag": tag,
                        "Timestamp": row_value(row, "Timestamp", 0),
                        "Value": row_value(row, "Value", 1)
                    })

            except Exception:
                continue

        def _trend_timestamp(item):
            value = item.get("Timestamp")

            if value is None:
                return datetime.min

            if hasattr(value, "timestamp"):
                return value

            text = str(value).replace("T", " ")

            for fmt in (
                "%Y-%m-%d %H:%M:%S.%f",
                "%Y-%m-%d %H:%M:%S",
                "%Y-%m-%d %H:%M"
            ):
                try:
                    return datetime.strptime(text, fmt)
                except ValueError:
                    pass

            return datetime.min

        trend.sort(
            key=_trend_timestamp,
            reverse=True
        )

        data["TrendRequest"] = {
            "Tag": selected_tag,
            "Tags": tags,
            "Start": start,
            "End": end,
            "Calendar": request.get("Calendar", "Gregorian"),
            "CompanyID": company_id,
            "TimezoneOffset": None
        }
        data["TrendData"] = trend

        return data
