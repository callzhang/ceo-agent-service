import pytest

from app.store import AutoReplyStore
from app.dingtalk_models import CodexAction, CodexDecision
from app.wechat.models import WechatAccount
from app.wechat.consumer import WechatReplyConsumer


class FakeCodexRunner:
    def __init__(self):
        self.decision = None
        self.prompts: list[str] = []

    def decide(self, prompt, session_id, image_paths=None):
        self.prompts.append(prompt)
        return self.decision


@pytest.fixture
def store(tmp_path):
    return AutoReplyStore(tmp_path / "w.sqlite3")


@pytest.fixture
def account():
    return WechatAccount(account_id="acct-1", display_name="derek", self_user_id="self-1",
                         account_dir="/a", db_dir="/a/db_storage", app_version="4.1.10")


@pytest.fixture
def fake_codex():
    return FakeCodexRunner()


@pytest.fixture
def consumer(store, fake_codex, account):
    store.enqueue_reply_task(
        channel="wechat", conversation_id="u9", conversation_title="Alex",
        single_chat=True, trigger_message_id="m1",
        trigger_create_time="2026-07-17T10:00:00", trigger_sender="Alex",
        trigger_text="下午能给结论吗",
    )
    return WechatReplyConsumer(store, fake_codex, reader=None, account=account)


def test_send_reply_creates_ready_delivery(fake_codex, consumer, store):
    fake_codex.decision = CodexDecision(
        action=CodexAction.SEND_REPLY, reply_text="收到，我下午给你结论。",
        reason="明确承诺", audit_summary="明确承诺",
    )
    assert consumer.run_once(limit=1) == 1
    delivery = store.get_wechat_delivery_for_task(1)
    assert delivery is not None
    assert delivery.status == "ready_to_send"
    assert delivery.reply_text == "收到，我下午给你结论。"
    assert "memory_recall" in fake_codex.prompts[0]


def test_no_reply_completes_without_delivery(fake_codex, consumer, store):
    fake_codex.decision = CodexDecision(action=CodexAction.NO_REPLY, audit_summary="无需回复")
    assert consumer.run_once(limit=1) == 1
    assert store.get_wechat_delivery_for_task(1) is None


def test_dingtalk_system_actions_rejected(fake_codex, consumer, store):
    fake_codex.decision = CodexDecision(
        action=CodexAction.SEND_REPLY, reply_text="x",
        system_actions=[{"tool": "dws"}], audit_summary="s",
    )
    assert consumer.run_once(limit=1) == 1
    assert store.get_wechat_delivery_for_task(1) is None
