"""Lossless sharing of repeated fields in a DingTalk message tool result."""

import asyncio
import json
from copy import deepcopy
from types import SimpleNamespace

import pytest
import pydantic_core

from app.agent_cli import build_role_server
from app.dingtalk_models import DingTalkMessage
from app.dingtalk_message_payload_sharing import share_message_raw_payload


def _message(content: str) -> dict[str, object]:
    return {
        "open_message_id": "msg-synthetic-1",
        "open_conversation_id": "cid-synthetic-1",
        "content": content,
        "raw_payload": {
            "messageId": "msg-synthetic-1",
            "content": content,
            "text": content,
            "quotedMessage": {"messageId": "msg-parent", "text": "prior context"},
            "reactions": [{"emoji": "working", "userId": "principal"}],
            "resourceRefs": [{"id": "attachment-1"}],
        },
    }


def _expanded_raw_payload(message: dict[str, object]) -> dict[str, object]:
    raw = message["raw_payload"]
    assert isinstance(raw, dict)
    refs = message.get("raw_payload_field_refs", {})
    assert isinstance(refs, dict)
    return {key: message[refs[key]] if key in refs else value for key, value in raw.items()}


def test_message_projection_reconstructs_exact_raw_payload_and_shrinks_text():
    original = _message("请核对当前材料及结论。" * 250)
    saved = deepcopy(original)

    projected = share_message_raw_payload(original)

    assert original == saved
    assert _expanded_raw_payload(projected) == original["raw_payload"]
    assert list(_expanded_raw_payload(projected)) == list(original["raw_payload"])
    assert json.dumps(_expanded_raw_payload(projected), ensure_ascii=False) == json.dumps(
        original["raw_payload"], ensure_ascii=False
    )
    assert projected["content"] == original["content"]
    assert projected["raw_payload"]["quotedMessage"] == original["raw_payload"]["quotedMessage"]
    assert projected["raw_payload"]["reactions"] == original["raw_payload"]["reactions"]
    assert projected["raw_payload"]["resourceRefs"] == original["raw_payload"]["resourceRefs"]
    assert len(json.dumps(projected, ensure_ascii=False)) < len(json.dumps(original, ensure_ascii=False))


@pytest.mark.parametrize("message", [
    _message("ok"),
    {"content": "long" * 50, "raw_payload": {"content": "different"}},
    {"content": "long" * 50, "raw_payload": {"content": "long" * 50}, "raw_payload_field_refs": {"old": "content"}},
    {"content": "long" * 50, "raw_payload": "provider-opaque"},
])
def test_projection_preserves_short_nonmatching_or_existing_reference_shapes(message):
    assert share_message_raw_payload(message) == message


@pytest.mark.parametrize("role", ["consumer", "audit"])
def test_recent_message_tool_returns_lossless_projection_for_both_roles(monkeypatch, role):
    original = _message("源消息正文及补充上下文。" * 250)

    class FakeDwsClient:
        def read_recent_messages(self, _conversation, *, limit):
            assert limit == 50
            return [SimpleNamespace(model_dump=lambda mode: deepcopy(original))]

    monkeypatch.setattr("app.dws_client.DwsClient", FakeDwsClient)
    tool = build_role_server(role)._tool_manager.get_tool("read_dingtalk_messages").fn

    result = tool("cid-synthetic-1")

    assert len(result["messages"]) == 1
    assert _expanded_raw_payload(result["messages"][0]) == original["raw_payload"]
    assert result["messages"][0]["content"] == original["content"]
    assert len(json.dumps(result, ensure_ascii=False)) < len(
        json.dumps({"messages": [original]}, ensure_ascii=False)
    )


def test_short_duplicate_does_not_grow_fastmcp_model_visible_text(monkeypatch):
    source = DingTalkMessage(
        open_conversation_id="cid-synthetic-1",
        open_message_id="msg-synthetic-1",
        conversation_title="Synthetic",
        single_chat=False,
        sender_name="Sender",
        create_time="2026-10-18T00:00:00Z",
        content="x" * 47,
        raw_payload={"text": "x" * 47},
    )

    class FakeDwsClient:
        def read_recent_messages(self, _conversation, *, limit):
            assert limit == 50
            return [source]

    monkeypatch.setattr("app.dws_client.DwsClient", FakeDwsClient)
    server = build_role_server("consumer")
    content, _structured = asyncio.run(
        server.call_tool("read_dingtalk_messages", {"conversation_id": "cid-synthetic-1"})
    )
    assert len(content) == 1
    assert json.loads(content[0].text)["messages"] == [source.model_dump(mode="json")]


@pytest.mark.parametrize("body", ["x" * length for length in (1, 20, 47, 65, 100, 1000)] + ["中文" * 100])
def test_projection_never_enlarges_actual_indented_tool_result(body):
    original = _message(body)
    projected = share_message_raw_payload(original)
    before = pydantic_core.to_json({"messages": [original]}, fallback=str, indent=2)
    after = pydantic_core.to_json({"messages": [projected]}, fallback=str, indent=2)

    assert len(after.decode()) <= len(before.decode())
    assert _expanded_raw_payload(projected) == original["raw_payload"]
