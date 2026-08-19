import json

import pytest

import app.approval_history as approval_history_module

from app.agent_contracts import (
    AgentError,
    AuditAgentResult,
    AuditExternalResult,
    AuditOutcome,
    ConsumerAgentResult,
    ConsumerOutcome,
    ConsumerProposal,
    ProposedAction,
)
from app.agent_result import SideEffectState
from app.approval_history import ApprovalHistoryResult, resolve_approval_history_result
from app.store import AgentRole, AgentRun, ReplyAttempt


def _attempt(**overrides: object) -> ReplyAttempt:
    values: dict[str, object] = {
        "id": 1,
        "conversation_id": "conversation",
        "conversation_title": "title",
        "trigger_message_id": "message",
        "trigger_sender": "sender",
        "trigger_text": "text",
        "action": "oa_approval",
        "sensitivity_kind": "normal",
        "codex_reason": "",
        "draft_reply_text": "",
        "final_reply_text": "",
        "permission_action": "",
        "permission_reason": "",
        "send_status": "pending",
        "send_error": "",
        "retry_count": 0,
        "created_at": "2026-08-18T00:00:00Z",
        "updated_at": "2026-08-18T00:00:00Z",
    }
    values.update(overrides)
    return ReplyAttempt.model_validate(values)


def _run(
    run_id: int,
    role: AgentRole,
    result: object,
    *,
    parent_agent_run_id: int | None = None,
    status: str = "completed",
    side_effect_state: str = "none",
    proposal_revision: int = 0,
    reply_task_id: int = 1,
    execution_generation: str = "initial",
    operation_id: str | None = None,
) -> AgentRun:
    return AgentRun(
        id=run_id,
        reply_task_id=reply_task_id,
        execution_generation=execution_generation,
        role=role,
        proposal_revision=proposal_revision,
        turn_attempt=1,
        parent_agent_run_id=parent_agent_run_id,
        operation_id=operation_id or f"operation-{run_id}",
        status=status,
        final_result_json=result if isinstance(result, str) else result.model_dump_json(),
        side_effect_state=side_effect_state,
        created_at=f"2026-08-18T00:00:0{run_id}Z",
        updated_at=f"2026-08-18T00:00:0{run_id}Z",
    )


def _consumer(
    operation: str | None = "oa approval approve",
    *,
    outcome: ConsumerOutcome = ConsumerOutcome.PROPOSAL,
    target_process: str | None = "process",
) -> ConsumerAgentResult:
    proposal = None
    if operation is not None:
        proposal = ConsumerProposal(
            objective="objective",
            actions=(
                ProposedAction(
                    description="prose can disagree with operation",
                    capability="dingtalk_oa",
                    operation=operation,
                    target=(
                        {"process_instance_id": target_process}
                        if target_process is not None
                        else {"other": "target"}
                    ),
                    payload={},
                    expected_verification="verification",
                ),
            ),
            sourced_facts=(),
            authored_judgment="judgment",
        )
    return ConsumerAgentResult(
        outcome=outcome,
        summary="summary",
        proposal=proposal,
        error=AgentError(),
    )


def _consumer_command(
    argv: list[str],
    *,
    outcome: ConsumerOutcome = ConsumerOutcome.PROPOSAL,
    operation_label: str = "misleading generated label",
) -> ConsumerAgentResult:
    proposal = None
    if outcome is ConsumerOutcome.PROPOSAL:
        proposal = ConsumerProposal(
            objective="objective",
            actions=(
                ProposedAction(
                    description="the executable payload is authoritative",
                    capability="misleading.generated.capability",
                    operation=operation_label,
                    target={"process_instance_id": "misleading-target"},
                    payload={"argv": argv},
                    expected_verification="verification",
                ),
            ),
            sourced_facts=(),
            authored_judgment="judgment",
        )
    return ConsumerAgentResult(
        outcome=outcome,
        summary="summary",
        proposal=proposal,
        error=AgentError(),
    )


def _confirmed_audit(
    operation_id: str = "operation-2",
    live_process: str | None = None,
) -> AuditAgentResult:
    return AuditAgentResult(
        outcome=AuditOutcome.EXECUTED,
        summary="audit summary",
        proposal_revision=0,
        side_effect_state=SideEffectState.CONFIRMED,
        feedback=None,
        external_result=AuditExternalResult(
            operation_id=operation_id,
            verification_summary="verified",
            live_result_reference=(
                {"process_instance_id": live_process} if live_process is not None else {}
            ),
        ),
        error=AgentError(),
    )


