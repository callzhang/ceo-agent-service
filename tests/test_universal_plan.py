import pytest
from pydantic import ValidationError

from app.universal_plan import (
    DependencyName,
    PlannedAction,
    PlannedActionKind,
    UniversalAudit,
    UniversalPlan,
)
from app.dingtalk_models import SensitivityKind


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
        sensitivity_kind="general",
        payload={"text": "The request is complete."},
    )


def test_reply_and_memory_plan_validates_and_converts_enums() -> None:
    plan = make_plan(
        PlannedAction(
            kind="send_reply",
            reason="Answer the requester",
            sensitivity_kind="general",
            payload={"text": "The request is complete."},
        ),
        PlannedAction(
            kind="memory_write",
            reason="Persist the decision",
            payload={"data": "The request was completed.", "type": "text"},
        ),
    )

    assert isinstance(plan.actions[0].kind, PlannedActionKind)
    assert plan.actions[0].kind is PlannedActionKind.SEND_REPLY
    assert plan.actions[1].kind is PlannedActionKind.MEMORY_WRITE
    assert plan.dependencies == []
    assert DependencyName("memory") is DependencyName.MEMORY
    assert plan.planner_version == "2026-07-20"


@pytest.mark.parametrize(
    "extra_field",
    ["created_at", "source_description", "user_id", "graph_id"],
)
def test_memory_write_rejects_planner_controlled_identity_fields(
    extra_field: str,
) -> None:
    with pytest.raises(ValidationError, match="memory_write payload"):
        PlannedAction(
            kind="memory_write",
            reason="Persist the durable decision",
            payload={
                "data": "The launch decision is approved.",
                "type": "text",
                extra_field: "planner-controlled",
            },
        )


@pytest.mark.parametrize("memory_type", ["text", "message"])
def test_memory_write_accepts_only_current_client_fields(memory_type: str) -> None:
    action = PlannedAction(
        kind="memory_write",
        reason="Persist the durable decision",
        payload={"data": "The launch decision is approved.", "type": memory_type},
    )

    assert action.payload == {
        "data": "The launch decision is approved.",
        "type": memory_type,
    }


@pytest.mark.parametrize(
    "payload",
    [
        {"data": "", "type": "text"},
        {"data": "x", "type": "json"},
        {"data": "x" * 2001, "type": "text"},
        {"data": "source: /private/var/runtime.log", "type": "text"},
        {
            "data": (
                "aB3dE5gH7jK9mN2pQ4sT6vW8yZ1cF3hJ5lM7oR9uX2zC4eG6iL8nP1qS3tV5xY7"
            ),
            "type": "text",
        },
        {"data": "\n".join(f"log line {index}" for index in range(13)), "type": "text"},
    ],
)
def test_memory_write_rejects_invalid_or_sensitive_content(payload) -> None:
    with pytest.raises(ValidationError, match="memory_write payload"):
        PlannedAction(
            kind="memory_write",
            reason="Persist the durable decision",
            payload=payload,
        )


@pytest.mark.parametrize(
    "data",
    [
        'Traceback (most recent call last):\n  File "worker.py", line 10',
        "RuntimeError: connection reset",
        "Exception: backend unavailable",
        "stack trace: at executeMemory (worker.js:10:2)",
        "processing",
        "status: pending",
        "task is temporarily unavailable",
        "一次性错误：刚才网络失败",
        "api_key=abc123",
        "Authorization: Bearer abc123",
        "token: short-secret",
        "password=hunter2",
        "-----BEGIN PRIVATE KEY-----",
    ],
)
def test_memory_write_rejects_transient_errors_and_short_secrets(data: str) -> None:
    with pytest.raises(ValidationError, match="memory_write payload"):
        PlannedAction(
            kind="memory_write",
            reason="Persist durable state",
            payload={"data": data, "type": "text"},
        )


@pytest.mark.parametrize(
    "data",
    [
        "OPENAI_API_KEY=sk-proj-abcDEF1234567890",
        "sk-abcDEF1234567890",
        "AWS_ACCESS_KEY_ID=AKIAIOSFODNN7EXAMPLE",
        "2026-07-21T09:15:00Z ERROR memory_write failed: connection reset",
        "Memory write failed and is retrying because the backend is unavailable.",
    ],
)
def test_memory_write_rejects_reviewer_unsafe_examples(data: str) -> None:
    with pytest.raises(ValidationError, match="memory_write payload"):
        PlannedAction(
            kind="memory_write",
            reason="Persist durable state",
            payload={"data": data, "type": "text"},
        )


@pytest.mark.parametrize(
    "data",
    [
        "api_key: 'abcd1234'",
        "password = correct-horse",
        "token=short-token",
        "private_key: key-material",
        "Bearer abc.def.ghi",
        "ASIAIOSFODNN7EXAMPLE",
    ],
)
def test_memory_write_rejects_explicit_credential_shapes(data: str) -> None:
    with pytest.raises(ValidationError, match="memory_write payload"):
        PlannedAction(
            kind="memory_write",
            reason="Persist durable state",
            payload={"data": data, "type": "text"},
        )


