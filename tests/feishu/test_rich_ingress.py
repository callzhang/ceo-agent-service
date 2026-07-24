import importlib.metadata
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from app.feishu.ingress import (
    MAX_BODY_BYTES,
    evaluate_ingress,
    normalize_sdk_envelope,
    normalize_sdk_message,
)
from app.feishu.models import (
    FeishuInboundResourceCandidate,
    FeishuReplyScope,
)


NOW = datetime(2026, 7, 22, 3, 20, tzinfo=timezone.utc)


def _sdk_message(
    message_type="text",
    *,
    content=None,
    resources=(),
    body_text="",
    safe_content_text="",
    **updates,
):
    values = {
        "id": "om_rich_1",
        "create_time": int(NOW.timestamp() * 1000),
        "conversation": SimpleNamespace(
            chat_id="oc_rich", chat_type="group", thread_id="omt_1"
        ),
        "sender": SimpleNamespace(
            open_id="ou_rich",
            display_name="Alex",
            is_bot=False,
            sender_type="user",
        ),
        "content": content or SimpleNamespace(kind=message_type),
        "resources": resources,
        "raw_content_type": message_type,
        "body_text": body_text,
        "safe_content_text": safe_content_text,
        "content_text": "UNSAFE CONTENT MUST NOT BE USED",
        "mentioned_bot": True,
        "raw": {
            "header": {"event_id": "evt_rich_1"},
            "secret": "RAW_PAYLOAD_MUST_NOT_BE_COPIED",
        },
    }
    values.update(updates)
    return SimpleNamespace(**values)


def _scope():
    return FeishuReplyScope(
        app_id="app_rich",
        target_type="group",
        target_id="oc_rich",
        display_name="Rich Group",
        trigger_mode="mention_bot",
        enabled=True,
        binding_status="verified",
    )


def _normalize(sdk):
    return normalize_sdk_envelope(
        sdk, app_id="app_rich", now=lambda: NOW
    )


@pytest.mark.parametrize(
    ("message_type", "content", "expected_type", "expected_summary"),
    [
        (
            "image",
            SimpleNamespace(kind="image", image_key="img_key"),
            "image",
            "[图片]",
        ),
        (
            "file",
            SimpleNamespace(
                kind="file",
                file_key="file_key",
                file_name="../../private/\x00report.pdf",
            ),
            "file",
            "[文件: report.pdf]",
        ),
        (
            "audio",
            SimpleNamespace(
                kind="audio", file_key="audio_key", duration_ms=1234
            ),
            "audio",
            "[音频]",
        ),
        (
            "sticker",
            SimpleNamespace(kind="sticker", file_key="sticker_key"),
            "sticker",
            "[表情贴纸]",
        ),
    ],
)
def test_typed_media_content_becomes_bounded_resource_candidate(
    message_type, content, expected_type, expected_summary
):
    envelope = _normalize(
        _sdk_message(
            message_type,
            content=content,
            body_text="<opaque key must not enter body>",
            safe_content_text="&lt;opaque key must not enter body&gt;",
        )
    )

    assert envelope.message.message_type == message_type
    assert envelope.message.body_text == ""
    assert envelope.message.normalized_summary == expected_summary
    assert len(envelope.resources) == 1
    assert envelope.resources[0].resource_type == expected_type
    assert envelope.resources[0].ordinal == 0
    assert "key" not in envelope.message.model_dump_json()


def test_video_content_emits_content_and_cover_candidates_once():
    content = SimpleNamespace(
        kind="media",
        file_key="video_key",
        image_key="cover_key",
        duration_ms=5000,
        file_name="clip.mp4",
    )
    descriptor = SimpleNamespace(
        type="video",
        file_key="video_key",
        cover_image_key="cover_key",
        duration_ms=5000,
        file_name="clip.mp4",
    )

    envelope = _normalize(
        _sdk_message("media", content=content, resources=[descriptor])
    )

    assert [(item.resource_type, item.role, item.ordinal) for item in envelope.resources] == [
        ("video", "content", 0),
        ("image", "cover", 1),
    ]
    assert envelope.resources[0].duration_ms == 5000
    assert envelope.message.normalized_summary == "[视频]"
    assert not envelope.resource_truncated


def test_post_uses_safe_flat_text_and_sdk_resources_without_copying_ast():
    content = SimpleNamespace(
        kind="post",
        text="UNSAFE_TYPED_TEXT",
        post={"secret": "POST_AST_MUST_NOT_BE_COPIED"},
        raw={"secret": "CONTENT_RAW_MUST_NOT_BE_COPIED"},
    )
    resource = SimpleNamespace(
        type="image", file_key="img_post", file_name=None
    )
    envelope = _normalize(
        _sdk_message(
            "post",
            content=content,
            resources=[resource],
            body_text="",
            safe_content_text="Safe rendered post",
        )
    )

    serialized = envelope.model_dump_json()
    assert envelope.message.body_text == "Safe rendered post"
    assert envelope.resources[0].file_key == "img_post"
    assert "POST_AST_MUST_NOT_BE_COPIED" not in serialized
    assert "CONTENT_RAW_MUST_NOT_BE_COPIED" not in serialized
    assert "RAW_PAYLOAD_MUST_NOT_BE_COPIED" not in serialized
    assert "UNSAFE CONTENT MUST NOT BE USED" not in serialized


