from datetime import UTC, datetime, timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.store import AutoReplyStore
from app.web_api.history_tasks import register_history_task_routes
from tests.test_audit_agent import _candidate


def fixture_task(tmp_path, *, role="audit"):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    now = datetime.now(UTC).replace(microsecond=0)
    old = now - timedelta(hours=3)
    store.enqueue_reply_task(conversation_id="cid-stalled", conversation_title="Stalled",
        single_chat=True, trigger_message_id="msg-stalled", trigger_sender="Principal",
        trigger_text="Review current source", trigger_create_time=old.isoformat(),
        execution_generation="generation-stalled")
    from app.dispatcher.adapters import ReplyQueueAdapter
    adapter = ReplyQueueAdapter(store)
    envelope = adapter.claim(old, owner="dispatcher-owner", lease=timedelta(minutes=5))
    task = store.get_reply_task(int(envelope.source_id))
    adapter.let_go(envelope, owner="dispatcher-owner")
    consumer = store.claim_agent_run(task.id, task.execution_generation, role="consumer",
        proposal_revision=0, turn_attempt=0, parent_agent_run_id=None,
        operation_id="", owner="consumer-owner", now=old).run
    run = consumer
    if role == "audit":
        store.complete_agent_run(consumer.id, _candidate().model_dump(mode="json"),
                                 owner="consumer-owner", now=old)
        run = store.claim_agent_run(task.id, task.execution_generation, role="audit",
            proposal_revision=0, turn_attempt=0, parent_agent_run_id=consumer.id,
            operation_id="review", owner="audit-owner", now=old).run
    runtime = store.claim_agent_runtime_attempt(run.id, "codex_oauth", "codex_cli",
                                               "local_oauth", "gpt-test")
    with store._connect() as db:
        db.execute("update tasks set locked_at=? where id=?", (old.strftime("%Y-%m-%d %H:%M:%S"), task.id))
        db.execute("update runtime_attempts set updated_at=?,started_at=? where id=?",
                   (old.strftime("%Y-%m-%d %H:%M:%S"), old.strftime("%Y-%m-%d %H:%M:%S"), runtime.id))
    return store, task, run, runtime, now


@pytest.mark.parametrize("role", ["consumer", "audit"])
def test_exact_stalled_task_recovery_preserves_candidate_and_history(tmp_path, role):
    from app.reply_task_recovery import recover_stalled_reply_task
    store, task, run, runtime, now = fixture_task(tmp_path, role=role)
    recovered = recover_stalled_reply_task(store, task.id,
        expected_execution_generation=task.execution_generation, now=now)
    assert recovered.status == "pending"
    assert recovered.execution_generation == task.execution_generation
    assert store.get_agent_run(run.id).status == "failed"
    assert store.get_agent_runtime_attempt(runtime.id).status == "failed"
    assert store.get_reply_task(task.id).force_new_decision is True
    if role == "audit":
        parent = store.get_agent_run(run.parent_agent_run_id)
        assert parent.status == "completed"
        assert parent.adopted_result_json


@pytest.mark.parametrize("change", ["wrong_generation", "recent_run", "recent_runtime", "started_effect"])
def test_recovery_rejects_changed_live_or_effectful_task_without_mutation(tmp_path, change):
    from app.reply_task_recovery import recover_stalled_reply_task
    store, task, run, runtime, now = fixture_task(tmp_path)
    generation = task.execution_generation
    if change == "wrong_generation":
        generation = "another-generation"
    else:
        with store._connect() as db:
            if change == "recent_run":
                db.execute("update runs set updated_at=? where id=?", (now.strftime("%Y-%m-%d %H:%M:%S"), run.id))
            elif change == "recent_runtime":
                db.execute("update runtime_attempts set updated_at=? where id=?", (now.strftime("%Y-%m-%d %H:%M:%S"), runtime.id))
            else:
                db.execute("update runtime_attempts set first_effect_started_at=? where id=?", (now.strftime("%Y-%m-%d %H:%M:%S"), runtime.id))
    with pytest.raises(ValueError):
        recover_stalled_reply_task(store, task.id, expected_execution_generation=generation, now=now)
    assert store.get_reply_task(task.id).status == "processing"
    assert store.get_agent_run(run.id).status == "running"


def test_stalled_recovery_api_requires_explicit_current_generation(tmp_path):
    store, task, run, runtime, now = fixture_task(tmp_path)
    app = FastAPI()
    register_history_task_routes(app, lambda: store)
    client = TestClient(app)
    url = f"/api/console/history/tasks/{task.id}/recover-stalled"
    assert client.post(url, json={}).status_code == 422
    assert client.post(url, json={"execution_generation": "wrong"}).status_code == 409
    response = client.post(url, json={"execution_generation": task.execution_generation})
    assert response.status_code == 200, response.text
    assert response.json()["item"]["status"] == "pending"
    assert client.post(url, json={"execution_generation": task.execution_generation}).status_code == 409


