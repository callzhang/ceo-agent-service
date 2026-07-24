import pytest

from app.universal_plan import (
    PlannedAction,
    PlannedActionKind,
    UniversalAudit,
    UniversalPlan,
)
from app.universal_validator import (
    DependencyStatus,
    UniversalValidationContext,
    UniversalValidator,
    ValidatedUniversalPlan,
)


CONVERSATION_ID = "conversation-1"
TRIGGER_MESSAGE_ID = "trigger-1"


def make_plan(
    *actions: PlannedAction, dependencies: tuple[str, ...] = ()
) -> UniversalPlan:
    return UniversalPlan(
        task_kind="message_handling",
        reason="Handle the incoming request",
        dependencies=list(dependencies),
        actions=list(actions),
        audit=UniversalAudit(
            summary="Validate the proposed actions",
            confidence=0.9,
        ),
    )


def make_context(
    *,
    dependency_status: dict[str, DependencyStatus] | None = None,
    existing_terminal_attempt: bool = False,
    existing_sent_reply: bool = False,
    dry_run: bool = False,
    required_dependencies: tuple[str, ...] = ("dws",),
    trusted_document_url: str = "",
) -> UniversalValidationContext:
    return UniversalValidationContext(
        conversation_id=CONVERSATION_ID,
        trigger_message_id=TRIGGER_MESSAGE_ID,
        dependency_status=(
            {"dws": DependencyStatus(ready=True)}
            if dependency_status is None
            else dependency_status
        ),
        existing_terminal_attempt=existing_terminal_attempt,
        existing_sent_reply=existing_sent_reply,
        dry_run=dry_run,
        required_dependencies=required_dependencies,
        trusted_document_url=trusted_document_url,
    )


def reply_action(
    *, conversation_id: str = CONVERSATION_ID, message_id: str = TRIGGER_MESSAGE_ID
) -> PlannedAction:
    return PlannedAction(
        kind=PlannedActionKind.SEND_REPLY,
        reason="Answer the requester",
        sensitivity_kind="general",
        target={
            "conversation_id": conversation_id,
            "trigger_message_id": message_id,
        },
        payload={"text": "Done."},
    )


def assert_synthesized_block(
    result: ValidatedUniversalPlan,
    *,
    kind: PlannedActionKind,
    reason: str,
    terminal: bool,
) -> None:
    assert result.allowed is False
    assert result.block_reason == reason
    assert result.terminal is terminal
    assert len(result.actions) == 1
    action = result.actions[0]
    assert action.kind is kind
    assert action.reason == reason
    assert action.target == {
        "conversation_id": CONVERSATION_ID,
        "trigger_message_id": TRIGGER_MESSAGE_ID,
    }
    assert action.payload == {"blocker": reason, "terminal": terminal}


def test_dws_unavailable_blocks_plan() -> None:
    result = UniversalValidator().validate(
        make_plan(reply_action()),
        make_context(
            dependency_status={
                "dws": DependencyStatus(
                    ready=False, reason="dws_authorization_required"
                )
            }
        ),
    )

    assert_synthesized_block(
        result,
        kind=PlannedActionKind.BLOCKED,
        reason="dws_authorization_required",
        terminal=False,
    )


@pytest.mark.parametrize(
    ("values", "message"),
    [
        ({"ready": 1}, "ready must be bool"),
        ({"ready": True, "reason": None}, "reason must be str"),
    ],
)
def test_dependency_status_rejects_invalid_field_types(
    values: dict[str, object], message: str
) -> None:
    with pytest.raises(TypeError, match=message):
        DependencyStatus(**values)


def test_missing_required_dws_status_blocks_even_when_model_omits_dws() -> None:
    result = UniversalValidator().validate(
        make_plan(reply_action()),
        make_context(dependency_status={}),
    )

    assert_synthesized_block(
        result,
        kind=PlannedActionKind.BLOCKED,
        reason="dependency_status_missing:dws",
        terminal=False,
    )


