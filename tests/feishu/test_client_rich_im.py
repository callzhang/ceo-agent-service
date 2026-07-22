import asyncio
import importlib.metadata
import json
import logging
from types import SimpleNamespace

import pytest

import app.feishu.client as client_module
from app.feishu.actions import build_message_action
from app.feishu.client import (
    ALLOWED_REACTION_EMOJI_TYPES,
    FeishuChannelClient,
    FeishuClientConfig,
    FeishuSdkOperationError,
    build_channel,
    normalize_send_result,
)
from app.feishu.ingress import normalize_sdk_envelope
from app.feishu.prompt import build_feishu_turn_prompt
from app.store import AutoReplyStore


def _delivery(**updates):
    values = {
        "app_id": "cli_test",
        "chat_id": "oc_1",
        "reply_to_message_id": "om_source",
        "reply_in_thread": True,
        "reply_text": "收到",
        "reply_format": "text",
        "mention_open_ids": (),
        "idempotency_key": "e7c1c1ad-c345-5f9e-bddd-dace542577c9",
    }
    values.update(updates)
    return SimpleNamespace(**values)


class RichRawChannel:
    def __init__(self):
        self.send_result = SimpleNamespace(
            success=True,
            message_id="om_reply",
            chunk_ids=None,
            error=None,
            raw={},
        )
        self.fetch_result = {"code": 0, "data": {"items": [{"message_id": "om_1"}]}}
        self.download_result = b"payload"
        self.reaction_result = SimpleNamespace(
            success=True,
            message_id="om_1",
            chunk_ids=None,
            error=None,
            raw={},
        )
        self.recall_result = self.reaction_result
        self.send_calls = []
        self.fetch_calls = []
        self.download_calls = []
        self.reaction_calls = []
        self.recall_calls = []

    async def send(self, *args):
        self.send_calls.append(args)
        if isinstance(self.send_result, BaseException):
            raise self.send_result
        return self.send_result

    async def fetch_message(self, message_id):
        self.fetch_calls.append(message_id)
        if isinstance(self.fetch_result, BaseException):
            raise self.fetch_result
        return self.fetch_result

    async def download_resource(self, *args, **kwargs):
        self.download_calls.append((args, kwargs))
        if isinstance(self.download_result, BaseException):
            raise self.download_result
        return self.download_result

    async def add_reaction(self, *args):
        self.reaction_calls.append(args)
        if isinstance(self.reaction_result, BaseException):
            raise self.reaction_result
        return self.reaction_result

    async def recall_message(self, *args):
        self.recall_calls.append(args)
        if isinstance(self.recall_result, BaseException):
            raise self.recall_result
        return self.recall_result


def test_normalize_send_result_preserves_bounded_ordered_chunk_ids_without_raw():
    chunk_ids = ["om_first", "om_second", "om_second"] + [
        f"om_{index}" for index in range(2, 150)
    ]
    result = normalize_send_result(
        SimpleNamespace(
            success=True,
            message_id="om_first",
            chunk_ids=chunk_ids,
            error=None,
            raw={
                "headers": {
                    "x-tt-logid": "log-safe",
                    "authorization": "secret-token",
                },
                "content": "private reply text",
            },
        )
    )

    assert result.success is True
    assert result.message_id == "om_first"
    assert result.message_ids[:2] == ("om_first", "om_second")
    assert len(result.message_ids) == 100
    assert result.request_log_id == "log-safe"
    assert not hasattr(result, "raw")
    assert "secret-token" not in repr(result)
    assert "private reply text" not in repr(result)


def test_success_without_any_message_id_remains_success_for_unknown_reconciliation():
    result = normalize_send_result(
        SimpleNamespace(
            success=True,
            message_id=None,
            chunk_ids=[],
            error=None,
            raw={"body": "must not survive"},
        )
    )

    assert result.success is True
    assert result.message_id == ""
    assert result.message_ids == ()
    assert "must not survive" not in repr(result)


def test_chunk_id_becomes_legacy_message_id_when_sdk_omits_primary():
    result = normalize_send_result(
        SimpleNamespace(
            success=True,
            message_id=None,
            chunk_ids=["om_chunk_1", "om_chunk_2"],
            error=None,
            raw={},
        )
    )

    assert result.message_id == "om_chunk_1"
    assert result.message_ids == ("om_chunk_1", "om_chunk_2")


