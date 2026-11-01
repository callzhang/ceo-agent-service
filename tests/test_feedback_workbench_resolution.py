"""Feedback closure uses genuine completed native Workbench execution."""
import pytest

from app.store import AutoReplyStore
from app.workbench.store import WorkbenchStore


def _receipt():
    return {
        "commit_sha": "b" * 40,
        "test_evidence": {"pytest": {"exit_code": 0}},
        "restart_evidence": {"launchd_label": "com.ceo-agent-service.main", "before_pid": 10, "after_pid": 11},
        "health_evidence": {"url": "http://127.0.0.1:8765/healthz", "status_code": 200, "ok": True},
        "backlog_evidence": {"processing": 0, "failed": 0, "retryable": 0},
    }


def _prepared(tmp_path, *, fault=""):
    store = AutoReplyStore(tmp_path / "feedback.sqlite3")
    if fault != "old_completion":
        store.upsert_feedback_event(key="feedback-native", feedback_token="native")
        store.claim_feedback_processing_items("batch-native", ["feedback-native"])
    native_now = {"now": "2000-01-01T00:00:00Z"} if fault == "old_completion" else {}
    wb = WorkbenchStore(store.path)
    task = wb.create_task(title="Repair feedback", runtime_kind="codex")
    turn = wb.create_turn(task.id, user_text="Repair this issue", client_request_id="repair")
    assert wb.claim_next_turn(owner="fixture", **native_now) is not None
    if fault != "runtime_missing":
        runtime = store.claim_runtime_operation_attempt(
            "workbench", turn.id, "codex_oauth", "codex_cli", "local_oauth", "test-model", owner="fixture", **native_now,
        )
        if fault != "runtime_running":
            store.complete_agent_runtime_attempt(runtime.id, "fixture-session", "", 0, 1, owner="fixture", **native_now)
    if fault != "turn_running":
        wb.complete_turn(turn.id, status="completed", final_text="Repair verified", owner="fixture", **native_now)
    association = {"workbench_task_id": task.id, "workbench_turn_id": turn.id, "attempt_id": 0, "agent_run_id": 0}
    if fault in {"valid_business", "mismatched_business", "old_business"}:
        from app.action_storage import update_action
        store.enqueue_reply_task(
            conversation_id="business", conversation_title="Business", single_chat=True,
            trigger_message_id="message", trigger_create_time="2026-01-01",
            trigger_sender="Fixture", trigger_text="Local no-effect decision",
        )
        business_task = store.get_reply_task_for_message("business", "message")
        business_task = store.claim_reply_task(business_task.id)
        business_now = {"now": "2000-01-01T00:00:00Z"} if fault == "old_business" else {}
        run = store.claim_agent_run(
            business_task.id, business_task.execution_generation, role="consumer",
            proposal_revision=0, turn_attempt=0, parent_agent_run_id=None,
            operation_id="", owner="fixture", **business_now,
        ).run
        reply_id = store.record_reply_attempt(
            conversation_id="business", conversation_title="Business",
            trigger_message_id="message", trigger_sender="Fixture", trigger_text="Local no-effect decision",
            action="no_action", sensitivity_kind="general", send_status="completed",
        )
        with store._connect() as db:
            update_action(db, "reply_attempts", reply_id, {"agent_run_id": run.id})
        store.complete_agent_run(run.id, {"outcome": "no_action", "summary": "Local fixture complete"}, owner="fixture", **business_now)
        association.update(attempt_id=reply_id, agent_run_id=run.id)
        if fault == "mismatched_business":
            other = store.claim_agent_run(
                business_task.id, business_task.execution_generation, role="audit",
                proposal_revision=0, turn_attempt=0, parent_agent_run_id=run.id,
                operation_id="fixture-audit", owner="fixture",
            ).run
            association["agent_run_id"] = other.id
    if fault == "wrong_task":
        association["workbench_task_id"] = wb.create_task(title="Unrelated", runtime_kind="codex").id
    if fault == "invalid_business":
        association.update(attempt_id=999999, agent_run_id=999999)
    elif fault == "attempt_only":
        association["attempt_id"] = 999999
    elif fault == "run_only":
        association["agent_run_id"] = 999999
    if fault == "old_completion":
        store.upsert_feedback_event(key="feedback-native", feedback_token="native")
        store.claim_feedback_processing_items("batch-native", ["feedback-native"])
    store.associate_feedback_processing_turn("feedback-native", **association)
    receipt = _receipt()
    store.patch_feedback_processing_item_evidence(
        "feedback-native", **{k: receipt[k] for k in ("commit_sha", "test_evidence", "restart_evidence", "health_evidence")},
    )
    receipt["associations"] = {"feedback-native": association}
    return store, receipt


@pytest.mark.parametrize("fault", ["", "valid_business"])
def test_completed_workbench_resolves_without_business_reply_ids(tmp_path, fault):
    store, receipt = _prepared(tmp_path, fault=fault)
    assert store.resolve_feedback_processing_batch("batch-native", receipt, commit_is_ancestor=True)
    assert store.get_feedback_processing_item("feedback-native").status == "resolved"


@pytest.mark.parametrize("fault", ["wrong_task", "turn_running", "runtime_missing", "runtime_running", "invalid_business", "attempt_only", "run_only", "old_completion", "mismatched_business", "old_business"])
def test_unproven_workbench_completion_rejected_without_state_change(tmp_path, fault):
    store, receipt = _prepared(tmp_path, fault=fault)
    with pytest.raises(ValueError):
        store.resolve_feedback_processing_batch("batch-native", receipt, commit_is_ancestor=True)
    assert store.get_feedback_processing_item("feedback-native").status == "processing"
    assert store.get_feedback_processing_batch("batch-native").status == "processing"
