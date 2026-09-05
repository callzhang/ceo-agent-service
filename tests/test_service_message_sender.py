from __future__ import annotations

from dataclasses import replace

import pytest

from app.outbound_postfix import PreparedOutboundMessage
from app.dws_client import DwsClient
from app.service_message_sender import ServiceMessageSender
from app.store import AutoReplyStore


class RecordingDingTalkAdapter:
    def __init__(self) -> None:
        self.calls: list[tuple[object, ...]] = []
        self.ding_calls: list[tuple[str | None, str]] = []

    def send_message(
        self,
        conversation_id: str | None,
        text: str,
        *,
        at_users: list[str] | None = None,
        at_open_dingtalk_ids: list[str] | None = None,
        at_open_dingtalk_names: list[str] | None = None,
        user_id: str | None = None,
        open_dingtalk_id: str | None = None,
        title: str | None = None,
        idempotency_uuid: str | None = None,
    ) -> dict[str, str]:
        self.calls.append(
            (
                conversation_id,
                text,
                at_users,
                at_open_dingtalk_ids,
                at_open_dingtalk_names,
                user_id,
                open_dingtalk_id,
                title,
                idempotency_uuid,
            )
        )
        return {"message_id": "dingtalk-message-1"}

    def ding_user(self, user_id: str, text: str) -> None:
        self.ding_calls.append((user_id, text))

    def ding_self(self, text: str) -> None:
        self.ding_calls.append((None, text))


class RecordingWechatRunner:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, str | None, str | None]] = []

    def send(
        self,
        target_label: str,
        reply_text: str,
        *,
        search_query: str | None = None,
        expected_recent_text: str | None = None,
    ) -> dict[str, bool]:
        self.calls.append(
            (target_label, reply_text, search_query, expected_recent_text)
        )
        return {"sent": True}


class RecordingNativeReplyAdapter:
    def __init__(self) -> None:
        self.calls: list[tuple[object, ...]] = []

    def send_reply_to_trigger(self, conversation, trigger, text):
        self.calls.append((conversation, trigger, text))
        return {"message_id": "native-reply-1"}

    @staticmethod
    def verify_message_send_result(result):
        return {"state": "sent"}


class UnverifiedNativeReplyAdapter(RecordingNativeReplyAdapter):
    def __init__(self, result, verification) -> None:
        super().__init__()
        self.result = result
        self.verification = verification

    def send_reply_to_trigger(self, conversation, trigger, text):
        self.calls.append((conversation, trigger, text))
        return self.result

    def verify_message_send_result(self, result):
        assert result == self.result
        return self.verification


class NativeProcessQueryDwsClient(DwsClient):
    def __init__(self) -> None:
        super().__init__(dws_bin="dws")
        self.calls = []

    def send_reply_to_trigger(self, conversation, trigger, text, at_users=None):
        self.calls.append((conversation, trigger, text))
        return {"result": {"processQueryKey": "native-reply-1"}}


def test_dingtalk_facade_dispatches_only_the_prepared_final_body(
    tmp_path, monkeypatch,
) -> None:
    monkeypatch.setenv(
        "CEO_FEEDBACK_SPIKE_VERCEL_BASE_URL", "https://feedback.example.test",
    )
    store = AutoReplyStore(tmp_path / "service-message-sender.sqlite3")
    dingtalk = RecordingDingTalkAdapter()
    sender = ServiceMessageSender(store=store, dingtalk=dingtalk)

    receipt = sender.send_dingtalk(
        delivery_key="follow-up:revision-1",
        body="请确认",
        original_text="原消息",
        conversation_id="cid-1",
        at_users=["user-1"],
        at_open_dingtalk_ids=["at-open-1"],
        at_open_dingtalk_names=["Alex"],
        user_id="user-2",
        open_dingtalk_id="open-1",
        title="Direct message",
        idempotency_uuid="idempotency-1",
    )

    assert dingtalk.calls == [
        (
            "cid-1",
            receipt.message.final_body,
            ["user-1"],
            ["at-open-1"],
            ["Alex"],
            "user-2",
            "open-1",
            "Direct message",
            "idempotency-1",
        )
    ]
    assert receipt.message.final_body.count("/api/dingtalk-feedback-spike") == 2
    assert receipt.provider_result == {"message_id": "dingtalk-message-1"}


@pytest.mark.parametrize("user_id", [None, "user-1"])
def test_dingtalk_ding_facade_dispatches_only_the_prepared_final_body(
    tmp_path, monkeypatch, user_id,
) -> None:
    monkeypatch.setenv(
        "CEO_FEEDBACK_SPIKE_VERCEL_BASE_URL", "https://feedback.example.test",
    )
    store = AutoReplyStore(tmp_path / "service-message-sender.sqlite3")
    dingtalk = RecordingDingTalkAdapter()
    sender = ServiceMessageSender(store=store, dingtalk=dingtalk)

    message = sender.prepare(
        channel="dingtalk",
        delivery_key=f"diagnostic:{user_id or 'self'}",
        body="诊断消息",
    )
    receipt = sender.send_dingtalk_ding_prepared(message, user_id=user_id)

    assert dingtalk.ding_calls == [(user_id, receipt.message.final_body)]
    assert receipt.message.final_body.count("/api/dingtalk-feedback-spike") == 2