def test_post_reply_uses_controlled_markdown_shape_and_fail_closed_options():
    raw = RichRawChannel()
    delivery = _delivery(reply_format="post", reply_text="**完成**")

    result = asyncio.run(
        FeishuChannelClient(raw, app_id="cli_test").send_reply(delivery)
    )

    assert result.success is True
    to, message, opts = raw.send_calls[0]
    assert to == "oc_1"
    assert message == {"markdown": "**完成**"}
    assert opts == {
        "reply_to": "om_source",
        "reply_in_thread": True,
        "receive_id_type": "chat_id",
        "reply_target_gone": "fail",
        "uuid": delivery.idempotency_key,
        "resolve_mentions_in_text": False,
    }


@pytest.mark.parametrize("reply_format", ["text", "post"])
def test_mentions_use_typed_sdk_outbound_and_never_text_name_resolution(reply_format):
    sdk = pytest.importorskip("lark_channel")
    raw = RichRawChannel()
    delivery = _delivery(
        reply_format=reply_format,
        mention_open_ids=("ou_alice_1", "ou_bob-2", "ou_alice_1"),
    )

    asyncio.run(FeishuChannelClient(raw, app_id="cli_test").send_reply(delivery))

    _, outbound, opts = raw.send_calls[0]
    expected_type = sdk.OutboundText if reply_format == "text" else sdk.OutboundPost
    assert isinstance(outbound, expected_type)
    assert [identity.open_id for identity in outbound.mentions] == [
        "ou_alice_1",
        "ou_bob-2",
    ]
    assert opts["resolve_mentions_in_text"] is False


@pytest.mark.parametrize(
    "mention_open_ids",
    [
        ("all",),
        ("on_union",),
        ("cli_app",),
        ("ou_good\"><at user_id=\"all",),
        (" ou_good",),
        "ou_good",
    ],
)
def test_invalid_mention_identity_is_rejected_before_sdk_call(mention_open_ids):
    raw = RichRawChannel()
    with pytest.raises(ValueError, match="mention_open_ids"):
        asyncio.run(
            FeishuChannelClient(raw, app_id="cli_test").send_reply(
                _delivery(mention_open_ids=mention_open_ids)
            )
        )
    assert raw.send_calls == []


def test_reply_format_and_uuid_limits_are_enforced_before_sdk_call():
    raw = RichRawChannel()
    client = FeishuChannelClient(raw, app_id="cli_test")

    with pytest.raises(ValueError, match="reply_format"):
        asyncio.run(client.send_reply(_delivery(reply_format="card")))
    with pytest.raises(ValueError, match="idempotency_key"):
        asyncio.run(client.send_reply(_delivery(idempotency_key="x" * 51)))
    assert raw.send_calls == []


def test_sdk_send_exception_becomes_payload_free_failure_result():
    raw = RichRawChannel()
    raw.send_result = RuntimeError("private reply text; token=secret")

    result = asyncio.run(
        FeishuChannelClient(raw, app_id="cli_test").send_reply(_delivery())
    )

    assert result.success is False
    assert result.error_code == "unknown"
    assert "private reply text" not in repr(result)
    assert "secret" not in repr(result)


def test_preplanned_chunk_is_one_adapter_call_with_exact_stable_uuid():
    raw = RichRawChannel()
    client = FeishuChannelClient(raw, app_id="cli_test")
    chunk_uuid = "234d8a19-c999-54fd-8875-08d2748e3897"

    result = asyncio.run(
        client.send_reply_chunk(
            _delivery(reply_text="unused-full-snapshot"),
            text="one local chunk",
            ordinal=1,
            expected_chunks=3,
            idempotency_key=chunk_uuid,
        )
    )

    assert result.success is True
    assert len(raw.send_calls) == 1
    _, outbound, opts = raw.send_calls[0]
    assert outbound == {"text": "one local chunk"}
    assert opts["uuid"] == chunk_uuid
    assert len(opts["uuid"]) <= 50


def test_direct_adapter_rejects_long_text_that_would_hide_sdk_chunks():
    raw = RichRawChannel()
    with pytest.raises(ValueError, match="deterministic local chunks"):
        asyncio.run(
            FeishuChannelClient(raw, app_id="cli_test").send_reply(
                _delivery(reply_text="x" * 3501)
            )
        )
    assert raw.send_calls == []


