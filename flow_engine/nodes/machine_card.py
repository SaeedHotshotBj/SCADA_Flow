# =====================================================
# SCADA_FLOW MACHINE CARD NODE
# CONFIGURABLE INDUSTRIAL MACHINE DASHBOARD CARDS
# =====================================================


class MachineCard:
    """
    Converts live flow Tags into configured machine-card data.

    The node does not read PLC values itself. It only maps the configured
    machine parameters to the tags already produced by upstream nodes.
    """

    def __init__(self, config=None):
        self.config = config or {}
        self.machines = self.config.get("machines", [])
        self.icon_library = self.config.get("icon_library", [])

    def execute(self, data=None):
        if data is None:
            data = {}

        tags = data.get("Tags", {})
        if not isinstance(tags, dict):
            tags = {}

        machine_cards = []

        for machine in self.machines:
            if not isinstance(machine, dict):
                continue

            machine_id = str(machine.get("id", "")).strip()
            name = str(machine.get("name", "")).strip()

            if not machine_id:
                machine_id = "machine_%s" % (len(machine_cards) + 1)
            if not name:
                name = machine_id

            parameters = []

            for parameter in machine.get("parameters", []):
                if not isinstance(parameter, dict):
                    continue

                tag = str(parameter.get("tag", "")).strip()
                if not tag:
                    continue

                value = tags.get(tag)
                parameters.append({
                    "label": str(parameter.get("label", "")).strip() or tag,
                    "tag": tag,
                    "unit": str(parameter.get("unit", "")).strip(),
                    "value": value,
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
