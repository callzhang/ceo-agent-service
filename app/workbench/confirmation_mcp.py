"""Data-only MCP boundary for reviewed workbench actions."""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

from app.leak_check import assert_no_credential_arguments, assert_no_credentials


_INCOMPLETE_DETAILS = "complete reviewed action details are required"


def _validate_argv(argv: object) -> list[str]:
    if (
        not isinstance(argv, list)
        or not argv
        or any(
            not isinstance(argument, str) or not argument.strip()
            for argument in argv
        )
    ):
        raise ValueError(_INCOMPLETE_DETAILS)
    try:
        assert_no_credential_arguments(argv)
    except ValueError as exc:
        raise ValueError(_INCOMPLETE_DETAILS) from exc
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
    ):
        raise ValueError(_INCOMPLETE_DETAILS)
    try:
        assert_no_credentials((target, summary, risk))
    except ValueError as exc:
        raise ValueError(_INCOMPLETE_DETAILS) from exc
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
