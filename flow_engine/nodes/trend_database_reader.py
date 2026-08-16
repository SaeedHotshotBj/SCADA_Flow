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

    def normalize_date(self, value, calendar):
        if not value:
            return None
        try:
            if calendar == "Jalali":
                value = value.replace("-", "/")
                jalali = jdatetime.datetime.strptime(value, "%Y/%m/%d %H:%M")
                return jalali.togregorian()

            value = value.replace("T", " ")
            return datetime.strptime(value, "%Y-%m-%d %H:%M")
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

        start = self.normalize_date(
            request.get("Start"),
            request.get("Calendar", "Gregorian")
        )
        end = self.normalize_date(
            request.get("End"),
            request.get("Calendar", "Gregorian")
        )

        # CompanyID comes from the authenticated request/flow execution.
        # Node configuration is only a fallback and never overrides it.
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
            "CompanyID": company_id
        }
        data["TrendData"] = trend

        print("TOTAL TREND POINTS:", len(trend))
        return data