@pytest.mark.parametrize(
    ("existing_terminal_attempt", "existing_sent_reply"),
    [(True, False), (False, True)],
)
def test_duplicate_final_state_wins_despite_dws_unavailable(
    existing_terminal_attempt: bool, existing_sent_reply: bool
) -> None:
    result = UniversalValidator().validate(
        make_plan(reply_action()),
        make_context(
            dependency_status={"dws": DependencyStatus(ready=False)},
            existing_terminal_attempt=existing_terminal_attempt,
            existing_sent_reply=existing_sent_reply,
        ),
    )

    assert_synthesized_block(
        result,
        kind=PlannedActionKind.NO_REPLY,
        reason="duplicate_trigger_already_terminal",
        terminal=True,
    )


def test_ready_dependencies_allow_matching_non_terminal_reply() -> None:
    action = reply_action()

    result = UniversalValidator().validate(
        make_plan(action, dependencies=("dws",)),
        make_context(),
    )

    assert result.allowed is True
    assert result.actions == (action,)
    assert result.block_reason == ""
    assert result.terminal is False


def test_dependency_union_is_ordered_and_uses_unavailable_default_reason() -> None:
    result = UniversalValidator().validate(
        make_plan(reply_action(), dependencies=("mail", "dws")),
        make_context(
            dependency_status={
                "dws": DependencyStatus(ready=True),
                "mail": DependencyStatus(ready=False),
            },
            required_dependencies=("dws", "mail"),
        ),
    )

    assert_synthesized_block(
        result,
        kind=PlannedActionKind.BLOCKED,
        reason="mail_unavailable",
        terminal=False,
    )


def test_dry_run_preserves_actions_and_disallows_execution() -> None:
    action = reply_action()

    result = UniversalValidator().validate(
        make_plan(action),
        make_context(dry_run=True),
    )

    assert result.allowed is False
    assert result.actions == (action,)
    assert result.block_reason == "dry_run"
    assert result.terminal is False


@pytest.mark.parametrize("dry_run", [False, True])
def test_result_actions_are_isolated_from_original_plan_mutation(
    dry_run: bool,
) -> None:
    action = reply_action()
    plan = make_plan(action)

    result = UniversalValidator().validate(plan, make_context(dry_run=dry_run))

    action.target["conversation_id"] = "mutated-conversation"
    action.payload["text"] = "Mutated reply."
    plan.actions.append(
        PlannedAction(
            kind=PlannedActionKind.MEMORY_WRITE,
            reason="Mutate the source plan",
            payload={"data": "Mutation.", "type": "text"},
        )
    )

    assert result.actions[0] is not action
    assert result.actions[0].target == {
        "conversation_id": CONVERSATION_ID,
        "trigger_message_id": TRIGGER_MESSAGE_ID,
    }
    assert result.actions[0].payload == {"text": "Done."}
    assert len(result.actions) == 1


@pytest.mark.parametrize(
    "kind",
    [
        PlannedActionKind.SEND_REPLY,
        PlannedActionKind.ASK_CLARIFYING_QUESTION,
    ],
)
def test_reply_actions_missing_target_are_blocked(kind: PlannedActionKind) -> None:
    result = UniversalValidator().validate(
        make_plan(
            PlannedAction(
                kind=kind,
                reason="Respond to the requester",
                sensitivity_kind="general",
                payload={"text": "Please clarify."},
            )
        ),
        make_context(),
    )

    assert_synthesized_block(
        result,
        kind=PlannedActionKind.BLOCKED,
        reason="missing_action_target",
        terminal=False,
    )


