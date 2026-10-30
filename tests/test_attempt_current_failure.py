"""Current stop reasons must not inherit failures recovered by later turns."""

import json
from types import SimpleNamespace

from app.attempt_what_happened import build_what_happened
from app.audit_web import _agent_failure_reason_text
from app.store import AgentRole, AgentRun


def run(run_id, status, *, detail="", summary="", outcome="", role=AgentRole.AUDIT):
    return AgentRun.model_construct(
        id=run_id, role=role, status=status,
        structured_error_json=json.dumps({"code": "codex_result_invalid", "detail": detail}),
        adopted_result_json=json.dumps({"summary": summary, "outcome": outcome}),
        final_result_json="", tool_events=[], reply_task_id=None,
        execution_generation="current", side_effect_state="none",
    )


def attempt(summary="", code=""):
    return SimpleNamespace(send_status="failed", audit_summary=summary,
                           send_error=code, codex_reason="", agent_run_id=3)


def test_current_failure_uses_latest_failed_turn_only():
    runs = [run(1, "failed", detail="old sensitive-value error"),
            run(2, "failed", detail="old document permission error"),
            run(3, "failed", detail="current provider unavailable")]
    reason = _agent_failure_reason_text(attempt(), runs)
    assert "current provider unavailable" in reason
    assert "old sensitive" not in reason
    assert "old document" not in reason


def test_current_failure_after_recovered_retry_uses_persisted_stop():
    runs = [run(1, "failed", detail="agent_result_contains_sensitive_value"),
            run(3, "completed", summary="Current revision still unsuitable", outcome="return")]
    reason = _agent_failure_reason_text(
        attempt("audit_revision_exhausted: Current revision still unsuitable", "audit_revision_exhausted"), runs,
    )
    assert reason == "audit_revision_exhausted: Current revision still unsuitable"
    assert "agent_result_contains_sensitive_value" not in reason
    assert "旧记录" not in reason


def test_later_completed_turn_clears_historical_runtime_stop():
    result = build_what_happened([
        run(1, "failed", detail="agent_result_contains_sensitive_value"),
        run(2, "completed", outcome="approve"),
    ])
    assert result["stopped_because"] == {"kind": "none", "code": "", "sentence": ""}


def test_final_audit_return_is_current_stop():
    result = build_what_happened([
        run(1, "failed", detail="old sensitive-value error"),
        run(2, "completed", summary="Current candidate requires correction", outcome="return"),
    ])
    assert result["stopped_because"] == {
        "kind": "rule", "code": "return", "sentence": "Current candidate requires correction",
    }


def test_persisted_execution_stop_overrides_recovered_agent_errors():
    stop = {"kind": "execution", "code": "external_action_uncertain",
            "sentence": "Accepted return awaits native readback"}
    result = build_what_happened([
        run(1, "failed", detail="old document access error"),
        run(2, "completed", outcome="approve"),
    ], stop_reason=stop)
    assert result["stopped_because"] == stop
    assert result["reached_the_outside_world"] is False
    assert result["external_actions"] == []


def test_reconciled_execution_does_not_keep_showing_the_old_current_failure(tmp_path, monkeypatch):
    from app.agent_contracts import ConsumerAgentResult
    from app.external_action_identity import expected_external_action
    from app.store import AutoReplyStore
    from app.system_executor import ActionOutcome, SystemExecutor
    from app.web_api.attempts import build_attempt_detail
    from tests.test_audit_agent import _candidate
    from tests.test_console_attempt_detail_api import (
        _consumer_result_task, _complete_consumer_run, _complete_audit_run,
    )
    store = AutoReplyStore(tmp_path / "reconciled.sqlite3")
    task = _consumer_result_task(store)
    payload = _candidate().model_dump(mode="json")
    payload["proposal"]["actions"][0].update(
        capability="dingtalk-oa", operation="revert_task",
        target={"process_instance_id": "process", "task_id": "original-task",
                "target_activity_id": "sid-startevent"},
        payload={"remark": "Exact reviewed material request"},
    )
    consumer = _complete_consumer_run(store, task, owner="consumer", payload=payload)
    audit = _complete_audit_run(store, task, consumer, owner="audit")
    reviewed = store.current_reviewed_candidate(task.id, task.execution_generation)
    candidate = ConsumerAgentResult.model_validate_json(reviewed["candidate_json"])
    action = candidate.proposal.actions[0]
    key = expected_external_action(action, action_index=0, business_object_key=task.business_object_key)["external_action_key"]
    execution = store.claim_candidate_execution(reviewed["id"], reviewed["review_id"], "initial", 300)
    store.begin_candidate_action(execution["id"], "initial", 0, key)
    store.record_candidate_action_outcome(execution["id"], "initial", 0, "uncertain", {"accepted": True})
    store.finish_candidate_execution(execution["id"], "initial", "uncertain", {
        "outcome": "failed", "summary": "Previous verification pending",
        "error": {"code": "external_action_uncertain", "retryable": True, "authorization_required": False},
    })
    attempt_id = store.finalize_orchestrated_reply_task(
        task_id=task.id, expected_execution_generation=task.execution_generation,
        run_id=audit.id, task_status="failed", task_error="external_action_uncertain", available_at="",
        conversation_id=task.conversation_id, conversation_title=task.conversation_title,
        trigger_message_id=task.trigger_message_id, trigger_sender=task.trigger_sender,
        trigger_text=task.trigger_text, audit_tool_events_json="[]",
        audit_summary="Previous verification pending", send_status="failed",
        send_error="external_action_uncertain", channel="dingtalk",
    )
    class ReconcileOnly:
        def reconcile(self, *args, **kwargs):
            return ActionOutcome("verified", {"verified": True, "receipt_id": "native-readback"})

        def dispatch(self, *args, **kwargs):
            raise AssertionError("Must never replay the accepted action")

    assert SystemExecutor(store, {(action.capability, action.operation): ReconcileOnly()}).execute(
        task, reviewed["id"], reviewed["review_id"],
    ).outcome == "executed"
    assert store.close_unresolved_reply_task_already_settled(
        task.id, expected_execution_generation=task.execution_generation,
        settled_evidence="Exact existing native receipt verified",
    )
    # Current input identity may no longer resolve an old OA scanner message key.
    monkeypatch.setattr(store, "get_reply_task_for_message", lambda *a, **kw: None)
    original = store.get_reply_attempt(attempt_id)
    status, item = build_attempt_detail(store, attempt_id)
    assert status == 200
    assert item["system_execution"]["verified_actions"] == 1
    assert item["failure_reason"] == ""
    assert item["what_happened"]["stopped_because"]["kind"] == "none"
    assert store.get_reply_attempt(attempt_id) == original
