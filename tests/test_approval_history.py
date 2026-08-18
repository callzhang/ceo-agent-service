import json

import pytest

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
    ("operation", "expected"),
    [
        ("oa approval approve", ApprovalHistoryResult.APPROVED),
        ("oa approval return", ApprovalHistoryResult.RETURNED),
        ("oa approval reject", ApprovalHistoryResult.REJECTED),
        ("oa approval comment", ApprovalHistoryResult.COMMENTED_PENDING),
    ],
)
def test_confirmed_structured_approval_operations(operation, expected):
    consumer = _run(1, AgentRole.CONSUMER, _consumer(operation))
    audit = _run(
        2,
        AgentRole.AUDIT,
        _confirmed_audit(),
        parent_agent_run_id=consumer.id,
        side_effect_state=SideEffectState.CONFIRMED,
    )

    assert resolve_approval_history_result(_attempt(), [consumer, audit]) is expected


@pytest.mark.parametrize(
    ("action", "expected"),
    [
        ("approve", ApprovalHistoryResult.APPROVED),
        ("approved", ApprovalHistoryResult.APPROVED),
        ("同意", ApprovalHistoryResult.APPROVED),
        ("通过", ApprovalHistoryResult.APPROVED),
        ("return", ApprovalHistoryResult.RETURNED),
        ("returned", ApprovalHistoryResult.RETURNED),
        ("退回", ApprovalHistoryResult.RETURNED),
        ("reject", ApprovalHistoryResult.REJECTED),
        ("rejected", ApprovalHistoryResult.REJECTED),
        ("拒绝", ApprovalHistoryResult.REJECTED),
        ("comment", ApprovalHistoryResult.COMMENTED_PENDING),
        ("commented", ApprovalHistoryResult.COMMENTED_PENDING),
        ("评论", ApprovalHistoryResult.COMMENTED_PENDING),
        ("留言", ApprovalHistoryResult.COMMENTED_PENDING),
    ],
)
def test_successful_direct_chinese_actions(action, expected):
    assert (
        resolve_approval_history_result(
            _attempt(
                oa_action=action,
                send_status="completed" if action in {"comment", "commented", "评论", "留言"} else "sent",
                oa_action_result_json=json.dumps({"errcode": 0, "errmsg": "ok"}),
            ),
            [],
        )
        is expected
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
    consumer = _run(1, AgentRole.CONSUMER, _consumer("oa approval approve"))
    audit = _run(
        2,
        AgentRole.AUDIT,
        _confirmed_audit(),
        parent_agent_run_id=consumer.id,
    )
    assert audit.side_effect_state == "none"
    assert resolve_approval_history_result(_attempt(), [consumer, audit]) is ApprovalHistoryResult.APPROVED


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


def test_latest_valid_consumer_skips_malformed_newest_run():
    valid = _run(
        1,
        AgentRole.CONSUMER,
        _consumer(None, outcome=ConsumerOutcome.NO_ACTION),
        status="completed",
    )
    malformed = _run(2, AgentRole.CONSUMER, "not-json", status="completed")
    assert resolve_approval_history_result(_attempt(send_status="closed"), [valid, malformed]) is ApprovalHistoryResult.NO_ACTION


def test_conflicting_confirmed_approval_actions_are_unknown():
    consumer_approve = _run(1, AgentRole.CONSUMER, _consumer("oa approval approve"))
    consumer_reject = _run(3, AgentRole.CONSUMER, _consumer("oa approval reject"))
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
    assert resolve_approval_history_result(_attempt(), [consumer_approve, audit_approve, consumer_reject, audit_reject]) is ApprovalHistoryResult.UNKNOWN


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


def test_terminal_direct_action_succeeds_without_receipt():
    assert (
        resolve_approval_history_result(
            _attempt(
                oa_action="approve",
                send_status="sent",
            ),
            [],
        )
        is ApprovalHistoryResult.APPROVED
    )


@pytest.mark.parametrize(
    "receipt",
    [json.dumps({"success": True}), json.dumps({"errcode": 0, "errmsg": "ok"})],
)
def test_structured_direct_receipt_succeeds_while_pending(receipt):
    assert (
        resolve_approval_history_result(
            _attempt(oa_action="approve", send_status="pending", oa_action_result_json=receipt),
            [],
        )
        is ApprovalHistoryResult.APPROVED
    )


def test_confirmed_structured_action_precedes_workflow_status():
    consumer = _run(1, AgentRole.CONSUMER, _consumer("oa approval approve"))
    audit = _run(
        2,
        AgentRole.AUDIT,
        _confirmed_audit(),
        parent_agent_run_id=consumer.id,
        side_effect_state=SideEffectState.CONFIRMED,
    )
    assert resolve_approval_history_result(_attempt(send_status="processing"), [consumer, audit]) is ApprovalHistoryResult.APPROVED


@pytest.mark.parametrize(
    "payload",
    [
        {"result": {"success": True}},
        {"dws_action_result": {"success": True}},
    ],
)
def test_nested_direct_receipt_success_shapes(payload):
    assert (
        resolve_approval_history_result(
            _attempt(oa_action="approve", send_status="pending", oa_action_result_json=json.dumps(payload)),
            [],
        )
        is ApprovalHistoryResult.APPROVED
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
