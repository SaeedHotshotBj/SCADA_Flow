# =====================================================
# SCADA_FLOW TREND DATABASE READER NODE
# =====================================================

from datetime import datetime, timedelta
import jdatetime

from services.trend_aggregation import (
    get_trend_series,
    get_trend_stats,
    start_aggregation_worker,
)
from database import row_value


try:
    start_aggregation_worker()
except Exception as exc:
    print("TREND AGGREGATION START ERROR:", exc)


class TrendDatabaseReader:

    def __init__(self, config=None):
        self.config = config or {}
        self.company_id = self.config.get("company_id")

    @staticmethod
    def _normalize_digits(text):
        if text is None:
            return text
        return str(text).translate(str.maketrans(
            "۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩",
            "01234567890123456789"
        ))

    @staticmethod
    def _normalize_tag(value):
        return str(value or "").strip().lower()

    @classmethod
    def _split_tags(cls, value):
        if value is None:
            return []
        if isinstance(value, (list, tuple, set)):
            values = value
        else:
            values = str(value).replace(";", ",").split(",")
        result = []
        seen = set()
        for item in values:
            tag = str(item).strip()
            if not tag:
                continue
            key = cls._normalize_tag(tag)
            if key in seen:
                continue
            seen.add(key)
            result.append(tag)
        return result

    def normalize_date(self, value, calendar, timezone_offset=None):
        if not value:
            return None

        text = self._normalize_digits(value)
        text = text.strip().replace("T", " ")

        if calendar == "Jalali":
            text = text.replace("-", "/")
            for fmt in (
                "%Y/%m/%d %H:%M:%S.%f",
                "%Y/%m/%d %H:%M:%S",
                "%Y/%m/%d %H:%M",
            ):
                try:
                    return jdatetime.datetime.strptime(text, fmt).togregorian()
                except ValueError:
                    pass
            return None

        for fmt in (
            "%Y-%m-%d %H:%M:%S.%f",
            "%Y-%m-%d %H:%M:%S",
            "%Y-%m-%d %H:%M",
            "%Y/%m/%d %H:%M:%S.%f",
            "%Y/%m/%d %H:%M:%S",
            "%Y/%m/%d %H:%M",
        ):
            try:
                return datetime.strptime(text, fmt)
            except ValueError:
                pass

        return None

    @staticmethod
    def _row_timestamp(row):
        return row_value(row, "Timestamp", 0)

    @staticmethod
    def _row_value(row):
        return row_value(row, "Value", 1)

    @staticmethod
    def _sort_timestamp(item):
        value = item.get("Timestamp")
        if value is None:
            return datetime.min
        if isinstance(value, datetime):
            return value

        text = str(value).strip().replace("T", " ")
        for fmt in (
            "%Y-%m-%d %H:%M:%S.%f",
            "%Y-%m-%d %H:%M:%S",
            "%Y-%m-%d %H:%M",
        ):
            try:
                return datetime.strptime(text, fmt)
            except ValueError:
                pass
        return datetime.min

    def execute(self, data=None):
        if data is None:
            data = {}

        request = data.get("TrendRequest", {}) or {}

        selected_tag = request.get("Tag")
        tags = self._split_tags(request.get("Tags"))
        if not tags:
            tags = self._split_tags(selected_tag)

        if selected_tag and len(tags) <= 1:
            selected_tag = tags[0] if tags else str(selected_tag).strip()
        elif len(tags) != 1:
            selected_tag = None

        calendar = request.get("Calendar", "Gregorian")

        start = self.normalize_date(request.get("Start"), calendar)
        end = self.normalize_date(request.get("End"), calendar)

        if start is None and end is None:
            end = datetime.now().replace(microsecond=0)
            start = end - timedelta(hours=2)
        elif start is None or end is None:
            print(
                "TREND DATABASE READER: incomplete date range",
                request.get("Start"),
                request.get("End")
            )
            data["TrendData"] = []
            data["TrendStats"] = {}
            data["TrendResolution"] = {}
            return data

        company_id = data.get("CompanyID")
        if company_id is None:
            company_id = request.get("CompanyID")
        if company_id is None:
            company_id = self.company_id

        try:
            company_id = int(company_id)
        except (TypeError, ValueError):
            print("TREND DATABASE READER: invalid CompanyID:", company_id)
            data["TrendData"] = []
            data["TrendStats"] = {}
            data["TrendResolution"] = {}
            return data

        trend = []
        stats_by_tag = {}
        resolutions = {}

        for tag in tags:
            try:
                resolution, rows = get_trend_series(
                    company_id,
                    tag,
                    start,
                    end
                )

                resolutions[tag] = resolution
                matched_rows = rows or []

                for row in matched_rows:
                    value = self._row_value(row)
                    timestamp = self._row_timestamp(row)
                    if timestamp is None or value is None:
                        continue
                    trend.append({
                        "Tag": tag,
                        "Timestamp": timestamp,
                        "Value": value,
                    })

                stats = get_trend_stats(
                    company_id,
                    tag,
                    start,
                    end
                )
                stats_by_tag[tag] = stats

                print(
                    "TREND DATABASE READER:",
                    "Company=", company_id,
                    "Tag=", tag,
                    "Start=", start,
                    "End=", end,
                    "Resolution=", resolution,
                    "Rows=", len(matched_rows),
                    "Stats=", stats,
                )

            except Exception as exc:
                resolutions[tag] = "error"
                stats_by_tag[tag] = {
                    "resolution": "error",
                    "min": None,
                    "max": None,
                    "weighted_average": None,
                    "sample_count": 0,
                }
                print(
                    "TREND DATABASE READER ERROR:",
                    "Company=", company_id,
                    "Tag=", tag,
                    "Error=", repr(exc),
                )

        trend.sort(key=self._sort_timestamp)

        data["CompanyID"] = company_id
        data["TrendRequest"] = {
            "Tag": selected_tag,
            "Tags": tags,
            "Start": start,
            "End": end,
            "Calendar": calendar,
            "CompanyID": company_id,
            "TimezoneOffset": None,
        }
        data["TrendData"] = trend
        data["TrendStats"] = stats_by_tag
        data["TrendResolution"] = resolutions
        data["TrendRecordCount"] = len(trend)

        print(
            "TREND DATABASE READER TOTAL:",
            "Company=", company_id,
            "Tags=", tags,
            "Records=", len(trend),
        )

        return data