@pytest.mark.parametrize(
    "data",
    [
        "Derek prefers using Codex workspaces for long-running code reviews.",
        "The source selection preference is to prioritize primary documentation.",
        "The team prefers temporary feature branches for isolated development.",
        "The durable API design avoids planner-controlled identity fields.",
        "Derek prefers explicit error handling in service integrations.",
        "The project decision is to retry connection resets with exponential backoff.",
        "The API key rotation policy requires quarterly review.",
        "The incident policy retries unavailable services with exponential backoff.",
    ],
)
def test_memory_write_allows_normal_durable_preferences(data: str) -> None:
    action = PlannedAction(
        kind="memory_write",
        reason="Persist a durable preference",
        payload={"data": data, "type": "text"},
    )

    assert action.payload["data"] == data


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
            sensitivity_kind="general",
            payload={"text": "  "},
        )


@pytest.mark.parametrize(
    "kind",
    [
        PlannedActionKind.SEND_REPLY,
        PlannedActionKind.ASK_CLARIFYING_QUESTION,
    ],
)
def test_reply_actions_require_explicit_sensitivity_kind(
    kind: PlannedActionKind,
) -> None:
    with pytest.raises(ValidationError, match="sensitivity_kind is required"):
        PlannedAction(
            kind=kind,
            reason="Answer the requester",
            payload={"text": "Done."},
        )


def test_reply_action_preserves_permission_metadata() -> None:
    action = PlannedAction(
        kind="send_reply",
        reason="Answer a candidate question",
        sensitivity_kind="external_candidate",
        personnel_subject_user_id="subject-user",
        candidate_context_known=True,
        candidate_department_ids=["dept-1", "dept-2"],
        payload={"text": "Candidate response."},
    )

    assert action.sensitivity_kind is SensitivityKind.EXTERNAL_CANDIDATE
    assert action.personnel_subject_user_id == "subject-user"
    assert action.candidate_context_known is True
    assert action.candidate_department_ids == ["dept-1", "dept-2"]


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
            {"mailbox": "mailbox", "message_id": "message-1", "subject": "S"},
            {},
            "payload.content",
        ),
        (
            {"mailbox": "mailbox", "message_id": "message-1", "subject": "S"},
            {"content": "  "},
            "payload.content",
        ),
        (
            {"mailbox": "mailbox", "message_id": "message-1", "subject": "  "},
            {"content": "Done"},
            "target.subject",
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
        target={
            "mailbox": "derek@example.com",
            "message_id": "message-1",
            "subject": "Subject",
        },
        payload={"content": "Done"},
    )

    assert action.kind is PlannedActionKind.MAIL_REPLY


def test_message_reaction_rejects_unsupported_reaction_type() -> None:
    with pytest.raises(ValidationError, match="reaction_type"):
        PlannedAction(
            kind="dws_message_reaction",
            reason="React to the immutable trigger.",
            target={"conversation_id": "cid-1", "message_id": "msg-1"},
            payload={"reaction_type": "gif", "emoji": "👍"},
        )


@pytest.mark.parametrize("response_status", ["accepted", "tentative", "declined"])
def test_calendar_response_with_supported_status_validates(response_status: str) -> None:
    action = PlannedAction(
        kind="calendar_response",
        reason="Respond to the trusted invitation.",
        target={"event_id": "event-1"},
        payload={"response_status": response_status},
    )

    assert action.kind is PlannedActionKind.CALENDAR_RESPONSE


@pytest.mark.parametrize("response_status", ["", "yes", "ACCEPTED", None])
def test_calendar_response_rejects_unsupported_status(response_status) -> None:
    with pytest.raises(ValidationError, match="calendar_response"):
        PlannedAction(
            kind="calendar_response",
            reason="Respond to the trusted invitation.",
            target={"event_id": "event-1"},
            payload={"response_status": response_status},
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


def test_oa_approval_with_supported_action_and_remark_validates() -> None:
    action = PlannedAction(
        kind="oa_approval",
        reason="Process approval",
        payload={"action": "同意", "remark": "Reviewed and approved."},
    )

    assert action.kind is PlannedActionKind.OA_APPROVAL


def test_oa_return_requires_activity_and_supported_revert_action() -> None:
    with pytest.raises(ValidationError, match="target_activity_id"):
        PlannedAction(
            kind="oa_approval",
            reason="Return for correction",
            payload={"action": "退回", "remark": "请补充材料"},
        )
    with pytest.raises(ValidationError, match="revert_action"):
        PlannedAction(
            kind="oa_approval",
            reason="Return for correction",
            payload={
                "action": "退回",
                "remark": "请补充材料",
                "target_activity_id": "activity-1",
                "revert_action": "INVALID",
            },
        )

    action = PlannedAction(
        kind="oa_approval",
        reason="Return for correction",
        payload={
            "action": "退回",
            "remark": "请补充材料",
            "target_activity_id": "activity-1",
            "revert_action": "REVERT_FOR_APPROVAL",
        },
    )
    assert action.payload["target_activity_id"] == "activity-1"


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
