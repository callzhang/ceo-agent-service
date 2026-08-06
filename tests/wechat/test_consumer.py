import pytest

from app.store import AutoReplyStore
from app.dingtalk_models import CodexAction, CodexDecision
from app.wechat.models import WechatAccount
from app.wechat.consumer import WechatReplyConsumer
from app.store import AgentRunLeaseLostError


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
    assert delivery.evidence["trigger_text"] == "下午能给结论吗"
    attempt = store.get_reply_attempt(1)
    assert attempt is not None
    assert attempt.send_status == "pending"
    assert "memory_recall" in fake_codex.prompts[0]


def test_no_reply_completes_without_delivery(fake_codex, consumer, store):
    fake_codex.decision = CodexDecision(action=CodexAction.NO_REPLY, audit_summary="无需回复")
    assert consumer.run_once(limit=1) == 1
    assert store.get_wechat_delivery_for_task(1) is None
    attempt = store.get_reply_attempt(1)
    assert attempt is not None
    assert attempt.send_status == "skipped"
    assert attempt.send_error == "no_reply"


def test_dingtalk_system_actions_rejected(fake_codex, consumer, store):
    fake_codex.decision = CodexDecision(
        action=CodexAction.SEND_REPLY, reply_text="x",
        system_actions=[{"tool": "dws"}], audit_summary="s",
    )
    assert consumer.run_once(limit=1) == 1
    assert store.get_wechat_delivery_for_task(1) is None
    attempt = store.get_reply_attempt(1)
    assert attempt is not None
    assert attempt.send_status == "failed"
    assert attempt.send_error == "dingtalk_only_system_actions_rejected"


def test_reply_transport_action_creates_ready_wechat_delivery(
    fake_codex, consumer, store
):
    fake_codex.decision = CodexDecision(
        action=CodexAction.SEND_REPLY,
        reply_text="我也去餐厅吃饭。",
        system_actions=[
            {
                "type": "send_dingtalk_reply",
                "reply_text_ref": "user_response.text",
            }
        ],
        audit_summary="基于同一对话上下文澄清晚饭安排。",
    )

    assert consumer.run_once(limit=1) == 1

    delivery = store.get_wechat_delivery_for_task(1)
    assert delivery is not None
    assert delivery.status == "ready_to_send"
    attempt = store.get_reply_attempt(1)
    assert attempt is not None
    assert attempt.send_status == "pending"


def test_stop_with_error_records_failed_attempt(fake_codex, consumer, store):
    fake_codex.decision = CodexDecision(
        action=CodexAction.STOP_WITH_ERROR,
        reason="missing_wechat_context",
        audit_summary="缺上下文",
    )

    assert consumer.run_once(limit=1) == 1

    attempt = store.get_reply_attempt(1)
    assert attempt is not None
    assert attempt.send_status == "failed"
    assert attempt.send_error == "missing_wechat_context"


def test_consumer_marks_read_only_decision_phase_before_calling_codex(
    consumer, store
):
    observed_phases: list[str] = []

    class PhaseInspectingRunner:
        def decide(self, *_args, **_kwargs):
            task = store.get_reply_task(1)
            assert task is not None
            observed_phases.append(task.error)
            return CodexDecision(
                action=CodexAction.NO_REPLY,
                audit_summary="无需回复。",
            )

    consumer.runner = PhaseInspectingRunner()

    assert consumer.run_once(limit=1) == 1
    assert observed_phases == ["wechat_read_only_decision_running"]


def test_corrected_generation_replaces_unsent_wechat_delivery(
    fake_codex, consumer, store
):
    fake_codex.decision = CodexDecision(
        action=CodexAction.SEND_REPLY,
        reply_text="旧回复",
        reason="first",
        audit_summary="first",
    )
    assert consumer.run_once(limit=1) == 1
    first = store.get_reply_task(1)
    assert first is not None

    with store._connect() as db:
        db.execute(
            "update reply_tasks set status='pending', execution_generation='corrected' "
            "where id=1"
        )
    fake_codex.decision = CodexDecision(
        action=CodexAction.SEND_REPLY,
        reply_text="修正版回复",
        reason="corrected",
        audit_summary="corrected",
    )

    assert consumer.run_once(limit=1) == 1

    delivery = store.get_wechat_delivery_for_task(1)
    assert delivery is not None
    assert delivery.reply_text == "修正版回复"
    assert delivery.execution_generation == "corrected"


def test_stale_wechat_worker_cannot_persist_attempt_or_delivery(
    fake_codex, consumer, store
):
    [claimed] = store.claim_reply_tasks(1, channel="wechat")

    class RotatingRunner:
        def decide(self, *_args, **_kwargs):
            store.rotate_reply_task_execution_generation(claimed.id)
            return CodexDecision(
                action=CodexAction.SEND_REPLY,
                reply_text="旧 worker 回复",
                reason="stale",
                audit_summary="stale",
            )

    stale_consumer = WechatReplyConsumer(
        store, RotatingRunner(), reader=None, account=consumer.account
    )

    with pytest.raises(AgentRunLeaseLostError):
        stale_consumer.process(claimed)

    assert store.get_wechat_delivery_for_task(claimed.id) is None
    assert store.get_latest_reply_attempt_for_trigger("u9", "m1") is None
