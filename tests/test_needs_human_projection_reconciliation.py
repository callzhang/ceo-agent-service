from tests.support.native_task_fields import set_native_task_fields
import json

from app.agent_contracts import (
    AuditAgentResult,
    parse_persisted_consumer_result,
)
from app.reviewed_candidates import candidate_digest
from app.store import AgentRole, AutoReplyStore
from app.action_storage import update_action


def _legacy_input_only_question():
    fact = {"assertion": "Mina cannot verify the candidate's role", "references": ["message:mina"]}
    return parse_persisted_consumer_result({
        "outcome": "needs_human",
        "summary": "Need the principal to choose this hiring case",
        "proposal": None,
        "decision_options": [],
        "requested_input": "Choose the candidate's role and salary ceiling",
        "needs_human_reason": "Only Derek can make this current-instance choice",
        "decision_basis": {
            "verified_facts": [fact],
            "rule_evidence": [fact],
            "quality_explanation": "This is a current-instance hiring decision.",
            "no_external_action_evidence": [fact],
            "conclusion": "Ask Derek for the current hiring decision.",
        },
        "risk": "high",
        "confidence": 0.96,
        "rule_coverage": 1.0,
        "information_completeness": 0.62,
        "error": {"code": "", "retryable": False, "authorization_required": False},
    })


def _reusable_rule_question():
    fact = {"assertion": "The task class needs a reusable rule", "references": ["skill:policy"]}
    return parse_persisted_consumer_result({
        "outcome": "needs_human",
        "summary": "Choose the reusable rule for this task class",
        "proposal": None,
        "decision_options": [
            {
                "key": "wait",
                "label": "Wait for more evidence",
                "instruction": "For this task class, request source evidence first.",
                "consequence": "Future tasks wait for source evidence.",
                "applies_to": "task_class",
                "terminal_outcome": "skipped",
                "reason": "No current instance should be changed without evidence.",
            },
            {
                "key": "proceed",
                "label": "Proceed with the verified source",
                "instruction": "For this task class, use the verified source as the basis.",
                "consequence": "Future tasks use the verified source.",
                "applies_to": "task_class",
                "terminal_outcome": "skipped",
                "reason": "The rule applies to future tasks only.",
            },
        ],
        "requested_input": None,
        "needs_human_reason": "No reusable task-class rule covers this decision.",
        "decision_basis": {
            "verified_facts": [fact],
            "rule_evidence": [fact],
            "quality_explanation": "No current reusable rule settles this.",
            "no_external_action_evidence": [fact],
            "conclusion": "A reusable task-class rule is required.",
        },
        "risk": "low",
        "confidence": 0.99,
        "rule_coverage": 0.4,
        "information_completeness": 1.0,
        "error": {"code": "", "retryable": False, "authorization_required": False},
    })


def _approved_current_projection(path, candidate_result=None):
    store = AutoReplyStore(path)
    store.enqueue_reply_task(
        conversation_id="cid-mina",
        conversation_title="Mina",
        single_chat=False,
        trigger_message_id="msg-mina",
        trigger_create_time="2026-10-16 08:00:00",
        trigger_sender="Mina",
        trigger_text="Please confirm role and salary scope.",
        trigger_message_json=json.dumps({
            "open_conversation_id": "cid-mina",
            "open_message_id": "msg-mina",
            "conversation_title": "Mina",
            "single_chat": False,
            "sender_name": "Mina",
            "content": "Please confirm role and salary scope.",
            "create_time": "2026-10-16 08:00:00",
            "message_type": "text",
        }),
    )
    task = store.claim_reply_tasks(1)[0]
    consumer = store.claim_agent_run(
        task.id,
        task.execution_generation,
        role=AgentRole.CONSUMER,
        proposal_revision=0,
        turn_attempt=0,
        parent_agent_run_id=None,
        operation_id="",
        owner="consumer",
    ).run
    candidate_result = candidate_result or _legacy_input_only_question()
    store.complete_agent_run(
        consumer.id, candidate_result.model_dump(mode="json"), owner="consumer"
    )
    candidate = store.persist_review_candidate(task, consumer, candidate_result)
    audit = store.claim_agent_run(
        task.id,
        task.execution_generation,
        role=AgentRole.AUDIT,
        proposal_revision=0,
        turn_attempt=0,
        parent_agent_run_id=consumer.id,
        operation_id="audit-mina",
        owner="audit",
    ).run
    audit_result = AuditAgentResult(
        outcome="approve",
        summary="Legacy input-only question was approved.",
        proposal_revision=0,
        candidate_digest=candidate_digest(candidate_result),
        evidence_refs=(),
        feedback=None,
        error={"code": "", "retryable": False},
        risk="low",
        confidence=1.0,
        rule_coverage=1.0,
        information_completeness=1.0,
    ).model_dump(mode="json")
    store.complete_agent_run(audit.id, audit_result, owner="audit")
    store.record_candidate_review(candidate["id"], audit.id, audit_result)
    attempt_id = store.record_reply_attempt(
        conversation_id=task.conversation_id,
        conversation_title=task.conversation_title,
        trigger_message_id=task.trigger_message_id,
        trigger_sender=task.trigger_sender,
        trigger_text=task.trigger_text,
        action="agent_run",
        sensitivity_kind="general",
        audit_summary="Current-instance choice was requested.",
        send_status="needs_human",
        channel=task.channel,
    )
    with store._connect() as db:
        update_action(db, "reply_attempts", attempt_id, {"agent_run_id": audit.id})
        set_native_task_fields(db, 'reply_tasks', task.id, {'status': 'needs_human'})
    return store, task, attempt_id, candidate["id"], audit.id


def test_legacy_input_only_candidate_is_not_restored_as_current_needs_human(tmp_path):
    store, task, attempt_id, candidate_id, audit_run_id = _approved_current_projection(
        tmp_path / "legacy.sqlite3"
    )

    assert store.reconcile_invalid_needs_human_projections() == 1

    attempt = store.get_reply_attempt(attempt_id)
    assert attempt is not None
    assert attempt.send_status == "failed"
    assert attempt.send_error == "invalid_needs_human_projection"
    assert json.loads(attempt.human_decision_options_json) == []
    current_task = store.get_reply_task(task.id)
    assert current_task is not None
    assert current_task.status == "failed"
    assert current_task.error == "invalid_needs_human_projection"
    assert store.get_agent_run(audit_run_id).status == "completed"
    assert store.current_reviewed_candidate(task.id, task.execution_generation)["id"] == candidate_id
    assert store.get_candidate_execution(candidate_id) is None
    assert store.reconcile_invalid_needs_human_projections() == 0


def test_invalid_legacy_question_cannot_be_reactivated_after_reconciliation(tmp_path):
    store, task, attempt_id, _candidate_id, _audit_run_id = _approved_current_projection(
        tmp_path / "reactivation.sqlite3"
    )

    assert store.reconcile_invalid_needs_human_projections() == 1
    assert store.reconcile_valid_needs_human_projections() == 0
    assert store.get_reply_task(task.id).status == "failed"
    assert store.get_reply_attempt(attempt_id).send_status == "failed"


def test_reusable_task_class_question_is_not_demoted(tmp_path):
    store, task, attempt_id, _candidate_id, _audit_run_id = _approved_current_projection(
        tmp_path / "task-class.sqlite3", _reusable_rule_question()
    )

    assert store.reconcile_invalid_needs_human_projections() == 0
    assert store.reconcile_valid_needs_human_projections() == 0
    assert store.get_reply_task(task.id).status == "needs_human"
    assert store.get_reply_attempt(attempt_id).send_status == "needs_human"
