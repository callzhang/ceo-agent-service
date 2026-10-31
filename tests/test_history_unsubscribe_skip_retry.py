"""Formal skipped-unsubscribe recovery; all provider observations are scripted."""
import json
from dataclasses import replace

import pytest

from app.action_storage import insert_email_action, update_email_action, email_action_projection
from app.email_unsubscribe import UnsubscribePageState
from test_email_unsubscribe_direct import (
    ACTION_IDENTITY, CLASSIFICATION_ID, _operation, _action_required, _control,
    _effect, _terminal,
)
from test_history_task_retry_api import _client


def settled(tmp_path, state=None):
    observation = (
        _terminal(_effect(), state, "Provider terminal page") if state is not None
        else _action_required(_control("confirmation_email", "confirm", "control-email"))
    )
    operation, task, email, browser = _operation(tmp_path, [observation])
    result = operation.execute(task.id)
    assert result["status"] == "done", result
    store = operation.task_store
    attempt_id = store.record_reply_attempt(
        conversation_id=task.conversation_id, conversation_title=task.conversation_title,
        trigger_message_id=task.trigger_message_id, trigger_sender=task.trigger_sender,
        trigger_text=task.trigger_text, action="direct_unsubscribe",
        sensitivity_kind="normal", send_status=("skipped" if result["outcome"].startswith("skipped") else "completed"), channel="email",
        mail_action_result_json=json.dumps(result),
    )
    store.complete_reply_task(task.id, expected_execution_generation=task.execution_generation)
    return operation, store.get_reply_task(task.id), email, browser, attempt_id


@pytest.mark.parametrize("state", [None, UnsubscribePageState.LOGIN_REQUIRED])
def test_formal_retry_restores_compacted_skip_and_preserves_evidence(tmp_path, state):
    operation, task, email, browser, attempt_id = settled(tmp_path, state)
    store = operation.task_store
    before = email.get_email_unsubscribe_state_snapshot(ACTION_IDENTITY)
    old_attempt = store.get_reply_attempt(attempt_id)
    assert json.loads(task.trigger_message_json)["input_compacted"] is True

    response = _client(store).post(f"/api/console/history/tasks/{task.id}/retry")

    assert response.status_code == 200, response.text
    current = store.get_reply_task(task.id)
    assert current.status == "pending"
    assert current.execution_generation != task.execution_generation
    assert current.current_input_id == task.current_input_id
    assert email.get_email_unsubscribe_state_snapshot(ACTION_IDENTITY) == before
    assert store.get_reply_attempt(attempt_id) == old_attempt
    assert len(browser.calls) == 1  # The API itself performs no provider call.
    assert json.loads(store.get_service_state(
        f"email_unsubscribe_manual_retry:v1:{ACTION_IDENTITY}"
    )) == {"retry_count": 1}

    browser.observations.append(_terminal(
        replace(_effect(), previous_effect_digest=before["receipt"]["effect_digest"]),
        UnsubscribePageState.DONE, "You are unsubscribed.",
    ))
    claimed = store.claim_reply_task(task.id)
    assert claimed is not None
    result = operation.execute(task.id)
    assert result["outcome"] == "done", result
    assert email.get_email_unsubscribe_effect(
        ACTION_IDENTITY, before["receipt"]["effect_digest"]
    ) is not None


@pytest.mark.parametrize("state", [
    UnsubscribePageState.DONE, UnsubscribePageState.CAPTCHA, UnsubscribePageState.PAYMENT,
])
def test_formal_retry_does_not_reopen_nonretryable_receipt(tmp_path, state):
    operation, task, email, browser, _ = settled(tmp_path, state)
    response = _client(operation.task_store).post(f"/api/console/history/tasks/{task.id}/retry")
    assert response.status_code == 409
    assert operation.task_store.get_reply_task(task.id).execution_generation == task.execution_generation
    assert len(browser.calls) == 1


