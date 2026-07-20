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


def make_reply_action() -> PlannedAction:
    return PlannedAction(
        kind="send_reply",
        reason="Answer the requester",
        payload={"text": "The request is complete."},
    )


def test_reply_and_memory_plan_validates_and_converts_enums() -> None:
    plan = make_plan(
        PlannedAction(
            kind="send_reply",
            reason="Answer the requester",
            payload={"text": "The request is complete."},
        ),
        PlannedAction(
            kind="memory_write",
            reason="Persist the decision",
            payload={"content": "The request was completed."},
        ),
    )

    assert isinstance(plan.actions[0].kind, PlannedActionKind)
    assert plan.actions[0].kind is PlannedActionKind.SEND_REPLY
    assert plan.actions[1].kind is PlannedActionKind.MEMORY_WRITE
    assert plan.dependencies == []
    assert DependencyName("memory") is DependencyName.MEMORY
    assert plan.planner_version == "2026-07-20"


def test_dependency_and_action_enum_members_are_exact() -> None:
    assert [member.value for member in DependencyName] == [
        "dws",
        "lark",
        "exa",
        "memory",
        "xiaoqing_interview",
        "mail",
        "calendar",
    ]
    assert [member.value for member in PlannedActionKind] == [
        "send_reply",
        "ask_clarifying_question",
        "oa_approval",
        "mail_reply",
        "calendar_response",
        "dws_markdown_document_reply",
        "dws_message_reaction",
        "memory_write",
        "no_reply",
        "handoff_to_human",
        "blocked",
        "stop_with_error",
    ]


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


@pytest.mark.parametrize(
    ("target", "payload", "message"),
    [
        ({"message_id": "message-1"}, {"content": "Done"}, "target.mailbox"),
        (
            {"mailbox": "  ", "message_id": "message-1"},
            {"content": "Done"},
            "target.mailbox",
        ),
        ({"mailbox": "mailbox"}, {"content": "Done"}, "target.message_id"),
        (
            {"mailbox": "mailbox", "message_id": "  "},
            {"content": "Done"},
            "target.message_id",
        ),
        (
            {"mailbox": "mailbox", "message_id": "message-1"},
            {},
            "payload.content",
        ),
        (
            {"mailbox": "mailbox", "message_id": "message-1"},
            {"content": "  "},
            "payload.content",
        ),
    ],
)
def test_mail_reply_rejects_missing_or_blank_required_fields(
    target: dict[str, str], payload: dict[str, str], message: str
) -> None:
    with pytest.raises(
        ValidationError, match=f"mail_reply requires {message}"
    ):
        PlannedAction(
            kind="mail_reply",
            reason="Reply to the email",
            target=target,
            payload=payload,
        )


def test_mail_reply_with_required_fields_validates() -> None:
    action = PlannedAction(
        kind="mail_reply",
        reason="Reply to the email",
        target={"mailbox": "derek@example.com", "message_id": "message-1"},
        payload={"content": "Done"},
    )

    assert action.kind is PlannedActionKind.MAIL_REPLY


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


def test_oa_approval_with_supported_action_and_remark_validates() -> None:
    action = PlannedAction(
        kind="oa_approval",
        reason="Process approval",
        payload={"action": "同意", "remark": "Reviewed and approved."},
    )

    assert action.kind is PlannedActionKind.OA_APPROVAL


def test_empty_actions_are_rejected_with_valid_audit() -> None:
    with pytest.raises(ValidationError):
        UniversalPlan(
            task_kind="message_handling",
            reason="Handle the request",
            actions=[],
            audit=UniversalAudit(summary="Summary", confidence=0.5),
        )


def test_missing_audit_is_rejected() -> None:
    with pytest.raises(ValidationError, match="audit"):
        UniversalPlan(
            task_kind="message_handling",
            reason="Handle the request",
            actions=[make_reply_action()],
        )


@pytest.mark.parametrize("confidence", [-0.1, 1.1])
def test_confidence_out_of_bounds_is_rejected(confidence: float) -> None:
    with pytest.raises(ValidationError):
        UniversalAudit(summary="Summary", confidence=confidence)


@pytest.mark.parametrize(
    ("model", "fields"),
    [
        (UniversalAudit, {"summry": "Summary"}),
        (PlannedAction, {"reasn": "Answer the requester"}),
        (UniversalPlan, {"dependecies": []}),
        (UniversalPlan, {"planner_verison": "2026-07-20"}),
    ],
)
def test_unknown_fields_are_rejected(
    model: type[object], fields: dict[str, object]
) -> None:
    valid_values: dict[type[object], dict[str, object]] = {
        UniversalAudit: {"summary": "Summary", "confidence": 0.5},
        PlannedAction: {
            "kind": "no_reply",
            "reason": "No response is needed",
        },
        UniversalPlan: {
            "task_kind": "message_handling",
            "reason": "Handle the request",
            "actions": [make_reply_action()],
            "audit": UniversalAudit(summary="Summary", confidence=0.5),
        },
    }

    with pytest.raises(ValidationError, match="extra_forbidden"):
        model(**valid_values[model], **fields)