@pytest.mark.parametrize(
    ("argv", "expected"),
    [
        (
            [
                "dws", "oa", "approval", "approve",
                "--instance-id", "process", "--task-id", "task", "--yes",
            ],
            ApprovalHistoryResult.APPROVED,
        ),
        (
            [
                "dws", "oa", "approval", "oa-comments",
                "--instance-id", "process", "--content", "need details", "--yes",
            ],
            ApprovalHistoryResult.COMMENTED_PENDING,
        ),
        (
            [
                "dws", "oa", "approval", "reject",
                "--instance-id", "process", "--task-id", "task", "--yes",
            ],
            ApprovalHistoryResult.REJECTED,
        ),
        (
            [
                "dws", "oa", "approval", "revert-task",
                "--instance-id", "process", "--task-id", "task",
                "--target-activity-id", "activity",
                "--action", "REVERT_FOR_APPROVAL", "--yes",
            ],
            ApprovalHistoryResult.RETURNED,
        ),
        (
            [
                "dws", "oa", "approval", "revert-task",
                "--instance-id", "process", "--task-id", "task",
                "--target-activity-id", "sid-startevent",
                "--action", "REVERT_FOR_RESUBMIT", "--yes",
            ],
            ApprovalHistoryResult.RETURNED,
        ),
    ],
)
def test_confirmed_structured_result_uses_native_dws_command_descriptor(argv, expected):
    consumer = _run(1, AgentRole.CONSUMER, _consumer_command(argv))
    audit = _run(
        2,
        AgentRole.AUDIT,
        _confirmed_audit(),
        parent_agent_run_id=consumer.id,
        side_effect_state=SideEffectState.CONFIRMED,
    )

    attempt = _attempt(oa_process_instance_id="process", oa_task_id="task")

    assert resolve_approval_history_result(attempt, [consumer, audit]) is expected


@pytest.mark.parametrize(
    "argv",
    [
        ["dws", "oa", "approval", "redirect-task", "--instance-id", "process", "--task-id", "task"],
        ["dws", "oa", "approval", "revert-activities", "--task-id", "task"],
        ["dws", "oa", "approval", "revoke", "--instance-id", "process", "--task-id", "task"],
        ["other-cli", "oa", "approval", "approve", "--instance-id", "process", "--task-id", "task"],
        ["dws", "oa", "approval", "approve", "--task-id", "task"],
        ["dws", "oa", "approval", "approve", "--instance-id", "other", "--task-id", "task"],
        ["dws", "oa", "approval", "approve", "--instance-id", "process", "--task-id", "other"],
        [
            "dws", "oa", "approval", "revert-task",
            "--instance-id", "process", "--task-id", "task",
            "--target-activity-id", "activity", "--action", "REDIRECT_PROCESS",
        ],
    ],
)
def test_unrecognized_or_mismatched_native_command_is_unknown(argv):
    consumer = _run(1, AgentRole.CONSUMER, _consumer_command(argv))
    audit = _run(
        2,
        AgentRole.AUDIT,
        _confirmed_audit(),
        parent_agent_run_id=consumer.id,
        side_effect_state=SideEffectState.CONFIRMED,
    )

    result = resolve_approval_history_result(
        _attempt(
            oa_process_instance_id="process",
            oa_task_id="task",
            send_status="closed",
        ),
        [consumer, audit],
    )

    assert result is ApprovalHistoryResult.UNKNOWN


