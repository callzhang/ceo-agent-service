from app.service_message_sender import ServiceMessageSender
from app.store import AutoReplyStore
from app.dingtalk_models import DingTalkConversation, DingTalkMessage
from app.dws_client import DwsClient
import pytest


class InterruptedReply:
    def __init__(self):
        self.sends = 0
        self.reads = 0
        self.readback = None

    def send_reply_to_trigger(self, conversation, trigger, text):
        self.sends += 1
        raise TimeoutError("provider response lost")

    def reconcile_reply_to_trigger(self, conversation, trigger, text):
        self.reads += 1
        return self.readback

    def verify_message_send_result(self, result):
        return {"state": "sent"}


@pytest.mark.parametrize("found", [False, True])
def test_restart_reconciles_interrupted_native_reply_without_resending(tmp_path, found):
    path = tmp_path / "reply.sqlite"
    store = AutoReplyStore(path)
    adapter = InterruptedReply()
    sender = ServiceMessageSender(store=store, dingtalk=adapter)
    prepared = sender.prepare(channel="dingtalk", delivery_key="reviewed-action", body="Status")
    with pytest.raises(TimeoutError):
        sender.send_dingtalk_reply_to_trigger_prepared(prepared, conversation="cid", trigger="msg")
    adapter.readback = {"result": {"processQueryKey": "readback-msg"}} if found else None
    restarted = ServiceMessageSender(store=AutoReplyStore(path), dingtalk=adapter)
    if found:
        receipt = restarted.send_dingtalk_reply_to_trigger_prepared(prepared, conversation="cid", trigger="msg")
        assert receipt.provider_result == adapter.readback
        assert store.get_outbound_postfix_receipt("dingtalk", prepared.delivery_key) == adapter.readback
    else:
        with pytest.raises(RuntimeError, match="reconciliation_inconclusive"):
            restarted.send_dingtalk_reply_to_trigger_prepared(prepared, conversation="cid", trigger="msg")
        assert store.get_outbound_postfix_receipt("dingtalk", prepared.delivery_key) is None
    assert adapter.sends == 1
    assert adapter.reads == 1


@pytest.mark.parametrize("mismatch", [None, "conversation", "trigger", "body", "sender", "recalled", "duplicate"])
def test_readback_requires_one_matching_message_from_current_sender(monkeypatch, mismatch):
    client = DwsClient()
    conversation = DingTalkConversation(open_conversation_id="cid", title="Group", single_chat=False, unread_point=0)
    trigger = DingTalkMessage(open_conversation_id="cid", open_message_id="trigger", conversation_title="Group", single_chat=False, sender_name="Mina", create_time="2026-10-01T21:00:00Z", content="Status?")
    message = trigger.model_copy(update={"open_message_id": "sent-id", "quoted_message_id": "trigger", "sender_user_id": "self", "content": "Public  status"})
    updates = {
        "conversation": {"open_conversation_id": "other"},
        "trigger": {"quoted_message_id": "other"},
        "body": {"content": "Different"},
        "sender": {"sender_user_id": "other"},
        "recalled": {"raw_payload": {"status": "recalled"}},
    }
    message = message.model_copy(update=updates.get(mismatch, {}))
    monkeypatch.setattr(client, "read_recent_messages", lambda _: [message, message] if mismatch == "duplicate" else [message])
    monkeypatch.setattr(client, "get_current_user_id", lambda: "self")
    result = client.reconcile_reply_to_trigger(conversation, trigger, "Public\nstatus")
    if mismatch is None:
        assert result["result"]["openMessageId"] == "sent-id"
        assert client.verify_message_send_result(result)["state"] == "sent"
    else:
        assert result is None


def test_readback_failure_never_dispatches_again(tmp_path):
    adapter = InterruptedReply()
    store = AutoReplyStore(tmp_path / "reply.sqlite")
    sender = ServiceMessageSender(store=store, dingtalk=adapter)
    prepared = sender.prepare(channel="dingtalk", delivery_key="reviewed-action", body="Status")
    with pytest.raises(TimeoutError):
        sender.send_dingtalk_reply_to_trigger_prepared(prepared, conversation="cid", trigger="msg")
    def unavailable(*args):
        raise TimeoutError("readback unavailable")
    adapter.reconcile_reply_to_trigger = unavailable
    with pytest.raises(TimeoutError, match="readback unavailable"):
        sender.send_dingtalk_reply_to_trigger_prepared(prepared, conversation="cid", trigger="msg")
    assert adapter.sends == 1
