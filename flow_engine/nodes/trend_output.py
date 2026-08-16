# =====================================================
# SCADA_FLOW TREND OUTPUT NODE
# =====================================================

from datetime import datetime, timezone
import calendar
import jdatetime


class TrendOutput:

    def __init__(self, config):
        self.config = config or {}

    def convert_time(self, value):
        """Return epoch milliseconds for a historian timestamp."""
        try:
            if value is None:
                return None

            if hasattr(value, "year") and hasattr(value, "month") and hasattr(value, "day"):
                dt = value
                if getattr(dt, "tzinfo", None) is not None:
                    return int(dt.timestamp() * 1000)
                return int(calendar.timegm(dt.timetuple()) * 1000 + getattr(dt, "microsecond", 0) // 1000)

            text = str(value).strip().replace("T", " ")
            if text.endswith("Z"):
                text = text[:-1]

            if "/" in text:
                for fmt in (
                    "%Y/%m/%d %H:%M:%S.%f",
                    "%Y/%m/%d %H:%M:%S",
                    "%Y/%m/%d %H:%M",
                ):
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

    def jalali_label(self, value):
        """Create an exact visible Jalali label from the historian timestamp."""
        try:
            if value is None:
                return ""

            if hasattr(value, "year") and hasattr(value, "month") and hasattr(value, "day"):
                dt = value
            else:
                text = str(value).strip().replace("T", " ")
                if text.endswith("Z"):
                    text = text[:-1]
                dt = None
                for fmt in (
                    "%Y-%m-%d %H:%M:%S.%f",
                    "%Y-%m-%d %H:%M:%S",
                    "%Y-%m-%d %H:%M",
                ):
                    try:
                        dt = datetime.strptime(text, fmt)
                        break
                    except Exception:
                        pass
                if dt is None:
                    dt = datetime.fromisoformat(text)

            if getattr(dt, "tzinfo", None) is not None:
                dt = dt.astimezone(timezone.utc).replace(tzinfo=None)

            jdt = jdatetime.datetime.fromgregorian(datetime=dt)
            return jdt.strftime("%Y/%m/%d %H:%M:%S")

        except Exception:
            return str(value)

    def _point(self, timestamp, value):
        x = self.convert_time(timestamp)
        if x is None:
            return None

        try:
            y = float(value)
        except (TypeError, ValueError):
            return None

        return {
            "x": x,
            "y": y,
            "label": self.jalali_label(timestamp),
        }

    def execute(self, data=None):
        if data is None:
            data = {}

        if "ReportRequest" in data:
            return data

        trend_data = data.get("TrendData", [])
        request = data.get("TrendRequest", {})
        selected_tag = request.get("Tag")

        if not selected_tag:
            tags = request.get("Tags", [])
            if len(tags) == 1:
                selected_tag = tags[0]

        grouped = {}
        for item in trend_data:
            tag = item.get("Tag")
            if not tag:
                continue

            point = self._point(
                item.get("Timestamp"),
                item.get("Value", 0)
            )
            if point is None:
                continue

            grouped.setdefault(tag, []).append(point)

        output = []

        if selected_tag:
            points = grouped.get(selected_tag, [])
            points.sort(key=lambda p: p["x"])

            output.append({
                "tag": selected_tag,
                "title": selected_tag,
                "data": points,
                "stepped": "after",
            })
        else:
            for tag, tag_points in grouped.items():
                tag_points.sort(key=lambda p: p["x"])
                output.append({
                    "tag": tag,
                    "title": tag,
                    "data": tag_points,
                    "stepped": "after",
                })

        data["ChartData"] = {"datasets": output}
        return data
