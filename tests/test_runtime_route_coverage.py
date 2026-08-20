from __future__ import annotations

import ast
from pathlib import Path

_DIRECT_CODEX_RUNNER_EXEMPTIONS = {
    # The route adapter is the single service-owned construction boundary.
    "app/codex_runtime_adapter.py",
    # Workbench runs an interactive, user-owned runtime. It does not execute a
    # persisted service operation and therefore has no operation-router parent.
    "app/workbench/codex_runtime.py",
}


class _CodexRunnerConstructorVisitor(ast.NodeVisitor):
    def __init__(self) -> None:
        self.lines: list[int] = []

    def visit_Call(self, node: ast.Call) -> None:
        target = node.func
        if (isinstance(target, ast.Name) and target.id == "CodexRunner") or (
            isinstance(target, ast.Attribute) and target.attr == "CodexRunner"
        ):
            self.lines.append(node.lineno)
        self.generic_visit(node)


def test_business_modules_do_not_construct_codex_runner_directly() -> None:
    offenders: list[tuple[str, list[int]]] = []
    for path in Path("app").rglob("*.py"):
        relative = path.as_posix()
        visitor = _CodexRunnerConstructorVisitor()
        visitor.visit(ast.parse(path.read_text(encoding="utf-8")))
        if visitor.lines and relative not in _DIRECT_CODEX_RUNNER_EXEMPTIONS:
            offenders.append((relative, visitor.lines))

    assert offenders == []


def test_direct_codex_runner_exemptions_are_exact_and_exercised() -> None:
    observed: set[str] = set()
    for relative in _DIRECT_CODEX_RUNNER_EXEMPTIONS:
        path = Path(relative)
        visitor = _CodexRunnerConstructorVisitor()
        visitor.visit(ast.parse(path.read_text(encoding="utf-8")))
        if visitor.lines:
            observed.add(relative)

    assert observed == _DIRECT_CODEX_RUNNER_EXEMPTIONS