@pytest.mark.parametrize("blocker", ["budget", "cancelled", "identity", "entry", "plan", "marker"])
def test_formal_skip_retry_rejects_unsafe_or_exhausted_recovery(tmp_path, blocker):
    operation, task, email, browser, _ = settled(tmp_path)
    store = operation.task_store
    before = email.get_email_unsubscribe_state_snapshot(ACTION_IDENTITY)
    if blocker == "budget":
        store.set_service_state(f"email_unsubscribe_manual_retry:v1:{ACTION_IDENTITY}", '{"retry_count":3}')
    elif blocker == "cancelled":
        with email._connect() as db:
            insert_email_action(db, "email_unsubscribe_cancellations", {
                "action_identity": ACTION_IDENTITY, "decision_json": '{"decision":"keep_subscription"}',
            })
    elif blocker in {"identity", "entry"}:
        field = "stable_message_identity" if blocker == "identity" else "unsubscribe_entries[0].reference"
        with store._connect() as db:
            db.execute(
                "update task_inputs set input_provenance_json=json_set(input_provenance_json,?,?) where id=?",
                ("$.source." + field, "different", task.current_input_id),
            )
    elif blocker == "plan":
        with email._connect() as db:
            db.execute("update emails set class_current_action_plan_id=null where classification_id=?", (CLASSIFICATION_ID,))
    else:
        store.set_service_state(f"email_unsubscribe_execution_pending:v1:{ACTION_IDENTITY}", '{"state":"unknown"}')
    response = _client(store).post(f"/api/console/history/tasks/{task.id}/retry")
    assert response.status_code == 409, response.text
    assert store.get_reply_task(task.id).status == "done"
    assert store.get_reply_task(task.id).execution_generation == task.execution_generation
    assert email.get_email_unsubscribe_state_snapshot(ACTION_IDENTITY) == before
    assert len(browser.calls) == 1


def test_formal_skip_retry_budget_cannot_be_reset_by_a_new_generation(tmp_path):
    operation, task, email, browser, _ = settled(tmp_path)
    store = operation.task_store
    for expected in (1, 2, 3):
        response = _client(store).post(f"/api/console/history/tasks/{task.id}/retry")
        assert response.status_code == 200, response.text
        current = store.claim_reply_task(task.id)
        assert current is not None
        browser.observations.append(_action_required(_control("confirmation_email", "confirm", "control-email")))
        assert operation.execute(task.id)["outcome"] == "skipped_no_reliable_entry"
        store.complete_reply_task(task.id, expected_execution_generation=current.execution_generation)
        assert json.loads(store.get_service_state(f"email_unsubscribe_manual_retry:v1:{ACTION_IDENTITY}"))["retry_count"] == expected
    response = _client(store).post(f"/api/console/history/tasks/{task.id}/retry")
    assert response.status_code == 409
    assert len(browser.calls) == 4


@pytest.mark.parametrize("blocker", ["run", "runtime", "claim", "continuation"])
def test_formal_skip_retry_never_reclaims_in_flight_execution(tmp_path, blocker):
    from app.store import AgentRole

    operation, task, email, browser, attempt_id = settled(tmp_path)
    store = operation.task_store
    old_attempt = store.get_reply_attempt(attempt_id)
    if blocker in {"run", "runtime"}:
        run = store.claim_agent_run(
            task.id, task.execution_generation, role=AgentRole.CONSUMER,
            proposal_revision=0, turn_attempt=0, parent_agent_run_id=None,
            operation_id="", owner="test-owner",
        ).run
        if blocker == "runtime":
            runtime = store.claim_agent_runtime_attempt(
                run.id, "codex_oauth", "codex_cli", "local_oauth", "test-model",
            )
            store.fail_agent_run(run.id, {"code": "fixture_parent_finished"}, owner="test-owner")
            assert store.get_agent_runtime_attempt(runtime.id).status == "starting"
    elif blocker == "claim":
        with email._connect() as db:
            rowid = db.execute(
                f"select rowid from ({email_action_projection('email_unsubscribe_claims', include_rowid=True)}) where action_identity=?",
                (ACTION_IDENTITY,),
            ).fetchone()[0]
            update_email_action(db, "email_unsubscribe_claims", rowid, {"status": "uncertain"})
        assert email.get_email_unsubscribe_claim(ACTION_IDENTITY)["status"] == "uncertain"
    else:
        receipt = email.get_email_unsubscribe_receipt(ACTION_IDENTITY)
        with email._connect() as db:
            insert_email_action(db, "email_unsubscribe_continuations", {
                "action_identity": ACTION_IDENTITY, "effect_digest": receipt["effect_digest"],
                "observation_reference": "state-test", "controls_json": "[]",
                "created_at": "2026-10-31T00:00:00Z", "updated_at": "2026-10-31T00:00:00Z",
            })
    before = email.get_email_unsubscribe_state_snapshot(ACTION_IDENTITY)
    response = _client(store).post(f"/api/console/history/tasks/{task.id}/retry")
    assert response.status_code == 409, response.text
    assert store.get_reply_task(task.id).execution_generation == task.execution_generation
    assert store.get_reply_attempt(attempt_id) == old_attempt
    assert email.get_email_unsubscribe_state_snapshot(ACTION_IDENTITY) == before
    assert len(browser.calls) == 1
