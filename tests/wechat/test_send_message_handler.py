"""The reviewed ``wechat / send_message`` action drives one delivery to an outcome.

The standard pipeline prepares the final body, the standard Audit approves it,
and this handler sends it through the existing Sender. A send WeChat cannot
confirm stays uncertain; nothing here ever sends twice.
"""

import pytest

from app.agent_contracts import ProposedAction
from app.service_message_sender import agent_message_delivery_key
from app.store import AutoReplyStore
from app.system_action_handlers import WechatSendMessageHandler
from app.wechat.accessibility import (
    AccessibilityResult, SenderExecutionError,
)
from app.wechat.models import WechatAccount
from tests.wechat.test_standard_review_gate import IDENTITY, _result, _reviewed, _task


class Runner:
    def __init__(self, result=None, error=None):
        self.result = result or AccessibilityResult(True, True, "fp")
        self.error = error
        self.calls = []

    def send(self, target_label, reply_text, *, search_query=None, expected_recent_text=None):
        self.calls.append((target_label, reply_text))
        if self.error is not None:
            raise self.error
        return self.result


class Reader:
    def read_messages(self, *_args, **_kwargs):
        from app.wechat.models import WechatMessage
        return [WechatMessage(
            account_id="acct-1", source_version="4.1.10",
            message_id="m1", conversation_id="u9", conversation_type="direct",
            sender_id="u9", sender_display_name="Alex", direction="inbound", kind="text",
            text="hi", sent_at="2026-10-24T10:00:00+08:00")]


def _account():
    return WechatAccount(account_id="acct-1", display_name="Derek", self_user_id="self-1",
                         account_dir="/a", db_dir="/a/db_storage", app_version="4.1.10")


def _setup(tmp_path, runner=None, *, enabled=True):
    store = AutoReplyStore(tmp_path / "handler.sqlite3")
    task = _task(store)
    key = agent_message_delivery_key(
        business_object_key=task.business_object_key, action_identity=IDENTITY,
        execution_generation=task.execution_generation, proposal_revision=0)
    prepared = store.prepare_outbound_postfix("wechat", key, "收到，稍后回复你。", "hi")
    candidate, review = _reviewed(store, task, _result(text=prepared.final_body))
    row = store.current_reviewed_candidate(task.id, task.execution_generation)
    action = ProposedAction.model_validate(
        _result(text=prepared.final_body).proposal.actions[0].model_dump(mode="json"))
    runner = runner or Runner()
    handler = WechatSendMessageHandler(
        store, sender_factory=lambda: runner, reader_factory=lambda: Reader(),
        account_factory=_account, send_enabled=lambda: enabled)
    return store, task, row, action, handler, runner, prepared


def test_an_approved_send_is_sent_once_and_verified(tmp_path):
    store, task, row, action, handler, runner, prepared = _setup(tmp_path)
    outcome = handler.dispatch(action, action_key="k", candidate=row)
    assert outcome.status == "verified"
    assert runner.calls == [("Alex", prepared.final_body)]
    assert store.get_wechat_delivery_for_task(task.id).status == "sent"
    again = handler.dispatch(action, action_key="k", candidate=row)
    assert again.status == "verified" and len(runner.calls) == 1


@pytest.mark.parametrize("entrypoint", ["auto", "approve"])
def test_sender_recovery_reuses_real_candidate_preparation_once(tmp_path, entrypoint):
    from app.wechat import service
    from app.wechat.accessibility import WechatSender

    store, task, row, _action, _handler, runner, prepared = _setup(tmp_path)
    delivery_id = store.create_standard_wechat_delivery(
        candidate_id=row["candidate_id"], review_id=row["review_id"],
        action_identity=IDENTITY,
    )
    assert store.get_outbound_postfix("wechat", f"wechat:{delivery_id}") is None
    sender = WechatSender(store, runner)
    if entrypoint == "auto":
        assert service.process_ready_wechat_deliveries(
            store, sender, mode="auto", sender_enabled=True,
        ) == 1
    else:
        assert service.approve_wechat_delivery(store, sender, delivery_id) == "sent"
    assert runner.calls == [("Alex", prepared.final_body)]
    assert store.get_wechat_delivery_by_id(delivery_id).status == "sent"
    assert service.process_ready_wechat_deliveries(
        store, sender, mode="auto", sender_enabled=True,
    ) == 0
    assert runner.calls == [("Alex", prepared.final_body)]
    assert store.get_outbound_postfix("wechat", prepared.delivery_key) == prepared
    assert store.get_wechat_delivery_for_task(task.id).id == delivery_id


