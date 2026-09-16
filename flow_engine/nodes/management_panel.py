import ast
import math


class ManagementPanel:
    """Flow-time calculation node used by production/report branches.

    The node never reads the database. It evaluates the calculations configured
    on this Drawflow node against the current payload Tags and production-event
    context, then exposes the resulting values to downstream Flow nodes.
    """

    def __init__(self, config=None):
        self.config = config or {}
        self.calculations = self.config.get("calculations", [])

    @staticmethod
    def _safe_eval(expression, variables):
        tree = ast.parse(str(expression or ""), mode="eval")
        allowed = {
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
            ast.FloorDiv,
            ast.USub,
            ast.UAdd,
        }
        for node in ast.walk(tree):
            if not isinstance(node, tuple(allowed)):
                raise ValueError("Unsupported expression operation")
            if isinstance(node, ast.Constant) and not isinstance(node.value, (int, float)):
                raise ValueError("Expression must contain numeric constants only")
            if isinstance(node, ast.Name) and node.id not in variables:
                raise ValueError(f"Unknown variable: {node.id}")

        value = eval(
            compile(tree, "<flow-management-expression>", "eval"),
            {"__builtins__": {}},
            variables,
        )
        value = float(value)
        if not math.isfinite(value):
            raise ValueError("Expression result is not finite")
        return value

    @staticmethod
    def _add_numeric(values, variables):
        for name, value in (values or {}).items():
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
            alias = "".join(
                char if (char.isalnum() or char == "_") else "_"
                for char in key
            )
            if alias and alias[0].isdigit():
                alias = "_" + alias
            if alias:
                variables.setdefault(alias, number)

    @classmethod
    def _variables(cls, tags, event_context=None):
        variables = {}
        cls._add_numeric(event_context, variables)
        cls._add_numeric(tags, variables)
        return variables

    def execute(self, data=None):
        data = data or {}
        tags = dict(data.get("Tags", {}) or {})
        event_context = data.get("ProductionEvent", {}) or {}
        variables = self._variables(tags, event_context)
        calculation_output = []

        for item in self.calculations:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name", item.get("result_name", ""))).strip()
            expression = str(item.get("expression", "")).strip()
            if not name or not expression:
                continue
            try:
                result = self._safe_eval(expression, variables)
            except Exception as exc:
                print("MANAGEMENT CALCULATION ERROR:", name, expression, exc)
                continue

            tags[name] = result
            variables[name] = result
            alias = "".join(
                char if (char.isalnum() or char == "_") else "_"
                for char in name
            )
            if alias:
                variables[alias] = result

            calculation_output.append({
                "name": name,
                "label": str(item.get("label", name)).strip() or name,
                "tag": name,
                "unit": str(item.get("unit", "")).strip(),
                "source": "management_calculation",
                "allowed_roles": item.get("allowed_roles", ""),
            })

        data["Tags"] = tags
        data["ReportCalculations"] = calculation_output
        return data


__all__ = ["ManagementPanel"]