@pytest.mark.parametrize("effect", ["prepared_intent", "delivery"])
def test_recovery_rejects_saved_external_evidence(tmp_path, effect):
    from app.reply_task_recovery import recover_stalled_reply_task
    from tests.test_reply_task_deferral import _insert_retained_action_fact
    store, task, run, runtime, now = fixture_task(tmp_path)
    if effect == "delivery":
        store.record_sent_reply(task.conversation_id, task.trigger_message_id, "already delivered")
    else:
        with store._connect() as db:
            _insert_retained_action_fact(db, "agent_effect_intents", {
                "agent_run_id": run.id, "authorization_id": "auth", "action_index": 0,
                "receipt_operation_id": "op", "capability": "c", "operation": "o",
                "operation_digest": "d", "arguments_digest": "a",
                "target_identifiers_json": "{}", "state": "prepared",
            }, run_id=run.id)
    with pytest.raises(ValueError, match="external action|delivery receipt"):
        recover_stalled_reply_task(store, task.id,
            expected_execution_generation=task.execution_generation, now=now)
    assert store.get_reply_task(task.id).status == "processing"
    assert store.get_agent_run(run.id).status == "running"


def test_recovery_cli_only_changes_the_named_task(tmp_path):
    from app.reply_task_recovery import main
    store, task, run, runtime, now = fixture_task(tmp_path)
    store.enqueue_reply_task(conversation_id="unrelated", conversation_title="Unrelated",
        single_chat=True, trigger_message_id="other", trigger_sender="Other",
        trigger_text="Not this recovery", trigger_create_time=now.isoformat())
    other = store.get_reply_task_for_message("unrelated", "other")
    assert main(["--db", str(store.path), "--task-id", str(task.id),
                 "--execution-generation", task.execution_generation, "--apply"]) == 0
    assert store.get_reply_task(task.id).status == "pending"
    assert store.get_reply_task(other.id).status == "pending"
    assert store.list_agent_runs_for_task_generation(other.id, other.execution_generation) == []


def test_completed_external_action_blocks_recovery(tmp_path):
    from app.reply_task_recovery import recover_stalled_reply_task
    from app.action_entity_storage import create_action, append_action_receipt
    store, task, run, runtime, now = fixture_task(tmp_path)
    with store._connect() as db:
        action = create_action(db, values={"operation": "send_message", "status": "verified",
            "external_action_key": "already-sent", "business_object_key": task.business_object_key,
            "action_identity": "notice", "agent_run_id": run.id})
        receipt = append_action_receipt(db, action["id"], values={
            "operation": "send_message", "target": "{}", "provider_result_json": "{}",
            "result_digest": "digest", "agent_run_id": run.id, "created_at": now.isoformat()})
        append_action_receipt(db, action["id"], values={
            "operation": "send_message", "target": "{}", "result_digest": "digest",
            "agent_run_id": run.id, "provider_receipt_id": receipt, "created_at": now.isoformat()})
    with pytest.raises(ValueError, match="external action result"):
        recover_stalled_reply_task(store, task.id,
            expected_execution_generation=task.execution_generation, now=now)
    assert store.get_reply_task(task.id).status == "processing"


def test_existing_failed_runtime_history_is_not_rewritten(tmp_path):
    from app.reply_task_recovery import recover_stalled_reply_task
    store, task, run, runtime, now = fixture_task(tmp_path)
    with store._connect() as db:
        db.execute("update runtime_attempts set status='failed',failure_code='original_failure' where id=?", (runtime.id,))
    recover_stalled_reply_task(store, task.id, expected_execution_generation=task.execution_generation, now=now)
    assert store.get_agent_runtime_attempt(runtime.id).failure_code == "original_failure"


@pytest.mark.parametrize("change", ["run_lease", "runtime_lease", "dispatcher_lease", "dispatcher_pid", "no_dispatcher"])
def test_recovery_never_steals_a_still_owned_task(tmp_path, change):
    from app.reply_task_recovery import recover_stalled_reply_task
    import os
    store, task, run, runtime, now = fixture_task(tmp_path)
    future = (now + timedelta(hours=1)).isoformat()
    with store._connect() as db:
        if change == "run_lease":
            db.execute("update runs set lease_expires_at=? where id=?", (future, run.id))
        elif change == "runtime_lease":
            db.execute("update runtime_attempts set lease_expires_at=? where id=?", (future, runtime.id))
        elif change == "dispatcher_lease":
            db.execute("update tasks set lease_expires_at=? where id=?", (future, task.id))
        elif change == "dispatcher_pid":
            db.execute("update tasks set owner_pid=? where id=?", (os.getpid(), task.id))
        else:
            db.execute("update tasks set adapter_name=null where id=?", (task.id,))
    with pytest.raises(ValueError):
        recover_stalled_reply_task(store, task.id, expected_execution_generation=task.execution_generation, now=now)
    assert store.get_reply_task(task.id).status == "processing"
    assert store.get_agent_run(run.id).status == "running"


def test_older_generation_started_effect_blocks_recovery(tmp_path):
    from app.reply_task_recovery import recover_stalled_reply_task
    store, task, run, runtime, now = fixture_task(tmp_path)
    old_run = store.claim_agent_run(task.id, task.execution_generation, role="consumer",
        proposal_revision=1, turn_attempt=0, parent_agent_run_id=run.id, operation_id="", owner="old").run
    old_runtime = store.claim_agent_runtime_attempt(old_run.id, "codex_oauth", "codex_cli", "local_oauth", "test")
    with store._connect() as db:
        db.execute("update runs set execution_generation='old-generation' where id=?", (old_run.id,))
        db.execute("update runtime_attempts set first_effect_started_at=? where id=?", (now.isoformat(), old_runtime.id))
    with pytest.raises(ValueError, match="started an external action"):
        recover_stalled_reply_task(store, task.id, expected_execution_generation=task.execution_generation, now=now)
    assert store.get_reply_task(task.id).status == "processing"