@pytest.mark.parametrize(
    ("raw_result", "expected"),
    [
        ({"code": 0, "data": {"items": [{"content": "secret"}]}}, "exists"),
        ({"code": 0, "data": {"items": []}}, "absent"),
        ({"code": 230002, "msg": "secret not found"}, "absent"),
        ({"code": 99991672, "msg": "secret permission details"}, "unknown"),
        ({"code": 0, "data": {"unexpected": "secret"}}, "unknown"),
        (RuntimeError("token=secret"), "unknown"),
    ],
)
def test_fetch_message_state_returns_only_redacted_existence(raw_result, expected):
    raw = RichRawChannel()
    raw.fetch_result = raw_result

    state = asyncio.run(
        FeishuChannelClient(raw, app_id="cli_test").fetch_message_state(
            "cli_test", "om_1"
        )
    )

    assert state.state == expected
    assert "secret" not in repr(state)
    assert raw.fetch_calls == ["om_1"]


def test_fetch_message_state_enforces_app_binding_before_sdk_call():
    raw = RichRawChannel()
    with pytest.raises(PermissionError, match="App ID"):
        asyncio.run(
            FeishuChannelClient(raw, app_id="cli_test").fetch_message_state(
                "cli_other", "om_1"
            )
        )
    assert raw.fetch_calls == []


@pytest.mark.parametrize(
    ("resource_type", "sdk_resource_type"),
    [
        ("image", "image"),
        ("file", "file"),
        ("audio", "file"),
        ("video", "file"),
    ],
)
def test_download_is_message_bound_and_maps_resource_types(
    resource_type, sdk_resource_type
):
    raw = RichRawChannel()
    raw.download_result = bytearray(b"resource")

    payload = asyncio.run(
        FeishuChannelClient(raw, app_id="cli_test").download_inbound_resource(
            "cli_test", "om_1", "file_key_1", resource_type, 64
        )
    )

    assert payload == b"resource"
    assert raw.download_calls == [
        (
            ("file_key_1",),
            {"resource_type": sdk_resource_type, "message_id": "om_1"},
        )
    ]


@pytest.mark.parametrize(
    "updates",
    [
        {"app_id": "cli_other"},
        {"message_id": ""},
        {"file_key": ""},
        {"resource_type": "sticker"},
        {"resource_type": "unknown"},
        {"max_bytes": 0},
        {"max_bytes": True},
    ],
)
def test_invalid_download_request_is_rejected_before_sdk_call(updates):
    raw = RichRawChannel()
    values = {
        "app_id": "cli_test",
        "message_id": "om_1",
        "file_key": "file_key_1",
        "resource_type": "file",
        "max_bytes": 64,
    }
    values.update(updates)

    with pytest.raises((PermissionError, ValueError)):
        asyncio.run(
            FeishuChannelClient(raw, app_id="cli_test").download_inbound_resource(
                **values
            )
        )
    assert raw.download_calls == []


def test_download_size_limit_and_sdk_error_are_sanitized():
    raw = RichRawChannel()
    client = FeishuChannelClient(raw, app_id="cli_test")
    raw.download_result = b"12345"
    with pytest.raises(ValueError, match="exceeds max_bytes"):
        asyncio.run(
            client.download_inbound_resource(
                "cli_test", "om_1", "file_1", "file", 4
            )
        )

    raw.download_result = RuntimeError("response body=secret; token=secret")
    with pytest.raises(FeishuSdkOperationError) as caught:
        asyncio.run(
            client.download_inbound_resource(
                "cli_test", "om_1", "file_1", "file", 64
            )
        )
    assert "secret" not in str(caught.value)
    assert caught.value.__cause__ is None


class _FakeStreamingResponse:
    def __init__(self, *, content_type, body, status_code=200):
        self.status_code = status_code
        self.headers = {
            "content-type": content_type,
            "content-length": str(len(body)),
        }
        self.body = body
        self.iterated = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False

    async def aiter_bytes(self, *, chunk_size):
        del chunk_size
        self.iterated = True
        yield self.body


