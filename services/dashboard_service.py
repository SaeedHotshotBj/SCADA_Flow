import json

from database import get_company_flow, get_connection


def _get_nodes(company_id):
    if company_id is None:
        return {}
    flow_json = get_company_flow(company_id)
    if not flow_json:
        return {}
    try:
        flow = json.loads(flow_json)
    except Exception as exc:
        print("Dashboard flow JSON error:", exc)
        return {}
    return flow.get("drawflow", {}).get("Home", {}).get("data", {})


def _to_plc_id(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _node_config(node):
    data = node.get("data", {}) or {}
    config = data.get("config")
    if isinstance(config, dict):
        merged = dict(config)
        merged.update({k: v for k, v in data.items() if k != "config"})
        return merged
    return data


def _node_connections(nodes, node_id, direction="outputs"):
    node = nodes.get(str(node_id), {})
    section = node.get(direction, {}) if isinstance(node, dict) else {}
    if not isinstance(section, dict):
        return []
    result = []
    for item in section.values():
        if not isinstance(item, dict):
            continue
        connections = item.get("connections", [])
        if not isinstance(connections, list):
            continue
        for connection in connections:
            if isinstance(connection, dict) and connection.get("node") is not None:
                result.append(str(connection["node"]))
    return result


def _company_plc_ids(company_id):
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT PLC_ID FROM PLCs WHERE CompanyID=? ORDER BY PLC_ID",
            (int(company_id),),
        ).fetchall()
        return [int(row["PLC_ID"]) for row in rows]
    finally:
        conn.close()


def _flow_tag_plcs(company_id, nodes):
    """Resolve Flow tag/register -> PLC identity, including PLCReader connections."""
    result = {}
    try:
        company_plc_ids = _company_plc_ids(company_id)
    except Exception as exc:
        print("Dashboard PLC lookup error:", exc)
        return result

    plc_reader_to_id = {}
    used_ids = set()
    fallback_index = 0

    for node_id, node in nodes.items():
        if not isinstance(node, dict) or node.get("name") != "PLCReader":
            continue
        data = _node_config(node)
        plc_id = _to_plc_id(data.get("plc_id", data.get("PLC_ID")))
        if plc_id is None:
            while fallback_index < len(company_plc_ids) and company_plc_ids[fallback_index] in used_ids:
                fallback_index += 1
            if fallback_index < len(company_plc_ids):
                plc_id = company_plc_ids[fallback_index]
                fallback_index += 1
        if plc_id is None or plc_id not in company_plc_ids or plc_id in used_ids:
            continue
        used_ids.add(plc_id)
        plc_reader_to_id[str(node_id)] = plc_id

    for node_id, node in nodes.items():
        if not isinstance(node, dict) or node.get("name") != "TagMapper":
            continue
        data = _node_config(node)
        mappings = data.get("mappings", [])
        if not isinstance(mappings, list):
            continue

        upstream_ids = []
        for source_id, plc_id in plc_reader_to_id.items():
            if str(node_id) in _node_connections(nodes, source_id, "outputs"):
                upstream_ids.append(plc_id)
        upstream_ids = list(dict.fromkeys(upstream_ids))

        for mapping in mappings:
            if not isinstance(mapping, dict):
                continue
            name = str(mapping.get("name", "")).strip()
            if not name:
                continue

            explicit = _to_plc_id(mapping.get("plc_id", mapping.get("PLC_ID")))
            plc_ids = [explicit] if explicit is not None else list(upstream_ids)
            if not plc_ids and len(company_plc_ids) == 1:
                plc_ids = [company_plc_ids[0]]

            keys = {name.lower()}
            register = mapping.get("register")
            normalized_register = None
            if register not in (None, ""):
                try:
                    normalized_register = str(int(float(register)))
                except (TypeError, ValueError):
                    normalized_register = str(register).strip()
                if normalized_register:
                    keys.add(normalized_register.lower())

            for key in keys:
                result.setdefault(key, set()).update(pid for pid in plc_ids if pid in company_plc_ids)

        break

    return result


def _resolve_widget_plc_id(widget, tag_plcs):
    explicit = _to_plc_id(widget.get("plc_id", widget.get("PLC_ID")))
    if explicit is not None:
        return explicit

    candidates = set()
    for raw in (widget.get("tag"), widget.get("configured_tag")):
        key = str(raw or "").strip().lower()
        if not key:
            continue
        candidates.update(tag_plcs.get(key, set()))

    return next(iter(candidates)) if len(candidates) == 1 else None


