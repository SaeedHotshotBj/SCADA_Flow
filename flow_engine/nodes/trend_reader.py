# =====================================================
# SCADA_FLOW TREND READER NODE
# =====================================================


class TrendReader:

    def __init__(self, config):
        self.config = config or {}

    # =====================================================
    # EXECUTE
    # =====================================================

    def execute(self, data=None):
        if data is None:
            data = {}

        # =================================================
        # HISTORICAL TREND REQUEST
        # =================================================
        # The Trend HTTP request already contains the complete
        # request. Build the database/output branch here so the
        # result does not depend on the realtime PLC branch.
        if "TrendRequest" in data:
            try:
                from flow_engine.nodes.trend_database_reader import TrendDatabaseReader
                from flow_engine.nodes.trend_output import TrendOutput

                reader = TrendDatabaseReader({
                    "company_id": self.config.get("company_id")
                })
                result = reader.execute(data)
                output = TrendOutput({})
                return output.execute(result)
            except Exception as exc:
                print("TREND REQUEST ERROR:", exc)
                data["ChartData"] = {
                    "datasets": [],
                    "stats": {},
                    "resolution": None,
                }
                return data

        # =================================================
        # OLD STYLE DIRECT REQUEST
        # =================================================
        if "tag" in data:
            data["TrendRequest"] = {
                "Tag": data["tag"],
                "Tags": [data["tag"]],
                "Start": data.get("start"),
                "End": data.get("end"),
                "Calendar": data.get("calendar", "Gregorian"),
            }
            return self.execute(data)

        # =================================================
        # FLOW STARTUP
        # =================================================
        tags = []
        definitions = data.get("TagDefinitions", [])

        for item in definitions:
            if item.get("storage", "").upper() == "TIME":
                name = item.get("name")
                if name:
                    tags.append(name)

        data["TrendRequest"] = {
            "Tags": tags,
            "Start": self.config.get("start"),
            "End": self.config.get("end"),
            "Calendar": self.config.get("calendar", "Gregorian"),
        }

        return data
