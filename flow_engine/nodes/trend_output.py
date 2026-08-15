# =====================================================
# SCADA_FLOW TREND OUTPUT NODE
# =====================================================

from datetime import datetime
import jdatetime


class TrendOutput:

    def __init__(self, config):
        self.config = config or {}

    def convert_time(self, value):
        """Return a numeric JavaScript timestamp (milliseconds)."""
        try:
            if value is None:
                return None
            if hasattr(value, "timestamp"):
                return int(value.timestamp() * 1000)

            text = str(value).strip().replace("T", " ")
            if text.endswith("Z"):
                text = text[:-1]

            if "/" in text:
                for fmt in ("%Y/%m/%d %H:%M:%S", "%Y/%m/%d %H:%M"):
                    try:
                        jalali = jdatetime.datetime.strptime(text, fmt)
                        gregorian = jalali.togregorian()
                        return int(gregorian.timestamp() * 1000)
                    except Exception:
                        pass

            for fmt in (
                "%Y-%m-%d %H:%M:%S.%f",
                "%Y-%m-%d %H:%M:%S",
                "%Y-%m-%d %H:%M",
            ):
                try:
                    dt = datetime.strptime(text, fmt)
                    return int(dt.timestamp() * 1000)
                except Exception:
                    pass

            dt = datetime.fromisoformat(text)
            return int(dt.timestamp() * 1000)

        except Exception as e:
            print("TIME CONVERSION ERROR:", e, value)
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

        print()
        print("========== TREND OUTPUT DEBUG ==========")
        print("Selected tag:", selected_tag)
        print("Datasets:", len(output))
        print("Points:", len(points))
        if points:
            print("FIRST POINT:", points[0])
            print("LAST POINT:", points[-1])
        print("========================================")
        print()

        return data