@pytest.mark.parametrize(
    "target",
    [
        {
            "conversation_id": "other-conversation",
            "trigger_message_id": TRIGGER_MESSAGE_ID,
        },
        {
            "conversation_id": CONVERSATION_ID,
            "trigger_message_id": "other-trigger",
        },
        {"conversation_id": "other-conversation"},
        {"trigger_message_id": "other-trigger"},
    ],
)
@pytest.mark.parametrize(
    "kind",
    [
        PlannedActionKind.SEND_REPLY,
        PlannedActionKind.ASK_CLARIFYING_QUESTION,
    ],
)
def test_reply_target_mismatch_is_blocked(
    target: dict[str, str], kind: PlannedActionKind
) -> None:
    result = UniversalValidator().validate(
        make_plan(
            PlannedAction(
                kind=kind,
                reason="Answer the requester",
                sensitivity_kind="general",
                target=target,
                payload={"text": "Done."},
            )
        ),
        make_context(),
    )

    assert_synthesized_block(
        result,
        kind=PlannedActionKind.BLOCKED,
        reason="action_target_mismatch",
        terminal=False,
    )


@pytest.mark.parametrize(
    "kind",
    [
        PlannedActionKind.DWS_MARKDOWN_DOCUMENT_REPLY,
        PlannedActionKind.DWS_MESSAGE_REACTION,
        PlannedActionKind.OA_APPROVAL,
        PlannedActionKind.MAIL_REPLY,
        PlannedActionKind.CALENDAR_RESPONSE,
    ],
)
def test_other_external_actions_with_supplied_matching_target_are_allowed(
    kind: PlannedActionKind,
) -> None:
    target = {
        "conversation_id": CONVERSATION_ID,
        "trigger_message_id": TRIGGER_MESSAGE_ID,
    }
    payload: dict[str, str] = {}
    if kind is PlannedActionKind.DWS_MARKDOWN_DOCUMENT_REPLY:
        payload = {"title": "Plan", "text": "# Plan\n\nDetails"}
    elif kind is PlannedActionKind.DWS_MESSAGE_REACTION:
        target = {
            "conversation_id": CONVERSATION_ID,
            "message_id": TRIGGER_MESSAGE_ID,
        }
        payload = {"reaction_type": "emoji", "emoji": "👍"}
    elif kind is PlannedActionKind.OA_APPROVAL:
        payload = {"action": "comment", "remark": "Reviewed."}
    elif kind is PlannedActionKind.MAIL_REPLY:
        target |= {
            "mailbox": "derek@example.com",
            "message_id": "mail-1",
            "subject": "Subject",
        }
        payload = {"content": "Done."}
    elif kind is PlannedActionKind.CALENDAR_RESPONSE:
        target["event_id"] = "event-1"
        payload = {"response_status": "accepted"}
    action = PlannedAction(
        kind=kind,
        reason="Perform the external action",
        target=target,
        payload=payload,
    )

    result = UniversalValidator().validate(make_plan(action), make_context())

    assert result.allowed is True
    assert result.actions == (action,)
    assert result.terminal is False


@pytest.mark.parametrize(
    "action",
    [
        PlannedAction(
            kind=PlannedActionKind.OA_APPROVAL,
            reason="Comment on an approval",
            target={"process_instance_id": "approval-1"},
            payload={"action": "comment", "remark": "Reviewed."},
        ),
        PlannedAction(
            kind=PlannedActionKind.MAIL_REPLY,
            reason="Reply to mail",
            target={
                "mailbox": "derek@example.com",
                "message_id": "mail-1",
                "subject": "Subject",
            },
            payload={"content": "Done."},
        ),
        PlannedAction(
            kind=PlannedActionKind.CALENDAR_RESPONSE,
            reason="Respond to an event",
            target={"event_id": "event-1"},
            payload={"response_status": "accepted"},
        ),
    ],
)
def test_non_reply_external_actions_do_not_require_conversation_target(
    action: PlannedAction,
) -> None:
    result = UniversalValidator().validate(make_plan(action), make_context())

    assert result.allowed is True
    assert result.actions == (action,)