def test_confirmed_comment_does_not_require_task_identifier():
    consumer = _run(
        1,
        AgentRole.CONSUMER,
        _consumer_command(
            [
                "dws", "oa", "approval", "oa-comments",
                "--instance-id", "process", "--content", "need details", "--yes",
            ]
        ),
    )
    audit = _run(
        2,
        AgentRole.AUDIT,
        _confirmed_audit(),
        parent_agent_run_id=consumer.id,
        side_effect_state=SideEffectState.CONFIRMED,
    )

    assert (
        resolve_approval_history_result(
            _attempt(oa_process_instance_id="process", oa_task_id="task"),
            [consumer, audit],
        )
        is ApprovalHistoryResult.COMMENTED_PENDING
    )


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        ("needs_human", ApprovalHistoryResult.NEEDS_HUMAN),
        ("pending", ApprovalHistoryResult.PROCESSING),
        ("pending_reconciliation", ApprovalHistoryResult.PROCESSING),
        ("processing", ApprovalHistoryResult.PROCESSING),
        ("failed", ApprovalHistoryResult.FAILED),
        ("blocked", ApprovalHistoryResult.FAILED),
    ],
)
def test_workflow_statuses(status, expected):
    assert resolve_approval_history_result(_attempt(send_status=status), []) is expected


def test_structured_consumer_no_action_returns_no_action():
    consumer = _run(1, AgentRole.CONSUMER, _consumer(None, outcome=ConsumerOutcome.NO_ACTION))
    assert resolve_approval_history_result(_attempt(send_status="closed"), [consumer]) is ApprovalHistoryResult.NO_ACTION


def test_proposal_without_audit_confirmation_is_unknown():
    consumer = _run(1, AgentRole.CONSUMER, _consumer())
    assert resolve_approval_history_result(_attempt(send_status="closed"), [consumer]) is ApprovalHistoryResult.UNKNOWN


def test_confirmed_audit_result_does_not_require_row_side_effect_state():
    consumer = _run(
        1,
        AgentRole.CONSUMER,
        _consumer_command(
            [
                "dws",
                "oa",
                "approval",
                "approve",
                "--instance-id",
                "process",
                "--task-id",
                "task",
                "--yes",
            ]
        ),
    )
    audit = _run(
        2,
        AgentRole.AUDIT,
        _confirmed_audit(),
        parent_agent_run_id=consumer.id,
    )
    assert audit.side_effect_state == "none"
    assert (
        resolve_approval_history_result(
            _attempt(oa_process_instance_id="process", oa_task_id="task"),
            [consumer, audit],
        )
        is ApprovalHistoryResult.APPROVED
    )


def test_malformed_consumer_json_is_unknown():
    consumer = _run(1, AgentRole.CONSUMER, "not-json")
    assert resolve_approval_history_result(_attempt(send_status="closed"), [consumer]) is ApprovalHistoryResult.UNKNOWN


@pytest.mark.parametrize(
    ("consumer", "status", "expected"),
    [
        (_consumer(), "failed", ApprovalHistoryResult.FAILED),
        (_consumer(), "processing", ApprovalHistoryResult.PROCESSING),
        (_consumer(None, outcome=ConsumerOutcome.NO_ACTION), "failed", ApprovalHistoryResult.FAILED),
        (_consumer(None, outcome=ConsumerOutcome.NO_ACTION), "processing", ApprovalHistoryResult.PROCESSING),
    ],
)
def test_workflow_status_precedes_unconfirmed_or_no_action_consumer(consumer, status, expected):
    run = _run(1, AgentRole.CONSUMER, consumer)
    assert resolve_approval_history_result(_attempt(send_status=status), [run]) is expected


def test_malformed_latest_completed_consumer_does_not_fall_back_to_older_valid_run():
    valid = _run(
        1,
        AgentRole.CONSUMER,
        _consumer(None, outcome=ConsumerOutcome.NO_ACTION),
        status="completed",
    )
    malformed = _run(2, AgentRole.CONSUMER, "not-json", status="completed")
    assert resolve_approval_history_result(_attempt(send_status="closed"), [valid, malformed]) is ApprovalHistoryResult.UNKNOWN


@pytest.mark.parametrize("status", ["pending", "processing", "failed", "unknown"])
def test_non_completed_no_action_consumer_does_not_resolve_terminal_no_action(status):
    run = _run(
        1,
        AgentRole.CONSUMER,
        _consumer(None, outcome=ConsumerOutcome.NO_ACTION),
        status=status,
    )
    assert resolve_approval_history_result(_attempt(send_status="closed"), [run]) is ApprovalHistoryResult.UNKNOWN


