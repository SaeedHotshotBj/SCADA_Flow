# =====================================================
# SCADA_FLOW TREND READER NODE
# =====================================================


class TrendReader:

    def __init__(self, config):
        self.config = config or {}

    @staticmethod
    def _split_tags(value):
        if value is None:
            return []
        if isinstance(value, (list, tuple, set)):
            values = value
        else:
            values = str(value).replace(";", ",").split(",")

        result = []
        seen = set()
        for item in values:
            tag = str(item).strip()
            if not tag:
                continue
            key = tag.lower()
            if key in seen:
                continue
            seen.add(key)
            result.append(tag)
        return result

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
        # this node. TrendReader only normalizes the selected tags.
        # FlowRunner continues through the actual Drawflow connections:
        # TrendReader -> SQLWriter -> TrendDatabaseReader -> TrendOutput.
        if "TrendRequest" in data:
            request = data.get("TrendRequest") or {}
            tags = request.get("Tags") or []
            selected = request.get("Tag")

            normalized = self._split_tags(tags)
            if not normalized:
                normalized = self._split_tags(selected)

            if selected and len(normalized) <= 1:
                selected_tag = normalized[0] if normalized else str(selected).strip()
            else:
                selected_tag = None

            request["Tags"] = normalized
            request["Tag"] = selected_tag
            data["TrendRequest"] = request
            return data

        # =================================================
        # OLD STYLE DIRECT REQUEST
        # =================================================
        if "tag" in data:
            tags = self._split_tags(data.get("tag"))
            data["TrendRequest"] = {
                "Tag": tags[0] if len(tags) == 1 else None,
                "Tags": tags,
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
            "Tag": None,
            "Tags": tags,
            "Start": self.config.get("start"),
            "End": self.config.get("end"),
            "Calendar": self.config.get("calendar", "Gregorian"),
        }

        return data
