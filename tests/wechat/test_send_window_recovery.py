"""Pre-send waiting and recovery use the original reviewed delivery evidence."""
import json
from types import SimpleNamespace

import pytest

from app.system_executor import SystemExecutor
from app.service_message_sender import agent_message_delivery_key
from app.wechat import accessibility
from tests.support.native_task_fields import set_native_task_fields
from tests.wechat.test_send_message_handler import _setup, Runner
from tests.wechat.test_standard_review_gate import IDENTITY, _result, _reviewed


def test_sender_keeps_waiting_after_activation_attempts_are_exhausted(monkeypatch):
    calls = []
    idle = []
    runner = accessibility.MacWechatAccessibility(restore_focus=False)
    fake_time = SimpleNamespace(sleep=lambda _seconds: None)
    monkeypatch.setattr(runner, "_ax", lambda: (
        fake_time, lambda: True, lambda _pid: object(),
        lambda *_args: (0, None), None, None, None,
    ))
    monkeypatch.setattr(runner, "_wechat_pid", lambda: 123)
    monkeypatch.setattr(runner, "_frontmost_app", lambda: None)
    monkeypatch.setattr(runner, "_wait_until_idle", lambda **kw: idle.append(kw))
    monkeypatch.setattr(runner, "_wait_for_interaction_slot", lambda **_kw: None)
    def activate(*_args, **_kwargs):
        calls.append(True)
        return len(calls) == 7
    monkeypatch.setattr(accessibility, "_activate_wait", activate)
    monkeypatch.setattr(accessibility, "_open_target", lambda *_args, **_kw: None)

    result = runner.send("Alex", "approved reply")

    assert len(calls) == 7
    assert len(idle) == 7
    assert result.failure_reason == "target_open_failed"
    assert result.action_performed is False


def _failed_action_and_new_review(tmp_path):
    runner = Runner(accessibility.AccessibilityResult(False, False, failure_reason="wechat_ui_not_ready"))
    store, task, row, action, handler, runner, prepared = _setup(tmp_path, runner)
    executor = SystemExecutor(store, {("wechat", "send_message"): handler}, owner="sender")
    assert executor.execute(task, row["candidate_id"], row["review_id"]).outcome == "failed"
    delivery = store.get_wechat_delivery_for_task(task.id)
    assert delivery.pre_action_failure and delivery.status == "failed"
    with store._connect() as db:
        set_native_task_fields(db, "reply_tasks", task.id, {"execution_generation": "next"})
    task = task.model_copy(update={"execution_generation": "next"})
    key = agent_message_delivery_key(business_object_key=task.business_object_key,
        action_identity=IDENTITY, execution_generation="next", proposal_revision=0)
    prepared = store.prepare_outbound_postfix("wechat", key, "新的已审核回复。", "hi")
    candidate, review = _reviewed(store, task, _result(text=prepared.final_body))
    row = store.current_reviewed_candidate(task.id, "next")
    action = _result(text=prepared.final_body).proposal.actions[0]
    prior = store.get_prior_candidate_action_attempt(
        store.list_candidate_action_attempts(int(delivery.evidence["standard_candidate_id"]))[0]["external_action_key"],
        row["candidate_id"],
    )
    return store, task, row, action, handler, runner, delivery, prior


def test_new_review_recovers_old_generation_only_after_complete_no_send_readback(tmp_path):
    store, task, row, action, handler, runner, old, prior = _failed_action_and_new_review(tmp_path)
    runner.result = accessibility.AccessibilityResult(True, True, "fp")

    result = SystemExecutor(store, {("wechat", "send_message"): handler}, owner="recovery").execute(
        task, row["candidate_id"], row["review_id"])

    assert result.outcome == "executed"
    assert len(runner.calls) == 2  # first pass proved no action; one actual send
    assert store.get_wechat_delivery_for_task(task.id).status == "sent"
    prior_attempt = store.list_candidate_action_attempts(prior["execution_id"])[0]
    assert prior_attempt["status"] == "confirmed_no_effect"
    proof = json.loads(prior_attempt["result_json"])
    assert proof["delivery_id"] == old.id
    assert proof["readback"]["window_complete"] is True
    assert proof["readback"]["match_count"] == 0


@pytest.mark.parametrize("invalid", ["sent", "incomplete", "scope", "account", "no_proof", "identity"])
def test_prior_failure_recovery_refuses_missing_or_conflicting_evidence(tmp_path, invalid):
    store, task, row, action, handler, runner, old, prior = _failed_action_and_new_review(tmp_path)
    message = SimpleNamespace(account_id=old.account_id, conversation_id=old.conversation_id,
        conversation_type=old.target_type, direction="outbound", text=old.reply_text,
        sent_at=old.action_started_at, message_id="existing-send")
    messages = []
    if invalid == "sent":
        messages = [message]
    elif invalid == "incomplete":
        messages = [SimpleNamespace(**{**vars(message), "direction": "inbound"})] * 200
    elif invalid == "scope":
        messages = [SimpleNamespace(**{**vars(message), "conversation_id": "another"})]
    elif invalid == "account":
        handler._account_factory = lambda: SimpleNamespace(account_id="another")
    elif invalid == "identity":
        action = action.model_copy(update={"target": {**action.target, "target_id": "another"}})
    elif invalid == "no_proof":
        from tests.support.action_fixtures import update_delivery_fixture
        with store._connect() as db:
            update_delivery_fixture(db, "wechat_deliveries", old.id, {"pre_action_failure": 0})
    handler._reader_factory = lambda: SimpleNamespace(read_messages=lambda *_a, **_k: messages)

    outcome = handler.reconcile(action, action_key=prior["external_action_key"],
        candidate={**row, "action_attempt": prior})

    assert outcome is not None and outcome.status == "uncertain"
    assert len(runner.calls) == 1


@pytest.mark.parametrize("invalid", ["window_complete", "match_count", "text_sha256", "account_id", "since"])
def test_action_ledger_rejects_unbound_no_send_proof(tmp_path, invalid):
    store, task, row, action, handler, runner, old, prior = _failed_action_and_new_review(tmp_path)
    outcome = handler.reconcile(action, action_key=prior["external_action_key"],
        candidate={**row, "action_attempt": prior})
    assert outcome.status == "confirmed_no_effect"
    outcome.provider_result["readback"][invalid] = "incorrect"
    claim = store.claim_candidate_execution(row["candidate_id"], row["review_id"], "recovery", 60)

    with pytest.raises(ValueError, match="exact no-send readback"):
        store.record_prior_candidate_action_reconciliation(
            claim["id"], "recovery", 0, prior["external_action_key"],
            "confirmed_no_effect", outcome.provider_result)

    assert store.list_candidate_action_attempts(prior["execution_id"])[0]["status"] == "failed"
    assert len(runner.calls) == 1