def test_latest_non_completed_no_action_consumer_falls_back_to_completed_evidence():
    completed = _run(
        1,
        AgentRole.CONSUMER,
        _consumer(None, outcome=ConsumerOutcome.NO_ACTION),
        status="completed",
    )
    newest = _run(
        2,
        AgentRole.CONSUMER,
        _consumer(None, outcome=ConsumerOutcome.NO_ACTION),
        status="processing",
    )
    assert resolve_approval_history_result(_attempt(send_status="closed"), [completed, newest]) is ApprovalHistoryResult.NO_ACTION


def test_conflicting_confirmed_approval_actions_are_unknown():
    common = ["--instance-id", "process", "--task-id", "task", "--yes"]
    consumer_approve = _run(
        1,
        AgentRole.CONSUMER,
        _consumer_command(["dws", "oa", "approval", "approve", *common]),
    )
    consumer_reject = _run(
        3,
        AgentRole.CONSUMER,
        _consumer_command(["dws", "oa", "approval", "reject", *common]),
    )
    audit_approve = _run(
        2,
        AgentRole.AUDIT,
        _confirmed_audit(),
        parent_agent_run_id=consumer_approve.id,
        side_effect_state=SideEffectState.CONFIRMED,
    )
    audit_reject = _run(
        4,
        AgentRole.AUDIT,
        _confirmed_audit(operation_id="operation-4"),
        parent_agent_run_id=consumer_reject.id,
        side_effect_state=SideEffectState.CONFIRMED,
    )
    assert (
        resolve_approval_history_result(
            _attempt(oa_process_instance_id="process", oa_task_id="task"),
            [consumer_approve, audit_approve, consumer_reject, audit_reject],
        )
        is ApprovalHistoryResult.UNKNOWN
    )


def test_non_approval_attempt_returns_none():
    assert resolve_approval_history_result(_attempt(action="chat_message"), []) is None


def test_failed_direct_receipt_does_not_yield_success():
    for receipt in ("not-json", json.dumps({"success": False}), json.dumps({"errcode": 1})):
        assert (
            resolve_approval_history_result(
                _attempt(
                    oa_action="approve",
                    send_status="closed",
                    oa_action_result_json=receipt,
                ),
                [],
            )
            is ApprovalHistoryResult.UNKNOWN
        )


def test_terminal_direct_action_without_receipt_is_unknown():
    assert (
        resolve_approval_history_result(
            _attempt(
                oa_action="approve",
                send_status="sent",
            ),
            [],
        )
        is ApprovalHistoryResult.UNKNOWN
    )


@pytest.mark.parametrize(
    "receipt",
    [json.dumps({"success": True}), json.dumps({"errcode": 0, "errmsg": "ok"})],
)
def test_generic_direct_receipt_confirms_persisted_approval_while_pending(receipt):
    assert (
        resolve_approval_history_result(
            _attempt(oa_action="approve", send_status="pending", oa_action_result_json=receipt),
            [],
        )
        is ApprovalHistoryResult.APPROVED
    )


def test_confirmed_structured_action_precedes_workflow_status():
    consumer = _run(
        1,
        AgentRole.CONSUMER,
        _consumer_command(
            [
                "dws",
                "oa",
                "approval",
                "approve",
                "--instance-id",
                "process",
                "--task-id",
                "task",
                "--yes",
            ]
        ),
    )
    audit = _run(
        2,
        AgentRole.AUDIT,
        _confirmed_audit(),
        parent_agent_run_id=consumer.id,
        side_effect_state=SideEffectState.CONFIRMED,
    )
    assert (
        resolve_approval_history_result(
            _attempt(
                send_status="processing",
                oa_process_instance_id="process",
                oa_task_id="task",
            ),
            [consumer, audit],
        )
        is ApprovalHistoryResult.APPROVED
    )


@pytest.mark.parametrize(
    "payload",
    [
        {"result": {"success": True}},
        {"dws_action_result": {"success": True}},
    ],
)
def test_nested_generic_receipt_confirms_persisted_approval(payload):
    assert (
        resolve_approval_history_result(
            _attempt(oa_action="approve", send_status="pending", oa_action_result_json=json.dumps(payload)),
            [],
        )
        is ApprovalHistoryResult.APPROVED
    )


def test_commented_legacy_return_is_a_pending_comment_not_a_return():
    attempt = _attempt(
        oa_action="退回",
        send_status="commented",
        oa_action_result_json=json.dumps(
            {"dingOpenErrcode": 0, "result": True, "success": True}
        ),
    )

    assert (
        resolve_approval_history_result(attempt, [])
        is ApprovalHistoryResult.COMMENTED_PENDING
    )