def test_a_send_wechat_cannot_confirm_is_uncertain_and_never_repeated(tmp_path):
    runner = Runner(AccessibilityResult(True, False, "fp"))
    store, task, row, action, handler, runner, _ = _setup(tmp_path, runner)
    assert handler.dispatch(action, action_key="k", candidate=row).status == "uncertain"
    assert store.get_wechat_delivery_for_task(task.id).status == "send_unknown"
    assert handler.dispatch(action, action_key="k", candidate=row).status == "uncertain"
    assert len(runner.calls) == 1
    assert handler.reconcile(action, action_key="k", candidate=row).status == "uncertain"


def test_an_interrupted_send_is_uncertain(tmp_path):
    runner = Runner(error=RuntimeError("transport stopped"))
    store, task, row, action, handler, _, _ = _setup(tmp_path, runner)
    assert handler.dispatch(action, action_key="k", candidate=row).status == "uncertain"
    assert store.get_wechat_delivery_for_task(task.id).status == "send_unknown"


def test_a_failure_before_dispatch_fails_and_a_retry_can_send(tmp_path):
    runner = Runner(error=SenderExecutionError("sender helper unavailable", action_may_have_started=False))
    store, task, row, action, handler, runner, prepared = _setup(tmp_path, runner)
    assert handler.dispatch(action, action_key="k", candidate=row).status == "failed"
    delivery = store.get_wechat_delivery_for_task(task.id)
    assert delivery.status == "failed" and delivery.pre_action_failure
    assert handler.reconcile(action, action_key="k", candidate=row).status == "failed"
    runner.error = None
    assert handler.dispatch(action, action_key="k", candidate=row).status == "verified"
    assert store.get_wechat_delivery_for_task(task.id).status == "sent"


def test_nothing_is_created_or_sent_while_sending_is_disabled(tmp_path):
    store, task, row, action, handler, runner, _ = _setup(tmp_path, enabled=False)
    outcome = handler.dispatch(action, action_key="k", candidate=row)
    assert outcome.status == "failed" and outcome.provider_result["reason"] == "wechat_sending_not_enabled"
    assert store.get_wechat_delivery_for_task(task.id) is None and runner.calls == []


def test_text_that_is_not_the_prepared_body_is_refused(tmp_path):
    store, task, row, action, handler, runner, _ = _setup(tmp_path)
    edited = ProposedAction.model_validate({
        **action.model_dump(mode="json"),
        "payload": {"text": action.payload["text"] + " (edited)"}})
    outcome = handler.dispatch(edited, action_key="k", candidate=row)
    assert outcome.status == "failed" and outcome.provider_result["reason"] == "reviewed_message_preparation_mismatch"
    assert runner.calls == [] and store.get_wechat_delivery_for_task(task.id) is None


def test_a_superseded_delivery_is_reported_not_sent(tmp_path):
    store, task, row, action, handler, runner, _ = _setup(tmp_path, enabled=True)
    store.create_standard_wechat_delivery(
        candidate_id=row["candidate_id"], review_id=row["review_id"], action_identity=IDENTITY)
    delivery = store.get_wechat_delivery_for_task(task.id)
    from tests.support.action_fixtures import update_delivery_fixture
    with store._connect() as db:
        update_delivery_fixture(db, "wechat_deliveries", delivery.id,
                                    {"status": "superseded", "error": "superseded_by_newer_wechat_trigger:9"})
    outcome = handler.dispatch(action, action_key="k", candidate=row)
    assert outcome.status == "business_state_changed" and runner.calls == []


def test_the_action_contract_requires_a_complete_target_and_only_text():
    base = _result().proposal.actions[0].model_dump(mode="json")
    ProposedAction.model_validate(base)
    for change in (
        {"target": {**base["target"], "target_type": "channel"}},
        {"target": {k: v for k, v in base["target"].items() if k != "account_id"}},
        {"payload": {"text": "ok", "extra": "x"}},
        {"payload": {"text": " "}},
    ):
        with pytest.raises(ValueError):
            ProposedAction.model_validate({**base, **change})
