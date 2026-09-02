# ============================================================
# SCADA_FLOW TAG MAPPER NODE
# One TagMapper can serve multiple PLCs.
# Runtime identity is CompanyID + PLC_ID + TagName.
# ============================================================


class TagMapper:

    def __init__(self, config=None, *args, **kwargs):
        self.config = config or {}
        self.mappings = self.config.get("mappings", [])

    @staticmethod
    def _to_int(value):
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    def execute(self, data=None):
        data = data or {}

        registers = data.get("Registers", {}) or data.get("registers", {}) or {}
        plc = data.get("PLC", {}) or {}
        runtime_plc_id = self._to_int(data.get("PLC_ID", plc.get("PLC_ID")))
        company_id = data.get("CompanyID")

        tags = {}
        plc_tags = {}
        active_definitions = []

        for item in self.mappings:
            if not isinstance(item, dict):
                continue

            mapping_plc_id = self._to_int(item.get("plc_id", item.get("PLC_ID")))
            if runtime_plc_id is not None:
                if mapping_plc_id != runtime_plc_id:
                    continue
            elif mapping_plc_id is not None:
                continue

            register = item.get("register")
            name = str(item.get("name", "")).strip()
            if register in (None, "") or not name:
                continue

            register_key = str(register).strip()
            if register_key not in registers:
                try:
                    register_key = str(int(float(register)))
                except (TypeError, ValueError):
                    pass
            if register_key not in registers:
                continue

            value = registers[register_key]
            try:
                value = float(value) * float(item.get("scale", 1))
            except (TypeError, ValueError):
                pass

            tags[name] = value
            identity = "%s:%s" % (runtime_plc_id if runtime_plc_id is not None else "", name.lower())
            plc_tags[identity] = value

            definition = dict(item)
            definition["plc_id"] = mapping_plc_id
            definition["TagIdentity"] = {
                "CompanyID": company_id,
                "PLC_ID": mapping_plc_id,
                "TagName": name,
            }
            active_definitions.append(definition)

        data["PLC_ID"] = runtime_plc_id
        if isinstance(data.get("PLC"), dict):
            data["PLC"]["PLC_ID"] = runtime_plc_id

        data["Tags"] = tags
        data["PLC_Tags"] = plc_tags
        data["TagDefinitions"] = active_definitions

        print()
        print("TAG MAPPER: PLC_ID=", runtime_plc_id)
        print(tags)
        print()

        return data
