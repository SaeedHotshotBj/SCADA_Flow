# =====================================================
# SCADA_FLOW TREND OUTPUT NODE
# =====================================================

from datetime import datetime
import jdatetime


class TrendOutput:

    def __init__(self, config):
        self.config = config or {}

    def _parse_timestamp(self, value):
        if value is None:
            return None

        if (
            hasattr(value, "year")
            and hasattr(value, "month")
            and hasattr(value, "day")
        ):
            dt = value
            if getattr(dt, "tzinfo", None) is not None:
                dt = dt.replace(tzinfo=None)
            return dt

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
                    return jdatetime.datetime.strptime(text, fmt).togregorian()
                except Exception:
                    pass

        for fmt in (
            "%Y-%m-%d %H:%M:%S.%f",
            "%Y-%m-%d %H:%M:%S",
            "%Y-%m-%d %H:%M",
        ):
            try:
                return datetime.strptime(text, fmt)
            except Exception:
                pass

        try:
            return datetime.fromisoformat(text).replace(tzinfo=None)
        except Exception:
            return None

    def convert_time(self, value):
        dt = self._parse_timestamp(value)
        if dt is None:
            return None

        day_ms = dt.toordinal() * 86_400_000
        time_ms = (
            dt.hour * 3_600_000
            + dt.minute * 60_000
            + dt.second * 1_000
            + dt.microsecond // 1_000
        )
        return day_ms + time_ms

    def jalali_label(self, value):
        try:
            dt = self._parse_timestamp(value)
            if dt is None:
                return str(value)

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

        trend_data = data.get("TrendData", []) or []
        request = data.get("TrendRequest", {}) or {}
        selected_tag = request.get("Tag")

        if not selected_tag:
            tags = request.get("Tags", []) or []
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

        stats = data.get("TrendStats", {}) or {}
        selected_stats = stats.get(selected_tag, {}) if selected_tag else {}

        data["ChartData"] = {
            "datasets": output,
            "stats": selected_stats,
            "resolution": selected_stats.get("resolution"),
        }

        return data
