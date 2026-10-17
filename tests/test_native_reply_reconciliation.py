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
    message = trigger.model_copy(update={"open_message_id": "sent-id", "quoted_message_id": "trigger", "sender_user_id": "self", "content": "@Mina Public  status"})
    updates = {
        "conversation": {"open_conversation_id": "other"},
        "trigger": {"quoted_message_id": "other"},
        "body": {"content": "Different"},
        "sender": {"sender_user_id": "other"},
        "recalled": {"raw_payload": {"status": "recalled"}},
    }
    message = message.model_copy(update=updates.get(mismatch, {}))
    monkeypatch.setattr(
        client, "read_complete_group_messages_in_window",
        lambda *args: [message, message] if mismatch == "duplicate" else [message],
    )
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


def test_exact_native_readback_confirms_reply_without_task_id_and_strips_feedback_suffix(monkeypatch):
    client = DwsClient()
    conversation = DingTalkConversation(open_conversation_id="cid", title="Group", single_chat=False, unread_point=0)
    trigger = DingTalkMessage(
        open_conversation_id="cid", open_message_id="trigger", conversation_title="Group",
        single_chat=False, sender_name="Mina", create_time="2026-10-01T21:00:00Z", content="Status?",
    )
    authored = "@Mina Public status"
    outbound = (
        "@Mina Public status\n\n反馈：[👍 有帮助](https://example.test/api/dingtalk-feedback-spike?feedback_token=x)"
        "｜[👎 需改进](https://example.test/api/dingtalk-feedback-spike?feedback_token=y)"
    )
    message = trigger.model_copy(update={
        "open_message_id": "sent-id", "quoted_message_id": "trigger",
        "sender_user_id": "self", "content": "@Mina  Public status",
    })
    monkeypatch.setattr(client, "read_complete_group_messages_in_window", lambda *args: [message])
    monkeypatch.setattr(client, "get_current_user_id", lambda: "self")

    receipt = client.reconcile_reply_to_trigger(conversation, trigger, outbound)

    assert receipt is not None
    assert receipt["result"]["openMessageId"] == "sent-id"
    assert receipt["readbackVerification"]["quotedMessageId"] == "trigger"
    assert client.verify_message_send_result(receipt)["state"] == "sent"
    assert authored in outbound


@pytest.mark.parametrize("field,value", [
    ("exactMatchCount", 2), ("recalled", True), ("quotedMessageId", "other"),
    ("senderUserId", ""), ("bodySha256", "not-a-digest"),
])
def test_unverified_readback_metadata_does_not_confirm_delivery(field, value):
    import hashlib

    digest = hashlib.sha256(b"body").hexdigest()
    receipt = {
        "success": True,
        "result": {"openMessageId": "sent-id"},
        "readback": {
            "open_message_id": "sent-id", "open_conversation_id": "cid",
            "quoted_message_id": "trigger", "sender_user_id": "self", "content": "body",
        },
        "readbackVerification": {
            "contractVersion": "dingtalk.reply-readback.v1",
            "messageId": "sent-id", "conversationId": "cid", "quotedMessageId": "trigger",
            "senderUserId": "self", "bodySha256": digest, "expectedBodySha256": digest,
            "exactMatchCount": 1, "recalled": False,
        },
    }
    receipt["readbackVerification"][field] = value

    assert DwsClient().verify_message_send_result(receipt)["state"] == "ambiguous"


def test_native_reply_readback_matches_provider_rendered_mentions_and_list_breaks(monkeypatch):
    client = DwsClient()
    conversation = DingTalkConversation(open_conversation_id="cid", title="Group", single_chat=False, unread_point=0)
    trigger = DingTalkMessage(open_conversation_id="cid", open_message_id="trigger", conversation_title="Group", single_chat=False, sender_name="Mina Zou", create_time="2026-10-01T21:00:00Z", content="Status?")
    sent = trigger.model_copy(update={"open_message_id": "sent", "quoted_message_id": "trigger", "sender_user_id": "self", "content": "@Mina Zou **Status**  \n- One- Two"})
    monkeypatch.setattr(client, "read_complete_group_messages_in_window", lambda *args: [sent])
    monkeypatch.setattr(client, "get_current_user_id", lambda: "self")
    result = client.reconcile_reply_to_trigger(conversation, trigger, "**Status**\n\n- One\n- Two")
    assert result is not None


def test_complete_group_message_window_rejects_partial_page(monkeypatch):
    from app.dws_client import DwsError

    client = DwsClient()
    monkeypatch.setattr(client, "run_json", lambda _: {
        "contractVersion": "im.message-list.v1", "complete": False,
        "partial": True, "hasMore": True, "truncated": False,
        "failedCount": 0, "failures": [], "count": 0, "messages": [],
        "queryRange": {"startTime": "2026-10-01T21:00:00+08:00",
                       "endTime": "2026-10-01T21:01:00+08:00", "order": "asc"},
    })

    with pytest.raises(DwsError, match="incomplete"):
        client.read_complete_group_messages_in_window(
            "cid", "2026-10-01T21:00:00+08:00", "2026-10-01T21:01:00+08:00",
        )
