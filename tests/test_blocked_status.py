from app.dingtalk_models import DingTalkConversation, DingTalkMessage
from app.store import AutoReplyStore
from app.worker import DingTalkAutoReplyWorker


class FakeDws:
    pass


class FakeCodex:
    pass


def _conversation() -> DingTalkConversation:
    return DingTalkConversation(
        open_conversation_id="cid-1",
        title="Friday",
        single_chat=False,
        unread_point=0,
    )


def _message() -> DingTalkMessage:
    return DingTalkMessage(
        open_conversation_id="cid-1",
        open_message_id="msg-1",
        conversation_title="Friday",
        single_chat=False,
        sender_name="Mina",
        create_time="2026-07-25 10:00:00",
        content="@Alex Chen 看一下",
    )


def _worker(store: AutoReplyStore) -> DingTalkAutoReplyWorker:
    return DingTalkAutoReplyWorker(store=store, dws=FakeDws(), codex=FakeCodex())


def test_recoverable_blocked_attempt_is_not_terminal(tmp_path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    trigger = _message()
    store.record_reply_attempt(
        conversation_id=trigger.open_conversation_id,
        conversation_title=trigger.conversation_title,
        trigger_message_id=trigger.open_message_id,
        trigger_sender=trigger.sender_name,
        trigger_text=trigger.content,
        action="blocked",
        sensitivity_kind="general",
        send_status="blocked",
    )

    handled = _worker(store)._handle_existing_attempt(
        _conversation(),
        trigger,
        [trigger],
    )

    assert handled is False
    assert store.has_seen(trigger.open_message_id) is False


def test_unrecoverable_blocked_attempt_is_terminal(tmp_path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    trigger = _message()
    attempt_id = store.record_reply_attempt(
        conversation_id=trigger.open_conversation_id,
        conversation_title=trigger.conversation_title,
        trigger_message_id=trigger.open_message_id,
        trigger_sender=trigger.sender_name,
        trigger_text=trigger.content,
        action="blocked",
        sensitivity_kind="general",
        send_status="blocked",
    )
    store.update_reply_attempt(
        attempt_id,
        send_error="blocked_unrecoverable_external_auth: not current user",
    )

    handled = _worker(store)._handle_existing_attempt(
        _conversation(),
        trigger,
        [trigger],
    )

    assert handled is True
    assert store.has_seen(trigger.open_message_id) is True
