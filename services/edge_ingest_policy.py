"""Flow-derived ingestion policy for synthetic trigger signals.

The Edge emits one synthetic sample per configured trigger register so the
server can reconstruct 0->1/1->0 transitions and survive Store & Forward
replay. Those synthetic tags are not ordinary TagMapper names, therefore the
normal Flow storage map is extended here with an explicit TRIGGER_SIGNAL type.
"""

import json

from database import get_company_flow

TRIGGER_SIGNAL_PREFIX = "__TRIGGER_REGISTER_"


def _nodes(company_id):
    flow = get_company_flow(company_id)
    if not flow:
        return {}
    try:
        flow = json.loads(flow) if isinstance(flow, str) else flow
    except Exception:
        return {}
    return (flow.get("drawflow", {}) or {}).get("Home", {}).get("data", {}) or {}


def _config(node):
    data = node.get("data", {}) or {}
    config = data.get("config", data)
    if isinstance(config, dict):
        merged = dict(config)
        merged.update({key: value for key, value in data.items() if key != "config"})
        return merged
    return {}


def _plc_candidates(storage_map, name):
    wanted = str(name or "").strip().lower()
    result = []
    for key in storage_map:
        if isinstance(key, tuple) and len(key) == 2 and str(key[1]).lower() == wanted:
            try:
                result.append(int(key[0]))
            except (TypeError, ValueError):
                pass
    return list(dict.fromkeys(result))


def extend_storage_map(company_id, storage_map):
    if not isinstance(storage_map, dict):
        storage_map = {}

    nodes = _nodes(company_id)
    for node in nodes.values():
        if not isinstance(node, dict) or node.get("name") != "TagMapper":
            continue

        config = _config(node)
        mappings = config.get("mappings", [])
        if not isinstance(mappings, list):
            continue

        for mapping in mappings:
            if not isinstance(mapping, dict):
                continue
            if str(mapping.get("storage", "TIME")).strip().upper() != "TRIGGER":
                continue

            trigger_register = mapping.get("trigger_register")
            if trigger_register in (None, ""):
                continue
            try:
                register = int(float(trigger_register))
            except (TypeError, ValueError):
                continue

            explicit = mapping.get("plc_id", mapping.get("PLC_ID"))
            plc_ids = []
            if explicit not in (None, ""):
                try:
                    plc_ids = [int(explicit)]
                except (TypeError, ValueError):
                    plc_ids = []
            if not plc_ids:
                plc_ids = _plc_candidates(storage_map, mapping.get("name"))

            signal_name = f"{TRIGGER_SIGNAL_PREFIX}{register}".lower()
            for plc_id in plc_ids:
                storage_map[(plc_id, signal_name)] = "TRIGGER_SIGNAL"

    return storage_map


def install():
    from services import edge_ingest

    original = getattr(edge_ingest, "_flow_tag_storage", None)
    if original is None or getattr(original, "_flow_trigger_policy", False):
        return False

    def flow_tag_storage_with_trigger_signals(company_id):
        result = original(company_id)
        return extend_storage_map(company_id, result)

    flow_tag_storage_with_trigger_signals._flow_trigger_policy = True
    edge_ingest._flow_tag_storage = flow_tag_storage_with_trigger_signals
    return True


__all__ = ["extend_storage_map", "install"]
