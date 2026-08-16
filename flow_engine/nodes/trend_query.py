# =====================================================
# SCADA_FLOW TREND QUERY NODE
# =====================================================

from database import get_trend_data, row_value


class TrendQuery:

    def __init__(self, config):
        self.config = config or {}

    # =================================================
    # EXECUTE
    # =================================================

    def execute(self, data=None):

        if data is None:
            data = {}

        request = data.get("TrendRequest", {}) or {}

        # DateConverterNode(J2G) converts dates directly inside
        # TrendRequest. Older flow versions used ConvertedDate, so keep
        # that as a fallback for compatibility.
        dates = data.get("ConvertedDate", {}) or {}

        start = request.get("Start")
        end = request.get("End")

        if start is None:
            start = dates.get("Start")

        if end is None:
            end = dates.get("End")

        company_id = self.config.get("company_id", 1)

        tags = request.get("Tags", []) or []

        # Normalize the single-tag form used by the Trend page.
        if not tags and request.get("Tag"):
            tags = [request.get("Tag")]

        result = {}

        for tag in tags:

            if not tag:
                continue

            rows = get_trend_data(
                company_id,
                tag,
                start,
                end
            )

            values = []

            for row in rows:

                values.append(
                    {
                        "Timestamp": row_value(row, "Timestamp", 0),
                        "Value": float(row_value(row, "Value", 1))
                    }
                )

            result[tag] = values

        data["TrendResult"] = result

        print()
        print("==============================")
        print("TREND QUERY")
        print("Company:", company_id)
        print("Start:", start)
        print("End:", end)
        print("Tags:", tags)
        print(result)
        print("==============================")
        print()

        return data