def _install_fake_bounded_transport(monkeypatch, response, *, emitted_logs=False):
    token = "TENANT_TOKEN_MUST_NOT_LOG"
    calls = []

    class TokenManager:
        @staticmethod
        def get_self_tenant_token(_config):
            return token

    class AsyncClient:
        def __init__(self, **kwargs):
            calls.append(("client", kwargs))

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        def stream(self, method, url, *, headers, params, timeout):
            calls.append((method, url, headers, params, timeout))
            if emitted_logs:
                logging.getLogger("httpx").info(
                    "HTTP request %s Authorization=%s", url, headers["Authorization"]
                )
                logging.getLogger("httpcore.connection").debug(
                    "transport url=%s token=%s", url, headers["Authorization"]
                )
            return response

    real_import = client_module.importlib.import_module

    def import_module(name):
        if name == "lark_channel.core.token":
            return SimpleNamespace(TokenManager=TokenManager)
        if name == "httpx":
            return SimpleNamespace(AsyncClient=AsyncClient)
        return real_import(name)

    monkeypatch.setattr(client_module.importlib, "import_module", import_module)
    official_type = type("OfficialChannel", (), {})
    official_type.__module__ = "lark_channel.testing"
    channel = official_type()
    channel.client = SimpleNamespace(
        config=SimpleNamespace(
            domain="https://open.feishu.cn",
            timeout=30.0,
            trust_env_proxy=False,
            proxy_url="",
        )
    )
    return FeishuChannelClient(channel, app_id="cli_test"), calls, token


def test_bounded_download_rejects_success_html_without_reading_body(monkeypatch):
    secret_body = b"<html>PRIVATE_PROXY_DIAGNOSTIC</html>"
    response = _FakeStreamingResponse(content_type="text/html", body=secret_body)
    client, _calls, _token = _install_fake_bounded_transport(
        monkeypatch, response
    )

    with pytest.raises(FeishuSdkOperationError) as caught:
        asyncio.run(
            client.download_inbound_resource(
                "cli_test", "om_1", "FILE_KEY_MUST_NOT_LOG", "file", 1024
            )
        )

    assert caught.value.code == "download_failed"
    assert response.iterated is False
    assert "PRIVATE_PROXY_DIAGNOSTIC" not in str(caught.value)


def test_bounded_download_scrubs_httpx_and_httpcore_resource_logs(
    monkeypatch, caplog
):
    response = _FakeStreamingResponse(
        content_type="application/octet-stream", body=b"resource"
    )
    client, calls, token = _install_fake_bounded_transport(
        monkeypatch, response, emitted_logs=True
    )
    file_key = "FILE_KEY_MUST_NOT_LOG"
    caplog.set_level(logging.DEBUG)

    payload = asyncio.run(
        client.download_inbound_resource(
            "cli_test", "om_1", file_key, "file", 1024
        )
    )

    assert payload == b"resource"
    assert response.iterated is True
    assert any(call[0] == "GET" for call in calls if len(call) == 5)
    assert file_key not in caplog.text
    assert token not in caplog.text
    assert "diagnostic redacted" in caplog.text


def test_channel_build_permanently_redacts_inbound_lark_debug_payload(caplog):
    pytest.importorskip("lark_channel")
    message_text = "INBOUND_PRIVATE_TEXT_MUST_NOT_LOG"
    file_key = "INBOUND_FILE_KEY_MUST_NOT_LOG"

    build_channel(
        FeishuClientConfig(app_id="cli_log_boundary", app_secret="secret")
    )
    # The SDK resets its logger to INFO during construction.  Operators may
    # explicitly turn DEBUG back on for diagnosis; payload redaction must
    # remain permanent in that mode.
    caplog.set_level(logging.DEBUG, logger="Lark")
    logging.getLogger("Lark").debug(
        "dispatch event=%r",
        {"content": message_text, "file_key": file_key, "chat_id": "oc_private"},
    )

    assert message_text not in caplog.text
    assert file_key not in caplog.text
    assert "oc_private" not in caplog.text
    assert "Feishu SDK inbound diagnostic redacted" in caplog.text


