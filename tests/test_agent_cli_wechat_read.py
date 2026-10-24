"""A WeChat task reads only its own conversation, through the standard agent tools."""

import sqlite3

import pytest

from app.agent_cli import build_role_server
from app.store import AutoReplyStore, REPLY_TASK_SQL
from app.wechat import service
from app.wechat.models import WechatMessage


def _message(index, *, direction="inbound", text=None):
    return WechatMessage(
        account_id="acct-1", conversation_id="u9", message_id=f"m{index}",
        sender_id="u9" if direction == "inbound" else "self-1",
        sender_display_name="Alex" if direction == "inbound" else "derek",
        conversation_type="direct", direction=direction,
        sent_at=f"2026-10-24T10:0{index}:00+08:00", kind="text",
        text=text or f"message {index}", source_version="test",
    )


class FakeReader:
    def __init__(self, messages):
        self.messages, self.calls = messages, []

    def read_messages(self, account, **kwargs):
        self.calls.append((account, kwargs))
        return self.messages


def _bound_task(tmp_path, *, channel="wechat", single_chat=True, ready=True):
    db_path = tmp_path / "service.sqlite3"
    store = AutoReplyStore(db_path)
    if ready:
        store.upsert_wechat_read_state(
            account_id="acct-1", account_dir="/a", db_dir="/a/db_storage",
            app_version="4.1.10", self_user_id="self-1", capability_status="ready",
        )
    assert store.enqueue_reply_task(
        channel=channel, conversation_id="u9", conversation_title="Alex",
        single_chat=single_chat, trigger_message_id="m1",
        trigger_create_time="2026-10-24T10:00:00+08:00", trigger_sender="Alex",
        trigger_text="hi", execution_generation="generation-one",
    )
    with sqlite3.connect(db_path) as db:
        task_id = db.execute(f"select id from ({REPLY_TASK_SQL})").fetchone()[0]
    assert store.claim_reply_task(task_id) is not None
    return db_path, task_id


def _tool(role, db_path, task_id, generation="generation-one"):
    return build_role_server(
        role, task_id=task_id, db_path=db_path, execution_generation=generation,
    )._tool_manager.get_tool("read_wechat_messages").fn


@pytest.mark.parametrize("role", ["consumer", "audit"])
@pytest.mark.parametrize(("single_chat", "kind"), [(True, "direct"), (False, "group")])
def test_wechat_task_reads_its_own_conversation(
    tmp_path, monkeypatch, role, single_chat, kind,
):
    db_path, task_id = _bound_task(tmp_path, single_chat=single_chat)
    reader = FakeReader([_message(1), _message(2, direction="outbound")])
    monkeypatch.setattr(service, "build_reader", lambda **_kw: reader)

    result = _tool(role, db_path, task_id)(limit=5)

    assert result["conversation_id"] == "u9"
    assert result["conversation_type"] == kind
    assert [m["text"] for m in result["messages"]] == ["message 1", "message 2"]
    assert result["messages"][1]["direction"] == "outbound"
    account, kwargs = reader.calls[0]
    assert account.account_id == "acct-1"
    assert kwargs == {"conversation_id": "u9", "conversation_type": kind, "limit": 5}


def test_wechat_read_uses_the_claimed_input_after_current_input_refresh(
    tmp_path, monkeypatch,
):
    db_path, task_id = _bound_task(tmp_path)
    store = AutoReplyStore(db_path)
    with store._immediate_write_transaction() as db:
        from app.task_entity_storage import (
            append_task_input,
            bind_current_input,
            get_task_with_inputs,
        )

        task = get_task_with_inputs(db, task_id)
        claimed = task["current_input"]
        refreshed = {
            key: value for key, value in claimed.items()
            if key not in {"id", "task_id"}
        }
        refreshed.update(
            conversation_id="u10",
            trigger_message_id="m2",
            input_revision_key="refresh:2:changed-conversation",
        )
        new_input, inserted = append_task_input(db, task_id=task_id, values=refreshed)
        assert inserted
        assert bind_current_input(
            db,
            task_id=task_id,
            input_id=int(new_input["id"]),
            expected_input_version=int(task["input_version"]),
            input_version=int(task["input_version"]) + 1,
        ) is not None

    reader = FakeReader([_message(1)])
    monkeypatch.setattr(service, "build_reader", lambda **_kw: reader)
    result = _tool("consumer", db_path, task_id)(limit=5)

    assert result["conversation_id"] == "u9"
    assert reader.calls[0][1]["conversation_id"] == "u9"


def test_wechat_read_cannot_name_another_conversation(tmp_path):
    db_path, task_id = _bound_task(tmp_path)

    with pytest.raises(TypeError):
        _tool("consumer", db_path, task_id)(conversation_id="someone-else")


def test_wechat_read_refuses_a_non_wechat_task(tmp_path, monkeypatch):
    db_path, task_id = _bound_task(tmp_path, channel="dingtalk")
    monkeypatch.setattr(service, "build_reader", lambda **_kw: pytest.fail("must not read"))

    with pytest.raises(ValueError, match="not a WeChat conversation"):
        _tool("consumer", db_path, task_id)()


def test_wechat_read_refuses_a_changed_generation(tmp_path, monkeypatch):
    db_path, task_id = _bound_task(tmp_path)
    monkeypatch.setattr(service, "build_reader", lambda **_kw: pytest.fail("must not read"))

    with pytest.raises(ValueError, match="generation"):
        _tool("consumer", db_path, task_id, generation="generation-two")()


@pytest.mark.parametrize("limit", [0, 101, True, "20"])
def test_wechat_read_validates_the_limit(tmp_path, monkeypatch, limit):
    db_path, task_id = _bound_task(tmp_path)
    monkeypatch.setattr(service, "build_reader", lambda **_kw: pytest.fail("must not read"))

    with pytest.raises(ValueError, match="limit"):
        _tool("consumer", db_path, task_id)(limit=limit)


def test_wechat_read_needs_a_ready_account(tmp_path, monkeypatch):
    db_path, task_id = _bound_task(tmp_path, ready=False)
    monkeypatch.setattr(service, "build_reader", lambda **_kw: pytest.fail("must not read"))

    with pytest.raises(ValueError, match="no ready WeChat account"):
        _tool("consumer", db_path, task_id)()
