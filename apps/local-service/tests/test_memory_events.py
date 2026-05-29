import json

from ceo_agent_service.memory_events import (
    build_reply_sent_memory_payload,
    build_review_correction_memory_payload,
    memory_payload_json,
)
from ceo_agent_service.store import ReplyAttempt, SentReply


def _reply_attempt() -> ReplyAttempt:
    return ReplyAttempt(
        id=42,
        conversation_id="cid-1",
        conversation_title="产品讨论",
        trigger_message_id="msg-1",
        trigger_sender="Mina",
        trigger_text="@Derek Zen 看一下这个方案",
        action="send_reply",
        sensitivity_kind="normal",
        codex_reason="用户需要明确答复",
        draft_reply_text="初稿",
        codex_session_id="session-1",
        codex_transcript_start_line=10,
        codex_transcript_end_line=24,
        audit_summary="已检查上下文",
        final_reply_text="最终回复",
        permission_action="allow",
        permission_reason="low risk",
        send_status="sent",
        send_error="",
        retry_count=0,
        reviewed_at="2026-05-29 10:15:00",
        reviewer_feedback="语气更直接",
        corrected_reply_text="修正回复",
        created_at="2026-05-29 10:00:00",
        updated_at="2026-05-29 10:20:00",
    )


def _sent_reply() -> SentReply:
    return SentReply(
        id=7,
        conversation_id="cid-1",
        trigger_message_id="msg-1",
        reply_text="已发送回复",
        send_result_json='{"ok": true, "message_id": "reply-1"}',
        recall_key="recall-1",
        sent_at="2026-05-29 10:21:00",
    )


def test_build_reply_sent_memory_payload_uses_sent_reply_details():
    payload = build_reply_sent_memory_payload(_reply_attempt(), _sent_reply())

    assert payload["event"] == "reply_sent"
    assert payload["conversation"] == {
        "conversation_id": "cid-1",
        "title": "产品讨论",
    }
    assert payload["trigger"]["message_id"] == "msg-1"
    assert payload["trigger"]["text"] == "@Derek Zen 看一下这个方案"
    assert payload["decision"]["codex_reason"] == "用户需要明确答复"
    assert payload["decision"]["audit_summary"] == "已检查上下文"
    assert payload["result"] == {
        "final_reply_text": "已发送回复",
        "send_status": "sent",
        "sent_at": "2026-05-29 10:21:00",
    }
    assert payload["provenance"]["attempt_id"] == 42
    assert payload["provenance"]["sent_reply_id"] == 7
    assert payload["provenance"]["recall_key"] == "recall-1"
    assert payload["provenance"]["send_result_json"] == (
        '{"ok": true, "message_id": "reply-1"}'
    )


def test_build_reply_sent_memory_payload_without_sent_reply_uses_attempt_timestamp():
    payload = build_reply_sent_memory_payload(_reply_attempt())

    assert payload["result"]["final_reply_text"] == "最终回复"
    assert payload["result"]["sent_at"] == "2026-05-29 10:20:00"
    assert "sent_reply_id" not in payload["provenance"]


def test_build_review_correction_memory_payload_includes_original_and_review():
    payload = build_review_correction_memory_payload(_reply_attempt())

    assert payload["event"] == "review_correction"
    assert payload["conversation"]["title"] == "产品讨论"
    assert payload["trigger"]["sender"] == "Mina"
    assert payload["original"] == {
        "action": "send_reply",
        "sensitivity_kind": "normal",
        "codex_reason": "用户需要明确答复",
        "draft_reply_text": "初稿",
        "final_reply_text": "最终回复",
        "send_status": "sent",
    }
    assert payload["review"] == {
        "reviewer_feedback": "语气更直接",
        "corrected_reply_text": "修正回复",
        "reviewed_at": "2026-05-29 10:15:00",
    }
    assert payload["provenance"] == {
        "attempt_id": 42,
        "codex_session_id": "session-1",
        "codex_transcript_start_line": 10,
        "codex_transcript_end_line": 24,
    }


def test_memory_payload_json_sorts_keys_and_preserves_chinese_text():
    encoded = memory_payload_json({"z": "后", "a": "中文"})

    assert encoded == '{"a": "中文", "z": "后"}'
    assert json.loads(encoded) == {"a": "中文", "z": "后"}