def test_reaction_and_recall_are_app_bound_allowlisted_and_payload_free():
    raw = RichRawChannel()
    sdk_raw = {
        "headers": {"x-tt-logid": "log-1", "authorization": "secret"},
        "data": {"reaction_id": "omr_reaction_1", "content": "private"},
    }
    raw.reaction_result = SimpleNamespace(
        success=True,
        message_id="om_1",
        chunk_ids=None,
        error=None,
        raw=sdk_raw,
    )
    raw.recall_result = SimpleNamespace(
        success=True,
        message_id="om_1",
        chunk_ids=None,
        error=None,
        raw={
            "headers": {"x-tt-logid": "log-1", "authorization": "secret"},
            "data": {"content": "private"},
        },
    )
    client = FeishuChannelClient(raw, app_id="cli_test")

    reacted = asyncio.run(client.add_reaction("cli_test", "om_1", "DONE"))
    recalled = asyncio.run(client.recall_message("cli_test", "om_1"))

    assert raw.reaction_calls == [("om_1", "DONE")]
    assert raw.recall_calls == [("om_1",)]
    assert reacted.success and recalled.success
    assert reacted.request_log_id == recalled.request_log_id == "log-1"
    assert reacted.reaction_id == "omr_reaction_1"
    assert recalled.reaction_id == ""
    assert reacted.message_ids == ()
    assert recalled.message_ids == ()
    assert not hasattr(reacted, "raw")
    assert "secret" not in repr((reacted, recalled))
    assert "private" not in repr((reacted, recalled))


def test_recall_success_without_message_id_remains_a_successful_action():
    raw = RichRawChannel()
    raw.recall_result = SimpleNamespace(
        success=True,
        message_id=None,
        chunk_ids=None,
        error=None,
        raw={"data": {"deleted": True, "content": "private"}},
    )

    result = asyncio.run(
        FeishuChannelClient(raw, app_id="cli_test").recall_message(
            "cli_test", "om_1"
        )
    )

    assert result.success is True
    assert result.message_id == ""
    assert result.message_ids == ()
    assert "private" not in repr(result)


def test_action_target_echo_is_stripped_only_for_exact_success():
    raw = RichRawChannel()
    client = FeishuChannelClient(raw, app_id="cli_test")
    raw.reaction_result = SimpleNamespace(
        success=True,
        message_id="om_other",
        chunk_ids=None,
        error=None,
        raw={"data": {"reaction_id": "omr_1"}},
    )
    raw.recall_result = SimpleNamespace(
        success=True,
        message_id="om_1",
        chunk_ids=["om_other"],
        error=None,
        raw={},
    )

    mismatched = asyncio.run(
        client.add_reaction("cli_test", "om_1", "DONE")
    )
    multiple = asyncio.run(client.recall_message("cli_test", "om_1"))

    assert mismatched.message_ids == ("om_other",)
    assert multiple.message_ids == ("om_1", "om_other")


def test_failed_action_target_echo_is_preserved_as_uncertain_evidence():
    raw = RichRawChannel()
    raw.reaction_result = SimpleNamespace(
        success=False,
        message_id="om_1",
        chunk_ids=None,
        error=RuntimeError("failed"),
        raw={},
    )

    result = asyncio.run(
        FeishuChannelClient(raw, app_id="cli_test").add_reaction(
            "cli_test", "om_1", "DONE"
        )
    )

    assert result.success is False
    assert result.message_ids == ("om_1",)


def test_reaction_rejects_non_allowlisted_emoji_and_cross_app_calls():
    raw = RichRawChannel()
    client = FeishuChannelClient(raw, app_id="cli_test")
    assert "DONE" in ALLOWED_REACTION_EMOJI_TYPES

    with pytest.raises(ValueError, match="allowlisted"):
        asyncio.run(client.add_reaction("cli_test", "om_1", "Typing"))
    with pytest.raises(PermissionError, match="App ID"):
        asyncio.run(client.add_reaction("cli_other", "om_1", "DONE"))
    with pytest.raises(PermissionError, match="App ID"):
        asyncio.run(client.recall_message("cli_other", "om_1"))
    assert raw.reaction_calls == []
    assert raw.recall_calls == []


def test_reaction_and_recall_sdk_exceptions_do_not_escape_payloads():
    raw = RichRawChannel()
    raw.reaction_result = RuntimeError("reaction raw=secret")
    raw.recall_result = RuntimeError("recall text=private")
    client = FeishuChannelClient(raw, app_id="cli_test")

    reacted = asyncio.run(client.add_reaction("cli_test", "om_1", "DONE"))
    recalled = asyncio.run(client.recall_message("cli_test", "om_1"))

    assert reacted.success is recalled.success is False
    assert reacted.error_code == recalled.error_code == "unknown"
    assert "secret" not in repr(reacted)
    assert "private" not in repr(recalled)