def test_post_replaces_bound_resource_keys_without_changing_normal_text():
    image_key = "img_secret_opaque"
    video_key = "file_secret_opaque"
    envelope = _normalize(
        _sdk_message(
            "post",
            resources=[
                SimpleNamespace(type="image", file_key=image_key),
                SimpleNamespace(type="video", file_key=video_key),
            ],
            safe_content_text=(
                "季度更新：正常文字和 [官方文档](https://open.larksuite.com) "
                f"![封面]({image_key}) [media:{video_key}]"
            ),
        )
    )

    assert envelope.message.body_text == (
        "季度更新：正常文字和 [官方文档](https://open.larksuite.com) "
        "[图片] [视频]"
    )
    assert image_key not in envelope.message.model_dump_json()
    assert video_key not in envelope.message.model_dump_json()
    assert [item.file_key for item in envelope.resources] == [
        image_key,
        video_key,
    ]


@pytest.mark.parametrize(
    "unsafe_body",
    [
        "正常文字 ![未绑定图片](img_unbound_opaque)",
        "正常文字 [media:file_unbound_opaque]",
    ],
)
def test_post_with_unbound_resource_reference_fails_closed(unsafe_body):
    with pytest.raises(ValueError, match="unbound resource reference"):
        _normalize(
            _sdk_message(
                "post",
                resources=[
                    SimpleNamespace(type="image", file_key="img_known_opaque")
                ],
                safe_content_text=unsafe_body,
            )
        )


def test_post_resource_key_colliding_with_placeholder_fails_closed():
    with pytest.raises(ValueError, match="unsafe resource key"):
        _normalize(
            _sdk_message(
                "post",
                resources=[
                    SimpleNamespace(type="image", file_key="[图片]")
                ],
                safe_content_text="正常文字 ![封面]([图片])",
            )
        )


def test_resource_only_post_uses_summary_for_ingress():
    envelope = _normalize(
        _sdk_message(
            "post",
            resources=[SimpleNamespace(type="image", file_key="img_post")],
        )
    )

    assert envelope.message.body_text == ""
    assert envelope.message.normalized_summary == "[富文本消息: 1 个资源]"
    decision = evaluate_ingress(
        envelope.message, _scope(), stale_event_seconds=300, now=NOW
    )
    assert decision.eligible


def test_text_does_not_fall_back_to_unsafe_content_text_or_typed_text():
    content = SimpleNamespace(kind="text", text="UNSAFE_TYPED_TEXT")
    envelope = _normalize(_sdk_message("text", content=content))

    assert envelope.message.body_text == ""
    decision = evaluate_ingress(
        envelope.message, _scope(), stale_event_seconds=300, now=NOW
    )
    assert decision.reason == "empty_message"


def test_body_is_capped_at_32k_utf8_bytes_with_explicit_flag():
    envelope = _normalize(
        _sdk_message("text", body_text="界" * MAX_BODY_BYTES)
    )

    assert len(envelope.message.body_text.encode("utf-8")) <= MAX_BODY_BYTES
    assert envelope.content_truncated


def test_resources_are_capped_at_eight_with_consecutive_ordinals():
    resources = [
        SimpleNamespace(type="image", file_key=f"img_{index}")
        for index in range(10)
    ]
    envelope = _normalize(
        _sdk_message(
            "post", resources=resources, safe_content_text="ten images"
        )
    )

    assert len(envelope.resources) == 8
    assert [item.ordinal for item in envelope.resources] == list(range(8))
    assert envelope.resource_truncated


def test_overlong_resource_key_and_duration_fail_closed_per_resource():
    resources = [
        SimpleNamespace(type="file", file_key="x" * 513),
        SimpleNamespace(
            type="audio", file_key="audio_ok", duration_ms=86_400_001
        ),
    ]
    envelope = _normalize(
        _sdk_message("post", resources=resources, body_text="resources")
    )

    assert [item.file_key for item in envelope.resources] == ["audio_ok"]
    assert envelope.resources[0].duration_ms is None
    assert envelope.resource_truncated


def test_resource_candidate_sanitizes_name_hides_key_and_is_frozen():
    candidate = FeishuInboundResourceCandidate(
        ordinal=0,
        resource_type="file",
        file_key="file_secret_key",
        file_name="C:\\private\\..\\\x00quarterly\nreport.pdf",
    )

    assert candidate.file_name == "quarterlyreport.pdf"
    assert "file_secret_key" not in repr(candidate)
    with pytest.raises(ValidationError):
        candidate.file_name = "changed.pdf"


def test_root_parent_and_legacy_message_view_are_preserved():
    sdk = _sdk_message(
        "text",
        body_text="hello",
        root_message_id="om_root",
        parent_message_id="om_parent",
        reply_to_message_id="om_parent",
    )
    envelope = _normalize(sdk)
    legacy = normalize_sdk_message(
        sdk, app_id="app_rich", now=lambda: NOW
    )

    assert envelope.message.root_message_id == "om_root"
    assert envelope.message.parent_message_id == "om_parent"
    assert envelope.message.reply_to_message_id == "om_parent"
    assert legacy == envelope.message


