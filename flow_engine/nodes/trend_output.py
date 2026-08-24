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
        return int(dt.replace(tzinfo=timezone.utc).timestamp() * 1000)

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
    def _stats_for_tag(stats, tag):
        if not isinstance(stats, dict):
            return {}
        direct = stats.get(tag)
        if isinstance(direct, dict):
            return direct
        wanted = str(tag or "").strip().lower()
        for key, value in stats.items():
            if str(key).strip().lower() == wanted and isinstance(value, dict):
                return value
        return {}

    def execute(self, data=None):
        if data is None:
            data = {}
        if "ReportRequest" in data:
            return data

        trend_data = data.get("TrendData", []) or []
        request = data.get("TrendRequest", {}) or {}
        selected_tag = request.get("Tag")
        selected_key = self._normalize_tag(selected_tag)

        grouped = {}
        for item in trend_data:
            tag = item.get("Tag")
            if not tag:
                continue
            point = self._point(item.get("Timestamp"), item.get("Value"))
            if point is None:
                continue
            key = self._normalize_tag(tag)
            grouped.setdefault(key, {
                "tag": tag,
                "title": tag,
                "data": [],
                "stepped": "after",
            })["data"].append(point)

        for group in grouped.values():
            group["data"].sort(key=lambda p: p["x"])

        if selected_key:
            output = [grouped[selected_key]] if selected_key in grouped else []
        else:
            requested_tags = request.get("Tags", []) or []
            output = []
            seen = set()
            for tag in requested_tags:
                key = self._normalize_tag(tag)
                if key in grouped and key not in seen:
                    output.append(grouped[key])
                    seen.add(key)
            for key, group in grouped.items():
                if key not in seen:
                    output.append(group)

        stats = data.get("TrendStats", {}) or {}
        resolutions = data.get("TrendResolution", {}) or {}

        selected_stats = self._stats_for_tag(stats, selected_tag) if selected_tag else {}
        result_stats = selected_stats if selected_tag else stats
        resolution = selected_stats.get("resolution") if selected_stats else None
        if not resolution and selected_tag:
            resolution = resolutions.get(selected_tag)

        data["ChartData"] = {
            "datasets": output,
            "stats": result_stats,
            "resolutions": resolutions,
            "multi": len(output) > 1,
            "selected": selected_tag,
        }

        print(
            "TREND OUTPUT:",
            "Selected=", selected_tag,
            "Datasets=", len(output),
            "Points=", sum(len(ds.get("data", [])) for ds in output),
            "Stats=", result_stats,
        )

        return data
