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

    @staticmethod
    def _normalize_tag(value):
        return str(value or "").strip().lower()

    def _value_for(self, data, plc_id, tag):
        plc_id = self._plc_id(plc_id)
        tag = str(tag or "").strip()
        normal_tag = self._normalize_tag(tag)

        plc_tags = data.get("PLC_Tags", {}) or {}
        if plc_id is not None and tag:
            key = "%s:%s" % (plc_id, normal_tag)
            if key in plc_tags:
                return plc_tags[key]

            for identity, value in plc_tags.items():
                if not isinstance(identity, str) or ":" not in identity:
                    continue
                identity_plc, identity_tag = identity.split(":", 1)
                try:
                    if int(identity_plc) == plc_id and self._normalize_tag(identity_tag) == normal_tag:
                        return value
                except (TypeError, ValueError):
                    continue

        tags = data.get("Tags", {}) or {}
        if tag in tags:
            return tags[tag]
        for name, value in tags.items():
            if self._normalize_tag(name) == normal_tag:
                return value

        definitions = data.get("TagDefinitions", []) or []
        if isinstance(definitions, list):
            for definition in definitions:
                if not isinstance(definition, dict):
                    continue
                definition_plc = self._plc_id(
                    definition.get("plc_id", definition.get("PLC_ID"))
                )
                if plc_id is not None and definition_plc not in (None, plc_id):
                    continue

                definition_name = str(definition.get("name", "")).strip()
                definition_register = str(definition.get("register", "")).strip()

                matches_name = self._normalize_tag(definition_name) == normal_tag
                matches_register = definition_register == tag
                if not (matches_name or matches_register):
                    try:
                        matches_register = str(int(float(definition_register))) == str(int(float(tag)))
                    except (TypeError, ValueError):
                        pass

                if matches_name or matches_register:
                    if definition_name:
                        if plc_id is not None:
                            key = "%s:%s" % (plc_id, self._normalize_tag(definition_name))
                            if key in plc_tags:
                                return plc_tags[key]
                        if definition_name in tags:
                            return tags[definition_name]
                        for name, value in tags.items():
                            if self._normalize_tag(name) == self._normalize_tag(definition_name):
                                return value
                    break

        return None

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
                configured_tag = str(parameter.get("tag", "")).strip()
                if not configured_tag:
                    continue
                plc_id = self._plc_id(
                    parameter.get("plc_id", parameter.get("PLC_ID", data.get("PLC_ID")))
                )
                resolved_tag = configured_tag
                definitions = data.get("TagDefinitions", []) or []
                if isinstance(definitions, list):
                    for definition in definitions:
                        if not isinstance(definition, dict):
                            continue
                        definition_plc = self._plc_id(
                            definition.get("plc_id", definition.get("PLC_ID"))
                        )
                        if plc_id is not None and definition_plc not in (None, plc_id):
                            continue
                        definition_name = str(definition.get("name", "")).strip()
                        definition_register = str(definition.get("register", "")).strip()
                        matches = self._normalize_tag(definition_name) == self._normalize_tag(configured_tag)
                        if not matches:
                            matches = definition_register == configured_tag
                        if not matches:
                            try:
                                matches = str(int(float(definition_register))) == str(int(float(configured_tag)))
                            except (TypeError, ValueError):
                                matches = False
                        if matches and definition_name:
                            resolved_tag = definition_name
                            break

                parameters.append({
                    "label": str(parameter.get("label", "")).strip() or resolved_tag,
                    "tag": resolved_tag,
                    "configured_tag": configured_tag,
                    "plc_id": plc_id,
                    "unit": str(parameter.get("unit", "")).strip(),
                    "value": self._value_for(data, plc_id, resolved_tag),
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
