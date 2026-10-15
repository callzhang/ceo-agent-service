"""An old Attempt's current stop belongs to its task's current generation."""

import pytest

from app.audit_web import handle_rerun_attempt_post
from app.agent_contracts import ConsumerAgentResult
from app.external_action_identity import expected_external_action
from app.store import AgentRole, AutoReplyStore
from app.web_api.attempts import build_attempt_detail
from tests.test_console_attempt_detail_api import (
    _complete_audit_run,
    _complete_consumer_run,
    _consumer_result_task,
)


OLD_DETAIL = "old generation source read failed"
NEW_DETAIL = "new generation provider read failed"


def _finalize_failed_attempt(store, task, run, *, detail, error):
    return store.finalize_orchestrated_reply_task(
        task_id=task.id,
        expected_execution_generation=task.execution_generation,
        run_id=run.id,
        task_status="failed",
        task_error=error,
        available_at="",
        conversation_id=task.conversation_id,
        conversation_title=task.conversation_title,
        trigger_message_id=task.trigger_message_id,
        trigger_sender=task.trigger_sender,
        trigger_text=task.trigger_text,
        codex_reason=detail,
        codex_session_id="",
        codex_transcript_start_line=0,
        codex_transcript_end_line=0,
        audit_tool_events_json="[]",
        audit_summary=detail,
        send_status="failed",
        send_error=error,
        channel="dingtalk",
    )


def _old_failed_attempt(store):
    task = _consumer_result_task(store)
    old = store.claim_agent_run(
        task.id, task.execution_generation,
        role=AgentRole.CONSUMER, proposal_revision=0, turn_attempt=0,
        parent_agent_run_id=None, operation_id="", owner="old-consumer",
    ).run
    old = store.fail_agent_run(
        old.id, {"code": "codex_process_failed", "detail": OLD_DETAIL},
        owner="old-consumer",
    )
    attempt_id = _finalize_failed_attempt(
        store, task, old, detail=OLD_DETAIL, error="codex_process_failed",
    )
    return task, old, attempt_id


def _rotate_to_new_generation(store, task, attempt_id):
    status, _, _ = handle_rerun_attempt_post(store, attempt_id)
    assert status == 303
    rotated = store.get_reply_task(task.id)
    assert rotated is not None
    assert rotated.execution_generation != task.execution_generation
    claimed = store.claim_reply_task(task.id)
    assert claimed is not None
    assert claimed.execution_generation == rotated.execution_generation
    return claimed


def _fail_current_task(store, task, *, error):
    # Finalizing a new run reuses the trigger's Attempt row; this fixture must
    # retain that row's original run binding to exercise historical display.
    with store._immediate_write_transaction() as db:
        changed = db.execute(
            "update tasks set status='failed', error=? "
            "where record_type='reply_tasks' and id=? and execution_generation=? "
            "and status='processing'",
            (error, task.id, task.execution_generation),
        )
        assert changed.rowcount == 1


@pytest.mark.parametrize("role", [AgentRole.CONSUMER, AgentRole.AUDIT])
def test_old_attempt_shows_new_generation_failed_turn(tmp_path, role):
    store = AutoReplyStore(tmp_path / "cross-generation.sqlite3")
    task, old, attempt_id = _old_failed_attempt(store)
    current = _rotate_to_new_generation(store, task, attempt_id)
    assert store.get_reply_attempt(attempt_id).agent_run_id == old.id
    consumer = (
        _complete_consumer_run(store, current, owner="new-consumer")
        if role is AgentRole.AUDIT else None
    )
    run = store.claim_agent_run(
        current.id, current.execution_generation,
        role=role, proposal_revision=0, turn_attempt=0,
        parent_agent_run_id=consumer.id if consumer else None,
        operation_id="new-audit" if consumer else "", owner="new-failure",
    ).run
    run = store.fail_agent_run(
        run.id, {"code": "provider_read_failed", "detail": NEW_DETAIL},
        owner="new-failure",
    )
    _fail_current_task(store, current, error="provider_read_failed")
    original = store.get_reply_attempt(attempt_id)

    status, item = build_attempt_detail(store, attempt_id)

    assert status == 200
    assert item["status"]["raw"] == "failed"
    assert NEW_DETAIL in item["failure_reason"]
    assert OLD_DETAIL not in item["failure_reason"]
    assert item["what_happened"]["stopped_because"]["sentence"] == NEW_DETAIL
    assert store.get_reply_attempt(attempt_id) == original
    assert store.get_agent_run(old.id).execution_generation == task.execution_generation


