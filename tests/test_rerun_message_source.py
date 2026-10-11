import json

import pytest

from app.audit_web import handle_rerun_attempt_post
from app.dws_client import DingTalkConversation, DingTalkMessage, DwsClient, DwsError
from app.store import AgentRole, AutoReplyStore


def source_message():
    return DingTalkMessage(
        open_conversation_id="source-cid", open_message_id="source-message",
        conversation_title="Source", single_chat=False, sender_name="Sender",
        create_time="2026-10-10 10:00:00", content="Original source",
    )


def settled_task(store):
    source = source_message()
    task_id = store.enqueue_reply_task(
        conversation_id=source.open_conversation_id,
        conversation_title=source.conversation_title, single_chat=False,
        trigger_message_id=source.open_message_id, trigger_sender=source.sender_name,
        trigger_create_time=source.create_time, trigger_text=source.content,
        trigger_message_json=source.model_dump_json(),
    )
    task = store.claim_reply_task(task_id)
    store.complete_reply_task(task.id, expected_execution_generation=task.execution_generation)
    attempt_id = store.record_reply_attempt(
        conversation_id=source.open_conversation_id, conversation_title="Source",
        trigger_message_id=source.open_message_id, trigger_sender=source.sender_name,
        trigger_text=source.content, action="agent_run", sensitivity_kind="normal",
        send_status="skipped",
    )
    return task, attempt_id


@pytest.mark.parametrize("wrong_identity", [False, True])
def test_compacted_rerun_requires_exact_provider_message(tmp_path, monkeypatch, wrong_identity):
    store = AutoReplyStore(tmp_path / "source.sqlite3")
    task, attempt_id = settled_task(store)
    source = source_message()
    if wrong_identity:
        source = source.model_copy(update={"open_conversation_id": "other-cid"})
    calls = []

    def read(_client, conversation, message_id):
        calls.append((conversation.open_conversation_id, message_id))
        return source

    monkeypatch.setattr(DwsClient, "read_message_by_id", read, raising=False)
    status, _, _ = handle_rerun_attempt_post(store, attempt_id)
    assert calls == [(task.conversation_id, task.trigger_message_id)]
    current = store.get_reply_task(task.id)
    if wrong_identity:
        assert status == 409
        assert current.status == "done"
        assert current.execution_generation == task.execution_generation
        assert current.input_compacted
    else:
        assert status == 303
        assert current.status == "pending"
        assert current.execution_generation != task.execution_generation
        assert not current.input_compacted
        assert current.trigger_text == source.content


@pytest.mark.parametrize("invalid", [None, "partial", "wrong-message", "wrong-conversation"])
def test_exact_message_reader_rejects_unverified_sources(monkeypatch, invalid):
    row = {"conversationId": "source-cid", "messageId": "source-message",
           "sender": "Sender", "createTime": "2026-10-10 10:00:00",
           "text": "Original source"}
    payload = {"contractVersion": "im.message-list.v1", "complete": True,
               "messagesComplete": True, "hasMore": False, "failures": [],
               "messages": [row]}
    if invalid == "partial":
        payload["messagesComplete"] = False
    elif invalid == "wrong-message":
        row["messageId"] = "another-message"
    elif invalid == "wrong-conversation":
        row["conversationId"] = "another-cid"
    client = DwsClient()
    commands = []
    monkeypatch.setattr(client, "run_json", lambda command: commands.append(command) or payload)
    result = client.read_message_by_id(
        DingTalkConversation(open_conversation_id="source-cid", title="Source",
                             single_chat=False, unread_point=0),
        "source-message",
    )
    assert len(commands) == 1
    assert "--no-reactions" in commands[0] and "--no-threads" in commands[0]
    if invalid is None:
        assert result.open_message_id == "source-message"
        assert result.content == "Original source"
    else:
        assert result is None


def test_refused_history_does_not_fetch_source_or_enqueue(tmp_path, monkeypatch):
    store = AutoReplyStore(tmp_path / "refused.sqlite3")
    task, attempt_id = settled_task(store)
    with store._connect() as db:
        db.execute("update reply_tasks set status='processing' where id=?", (task.id,))
    run = store.claim_agent_run(
        task.id, task.execution_generation, role=AgentRole.AUDIT,
        proposal_revision=0, turn_attempt=0, parent_agent_run_id=None,
        operation_id="refused", owner="refused-fixture",
    ).run
    store.fail_agent_run(run.id, {"code": "provider_risk_rejected", "retryable": False},
                         owner="refused-fixture")
    with store._connect() as db:
        db.execute("update reply_attempts set agent_run_id=?,send_status='failed' where id=?",
                   (run.id, attempt_id))
    before = store.get_reply_task(task.id)

    def forbidden_read(*args):
        raise AssertionError("historical refusal must stop before source read")

    monkeypatch.setattr(DwsClient, "read_message_by_id", forbidden_read)
    assert handle_rerun_attempt_post(store, attempt_id)[0] == 409
    assert store.get_reply_task(task.id) == before
    assert json.loads(store.get_agent_run(run.id).structured_error_json)["code"] == "provider_risk_rejected"


def test_provider_read_failure_does_not_rotate_generation(tmp_path, monkeypatch):
    store = AutoReplyStore(tmp_path / "provider-failure.sqlite3")
    task, attempt_id = settled_task(store)
    before = store.get_reply_task(task.id)

    def failed_read(*args):
        raise DwsError("provider unavailable")

    monkeypatch.setattr(DwsClient, "read_message_by_id", failed_read)
    with pytest.raises(DwsError, match="provider unavailable"):
        handle_rerun_attempt_post(store, attempt_id)
    assert store.get_reply_task(task.id) == before
