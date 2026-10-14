"""Static guard: every self.<name> used in SunsynkOptimizer is defined on it.

Catches an edit that drops a method's def line (the body then silently joins
the previous method), which py_compile can't see.
"""

from __future__ import annotations

import ast
from pathlib import Path

OPTIMIZER = Path(__file__).resolve().parents[1] / "custom_components" / "sunsynk_optimizer" / "optimizer.py"


def _class(tree: ast.Module, name: str) -> ast.ClassDef:
    return next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == name)


def test_self_attributes_are_defined():
    cls = _class(ast.parse(OPTIMIZER.read_text()), "SunsynkOptimizer")
    defined = {n.name for n in cls.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
    for node in cls.body:
        if isinstance(node, ast.Assign):
            defined |= {t.id for t in node.targets if isinstance(t, ast.Name)}
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            defined.add(node.target.id)
    used: set[str] = set()
    for node in ast.walk(cls):
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id == "self":
            if isinstance(node.ctx, ast.Store):
                defined.add(node.attr)
            else:
                used.add(node.attr)
    assert used - defined == set()