def test_generic_success_uses_persisted_direct_approval_action():
    attempt = _attempt(
        oa_action="通过",
        send_status="pending",
        oa_action_result_json=json.dumps({"result": True, "success": True}),
    )

    assert (
        resolve_approval_history_result(attempt, [])
        is ApprovalHistoryResult.APPROVED
    )


def test_typed_legacy_agree_receipt_is_strong_approval_evidence():
    attempt = _attempt(
        oa_process_instance_id="process",
        oa_task_id="task",
        oa_action="approve",
        send_status="completed",
        oa_action_result_json=json.dumps(
            {"success": True, "taskStatus": "COMPLETED", "taskResult": "AGREE"}
        ),
    )

    assert (
        resolve_approval_history_result(attempt, [])
        is ApprovalHistoryResult.APPROVED
    )


def test_typed_legacy_receipt_with_explicit_failure_is_unknown():
    attempt = _attempt(
        oa_process_instance_id="process",
        oa_task_id="task",
        oa_action="approve",
        send_status="closed",
        oa_action_result_json=json.dumps(
            {
                "success": False,
                "errorCode": 500,
                "taskStatus": "COMPLETED",
                "taskResult": "AGREE",
            }
        ),
    )

    assert (
        resolve_approval_history_result(attempt, [])
        is ApprovalHistoryResult.UNKNOWN
    )


def test_salvaged_typed_legacy_agree_receipt_is_strong_approval_evidence():
    attempt = _attempt(
        oa_process_instance_id="process",
        oa_task_id="task",
        oa_action="同意",
        send_status="skipped",
        oa_action_result_json=json.dumps(
            {
                "action": "同意",
                "outcome": "salvaged",
                "process_instance_id": "process",
                "task_id": "task",
                "readback": {
                    "taskResult": "AGREE",
                    "taskStatus": "COMPLETED",
                },
            }
        ),
    )

    assert (
        resolve_approval_history_result(attempt, [])
        is ApprovalHistoryResult.APPROVED
    )


def test_typed_redirect_process_receipt_is_strong_return_evidence():
    attempt = _attempt(
        oa_process_instance_id="process",
        oa_task_id="task",
        oa_action="退回",
        send_status="completed",
        oa_action_result_json=json.dumps(
            {
                "action": "退回",
                "outcome": "applied",
                "process_instance_id": "process",
                "task_id": "task",
                "invocation": {"canonical_path": "oa approval revert-task"},
                "readback": {
                    "taskResult": "REDIRECT_PROCESS",
                    "taskStatus": "COMPLETED",
                },
            }
        ),
    )

    assert (
        resolve_approval_history_result(attempt, [])
        is ApprovalHistoryResult.RETURNED
    )


def test_conflicting_structured_and_direct_evidence_on_same_attempt_is_unknown():
    consumer = _run(
        1,
        AgentRole.CONSUMER,
        _consumer_command(
            [
                "dws", "oa", "approval", "approve",
                "--instance-id", "process", "--task-id", "task", "--yes",
            ]
        ),
    )
    audit = _run(
        2,
        AgentRole.AUDIT,
        _confirmed_audit(),
        parent_agent_run_id=consumer.id,
        side_effect_state=SideEffectState.CONFIRMED,
    )
    attempt = _attempt(
        oa_process_instance_id="process",
        oa_task_id="task",
        oa_action="reject",
        send_status="completed",
        oa_action_result_json=json.dumps(
            {"success": True, "taskStatus": "COMPLETED", "taskResult": "REFUSE"}
        ),
    )

    assert (
        resolve_approval_history_result(attempt, [consumer, audit])
        is ApprovalHistoryResult.UNKNOWN
    )


def _resolve_group(
    attempts: list[ReplyAttempt],
    runs_by_attempt: dict[int, list[AgentRun]] | None = None,
) -> ApprovalHistoryResult:
    return approval_history_module.resolve_approval_history_group_result(
        attempts,
        runs_by_attempt or {},
    )


