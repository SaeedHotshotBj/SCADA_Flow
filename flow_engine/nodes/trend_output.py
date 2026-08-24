# =====================================================
# SCADA_FLOW TREND OUTPUT NODE
# =====================================================

from datetime import datetime, timezone
import jdatetime


class TrendOutput:

    def __init__(self, config=None):
        self.config = config or {}

    def _parse_timestamp(self, value):
        if value is None:
            return None

        if hasattr(value, "year") and hasattr(value, "month") and hasattr(value, "day"):
            dt = value
            if getattr(dt, "tzinfo", None) is not None:
                dt = dt.replace(tzinfo=None)
            return dt

        text = str(value).strip().replace("T", " ")
        if text.endswith("Z"):
            text = text[:-1]

        text = text.translate(str.maketrans(
            "۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩",
            "01234567890123456789"
        ))

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

        return int(
            dt.replace(tzinfo=timezone.utc).timestamp() * 1000
        )

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

    @staticmethod
    def _normalize_tag(value):
        return str(value or "").strip().lower()

    @staticmethod
    def _stats_from_points(points):
        values = [
            float(point["y"])
            for point in points
            if point.get("y") is not None
        ]

        if not values:
            return {
                "resolution": None,
                "min": None,
                "max": None,
                "weighted_average": None,
                "sample_count": 0,
            }

        return {
            "resolution": "minute",
            "min": min(values),
            "max": max(values),
            "weighted_average": sum(values) / len(values),
            "sample_count": len(values),
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
            if isinstance(tags, str):
                tags = [tags]
            if len(tags) == 1:
                selected_tag = tags[0]

        selected_key = self._normalize_tag(selected_tag)
        grouped = {}

        for item in trend_data:
            tag = item.get("Tag")
            if not tag:
                continue

            point = self._point(
                item.get("Timestamp"),
                item.get("Value")
            )
            if point is None:
                continue

            key = self._normalize_tag(tag)
            group = grouped.setdefault(key, {
                "tag": tag,
                "title": tag,
                "data": [],
                "stepped": "after",
            })
            group["data"].append(point)

        for group in grouped.values():
            group["data"].sort(key=lambda p: p["x"])

        output = []

        if selected_key:
            group = grouped.get(selected_key)

            # The requested tag is allowed to differ only by case/whitespace.
            # If the node chain returned exactly one tag, use that group as a
            # compatibility fallback rather than producing an empty chart.
            if group is None and len(grouped) == 1:
                group = next(iter(grouped.values()))

            if group is not None:
                output.append(group)
        else:
            output = list(grouped.values())

        stats = data.get("TrendStats", {}) or {}
        selected_stats = stats.get(selected_tag, {}) if selected_tag else {}

        if not selected_stats and selected_key:
            for key, value in stats.items():
                if self._normalize_tag(key) == selected_key:
                    selected_stats = value or {}
                    break

        if not selected_stats or selected_stats.get("min") is None:
            if output:
                selected_stats = self._stats_from_points(
                    output[0].get("data", [])
                )
            else:
                selected_stats = {
                    "resolution": None,
                    "min": None,
                    "max": None,
                    "weighted_average": None,
                    "sample_count": 0,
                }

        data["ChartData"] = {
            "datasets": output,
            "stats": selected_stats,
            "resolution": selected_stats.get("resolution"),
        }

        print(
            "TREND OUTPUT:",
            "Tag=", selected_tag,
            "InputRecords=", len(trend_data),
            "Datasets=", len(output),
            "Points=", sum(len(ds.get("data", [])) for ds in output),
            "Stats=", selected_stats,
        )

        return data