def test_handoff_is_direct_open_id_text_with_only_stable_uuid_options():
    raw = RichRawChannel()
    raw.send_result = SimpleNamespace(
        success=True,
        message_id="om_handoff",
        chunk_ids=None,
        error=None,
        raw={},
    )
    action = build_message_action(
        reply_task_id=7,
        attempt_id=9,
        app_id="cli_test",
        chat_id="oc_origin",
        action_key="handoff:owner",
        kind="handoff_notify",
        target_open_id="ou_owner_1",
        payload={"text": "请人工接管"},
    )

    result = asyncio.run(
        FeishuChannelClient(raw, app_id="cli_test").send_handoff(action)
    )

    assert result.message_id == "om_handoff"
    assert raw.send_calls == [
        (
            "ou_owner_1",
            {"text": "请人工接管"},
            {
                "receive_id_type": "open_id",
                "uuid": action.idempotency_key,
                "resolve_mentions_in_text": False,
            },
        )
    ]


@pytest.mark.parametrize(
    "update",
    [
        {"app_id": "cli_other"},
        {"target_open_id": "oc_group"},
        {"idempotency_key": "attacker-selected"},
        {"target_message_id": "om_forbidden"},
        {"payload_json": '{"text":"ok","receive_id_type":"chat_id"}'},
    ],
)
def test_handoff_rejects_identity_or_sdk_shape_drift_before_send(update):
    raw = RichRawChannel()
    action = build_message_action(
        reply_task_id=7,
        attempt_id=9,
        app_id="cli_test",
        chat_id="oc_origin",
        action_key="handoff:owner",
        kind="handoff_notify",
        target_open_id="ou_owner_1",
        payload={"text": "请人工接管"},
    ).model_copy(update=update)

    with pytest.raises((PermissionError, ValueError)):
        asyncio.run(
            FeishuChannelClient(raw, app_id="cli_test").send_handoff(action)
        )
    assert raw.send_calls == []