def test_external_action_with_supplied_wrong_partial_target_is_mismatch() -> None:
    result = UniversalValidator().validate(
        make_plan(
            PlannedAction(
                kind=PlannedActionKind.DWS_MESSAGE_REACTION,
                reason="React to the message",
                target={
                    "conversation_id": "other-conversation",
                    "message_id": TRIGGER_MESSAGE_ID,
                },
                payload={"reaction_type": "emoji", "emoji": "👍"},
            )
        ),
        make_context(),
    )

    assert_synthesized_block(
        result,
        kind=PlannedActionKind.BLOCKED,
        reason="action_target_mismatch",
        terminal=False,
    )


def test_reaction_target_message_id_spoof_is_blocked() -> None:
    action = PlannedAction(
        kind=PlannedActionKind.DWS_MESSAGE_REACTION,
        reason="React to the immutable trigger",
        target={
            "conversation_id": CONVERSATION_ID,
            "message_id": "spoof-message",
        },
        payload={"reaction_type": "emoji", "emoji": "👍"},
    )

    result = UniversalValidator().validate(make_plan(action), make_context())

    assert_synthesized_block(
        result,
        kind=PlannedActionKind.BLOCKED,
        reason="action_target_mismatch",
        terminal=False,
    )


def test_document_target_url_spoof_is_blocked() -> None:
    action = PlannedAction(
        kind=PlannedActionKind.DWS_MARKDOWN_DOCUMENT_REPLY,
        reason="Reply with a document",
        target={
            "conversation_id": CONVERSATION_ID,
            "trigger_message_id": TRIGGER_MESSAGE_ID,
            "document_url": "https://alidocs.dingtalk.com/i/nodes/spoof",
        },
        payload={"title": "Plan", "text": "# Plan\n\nDetails"},
    )

    result = UniversalValidator().validate(
        make_plan(action),
        make_context(
            trusted_document_url="https://alidocs.dingtalk.com/i/nodes/trusted"
        ),
    )

    assert_synthesized_block(
        result,
        kind=PlannedActionKind.BLOCKED,
        reason="action_target_mismatch",
        terminal=False,
    )


def test_terminal_action_cannot_be_combined_with_send() -> None:
    result = UniversalValidator().validate(
        make_plan(
            PlannedAction(
                kind=PlannedActionKind.NO_REPLY,
                reason="No reply is needed",
            ),
            reply_action(),
        ),
        make_context(),
    )

    assert_synthesized_block(
        result,
        kind=PlannedActionKind.BLOCKED,
        reason="conflicting_terminal_actions",
        terminal=False,
    )


def test_no_reply_can_be_combined_with_reaction() -> None:
    result = UniversalValidator().validate(
        make_plan(
            PlannedAction(
                kind=PlannedActionKind.DWS_MESSAGE_REACTION,
                reason="React lightly",
                target={
                    "conversation_id": CONVERSATION_ID,
                    "message_id": TRIGGER_MESSAGE_ID,
                },
                payload={"reaction_type": "emoji", "emoji": "👍"},
            ),
            PlannedAction(
                kind=PlannedActionKind.NO_REPLY,
                reason="No text reply is needed",
            ),
        ),
        make_context(),
    )

    assert result.allowed is True
    assert [action.kind for action in result.actions] == [
        PlannedActionKind.DWS_MESSAGE_REACTION,
        PlannedActionKind.NO_REPLY,
    ]


def test_no_reply_can_be_combined_with_calendar_response() -> None:
    result = UniversalValidator().validate(
        make_plan(
            PlannedAction(
                kind=PlannedActionKind.CALENDAR_RESPONSE,
                reason="Accept the trusted calendar invite",
                target={"event_id": "event-123"},
                payload={"response_status": "accepted"},
            ),
            PlannedAction(
                kind=PlannedActionKind.NO_REPLY,
                reason="No text reply is needed",
            ),
        ),
        make_context(),
    )

    assert result.allowed is True
    assert [action.kind for action in result.actions] == [
        PlannedActionKind.CALENDAR_RESPONSE,
        PlannedActionKind.NO_REPLY,
    ]


