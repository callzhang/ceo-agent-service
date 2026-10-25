"""A WeChat task runs on the standard Consumer -> Audit flow (migration, off by default).

Derek 2026-10-24, phase 3. Only the context, the message preparation and the
target binding are WeChat specific; the switch routing a target to this path is
empty by default, so nothing changes until a target is named.
"""

from datetime import datetime, timezone

import pytest

from app.agent_contracts import ConsumerAgentResult, ProposedAction
from app.agent_context import AgentTaskContext
from app.config import wechat_standard_pipeline_targets
from app.consumer_agent import ResultParseError, _prepare_outgoing_wechat_messages
from app.store import AutoReplyStore
from app.wechat.models import WechatAccount, WechatMessage
from app.wechat.standard_context import build_wechat_task_context, wechat_reply_target
from tests.wechat.test_standard_review_gate import IDENTITY, _result, _task

ACCOUNT = WechatAccount(account_id="acct-1", display_name="Derek", self_user_id="self-1",
                        account_dir="/a", db_dir="/a/db_storage", app_version="4.1.10")


class Reader:
    def __init__(self, *, fail=False):
        self.fail = fail
        self.calls = []

    def read_messages(self, account, **kwargs):
        self.calls.append(kwargs)
        if self.fail:
            raise RuntimeError("reader unavailable")
        return [WechatMessage(
            account_id="acct-1", source_version="4.1.10", message_id=f"m{i}",
            conversation_id="u9", conversation_type="direct", sender_id="u9",
            sender_display_name="Alex", direction="inbound", kind="text",
            text=f"message {i}", sent_at=f"2026-10-24T10:0{i}:00+08:00") for i in range(3)]


def _context(tmp_path, reader=None):
    store = AutoReplyStore(tmp_path / "adapter.sqlite3")
    task = _task(store)
    return store, task, build_wechat_task_context(task, reader=reader or Reader(), account=ACCOUNT)


def _proposal(task_context, *, text="收到，稍后回复你。", **change):
    action = {
        "description": "Reply on WeChat", "action_identity": IDENTITY,
        "capability": "wechat", "operation": "send_message",
        "target": dict(task_context.required_proposal_action["target"]),
        "payload": {"text": text},
    }
    action.update(change)
    return ConsumerAgentResult.model_validate({
        "outcome": "proposal", "summary": "Reply",
        "proposal": {"objective": "Reply", "actions": [action], "sourced_facts": [],
                     "authored_judgment": "Short acknowledgement"},
        "error": {"code": "", "retryable": False, "authorization_required": False},
        "risk": "low", "confidence": 1.0, "rule_coverage": 1.0, "information_completeness": 1.0,
    })


def test_the_context_carries_the_trigger_recent_messages_and_the_only_allowed_target(tmp_path):
    _, task, context = _context(tmp_path)
    assert context.channel == "wechat" and context.trigger_message_id == task.trigger_message_id
    assert [message.text for message in context.messages] == ["message 0", "message 1", "message 2"]
    assert context.required_proposal_action["target"] == {
        "account_id": "acct-1", "target_type": "direct", "target_id": "u9", "conversation_id": "u9"}
    assert "wechat" in context.render_business_context()


def test_an_unreadable_conversation_still_gives_a_context_with_the_trigger(tmp_path):
    _, task, context = _context(tmp_path, Reader(fail=True))
    assert context.messages == () and context.trigger_text == task.trigger_text


def test_a_correct_reply_gets_its_final_prepared_body(tmp_path):
    store, task, context = _context(tmp_path)
    prepared = _prepare_outgoing_wechat_messages(
        _proposal(context), store=store, task=task, context=context, proposal_revision=0)
    text = prepared.proposal.actions[0].payload["text"]
    assert text.startswith("收到，稍后回复你。") and text != "收到，稍后回复你。"
    assert set(prepared.proposal.actions[0].payload) == {"text"}


