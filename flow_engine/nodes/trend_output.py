# =====================================================
# SCADA_FLOW TREND OUTPUT NODE
# =====================================================

from datetime import datetime
import calendar
import jdatetime


class TrendOutput:

    def __init__(self, config):
        self.config = config or {}

    def convert_time(self, value):
        """Convert a historian timestamp to an exact JavaScript epoch.

        Historian timestamps are stored as timezone-naive local clock values.
        Using datetime.timestamp() makes Python apply the computer's local
        timezone, which can shift the chart date.  Treat the stored clock
        value as UTC for the transport timestamp so the browser can display
        exactly the same clock date/time and then convert it to Jalali.
        """
        try:
            if value is None:
                return None

            if hasattr(value, "year") and hasattr(value, "month") and hasattr(value, "day"):
                dt = value
                return int(calendar.timegm(dt.timetuple()) * 1000 + getattr(dt, "microsecond", 0) // 1000)

            text = str(value).strip().replace("T", " ")
            if text.endswith("Z"):
                text = text[:-1]

            if "/" in text:
                for fmt in ("%Y/%m/%d %H:%M:%S", "%Y/%m/%d %H:%M"):
                    try:
                        jalali = jdatetime.datetime.strptime(text, fmt)
                        gregorian = jalali.togregorian()
                        return int(calendar.timegm(gregorian.timetuple()) * 1000 + gregorian.microsecond // 1000)
                    except Exception:
                        pass

            for fmt in (
                "%Y-%m-%d %H:%M:%S.%f",
                "%Y-%m-%d %H:%M:%S",
                "%Y-%m-%d %H:%M",
            ):
                try:
                    dt = datetime.strptime(text, fmt)
                    return int(calendar.timegm(dt.timetuple()) * 1000 + dt.microsecond // 1000)
                except Exception:
                    pass

            dt = datetime.fromisoformat(text)
            if dt.tzinfo is not None:
                return int(dt.timestamp() * 1000)

            return int(calendar.timegm(dt.timetuple()) * 1000 + dt.microsecond // 1000)

        except Exception:
            return None

    def execute(self, data=None):
        if data is None:
            data = {}

        # Report requests use ReportOutput. Do not replace its ChartData.
        if "ReportRequest" in data:
            return data

        trend_data = data.get("TrendData", [])
        request = data.get("TrendRequest", {})
        selected_tag = request.get("Tag")

        if not selected_tag:
            tags = request.get("Tags", [])
            if len(tags) == 1:
                selected_tag = tags[0]

        output = []
        points = []

        for item in trend_data:
            item_tag = item.get("Tag")
            if selected_tag and item_tag != selected_tag:
                continue

            x = self.convert_time(item.get("Timestamp"))
            if x is None:
                continue

            try:
                y = float(item.get("Value", 0))
            except (TypeError, ValueError):
                continue

            points.append({"x": x, "y": y})

        if selected_tag:
            output.append({
                "tag": selected_tag,
                "title": selected_tag,
                "data": points,
            })
        else:
            grouped = {}
            for item in trend_data:
                tag = item.get("Tag")
                if not tag:
                    continue

                x = self.convert_time(item.get("Timestamp"))
                if x is None:
                    continue

                try:
                    y = float(item.get("Value", 0))
                except (TypeError, ValueError):
                    continue

                grouped.setdefault(tag, []).append({"x": x, "y": y})

            if grouped:
                first_tag = list(grouped.keys())[0]
                output.append({
                    "tag": first_tag,
                    "title": first_tag,
                    "data": grouped[first_tag],
                })

        data["ChartData"] = {"datasets": output}
        return data