def test_wechat_facade_reuses_the_prepared_final_body_for_a_retry(tmp_path) -> None:
    store = AutoReplyStore(tmp_path / "service-message-sender.sqlite3")
    wechat = RecordingWechatRunner()
    sender = ServiceMessageSender(store=store, wechat=wechat)

    first = sender.send_wechat(
        delivery_key="delivery:7",
        body="你好",
        original_text="原文",
        target_label="Alex",
        search_query="alex wxid",
        expected_recent_text="原文",
    )
    retry = sender.send_wechat(
        delivery_key="delivery:7",
        body="变化的正文",
        original_text="原文",
        target_label="Alex",
        search_query="alex wxid",
        expected_recent_text="原文",
    )

    assert retry.message == first.message
    assert wechat.calls == [
        ("Alex", first.message.final_body, "alex wxid", "原文"),
        ("Alex", first.message.final_body, "alex wxid", "原文"),
    ]


def test_dingtalk_prepared_dispatch_rejects_unpersisted_and_tampered_messages(
    tmp_path,
) -> None:
    store = AutoReplyStore(tmp_path / "service-message-sender.sqlite3")
    dingtalk = RecordingDingTalkAdapter()
    sender = ServiceMessageSender(store=store, dingtalk=dingtalk)
    forged = PreparedOutboundMessage(
        channel="dingtalk",
        delivery_key="forged:1",
        final_body="绕过服务后缀的正文",
        feedback_token="",
        postfix_version="1",
    )
    persisted = sender.prepare(
        channel="dingtalk",
        delivery_key="persisted:1",
        body="已准备的正文",
    )

    with pytest.raises(ValueError, match="persisted prepared message"):
        sender.send_dingtalk_prepared(forged, conversation_id="cid-1")
    with pytest.raises(ValueError, match="persisted prepared message"):
        sender.send_dingtalk_prepared(
            replace(persisted, final_body="被篡改的正文"),
            conversation_id="cid-1",
        )

    assert dingtalk.calls == []


def test_native_reply_facade_reuses_durable_receipt_after_partial_delivery(
    tmp_path,
) -> None:
    store = AutoReplyStore(tmp_path / "service-message-sender.sqlite3")
    adapter = RecordingNativeReplyAdapter()
    sender = ServiceMessageSender(store=store, dingtalk=adapter)
    message = sender.prepare(
        channel="dingtalk",
        delivery_key="okr-review:1:chunk:1",
        body="审核结果",
        original_text="请审核",
    )

    first = sender.send_dingtalk_reply_to_trigger_prepared(
        message,
        conversation="cid-1",
        trigger="msg-1",
    )
    replay = sender.send_dingtalk_reply_to_trigger_prepared(
        message,
        conversation="cid-1",
        trigger="msg-1",
    )

    assert first.provider_result == replay.provider_result == {"message_id": "native-reply-1"}
    assert adapter.calls == [("cid-1", "msg-1", message.final_body)]


def test_native_reply_facade_confirms_actual_dws_process_query_key_shape(tmp_path) -> None:
    store = AutoReplyStore(tmp_path / "service-message-sender.sqlite3")
    adapter = NativeProcessQueryDwsClient()
    sender = ServiceMessageSender(store=store, dingtalk=adapter)
    message = sender.prepare(
        channel="dingtalk",
        delivery_key="okr-review:1:chunk:1",
        body="审核结果",
        original_text="请审核",
    )

    receipt = sender.send_dingtalk_reply_to_trigger_prepared(
        message,
        conversation="cid-1",
        trigger="msg-1",
    )

    assert receipt.provider_result == {"result": {"processQueryKey": "native-reply-1"}}
    assert adapter.calls == [("cid-1", "msg-1", message.final_body)]
    assert store.get_outbound_postfix_receipt("dingtalk", message.delivery_key) == receipt.provider_result


@pytest.mark.parametrize(
    ("result", "verification", "message"),
    [
        ({"success": False}, {"state": "failed"}, "failed"),
        ({"success": True, "result": {}}, {"state": "ambiguous"}, "ambiguous"),
    ],
)
def test_native_reply_facade_does_not_cache_failed_or_ambiguous_provider_results(
    tmp_path,
    result,
    verification,
    message,
) -> None:
    store = AutoReplyStore(tmp_path / "service-message-sender.sqlite3")
    adapter = UnverifiedNativeReplyAdapter(result, verification)
    sender = ServiceMessageSender(store=store, dingtalk=adapter)
    prepared = sender.prepare(
        channel="dingtalk",
        delivery_key="okr-review:1:chunk:1",
        body="审核结果",
        original_text="请审核",
    )

    with pytest.raises(RuntimeError, match=message):
        sender.send_dingtalk_reply_to_trigger_prepared(
            prepared,
            conversation="cid-1",
            trigger="msg-1",
        )

    assert store.get_outbound_postfix_receipt("dingtalk", prepared.delivery_key) is None


def test_wechat_prepared_dispatch_rejects_unpersisted_and_tampered_messages(
    tmp_path,
) -> None:
    store = AutoReplyStore(tmp_path / "service-message-sender.sqlite3")
    wechat = RecordingWechatRunner()
    sender = ServiceMessageSender(store=store, wechat=wechat)
    forged = PreparedOutboundMessage(
        channel="wechat",
        delivery_key="forged:1",
        final_body="绕过服务后缀的正文",
        feedback_token="",
        postfix_version="1",
    )
    persisted = sender.prepare(
        channel="wechat",
        delivery_key="persisted:1",
        body="已准备的正文",
    )

    with pytest.raises(ValueError, match="persisted prepared message"):
        sender.send_wechat_prepared(forged, target_label="Alex")
    with pytest.raises(ValueError, match="persisted prepared message"):
        sender.send_wechat_prepared(
            replace(persisted, feedback_token="forged-token"),
            target_label="Alex",
        )

    assert wechat.calls == []
