import pytest
from pydantic import ValidationError

from app.universal_plan import (
    DependencyName,
    PlannedAction,
    PlannedActionKind,
    UniversalAudit,
    UniversalPlan,
)


def make_plan(*actions: PlannedAction) -> UniversalPlan:
    return UniversalPlan(
        task_kind="message_handling",
        reason="Handle the incoming request",
        actions=list(actions),
        audit=UniversalAudit(
            summary="Reply and preserve the decision",
            documents=[{"title": "Decision note"}],
            confidence=0.9,
        ),
    )


def test_reply_and_memory_plan_validates_and_converts_enums() -> None:
    plan = make_plan(
        PlannedAction(
            kind="send_reply",
            reason="Answer the requester",
            payload={"text": "The request is complete."},
        ),
        PlannedAction(
            kind=PlannedActionKind.MEMORY_WRITE,
            reason="Persist the decision",
            payload={"content": "The request was completed."},
        ),
    )

    assert isinstance(plan.actions[0].kind, PlannedActionKind)
    assert plan.actions[0].kind is PlannedActionKind.SEND_REPLY
    assert plan.dependencies == []
    assert DependencyName("memory") is DependencyName.MEMORY
    assert plan.planner_version == "2026-07-20"


def test_empty_send_reply_text_is_rejected() -> None:
    with pytest.raises(
        ValidationError, match="send_reply payload.text must be non-empty"
    ):
        PlannedAction(
            kind=PlannedActionKind.SEND_REPLY,
            reason="Answer the requester",
            payload={"text": "  "},
        )


def test_blocked_action_with_dws_authorization_blocker_validates() -> None:
    action = PlannedAction(
        kind="blocked",
        reason="DWS authorization is required",
        payload={"blocker": "dws_authorization_required"},
    )

    assert action.kind is PlannedActionKind.BLOCKED
    assert action.payload["blocker"] == "dws_authorization_required"


def test_audit_summary_and_action_reason_must_be_non_empty_after_trimming() -> None:
    with pytest.raises(ValidationError):
        UniversalAudit(summary="  ", confidence=0.5)
    with pytest.raises(ValidationError):
        PlannedAction(kind="no_reply", reason="\t")


def test_mail_reply_requires_mailbox_message_and_content() -> None:
    with pytest.raises(
        ValidationError, match="mail_reply requires target.mailbox"
    ):
        PlannedAction(
            kind="mail_reply",
            reason="Reply to the email",
            target={"message_id": "message-1"},
            payload={"content": "Done"},
        )


def test_oa_approval_requires_supported_action_and_remark() -> None:
    with pytest.raises(
        ValidationError, match="oa_approval payload.action"
    ):
        PlannedAction(
            kind="oa_approval",
            reason="Process approval",
            payload={"action": "approve", "remark": "Reviewed"},
        )
    with pytest.raises(ValidationError, match="oa_approval payload.remark"):
        PlannedAction(
            kind="oa_approval",
            reason="Process approval",
            payload={"action": "同意", "remark": "  "},
        )


def test_plan_requires_actions_and_audit_and_bounds_confidence() -> None:
    with pytest.raises(ValidationError):
        UniversalPlan(
            task_kind="message_handling",
            reason="Handle the request",
            actions=[],
            audit=UniversalAudit(summary="Summary", confidence=1.1),
        )
