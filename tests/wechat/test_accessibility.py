import pytest

from app.store import AutoReplyStore
from app.wechat.accessibility import (
    AccessibilityResult, WechatSender, reconcile_incomplete_deliveries,
)
from app.wechat.models import WechatReplyScope


class FakeRunner:
    def __init__(self, result=None):
        self.calls = []
        self.result = result or AccessibilityResult(True, True, "fp-1")

    def send(self, target_label, reply_text):
        self.calls.append((target_label, reply_text))
        return self.result


def _scope(binding_status):
    return WechatReplyScope(
        account_id="acct-1", target_type="direct", target_id="u9",
        conversation_id="u9", display_name="Alex",
        trigger_mode="every_inbound_text", binding_status=binding_status,
    )


def _seed_delivery(store):
    store.enqueue_reply_task(
        channel="wechat", conversation_id="u9", conversation_title="Alex",
        single_chat=True, trigger_message_id="m1",
        trigger_create_time="2026-07-17T10:00:00", trigger_sender="Alex", trigger_text="hi",
    )
    store.create_wechat_delivery(
        reply_task_id=1, account_id="acct-1", target_type="direct",
        target_id="u9", conversation_id="u9", reply_text="收到",
    )
    return store.get_wechat_delivery_for_task(1)


@pytest.fixture
def store(tmp_path):
    return AutoReplyStore(tmp_path / "w.sqlite3")


def test_unverified_binding_blocks_before_send(store):
    runner = FakeRunner()
    sender = WechatSender(store, runner)
    delivery = _seed_delivery(store)
    outcome = sender.send(delivery, _scope("unverified"))
    assert outcome.status == "failed"
    assert outcome.error == "target_binding_unverified"
    assert runner.calls == []
    assert store.get_wechat_delivery_for_task(1).status == "failed"


def test_verified_binding_sends(store):
    runner = FakeRunner(AccessibilityResult(True, True, "fp-1"))
    sender = WechatSender(store, runner)
    delivery = _seed_delivery(store)
    outcome = sender.send(delivery, _scope("verified"))
    assert outcome.status == "sent"
    assert runner.calls == [("Alex", "收到")]


def test_post_action_ambiguity_becomes_send_unknown(store):
    runner = FakeRunner(AccessibilityResult(action_performed=True, visible_confirmation=False))
    sender = WechatSender(store, runner)
    delivery = _seed_delivery(store)
    outcome = sender.send(delivery, _scope("verified"))
    assert outcome.status == "send_unknown"
    assert store.get_wechat_delivery_for_task(1).status == "send_unknown"


def test_recovery_never_resends_sending(store):
    delivery = _seed_delivery(store)
    store.mark_wechat_delivery_sending(delivery.id)
    recovered = reconcile_incomplete_deliveries(store, reader=None)
    assert recovered[0].status in {"sent", "send_unknown"}