def _register_to_tag(nodes, tag_plcs):
    lookup = {}
    for node in nodes.values():
        if not isinstance(node, dict) or node.get("name") != "TagMapper":
            continue
        data = _node_config(node)
        mappings = data.get("mappings", [])
        if not isinstance(mappings, list):
            continue
        for item in mappings:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name", "")).strip()
            register = item.get("register")
            if not name or register in (None, ""):
                continue
            try:
                register_key = str(int(float(register)))
            except (TypeError, ValueError):
                register_key = str(register).strip()

            explicit_plc = _to_plc_id(item.get("plc_id", item.get("PLC_ID")))
            if explicit_plc is not None:
                plc_ids = [explicit_plc]
            else:
                plc_ids = list(tag_plcs.get(name.lower(), set()))

            for plc_id in plc_ids:
                lookup[(plc_id, register_key)] = name

        break
    return lookup


def _resolve_machine_tag(raw_tag, plc_id, register_lookup):
    tag = str(raw_tag or "").strip()
    if not tag:
        return ""
    try:
        key_register = str(int(float(tag)))
    except (TypeError, ValueError):
        key_register = tag
    return register_lookup.get((plc_id, key_register), tag)


def get_dashboard_widgets(company_id):
    """Return DashboardOutput widgets and MachineCards with explicit PLC identity."""
    widgets = []
    machines = []
    icon_library = []

    try:
        nodes = _get_nodes(company_id)
        tag_plcs = _flow_tag_plcs(company_id, nodes)
        register_lookup = _register_to_tag(nodes, tag_plcs)

        for node in nodes.values():
            if not isinstance(node, dict):
                continue

            if node.get("name") == "DashboardOutput":
                data = _node_config(node)
                configured = data.get("widgets", [])
                if isinstance(configured, list):
                    for widget in configured:
                        if not isinstance(widget, dict):
                            continue
                        item = dict(widget)
                        item["plc_id"] = _resolve_widget_plc_id(item, tag_plcs)
                        widgets.append(item)

            elif node.get("name") == "MachineCard":
                data = _node_config(node)
                configured_machines = data.get("machines", [])
                configured_icons = data.get("icon_library", [])

                if isinstance(configured_machines, list):
                    for machine in configured_machines:
                        if not isinstance(machine, dict):
                            continue
                        normalized = {
                            "id": str(machine.get("id", "")).strip(),
                            "name": str(machine.get("name", "")).strip(),
                            "icon": machine.get("icon", "builtin:factory"),
                            "layout": machine.get("layout", "auto"),
                            "parameters": [],
                        }
                        if not normalized["id"]:
                            normalized["id"] = "machine_%s" % (len(machines) + 1)
                        if not normalized["name"]:
                            normalized["name"] = normalized["id"]

                        parameters = machine.get("parameters", [])
                        if isinstance(parameters, list):
                            for parameter in parameters:
                                if not isinstance(parameter, dict):
                                    continue
                                plc_id = _to_plc_id(parameter.get("plc_id", parameter.get("PLC_ID")))
                                raw_tag = str(parameter.get("tag", "")).strip()
                                if plc_id is None:
                                    plc_id = _resolve_widget_plc_id(parameter, tag_plcs)
                                resolved_tag = _resolve_machine_tag(raw_tag, plc_id, register_lookup)
                                if not resolved_tag:
                                    continue
                                label = str(parameter.get("label", "")).strip() or resolved_tag
                                unit = str(parameter.get("unit", "")).strip()
                                normalized["parameters"].append({
                                    "label": label,
                                    "tag": resolved_tag,
                                    "configured_tag": raw_tag,
                                    "plc_id": plc_id,
                                    "unit": unit,
                                })
                                widgets.append({
                                    "tag": resolved_tag,
                                    "configured_tag": raw_tag,
                                    "plc_id": plc_id,
                                    "title": normalized["name"] + " / " + label,
                                    "unit": unit,
                                    "_dashboard_type": "machine_parameter",
                                })
                        machines.append(normalized)

                if isinstance(configured_icons, list):
                    icon_library.extend(item for item in configured_icons if isinstance(item, dict))

        for machine in machines:
            widgets.append({"_dashboard_type": "machine", "machine": machine, "icon_library": icon_library})

    except Exception as exc:
        print("Dashboard widget error:", exc)

    return widgets
