"""Enforces the dependency rule from doc 11 §2: imports only go downward.

    api -> ai -> retrieval / db -> config / llm / timeutil

A lower layer importing a higher one is a design error (e.g. the database layer
depending on agent types). This test reads every module's imports, so such a
mistake fails the test suite instead of slipping in.
"""

import ast
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / "src"

# package -> packages it must never import
FORBIDDEN = {
    "src.db": ("src.ai", "src.api"),
    "src.retrieval": ("src.ai", "src.api"),
    "src.config": ("src.ai", "src.api", "src.db", "src.retrieval"),
    "src.llm": ("src.ai", "src.api", "src.db", "src.retrieval"),
    "src.timeutil": ("src.ai", "src.api", "src.db", "src.retrieval", "src.llm"),
    "src.ai": ("src.api",),
}


def module_name(path: Path) -> str:
    parts = path.relative_to(SRC.parent).with_suffix("").parts
    return ".".join(parts[:-1] if parts[-1] == "__init__" else parts)


def imported_modules(path: Path) -> set[str]:
    names = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            names.add(node.module)
    return names


@pytest.mark.parametrize("path", sorted(SRC.rglob("*.py")), ids=lambda p: module_name(p))
def test_imports_only_go_downward(path):
    name = module_name(path)
    for layer, forbidden in FORBIDDEN.items():
        if name == layer or name.startswith(layer + "."):
            bad = sorted(m for m in imported_modules(path) if any(m == f or m.startswith(f + ".") for f in forbidden))
            assert not bad, f"{name} must not import {bad} (doc 11 §2: imports only go downward)"
