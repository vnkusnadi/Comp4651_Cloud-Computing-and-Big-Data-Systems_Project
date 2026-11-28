"""Safe evaluation of schema formulas such as
'(OperatingCashFlow - abs(Capex)) / Revenue_total'. Shared by the engine and the fact sheet."""

from __future__ import annotations

import ast
from typing import Dict, List


def _formula_names(formula: str) -> List[str]:
    tree = ast.parse(formula, mode="eval")
    return sorted({n.id for n in ast.walk(tree) if isinstance(n, ast.Name) and n.id != "abs"})


def _safe_formula(formula: str) -> str:
    # '&' is not valid in a Python name (R&D_expense); swap for evaluation only
    return formula.replace("&", "_and_")


def eval_formula(formula: str, values: Dict[str, float]) -> float:
    """Evaluate a schema formula like '(OperatingCashFlow - abs(Capex)) / Revenue_total'.
    Only names, numbers, + - * /, parentheses and abs() are allowed."""
    expr = _safe_formula(formula)
    tree = ast.parse(expr, mode="eval")
    allowed = (ast.Expression, ast.BinOp, ast.UnaryOp, ast.Name, ast.Load, ast.Constant,
               ast.Add, ast.Sub, ast.Mult, ast.Div, ast.USub, ast.Call)
    for node in ast.walk(tree):
        if not isinstance(node, allowed):
            raise ValueError(f"formula not allowed: {formula}")
        if isinstance(node, ast.Call) and not (isinstance(node.func, ast.Name) and node.func.id == "abs"):
            raise ValueError(f"only abs() may be called: {formula}")
    env = {_safe_formula(k): v for k, v in values.items()}
    return float(eval(compile(tree, "<formula>", "eval"), {"__builtins__": {}, "abs": abs}, env))