def test_group_uses_newer_persisted_approval_with_generic_success():
    older_comment = _attempt(
        id=908,
        created_at="2026-08-18T00:00:01Z",
        oa_process_instance_id="process",
        oa_action="退回",
        send_status="commented",
        oa_action_result_json=json.dumps(
            {
                "invocation": {
                    "canonical_path": "oa.dingflow_comments",
                    "params": {"processInstanceId": "process", "text": "more"},
                },
                "response": {
                    "content": {"result": True, "success": True}
                },
            }
        ),
    )
    newer_approval = _attempt(
        id=912,
        created_at="2026-08-18T00:00:02Z",
        oa_process_instance_id="process",
        oa_action="通过",
        send_status="skipped",
        oa_action_result_json=json.dumps({"result": True, "success": True}),
    )

    assert (
        _resolve_group([older_comment, newer_approval])
        is ApprovalHistoryResult.APPROVED
    )


def test_group_uses_newer_typed_approval_after_older_blocked_comment():
    older_blocked_comment = _attempt(
        id=4029,
        created_at="2026-08-18T00:00:01Z",
        oa_process_instance_id="process",
        oa_action="comment",
        send_status="blocked",
        oa_action_result_json=json.dumps(
            {"action": "comment", "outcome": "blocked"}
        ),
    )
    newer_approval = _attempt(
        id=4057,
        created_at="2026-08-18T00:00:02Z",
        oa_process_instance_id="process",
        oa_action="approve",
        send_status="completed",
        oa_action_result_json=json.dumps(
            {"success": True, "taskStatus": "COMPLETED", "taskResult": "AGREE"}
        ),
    )

    assert (
        _resolve_group([older_blocked_comment, newer_approval])
        is ApprovalHistoryResult.APPROVED
    )


def test_group_preserves_older_confirmed_comment_when_latest_attempt_failed():
    comment = _attempt(
        id=4800,
        created_at="2026-08-18T00:00:01Z",
        oa_process_instance_id="process",
        send_status="commented",
        oa_action="comment",
    )
    latest_failure = _attempt(
        id=4824,
        created_at="2026-08-18T00:00:02Z",
        oa_process_instance_id="process",
        send_status="failed",
        oa_action="review",
    )

    assert (
        _resolve_group([comment, latest_failure])
        is ApprovalHistoryResult.COMMENTED_PENDING
    )


def test_group_terminal_action_has_priority_over_newer_confirmed_comment():
    approval = _attempt(
        id=10,
        created_at="2026-08-18T00:00:01Z",
        oa_process_instance_id="process",
        send_status="completed",
        oa_action_result_json=json.dumps(
            {"taskStatus": "COMPLETED", "taskResult": "AGREE"}
        ),
    )
    comment = _attempt(
        id=11,
        created_at="2026-08-18T00:00:02Z",
        oa_process_instance_id="process",
        send_status="commented",
        oa_action="comment",
    )

    assert _resolve_group([approval, comment]) is ApprovalHistoryResult.APPROVED


def test_group_uses_latest_confirmed_terminal_action():
    approval = _attempt(
        id=10,
        created_at="2026-08-18T00:00:01Z",
        oa_process_instance_id="process",
        send_status="completed",
        oa_action_result_json=json.dumps(
            {"taskStatus": "COMPLETED", "taskResult": "AGREE"}
        ),
    )
    rejection = _attempt(
        id=11,
        created_at="2026-08-18T00:00:02Z",
        oa_process_instance_id="process",
        send_status="completed",
        oa_action_result_json=json.dumps(
            {"taskStatus": "COMPLETED", "taskResult": "REFUSE"}
        ),
    )

    assert _resolve_group([approval, rejection]) is ApprovalHistoryResult.REJECTED


def test_group_same_attempt_conflict_is_unknown_even_with_older_terminal_evidence():
    older_approval = _attempt(
        id=9,
        created_at="2026-08-18T00:00:00Z",
        oa_process_instance_id="process",
        send_status="completed",
        oa_action_result_json=json.dumps(
            {"taskStatus": "COMPLETED", "taskResult": "AGREE"}
        ),
    )
    consumer = _run(
        1,
        AgentRole.CONSUMER,
        _consumer_command(
            [
                "dws", "oa", "approval", "approve",
                "--instance-id", "process", "--task-id", "task", "--yes",
            ]
        ),
    )
    audit = _run(
        2,
        AgentRole.AUDIT,
        _confirmed_audit(),
        parent_agent_run_id=consumer.id,
        side_effect_state=SideEffectState.CONFIRMED,
    )
    conflicting_latest = _attempt(
        id=10,
        created_at="2026-08-18T00:00:01Z",
        oa_process_instance_id="process",
        oa_task_id="task",
        send_status="completed",
        oa_action_result_json=json.dumps(
            {"taskStatus": "COMPLETED", "taskResult": "REFUSE"}
        ),
    )

    assert (
        _resolve_group(
            [older_approval, conflicting_latest],
            {conflicting_latest.id: [consumer, audit]},
        )
        is ApprovalHistoryResult.UNKNOWN
    )