def test_pinned_sdk_real_conversation_reply_and_raw_ids_cross_as_scalars_only():
    sdk = pytest.importorskip("lark_channel")
    assert importlib.metadata.version("lark-channel-sdk") == "1.2.0"
    raw_secret = "RAW_CONTENT_MUST_NOT_CROSS_THE_INGRESS_BOUNDARY"
    sdk_message = sdk.InboundMessage(
        id="om_sdk_child",
        create_time=int(NOW.timestamp() * 1000),
        conversation=sdk.Conversation(
            chat_id="oc_rich", chat_type="group", thread_id="omt_sdk"
        ),
        sender=sdk.Identity(
            open_id="ou_rich", display_name="Alex", sender_type="user"
        ),
        reply=sdk.ReplyRef(message_id="om_sdk_parent"),
        content=sdk.TextContent(text="hello"),
        raw={
            "message_id": "om_sdk_child",
            "root_id": "om_sdk_root",
            "parent_id": "om_sdk_parent",
            "content": raw_secret,
            "mentions": [{"private": raw_secret}],
        },
        raw_content_type="text",
        body_text="hello",
        safe_content_text="hello",
        mentioned_bot=True,
    )

    envelope = _normalize(sdk_message)
    serialized = envelope.model_dump_json()

    assert envelope.message.thread_id == "omt_sdk"
    assert envelope.message.root_message_id == "om_sdk_root"
    assert envelope.message.parent_message_id == "om_sdk_parent"
    assert envelope.message.reply_to_message_id == "om_sdk_parent"
    assert envelope.message.body_text == "hello"
    assert raw_secret not in serialized
    assert not hasattr(envelope.message, "raw")


def test_raw_preserves_parent_equal_to_root_when_sdk_reply_is_none():
    sdk = pytest.importorskip("lark_channel")
    sdk_message = sdk.InboundMessage(
        id="om_sdk_root_reply",
        create_time=int(NOW.timestamp() * 1000),
        conversation=sdk.Conversation(
            chat_id="oc_rich", chat_type="group", thread_id="omt_sdk"
        ),
        sender=sdk.Identity(open_id="ou_rich", sender_type="user"),
        reply=None,
        content=sdk.TextContent(text="root reply"),
        raw={
            "root_id": "om_same_root",
            "parent_id": "om_same_root",
            "unapproved": "must not survive",
        },
        raw_content_type="text",
        body_text="root reply",
        safe_content_text="root reply",
        mentioned_bot=True,
    )

    message = _normalize(sdk_message).message

    assert message.root_message_id == "om_same_root"
    assert message.parent_message_id == "om_same_root"
    assert message.reply_to_message_id == "om_same_root"
    assert "must not survive" not in message.model_dump_json()


def test_official_event_envelope_raw_paths_are_allowlisted_and_bounded():
    class NeverStringify:
        def __str__(self):
            raise AssertionError("unapproved raw field was inspected")

    sdk_message = _sdk_message(
        "text",
        body_text="hello",
        raw={
            "header": {"event_id": "evt_nested"},
            "event": {
                "message": {
                    "root_id": "om_nested_root",
                    "parent_id": "om_nested_parent",
                    "body_text": "raw body must not cross",
                    "secret": NeverStringify(),
                },
                "secret": NeverStringify(),
            },
            "secret": NeverStringify(),
        },
    )

    message = _normalize(sdk_message).message

    assert message.event_id == "evt_nested"
    assert message.root_message_id == "om_nested_root"
    assert message.parent_message_id == "om_nested_parent"
    assert message.body_text == "hello"
    assert "raw body must not cross" not in message.model_dump_json()


@pytest.mark.parametrize("field", ["root_id", "parent_id"])
def test_overlong_official_raw_thread_identifier_fails_closed(field):
    with pytest.raises(ValueError, match=field):
        _normalize(
            _sdk_message(
                "text",
                body_text="hello",
                raw={field: "x" * 513},
            )
        )


@pytest.mark.parametrize("message_type", ["system", "hongbao", "unknown", "interactive"])
def test_unapproved_message_kinds_fail_closed(message_type):
    message = _normalize(
        _sdk_message(message_type, body_text="must not be processed")
    ).message

    decision = evaluate_ingress(
        message, _scope(), stale_event_seconds=300, now=NOW
    )
    assert not decision.eligible
    assert decision.reason == "unsupported_media"
    assert not decision.store_body


def test_rich_message_in_unapproved_scope_never_stores_body():
    message = _normalize(
        _sdk_message(
            "file",
            content=SimpleNamespace(
                kind="file", file_key="file_key", file_name="report.pdf"
            ),
        )
    ).message

    decision = evaluate_ingress(
        message, None, stale_event_seconds=300, now=NOW
    )
    assert decision.reason == "scope_pending"
    assert not decision.store_body
