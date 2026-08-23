# =====================================================
# SCADA_FLOW TREND DATABASE READER NODE
# =====================================================

from datetime import datetime
import jdatetime

from services.trend_aggregation import (
    get_trend_series,
    get_trend_stats,
)
from database import row_value


class TrendDatabaseReader:

    def __init__(self, config=None):
        self.config = config or {}
        self.company_id = self.config.get("company_id")

    def normalize_date(self, value, calendar, timezone_offset=None):
        """Convert user wall-clock date/time to historian naive datetime."""
        if not value:
            return None
        try:
            if calendar == "Jalali":
                text = str(value).strip().replace("-", "/").replace("T", " ")
                return jdatetime.datetime.strptime(
                    text,
                    "%Y/%m/%d %H:%M"
                ).togregorian()

            text = str(value).strip().replace("T", " ")
            return datetime.strptime(
                text,
                "%Y-%m-%d %H:%M"
            )
        except Exception:
            return None

    @staticmethod
    def _row_timestamp(row):
        return row_value(row, "Timestamp", 0)

    @staticmethod
    def _row_value(row):
        return row_value(row, "Value", 1)

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

        start = self.normalize_date(
            request.get("Start"),
            request.get("Calendar", "Gregorian")
        )
        end = self.normalize_date(
            request.get("End"),
            request.get("Calendar", "Gregorian")
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
            data["TrendStats"] = {}
            return data

        trend = []
        stats_by_tag = {}
        resolutions = {}

        if start is None or end is None:
            end = datetime.now()
            start = end.replace(microsecond=0)
            start = start.fromtimestamp(start.timestamp() - 7200)

        for tag in tags:
            if not tag:
                continue

            try:
                resolution, rows = get_trend_series(
                    company_id,
                    tag,
                    start,
                    end
                )
                resolutions[tag] = resolution

                if resolution == "raw":
                    for row in rows:
                        trend.append({
                            "Tag": tag,
                            "Timestamp": row["Timestamp"],
                            "Value": row["Value"]
                        })
                else:
                    for row in rows:
                        trend.append({
                            "Tag": tag,
                            "Timestamp": self._row_timestamp(row),
                            "Value": self._row_value(row)
                        })

                stats_by_tag[tag] = get_trend_stats(
                    company_id,
                    tag,
                    start,
                    end
                )
            except Exception:
                resolutions[tag] = "error"
                stats_by_tag[tag] = {
                    "resolution": "error",
                    "min": None,
                    "max": None,
                    "weighted_average": None,
                    "sample_count": 0,
                }

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
            key=_trend_timestamp
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
        data["TrendStats"] = stats_by_tag
        data["TrendResolution"] = resolutions

        return data