def test_no_reply_can_be_combined_with_oa_approval() -> None:
    result = UniversalValidator().validate(
        make_plan(
            PlannedAction(
                kind=PlannedActionKind.OA_APPROVAL,
                reason="Approve through the trusted OA task",
                target={
                    "process_instance_id": "proc-1",
                    "task_id": "task-1",
                },
                payload={"action": "同意", "remark": "同意。"},
            ),
            PlannedAction(
                kind=PlannedActionKind.NO_REPLY,
                reason="The OA approval itself responds to the trigger",
            ),
        ),
        make_context(),
    )

    assert result.allowed is True
    assert [action.kind for action in result.actions] == [
        PlannedActionKind.OA_APPROVAL,
        PlannedActionKind.NO_REPLY,
    ]


def test_no_reply_can_be_combined_with_memory_write() -> None:
    memory_write = PlannedAction(
        kind=PlannedActionKind.MEMORY_WRITE,
        reason="Preserve durable style feedback",
        payload={
            "data": "Use a more natural, concise style for future replies.",
            "type": "message",
        },
    )
    no_reply = PlannedAction(
        kind=PlannedActionKind.NO_REPLY,
        reason="A human already acknowledged the trigger",
    )

    result = UniversalValidator().validate(
        make_plan(no_reply, memory_write),
        make_context(
            dependency_status={
                "dws": DependencyStatus(ready=True),
                "memory": DependencyStatus(ready=True),
            }
        ),
    )

    assert result.allowed is True
    assert result.actions == (no_reply, memory_write)
    assert result.terminal is False


def test_memory_write_is_not_terminal() -> None:
    action = PlannedAction(
        kind=PlannedActionKind.MEMORY_WRITE,
        reason="Preserve the decision",
        payload={"data": "Decision recorded.", "type": "text"},
    )

    result = UniversalValidator().validate(
        make_plan(action),
        make_context(
            dependency_status={
                "dws": DependencyStatus(ready=True),
                "memory": DependencyStatus(ready=True),
            }
        ),
    )

    assert result.allowed is True
    assert result.actions == (action,)
    assert result.terminal is False


def test_multiple_non_terminal_actions_are_not_terminal() -> None:
    reply = reply_action()
    memory_write = PlannedAction(
        kind=PlannedActionKind.MEMORY_WRITE,
        reason="Preserve the decision",
        payload={"data": "Decision recorded.", "type": "text"},
    )

    result = UniversalValidator().validate(
        make_plan(reply, memory_write),
        make_context(
            dependency_status={
                "dws": DependencyStatus(ready=True),
                "memory": DependencyStatus(ready=True),
            }
        ),
    )

    assert result.allowed is True
    assert result.actions == (reply, memory_write)
    assert result.terminal is False


@pytest.mark.parametrize(
    "kind",
    [
        PlannedActionKind.NO_REPLY,
        PlannedActionKind.HANDOFF_TO_HUMAN,
        PlannedActionKind.STOP_WITH_ERROR,
    ],
)
def test_sole_terminal_control_is_terminal(kind: PlannedActionKind) -> None:
    action = PlannedAction(
        kind=kind,
        reason="Stop processing this plan",
    )

    result = UniversalValidator().validate(make_plan(action), make_context())

    assert result.allowed is True
    assert result.actions == (action,)
    assert result.terminal is True


@pytest.mark.parametrize("terminal", [False, True])
def test_sole_blocked_action_reads_payload_terminal(terminal: bool) -> None:
    action = PlannedAction(
        kind=PlannedActionKind.BLOCKED,
        reason="A manual prerequisite remains",
        payload={"terminal": terminal},
    )

    result = UniversalValidator().validate(make_plan(action), make_context())

    assert result.allowed is True
    assert result.actions == (action,)
    assert result.terminal is terminal
