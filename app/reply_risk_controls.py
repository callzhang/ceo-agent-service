from __future__ import annotations

from collections.abc import Sequence

from app.agent_contracts import ExternalBoundary
from app.native_cli_metadata import dingtalk_message_text


_CONTROL_FIELDS: tuple[tuple[str, str], ...] = (
    ("allowed_now", "allowed_now"),
    ("concrete_risk", "concrete_risk"),
    ("do_not", "do_not"),
    ("decision_boundary", "decision_boundary"),
)


def missing_external_boundary_controls(
    message_body: str,
    boundary: ExternalBoundary,
) -> tuple[str, ...]:
    """Return control fields whose exact text is absent from ``message_body``."""

    body = message_body.strip()
    if not body:
        return tuple(field for field, _ in _CONTROL_FIELDS)
    return tuple(
        field
        for field, attribute in _CONTROL_FIELDS
        if getattr(boundary, attribute).strip() not in body
    )


def missing_external_boundary_controls_from_argv(
    argv: Sequence[str] | None,
    boundary: ExternalBoundary,
) -> tuple[str, ...]:
    """Validate the exact text carried by a reviewed DingTalk send command."""

    if argv is None:
        return ("message_body_missing",)
    message_body = dingtalk_message_text(tuple(argv))
    if not message_body:
        return ("message_body_missing",)
    return missing_external_boundary_controls(message_body, boundary)

