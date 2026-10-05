"""Attention exposes original current-run diagnostics without changing policy."""

import json

import pytest

import app.audit_web as audit_web
from app.store import AgentRole, AutoReplyStore


def _failed_task_and_run(tmp_path, error):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    store.enqueue_reply_task(
        conversation_id="diagnostic-chat", conversation_title="Diagnostic",
        single_chat=True, trigger_message_id="diagnostic-message",
        trigger_create_time="2026-10-04 13:05:00", trigger_sender="Morgan",
        trigger_text="Review this request.",
    )
    [task] = store.claim_reply_tasks(limit=1)
    run = store.claim_agent_run(
        task.id, task.execution_generation, role=AgentRole.AUDIT,
        proposal_revision=0, turn_attempt=0, parent_agent_run_id=None,
        operation_id="diagnostic", owner="test",
    ).run
    store.fail_agent_run(run.id, error, owner="test")
    store.fail_reply_task(
        task.id, "agent_reported_failure",
        expected_execution_generation=task.execution_generation,
    )
    return store, task, run


@pytest.mark.parametrize("source_code", ["provider_risk_rejected", "provider_rejected_risk"])
def test_reply_attention_shows_original_current_run_error_without_effect_claim(tmp_path, source_code):
    store, task, _ = _failed_task_and_run(tmp_path, {
        "code": "agent_reported_failure", "source": "agent", "source_code": source_code,
        "reported_summary": "运行工具调用被拒绝；尚未取得外部执行回执。", "retryable": False,
    })
    with store._connect() as db:
        before = {
            table: [tuple(row) for row in db.execute(f"select * from {table}")]
            for table in ("reply_tasks", "agent_runs", "reply_attempts", "sent_replies")
        }
    [row] = [row for row in audit_web._queue_attention_rows(store) if row["category"] == "Reply task"]
    assert row["id"] == str(task.id)
    assert row["status"] == "failed"
    assert row["error_code"] == source_code
    assert "agent" in row["error"]
    assert "Agent 说明" in row["error"]
    assert "尚未取得外部执行回执" in row["error"]
    with store._connect() as db:
        for table, original in before.items():
            assert [tuple(row) for row in db.execute(f"select * from {table}")] == original


@pytest.mark.parametrize("raw_error", ["invalid-json", "[]", '{"source_code":12}', "{}"])
def test_reply_attention_keeps_task_error_when_current_run_diagnostics_unusable(tmp_path, raw_error):
    store, _, run = _failed_task_and_run(tmp_path, {"code": "agent_reported_failure"})
    with store._connect() as db:
        db.execute("update agent_runs set structured_error_json=? where id=?", (raw_error, run.id))
    [row] = [row for row in audit_web._queue_attention_rows(store) if row["category"] == "Reply task"]
    assert row["error"] == "agent_reported_failure"
    assert row.get("error_code", "") in ("", "agent_reported_failure")


def test_reply_attention_does_not_use_another_generation_error(tmp_path):
    store, _, run = _failed_task_and_run(tmp_path, {
        "code": "agent_reported_failure", "source_code": "old-provider-code", "retryable": False,
    })
    with store._connect() as db:
        db.execute("update agent_runs set execution_generation='old' where id=?", (run.id,))
    [row] = [row for row in audit_web._queue_attention_rows(store) if row["category"] == "Reply task"]
    assert row["error"] == "agent_reported_failure"
    assert "old-provider-code" not in str(row)


def test_reply_attention_uses_latest_run_in_current_generation(tmp_path):
    store, task, _ = _failed_task_and_run(tmp_path, {
        "code": "agent_reported_failure", "source_code": "earlier-code", "retryable": False,
    })
    with store._connect() as db:
        db.execute(
            "insert into agent_runs (reply_task_id, execution_generation, role, status, "
            "operation_id, structured_error_json) values (?, ?, 'consumer', 'failed', ?, ?)",
            (task.id, task.execution_generation, "latest-diagnostic",
             json.dumps({"code": "latest-runtime-error", "source": "runtime"})),
        )
    [row] = [row for row in audit_web._queue_attention_rows(store) if row["category"] == "Reply task"]
    assert row["error_code"] == "latest-runtime-error"
    assert "runtime" in row["error"]
    assert "earlier-code" not in str(row)
