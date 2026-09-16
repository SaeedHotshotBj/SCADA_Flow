import ast
import math


class ExpressionNode:
    """Flow-configured numeric calculation node."""

    def __init__(self, config=None):
        self.config = config or {}
        self.expressions = self.config.get("expressions", [])

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
            compile(tree, "<flow-expression>", "eval"),
            {"__builtins__": {}},
            variables,
        )
        value = float(value)
        if not math.isfinite(value):
            raise ValueError("Expression result is not finite")
        return value

    @staticmethod
    def _variables(tags):
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
            alias = "".join(char if (char.isalnum() or char == "_") else "_" for char in key)
            if alias and alias[0].isdigit():
                alias = "_" + alias
            if alias:
                variables.setdefault(alias, number)
        return variables

    def execute(self, data=None):
        data = data or {}
        tags = dict(data.get("Tags", {}) or {})
        variables = self._variables(tags)

        for item in self.expressions:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name", "")).strip()
            expression = str(item.get("expression", "")).strip()
            if not name or not expression:
                continue

            try:
                result = self._safe_eval(expression, variables)
            except Exception as exc:
                print("EXPRESSION ERROR:", name, expression, exc)
                continue

            tags[name] = result
            variables[name] = result
            alias = "".join(
                char if (char.isalnum() or char == "_") else "_"
                for char in name
            )
            if alias:
                variables[alias] = result

        data["Tags"] = tags
        return data


__all__ = ["ExpressionNode"]