def test_group_without_business_evidence_uses_latest_workflow_state():
    older_unknown = _attempt(
        id=1,
        created_at="2026-08-18T00:00:01Z",
        oa_process_instance_id="process",
        send_status="completed",
    )
    latest_failure = _attempt(
        id=2,
        created_at="2026-08-18T00:00:02Z",
        oa_process_instance_id="process",
        send_status="failed",
    )

    assert (
        _resolve_group([older_unknown, latest_failure])
        is ApprovalHistoryResult.FAILED
    )


@pytest.mark.parametrize(
    "payload",
    [
        {"success": False, "errcode": 0},
        {"success": True, "errcode": 1},
        {"errcode": False},
        {"success": "true", "errcode": 0},
        {"result": {"success": 1}, "errcode": 0},
        {"dws_action_result": {"success": "true"}, "errcode": 0},
        {"errcode": 1},
    ],
)
def test_inconsistent_or_invalid_direct_receipts_do_not_confirm(payload):
    assert (
        resolve_approval_history_result(
            _attempt(oa_action="approve", send_status="pending", oa_action_result_json=json.dumps(payload)),
            [],
        )
        is ApprovalHistoryResult.PROCESSING
    )


@pytest.mark.parametrize(
    "case",
    [
        "audit_not_completed",
        "consumer_not_completed",
        "task_mismatch",
        "generation_mismatch",
        "proposal_revision_mismatch",
        "external_operation_mismatch",
        "external_operation_empty",
        "target_mismatch",
        "target_missing",
        "live_reference_mismatch",
    ],
)
def test_confirmed_evidence_must_match_persisted_approval_invariants(case):
    attempt = _attempt()
    consumer = _run(1, AgentRole.CONSUMER, _consumer())
    audit_result: object = _confirmed_audit()
    audit_kwargs: dict[str, object] = {"parent_agent_run_id": consumer.id}

    if case == "audit_not_completed":
        audit_kwargs["status"] = "processing"
    elif case == "consumer_not_completed":
        consumer = consumer.model_copy(update={"status": "processing"})
        audit_kwargs["parent_agent_run_id"] = consumer.id
    elif case == "task_mismatch":
        audit_kwargs["reply_task_id"] = 2
    elif case == "generation_mismatch":
        audit_kwargs["execution_generation"] = "recovery"
    elif case == "proposal_revision_mismatch":
        consumer = _run(1, AgentRole.CONSUMER, _consumer(), proposal_revision=1)
        audit_kwargs["parent_agent_run_id"] = consumer.id
    elif case == "external_operation_mismatch":
        audit_result = _confirmed_audit(operation_id="different-operation")
    elif case == "external_operation_empty":
        audit_result = _confirmed_audit().model_dump_json().replace(
            '"operation_id":"operation-2"', '"operation_id":""'
        )
    elif case == "target_mismatch":
        attempt = _attempt(oa_process_instance_id="process")
        consumer = _run(1, AgentRole.CONSUMER, _consumer(target_process="other"))
        audit_kwargs["parent_agent_run_id"] = consumer.id
    elif case == "target_missing":
        attempt = _attempt(oa_process_instance_id="process")
        consumer = _run(1, AgentRole.CONSUMER, _consumer(target_process=None))
        audit_kwargs["parent_agent_run_id"] = consumer.id
    elif case == "live_reference_mismatch":
        attempt = _attempt(oa_process_instance_id="process")
        audit_result = _confirmed_audit(live_process="other")

    audit = _run(2, AgentRole.AUDIT, audit_result, **audit_kwargs)
    assert resolve_approval_history_result(attempt, [consumer, audit]) is ApprovalHistoryResult.PROCESSING