def test_old_attempt_uses_current_task_error_when_new_generation_has_no_run(tmp_path):
    store = AutoReplyStore(tmp_path / "no-new-run.sqlite3")
    task, _, attempt_id = _old_failed_attempt(store)
    current = _rotate_to_new_generation(store, task, attempt_id)
    _fail_current_task(store, current, error="new_generation_source_unavailable")
    original = store.get_reply_attempt(attempt_id)

    status, item = build_attempt_detail(store, attempt_id)

    assert status == 200
    assert item["status"]["raw"] == "failed"
    assert "new_generation_source_unavailable" in item["failure_reason"]
    assert OLD_DETAIL not in item["failure_reason"]
    assert item["what_happened"]["stopped_because"]["code"] == "new_generation_source_unavailable"
    assert OLD_DETAIL not in item["what_happened"]["stopped_because"]["sentence"]
    assert store.get_reply_attempt(attempt_id) == original


@pytest.mark.parametrize(
    ("execution_status", "error_code"),
    [("uncertain", "external_action_uncertain"),
     ("failed", "external_action_failed")],
)
def test_old_attempt_shows_current_generation_execution_stop(
    tmp_path, execution_status, error_code,
):
    store = AutoReplyStore(tmp_path / "new-execution-stop.sqlite3")
    task, old, attempt_id = _old_failed_attempt(store)
    current = _rotate_to_new_generation(store, task, attempt_id)
    consumer = _complete_consumer_run(store, current, owner="current-consumer")
    audit = _complete_audit_run(store, current, consumer, owner="current-audit")
    reviewed = store.current_reviewed_candidate(current.id, current.execution_generation)
    assert reviewed is not None and reviewed["audit_run_id"] == audit.id
    candidate = ConsumerAgentResult.model_validate_json(reviewed["candidate_json"])
    action = candidate.proposal.actions[0]
    action_key = expected_external_action(
        action, action_index=0, business_object_key=current.business_object_key,
    )["external_action_key"]
    execution = store.claim_candidate_execution(
        reviewed["id"], reviewed["review_id"], "current-execution", 300,
    )
    assert execution is not None
    store.begin_candidate_action(execution["id"], "current-execution", 0, action_key)
    store.record_candidate_action_outcome(
        execution["id"], "current-execution", 0, execution_status,
        {"reason": "Current provider result requires attention"},
    )
    execution_summary = f"current execution {execution_status} after Audit approval"
    store.finish_candidate_execution(
        execution["id"], "current-execution", execution_status,
        {"outcome": "failed", "summary": execution_summary,
         "error": {"code": error_code, "retryable": execution_status == "uncertain",
                   "authorization_required": False}},
    )
    _fail_current_task(store, current, error=error_code)
    original = store.get_reply_attempt(attempt_id)

    status, item = build_attempt_detail(store, attempt_id)

    assert status == 200
    assert item["status"]["raw"] == "failed"
    assert execution_summary in item["failure_reason"]
    assert OLD_DETAIL not in item["failure_reason"]
    assert item["what_happened"]["stopped_because"] == {
        "kind": "execution", "code": error_code, "sentence": execution_summary,
    }
    assert "The reviewed update was published." not in item["failure_reason"]
    assert store.get_reply_attempt(attempt_id) == original
    assert store.get_agent_run(old.id).execution_generation == task.execution_generation


def test_settled_task_clears_audit_return_stop_without_rewriting_attempt(tmp_path):
    store = AutoReplyStore(tmp_path / "settled-return.sqlite3")
    task = _consumer_result_task(store)
    consumer = _complete_consumer_run(store, task, owner="return-consumer")
    candidate = store.adopted_candidate_for_consumer_run(consumer.id)
    assert candidate is not None
    audit = store.claim_agent_run(
        task.id, task.execution_generation,
        role=AgentRole.AUDIT, proposal_revision=0, turn_attempt=0,
        parent_agent_run_id=consumer.id, operation_id="return-audit",
        owner="return-audit",
    ).run
    audit = store.complete_agent_run(
        audit.id,
        {"outcome": "return", "summary": "Candidate needs correction",
         "proposal_revision": 0, "candidate_digest": candidate["candidate_digest"],
         "feedback": {"rule": "Material fact", "observation": "Evidence incomplete",
                      "requested_revision": "Read source", "observation_basis": "business",
                      "candidate_field_assertions": []},
         "error": {"code": "", "retryable": False, "authorization_required": False},
         "risk": "medium", "confidence": 0.8, "rule_coverage": 1.0,
         "information_completeness": 0.8},
        owner="return-audit",
    )
    attempt_id = _finalize_failed_attempt(
        store, task, audit, detail="Audit revision exhausted", error="audit_revision_exhausted",
    )
    assert store.close_unresolved_reply_task_already_settled(
        task.id, expected_execution_generation=task.execution_generation,
        settled_evidence="Verified independent completion of this task",
    )
    original = store.get_reply_attempt(attempt_id)

    status, item = build_attempt_detail(store, attempt_id)

    assert status == 200
    assert item["status"]["raw"] == "done"
    assert item["failure_reason"] == ""
    assert item["what_happened"]["stopped_because"] == {
        "kind": "none", "code": "", "sentence": "",
    }
    assert item["what_happened"]["open_for_human"] is False
    assert store.get_reply_attempt(attempt_id) == original
