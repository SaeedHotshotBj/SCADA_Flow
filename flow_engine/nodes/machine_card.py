# =====================================================
# SCADA_FLOW MACHINE CARD NODE
# PLC-AWARE INDUSTRIAL MACHINE DASHBOARD CARDS
# =====================================================


class MachineCard:
    """Convert live PLC-aware values into configured machine cards."""

    def __init__(self, config=None):
        self.config = config or {}
        self.machines = self.config.get("machines", [])
        self.icon_library = self.config.get("icon_library", [])

    @staticmethod
    def _plc_id(value):
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    def _value_for(self, data, plc_id, tag):
        plc_id = self._plc_id(plc_id)
        tag = str(tag or "").strip()
        plc_tags = data.get("PLC_Tags", {}) or {}
        if plc_id is not None and tag:
            key = "%s:%s" % (plc_id, tag.lower())
            if key in plc_tags:
                return plc_tags[key]
        tags = data.get("Tags", {}) or {}
        return tags.get(tag)

    def execute(self, data=None):
        data = data or {}
        machine_cards = []

        for machine in self.machines:
            if not isinstance(machine, dict):
                continue

            machine_id = str(machine.get("id", "")).strip() or "machine_%s" % (len(machine_cards) + 1)
            name = str(machine.get("name", "")).strip() or machine_id
            parameters = []

            for parameter in machine.get("parameters", []):
                if not isinstance(parameter, dict):
                    continue
                tag = str(parameter.get("tag", "")).strip()
                if not tag:
                    continue
                plc_id = self._plc_id(parameter.get("plc_id", parameter.get("PLC_ID", data.get("PLC_ID"))))
                parameters.append({
                    "label": str(parameter.get("label", "")).strip() or tag,
                    "tag": tag,
                    "plc_id": plc_id,
                    "unit": str(parameter.get("unit", "")).strip(),
                    "value": self._value_for(data, plc_id, tag),
                })

            machine_cards.append({
                "id": machine_id,
                "name": name,
                "icon": machine.get("icon", "builtin:factory"),
                "layout": machine.get("layout", "auto"),
                "parameters": parameters,
            })

        data["MachineCards"] = machine_cards
        data["MachineIconLibrary"] = self.icon_library
        return data