def test_handoff_sink_rejects_untrusted_at_markup_before_network():
    raw = RichRawChannel()
    action = build_message_action(
        reply_task_id=7,
        attempt_id=9,
        app_id="cli_test",
        chat_id="oc_origin",
        action_key="handoff:owner",
        kind="handoff_notify",
        target_open_id="ou_owner_1",
        payload={"text": "请人工接管"},
    ).model_copy(
        update={
            # Bypass model validation deliberately to prove the final SDK
            # sink remains fail-closed if an in-memory object is corrupted.
            "payload_json": json.dumps(
                {
                    "text": (
                        '<at user_id="ou_attacker">Attacker</at> 请人工接管'
                    )
                },
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
        }
    )

    with pytest.raises(ValueError, match="untrusted at markup"):
        asyncio.run(
            FeishuChannelClient(raw, app_id="cli_test").send_handoff(action)
        )

    assert raw.send_calls == []


def test_pinned_official_sdk_contract_keeps_typed_mentions_on_first_chunk_only():
    sdk = pytest.importorskip("lark_channel")
    assert importlib.metadata.version("lark-channel-sdk") == "1.2.0"
    from lark_channel.channel.config import OutboundConfig
    from lark_channel.channel.outbound.sender import OutboundSender, SendDriver

    async def unused(**kwargs):
        del kwargs
        return {"code": 0, "data": {"message_id": "om_unused"}}

    sender = OutboundSender(
        SendDriver(create_message=unused, reply_message=unused),
        OutboundConfig(text_chunk_limit=4, chunk_mode="none"),
    )
    identity = sdk.Identity(open_id="ou_alice")
    text_chunks = asyncio.run(
        sender._materialize(sdk.OutboundText(text="abcdefgh", mentions=[identity]))
    )
    post_chunks = asyncio.run(
        sender._materialize(sdk.OutboundPost(markdown="abcd\nefgh", mentions=[identity]))
    )

    assert len(text_chunks) == len(post_chunks) == 2
    text_bodies = [json.loads(chunk["content"])["text"] for chunk in text_chunks]
    assert "<at " in text_bodies[0]
    assert "<at " not in text_bodies[1]
    post_bodies = [json.loads(chunk["content"]) for chunk in post_chunks]
    assert '"tag": "at"' in json.dumps(post_bodies[0])
    assert '"tag": "at"' not in json.dumps(post_bodies[1])


@pytest.mark.parametrize("reply_format", ["text", "post"])
def test_pinned_sdk_sink_rejects_untrusted_at_markup_before_network(
    reply_format,
):
    sdk = pytest.importorskip("lark_channel")
    assert importlib.metadata.version("lark-channel-sdk") == "1.2.0"
    from lark_channel.channel.config import OutboundConfig
    from lark_channel.channel.outbound.sender import OutboundSender, SendDriver

    calls = []

    async def reply_message(**kwargs):
        calls.append(kwargs)
        return {"code": 0, "data": {"message_id": "om_forbidden"}}

    async def create_message(**kwargs):
        raise AssertionError("reply chunks must not become new messages")

    channel = sdk.FeishuChannel(app_id="cli_test", app_secret="secret")
    channel._sender = OutboundSender(
        SendDriver(
            create_message=create_message,
            reply_message=reply_message,
        ),
        OutboundConfig(),
    )
    client = FeishuChannelClient(channel, app_id="cli_test")

    with pytest.raises(ValueError, match="untrusted at markup"):
        asyncio.run(
            client.send_reply_chunk(
                _delivery(reply_format=reply_format),
                text=(
                    '# 标题\n<At\tuser_id="ou_attacker">Attacker</aT>'
                    if reply_format == "post"
                    else '<at user_id="ou_attacker">Attacker</at>'
                ),
                ordinal=0,
                expected_chunks=1,
                idempotency_key="234d8a19-c999-54fd-8875-08d2748e3897",
            )
        )

    assert calls == []


def test_pinned_sdk_post_resource_keys_never_reach_event_task_or_prompt(
    tmp_path,
):
    sdk = pytest.importorskip("lark_channel")
    assert importlib.metadata.version("lark-channel-sdk") == "1.2.0"
    image_key = "img_secret_contract_key"
    video_key = "file_secret_contract_key"
    client = build_channel(
        FeishuClientConfig(app_id="cli_contract", app_secret="secret")
    )

    async def normalize_post():
        return await client.channel._pipeline.normalize(
            event_id="evt_post_resources",
            message_event={
                "message_id": "om_post_resources",
                "root_id": "om_post_resources",
                "parent_id": "",
                "create_time": "1784685600000",
                "chat_id": "oc_contract",
                "chat_type": "group",
                "message_type": "post",
                "content": json.dumps(
                    {
                        "zh_cn": {
                            "title": "季度更新",
                            "content": [
                                [
                                    {"tag": "text", "text": "正文 "},
                                    {"tag": "img", "image_key": image_key},
                                    {"tag": "media", "file_key": video_key},
                                ]
                            ],
                        }
                    }
                ),
                "mentions": [],
            },
            sender={
                "sender_id": {"open_id": "ou_contract"},
                "sender_type": "user",
            },
        )

    sdk_message = asyncio.run(normalize_post())
    envelope = normalize_sdk_envelope(sdk_message, app_id="cli_contract")
    assert envelope.message.body_text == "# 季度更新\n\n正文 [图片][视频]"
    assert image_key not in envelope.message.model_dump_json()
    assert video_key not in envelope.message.model_dump_json()
    assert image_key not in repr(envelope)
    assert video_key not in repr(envelope)

    store = AutoReplyStore(tmp_path / "post-resources.sqlite3")
    stored = store.record_feishu_event(
        envelope.message,
        eligibility_status="eligible",
        store_body=True,
    )
    [task] = store.list_reply_tasks(channel="feishu")
    prompt = build_feishu_turn_prompt(envelope.message, [stored])
    for sink in (
        stored.body_text,
        task.trigger_message_json,
        prompt,
        repr(stored),
    ):
        assert image_key not in sink
        assert video_key not in sink


def test_pinned_sdk_channel_disables_implicit_inbound_network_enrichment(
    monkeypatch,
):
    """The real 1.2.0 defaults are unsafe for our pre-scope ingress edge."""
    sdk = pytest.importorskip("lark_channel")
    assert importlib.metadata.version("lark-channel-sdk") == "1.2.0"
    from lark_channel.channel import _api_helpers

    async def forbidden_default_lookup(*_args, **_kwargs):
        raise AssertionError("implicit Contact API lookup")

    async def forbidden_message_fetch(*_args, **_kwargs):
        raise AssertionError("implicit message/card/merge-forward fetch")

    monkeypatch.setattr(
        _api_helpers, "default_name_lookup", forbidden_default_lookup
    )
    client = build_channel(
        FeishuClientConfig(app_id="cli_contract", app_secret="secret")
    )
    channel = client.channel
    config = channel._config

    assert config.inbound.expand_merge_forward is False
    assert config.inbound.fetch_interactive_card is False
    assert config.inbound.reaction_notifications == "off"
    assert config.inbound.name_cache.enabled is False
    # SDK 1.2 has no typed root_id. Raw remains internal solely for the
    # bounded root_id/parent_id ingress extraction contract.
    assert config.inbound.include_raw is True
    assert config.inbound.emit_raw_events is False
    assert config.resolve_sender_names is False
    assert channel._identity_resolver._lookup(["ou_private"]) == {}

    channel._pipeline._deps.fetch_message = forbidden_message_fetch
    channel._pipeline._expander._fetch_message = forbidden_message_fetch
    sender = {
        "sender_id": {"open_id": "ou_contract"},
        "sender_type": "user",
    }

    async def normalize(message_type, content):
        return await channel._pipeline.normalize(
            event_id=f"evt_{message_type}",
            message_event={
                "message_id": f"om_{message_type}",
                "root_id": "om_root",
                "parent_id": "om_parent",
                "create_time": "1784685600000",
                "chat_id": "oc_contract",
                "thread_id": "omt_contract",
                "chat_type": "group",
                "message_type": message_type,
                "content": json.dumps(content),
                "mentions": [],
            },
            sender=sender,
        )

    text_message, card_message, forward_message = asyncio.run(
        _normalize_contract_messages(normalize)
    )
    assert isinstance(text_message.content, sdk.TextContent)
    assert text_message.safe_content_text == "hello"
    assert text_message.body_text == "hello"
    assert isinstance(card_message.content, sdk.InteractiveContent)
    assert isinstance(forward_message.content, sdk.MergeForwardContent)
    assert forward_message.content.loading is True
    envelope = normalize_sdk_envelope(text_message, app_id="cli_contract")
    assert envelope.message.root_message_id == "om_root"
    assert envelope.message.parent_message_id == "om_parent"
    assert envelope.message.thread_id == "omt_contract"
    assert not hasattr(envelope.message, "raw")


async def _normalize_contract_messages(normalize):
    return (
        await normalize("text", {"text": "hello"}),
        await normalize("interactive", {"elements": [{"tag": "div"}]}),
        await normalize("merge_forward", {}),
    )


def test_pinned_sdk_delivers_each_message_separately_and_serially():
    sdk = pytest.importorskip("lark_channel")
    assert importlib.metadata.version("lark-channel-sdk") == "1.2.0"
    from lark_channel.channel.safety.chat_pipeline import ChatPipelineManager

    client = build_channel(
        FeishuClientConfig(app_id="cli_contract", app_secret="secret")
    )
    config = client.channel._config.safety
    assert config.text_batch.delay_ms == 0
    assert config.text_batch.max_messages == 1
    assert config.media_batch.enabled is False
    assert config.chat_queue.enabled is True
    assert config.chat_queue.merge_while_busy is False

    async def exercise():
        manager = ChatPipelineManager(
            config.text_batch,
            asyncio.get_running_loop(),
            queue_config=config.chat_queue,
        )
        seen: list[tuple[str, tuple[str, ...]]] = []
        active = 0
        max_active = 0

        async def handler(message, sources):
            nonlocal active, max_active
            active += 1
            max_active = max(max_active, active)
            await asyncio.sleep(0)
            seen.append((message.id, tuple(item.id for item in sources)))
            active -= 1

        def message(message_id):
            return sdk.InboundMessage(
                id=message_id,
                create_time=1784685600000,
                conversation=sdk.Conversation(
                    chat_id="oc_contract", chat_type="group"
                ),
                sender=sdk.Identity(open_id="ou_contract"),
                content=sdk.TextContent(text=message_id),
                body_text=message_id,
                safe_content_text=message_id,
                raw_content_type="text",
            )

        manager.push("oc_contract", message("om_1"), handler)
        manager.push("oc_contract", message("om_2"), handler)
        await manager.dispose()
        return seen, max_active

    seen, max_active = asyncio.run(exercise())
    assert seen == [("om_1", ("om_1",)), ("om_2", ("om_2",))]
    assert max_active == 1
