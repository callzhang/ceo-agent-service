"""Data-only MCP boundary for reviewed workbench actions."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

from app.leak_check import contains_credential, is_sensitive_field_name


_INCOMPLETE_DETAILS = "complete reviewed action details are required"


def _has_sensitive_argument_name(argv: Sequence[str]) -> bool:
    for argument in argv:
        candidate = argument.lstrip("-")
        name = candidate.split("=", 1)[0].split(":", 1)[0]
        if is_sensitive_field_name(name):
            return True
        for separator in ("=", ":"):
            if separator in candidate:
                _, value = candidate.split(separator, 1)
                if _structured_value_has_sensitive_name(value):
                    return True
                break
        if _structured_value_has_sensitive_name(argument):
            return True
    return False


def _structured_value_has_sensitive_name(value: str) -> bool:
    stripped = value.strip()
    if not stripped or stripped[0] not in "[{":
        return False
    try:
        structured = json.loads(stripped)
    except (TypeError, ValueError):
        return False
    return _mapping_has_sensitive_name(structured)


def _mapping_has_sensitive_name(value: Any) -> bool:
    if isinstance(value, Mapping):
        return any(
            is_sensitive_field_name(key)
            or _mapping_has_sensitive_name(item)
            for key, item in value.items()
            if isinstance(key, str)
        )
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return any(_mapping_has_sensitive_name(item) for item in value)
    return False


def _validate_argv(argv: object) -> list[str]:
    if (
        not isinstance(argv, list)
        or not argv
        or any(not isinstance(argument, str) or not argument for argument in argv)
        or any(contains_credential(argument) for argument in argv)
        or _has_sensitive_argument_name(argv)
    ):
        raise ValueError(_INCOMPLETE_DETAILS)
    return list(argv)


server = FastMCP(
    "workbench_confirmation",
    instructions=(
        "Describe a reviewed external action for the workbench. This server "
        "records proposal data and never executes the action."
    ),
)


@server.tool(
    name="request_reviewed_action",
    annotations=ToolAnnotations(readOnlyHint=True),
)
def request_reviewed_action(
    argv: list[str],
    target: str,
    summary: str,
    risk: str,
) -> dict[str, object]:
    """Return a safe proposal for later human review without executing it."""
    if (
        not isinstance(target, str)
        or not isinstance(summary, str)
        or not isinstance(risk, str)
        or not target.strip()
        or not summary.strip()
        or not risk.strip()
        or contains_credential(target)
        or contains_credential(summary)
        or contains_credential(risk)
    ):
        raise ValueError(_INCOMPLETE_DETAILS)
    return {
        "kind": "reviewed_cli",
        "argv": _validate_argv(argv),
        "target": target.strip(),
        "summary": summary.strip(),
        "risk": risk.strip(),
        "executed": False,
    }


def main() -> None:
    server.run(transport="stdio")


if __name__ == "__main__":
    main()
