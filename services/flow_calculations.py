"""Flow-wide numeric calculation helpers.

Report and Management keep their own calculation definitions, while sharing
only the safe evaluator that can resolve values produced by connected
ExpressionNode stages.
"""

import ast
import json
import math

from database import get_company_flow


def _flow_nodes(company_id):
    flow = get_company_flow(company_id)
    if not flow:
        return {}
    if isinstance(flow, str):
        try:
            flow = json.loads(flow)
        except Exception:
            return {}
    return flow.get("drawflow", {}).get("Home", {}).get("data", {}) or {}


def safe_numeric_eval(expression, variables):
    tree = ast.parse(str(expression or ""), mode="eval")
    allowed_nodes = {
        ast.Expression,
        ast.Constant,
        ast.Name,
        ast.Load,
        ast.BinOp,
        ast.UnaryOp,
        ast.Add,
        ast.Sub,
        ast.Mult,
        ast.Div,
        ast.Mod,
        ast.Pow,
        ast.USub,
        ast.UAdd,
        ast.FloorDiv,
    }
    for node in ast.walk(tree):
        if not isinstance(node, tuple(allowed_nodes)):
            raise ValueError("Unsupported expression operation")
        if isinstance(node, ast.Constant) and not isinstance(node.value, (int, float)):
            raise ValueError("Expression must contain numeric constants only")
        if isinstance(node, ast.Name) and node.id not in variables:
            raise ValueError(f"Unknown variable: {node.id}")
    value = eval(
        compile(tree, "<flow-calculation>", "eval"),
        {"__builtins__": {}},
        variables,
    )
    value = float(value)
    if not math.isfinite(value):
        raise ValueError("Expression result is not finite")
    return value


def _alias(name):
    value = "".join(
        char if (char.isalnum() or char == "_") else "_"
        for char in str(name or "").strip()
    )
    if value and value[0].isdigit():
        value = "_" + value
    return value


def _numeric_variables(tags):
    variables = {}
    for name, value in (tags or {}).items():
        try:
            number = float(value)
        except (TypeError, ValueError):
            continue
        if not math.isfinite(number):
            continue
        key = str(name).strip()
        if not key:
            continue
        variables[key] = number
        alias = _alias(key)
        if alias:
            variables.setdefault(alias, number)
    return variables


def _node_config(node):
    data = node.get("data", {}) if isinstance(node, dict) else {}
    config = data.get("config", data) if isinstance(data, dict) else {}
    return config if isinstance(config, dict) else {}


def _ordered_node_ids(nodes):
    node_ids = {str(key) for key in nodes}
    children = {node_id: [] for node_id in node_ids}
    indegree = {node_id: 0 for node_id in node_ids}

    for node_id, node in nodes.items():
        source_id = str(node_id)
        if not isinstance(node, dict):
            continue
        for output in (node.get("outputs", {}) or {}).values():
            if not isinstance(output, dict):
                continue
            for connection in output.get("connections", []) or []:
                if not isinstance(connection, dict):
                    continue
                target_id = str(connection.get("node", ""))
                if target_id not in node_ids or target_id in children[source_id]:
                    continue
                children[source_id].append(target_id)
                indegree[target_id] += 1

    queue = [node_id for node_id in node_ids if indegree[node_id] == 0]
    queue.sort()
    order = []
    while queue:
        current = queue.pop(0)
        order.append(current)
        for child in children[current]:
            indegree[child] -= 1
            if indegree[child] == 0:
                queue.append(child)
        queue.sort()

    if len(order) < len(node_ids):
        order.extend(sorted(node_id for node_id in node_ids if node_id not in set(order)))
    return order


def enrich_with_flow_expressions(company_id, tags):
    """Return a copy of tags plus numeric ExpressionNode outputs from the Flow."""
    result = dict(tags or {})
    variables = _numeric_variables(result)

    nodes = _flow_nodes(company_id)
    for node_id in _ordered_node_ids(nodes):
        node = nodes.get(node_id)
        if not isinstance(node, dict) or node.get("name") != "ExpressionNode":
            continue
        expressions = _node_config(node).get("expressions", [])
        if not isinstance(expressions, list):
            continue
        for item in expressions:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name", "")).strip()
            expression = str(item.get("expression", "")).strip()
            if not name or not expression:
                continue
            try:
                value = safe_numeric_eval(expression, variables)
            except Exception as exc:
                print(
                    "FLOW EXPRESSION RESOLVE ERROR:",
                    "Node=", node_id,
                    "Name=", name,
                    "Expression=", expression,
                    "Error=", exc,
                )
                continue
            result[name] = value
            variables[name] = value
            alias = _alias(name)
            if alias:
                variables[alias] = value

    return result


def evaluate_calculations(calculations, tags, company_id=None, duration_seconds=0.0):
    """Evaluate a section's own calculations without sharing definitions."""
    variables = _numeric_variables(tags)
    duration = float(duration_seconds or 0.0)
    variables.update(
        {
            "DurationSeconds": duration,
            "WorkTimeSeconds": duration,
            "WorkTimeMinutes": duration / 60.0,
            "WorkTimeHours": duration / 3600.0,
            "ProductionTimeSeconds": duration,
        }
    )

    if company_id is not None:
        flow_tags = enrich_with_flow_expressions(company_id, tags)
        variables.update(_numeric_variables(flow_tags))

    results = {}
    for item in calculations or []:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name", item.get("result_name", ""))).strip()
        expression = str(item.get("expression", "")).strip()
        if not name or not expression:
            continue
        try:
            value = safe_numeric_eval(expression, variables)
        except Exception as exc:
            print(
                "FLOW CALCULATION ERROR:",
                "Name=", name,
                "Expression=", expression,
                "Error=", exc,
            )
            continue
        results[name] = value
        variables[name] = value
        alias = _alias(name)
        if alias:
            variables[alias] = value
    return results


__all__ = [
    "safe_numeric_eval",
    "enrich_with_flow_expressions",
    "evaluate_calculations",
]