@pytest.mark.parametrize("change", [
    {"target": {"account_id": "acct-1", "target_type": "direct", "target_id": "someone-else", "conversation_id": "u9"}},
    {"target": {"account_id": "acct-2", "target_type": "direct", "target_id": "u9", "conversation_id": "u9"}},
    {"capability": "dingtalk-chat", "operation": "reply_to_message",
     "target": {"conversation_id": "c", "message_id": "m"}, "payload": {"content": "hi"}},
])
def test_only_the_triggering_conversation_and_wechat_send_are_accepted(tmp_path, change):
    store, task, context = _context(tmp_path)
    with pytest.raises(ResultParseError):
        _prepare_outgoing_wechat_messages(
            _proposal(context, **change), store=store, task=task, context=context, proposal_revision=0)


def test_a_reply_may_not_carry_a_second_action(tmp_path):
    store, task, context = _context(tmp_path)
    result = _proposal(context)
    extra = result.proposal.actions[0].model_copy(update={"action_identity": "second"})
    result = result.model_copy(update={"proposal": result.proposal.model_copy(
        update={"actions": (result.proposal.actions[0], extra)})})
    with pytest.raises(ResultParseError):
        _prepare_outgoing_wechat_messages(result, store=store, task=task, context=context, proposal_revision=0)


def test_a_hand_typed_feedback_link_is_refused(tmp_path):
    store, task, context = _context(tmp_path)
    text = ("收到\n\n反馈：[👍 有帮助](https://x.example/api/dingtalk-feedback-spike?feedback_token=t1&rating=up)｜"
            "[👎 需改进](https://x.example/api/dingtalk-feedback-spike?feedback_token=t1&rating=down)")
    with pytest.raises(ResultParseError):
        _prepare_outgoing_wechat_messages(
            _proposal(context, text=text), store=store, task=task, context=context, proposal_revision=0)


def test_other_channels_are_left_alone(tmp_path):
    store, task, context = _context(tmp_path)
    other = AgentTaskContext(**{**context.__dict__, "channel": "dingtalk"})
    result = _proposal(context, capability="dingtalk-chat", operation="x", target={"a": "b"})
    assert _prepare_outgoing_wechat_messages(
        result, store=store, task=task, context=other, proposal_revision=0) is result


def test_no_target_uses_the_standard_pipeline_unless_named(monkeypatch):
    monkeypatch.delenv("CEO_WECHAT_STANDARD_PIPELINE_TARGETS", raising=False)
    assert wechat_standard_pipeline_targets() == frozenset()
    monkeypatch.setenv("CEO_WECHAT_STANDARD_PIPELINE_TARGETS", " filehelper , ,u9 ")
    assert wechat_standard_pipeline_targets() == frozenset({"filehelper", "u9"})


def test_the_worker_runs_a_claimed_wechat_task_through_the_shared_orchestrator(tmp_path):
    from app.worker import DingTalkAutoReplyWorker
    from tests.test_agent_runtime_worker import NoActionOrchestrator, ReadyGate

    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    task = _task(store)
    orchestrator = NoActionOrchestrator(store)
    worker = DingTalkAutoReplyWorker(
        store=store, dws=object(), codex=object(), agent_orchestrator=orchestrator,
        channel_gates={"dingtalk": ReadyGate("dingtalk"), "wechat": ReadyGate("wechat")},
        now_provider=lambda: datetime(2026, 10, 24, 10, 0, tzinfo=timezone.utc))
    worker.process_claimed_wechat_task(task, reader=Reader(), account=ACCOUNT)
    [(seen_task, context)] = orchestrator.calls
    assert seen_task.id == task.id and context.channel == "wechat"
    assert context.required_proposal_action["operation"] == "send_message"
    with pytest.raises(ValueError):
        worker.process_claimed_wechat_task(
            task.model_copy(update={"channel": "dingtalk"}), reader=Reader(), account=ACCOUNT)
