# =====================================================
# SCADA_FLOW TREND DATABASE READER NODE
# =====================================================

from datetime import datetime, timedelta
import jdatetime

from database import get_trend_data, row_value


class TrendDatabaseReader:

    def __init__(self, config=None):
        self.config = config or {}
        self.company_id = self.config.get("company_id")

    def normalize_date(self, value, calendar, timezone_offset=None):
        if not value:
            return None

        try:
            if calendar == "Jalali":
                value = str(value).replace("-", "/")
                jalali = jdatetime.datetime.strptime(value, "%Y/%m/%d %H:%M")
                result = jalali.togregorian()
            else:
                value = str(value).replace("T", " ")
                result = datetime.strptime(value, "%Y-%m-%d %H:%M")

            # Browser date/time is local time. Historian timestamps are stored
            # in the server clock, so use the browser's offset instead of a
            # hardcoded timezone.
            if timezone_offset is not None:
                result += timedelta(minutes=float(timezone_offset))

            return result

        except Exception as e:
            print("DATE NORMALIZE ERROR:", e)
            return None

    def execute(self, data=None):
        if data is None:
            data = {}

        request = data.get("TrendRequest", {})

        selected_tag = request.get("Tag")
        tags = request.get("Tags", [])

        if selected_tag:
            tags = [selected_tag]
        elif len(tags) == 1:
            selected_tag = tags[0]

        timezone_offset = request.get("TimezoneOffset")
        try:
            timezone_offset = float(timezone_offset) if timezone_offset is not None else None
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
            print("TREND DATABASE ERROR: invalid CompanyID")
            data["TrendData"] = []
            return data

        trend = []

        print("TREND DATABASE READER")
        print("Company:", company_id)
        print("Selected:", selected_tag)
        print("Tags:", tags)
        print("Start:", start)
        print("End:", end)
        print("Timezone Offset:", timezone_offset)

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
                print(tag, "->", len(rows), "rows")

                for row in rows:
                    trend.append({
                        "Tag": tag,
                        "Timestamp": row_value(row, "Timestamp", 0),
                        "Value": row_value(row, "Value", 1)
                    })

            except Exception as e:
                print("TREND DATABASE ERROR:", e)

        data["TrendRequest"] = {
            "Tag": selected_tag,
            "Tags": tags,
            "Start": start,
            "End": end,
            "Calendar": request.get("Calendar", "Gregorian"),
            "CompanyID": company_id,
            "TimezoneOffset": timezone_offset
        }
        data["TrendData"] = trend

        print("TOTAL TREND POINTS:", len(trend))
        return data
