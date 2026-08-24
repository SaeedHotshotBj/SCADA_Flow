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
        # The request is already present when FlowRunner reaches
        # this node. TrendReader must NOT execute downstream nodes
        # itself. FlowRunner follows the Drawflow connections:
        # TrendReader -> SQLWriter -> TrendDatabaseReader -> TrendOutput.
        # Keeping the payload unchanged preserves the user's
        # connection-driven flow architecture.
        if "TrendRequest" in data:
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
            return data

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
