import json
from typing import Any

from ceo_agent_service.store import AutoReplyStore, ReplyAttempt, SentReply


MEMORY_TEXT_LIMIT = 1200
TRUNCATED_SUFFIX = "[truncated]"
SEND_RESULT_VALUE_LIMIT = 160
SEND_RESULT_FIELD_LIMIT = 12
SEND_RESULT_METADATA_KEYS = {
    "code",
    "errcode",
    "errmsg",
    "message_id",
    "messageId",
    "ok",
    "open_message_id",
    "processQueryKey",
    "request_id",
    "requestId",
    "status",
    "task_id",
    "taskId",
}


def _memory_text(text: str) -> str:
    if len(text) <= MEMORY_TEXT_LIMIT:
        return text
    return f"{text[:MEMORY_TEXT_LIMIT]}{TRUNCATED_SUFFIX}"


def _conversation_payload(attempt: ReplyAttempt, single_chat: bool | None) -> dict:
    return {
        "conversation_id": attempt.conversation_id,
        "title": _memory_text(attempt.conversation_title),
        "single_chat": single_chat,
    }


def _trigger_payload(attempt: ReplyAttempt, created_at: str | None) -> dict:
    return {
        "message_id": attempt.trigger_message_id,
        "sender": _memory_text(attempt.trigger_sender),
        "text": _memory_text(attempt.trigger_text),
        "created_at": created_at,
    }


def _attempt_provenance_payload(attempt: ReplyAttempt) -> dict:
    return {
        "attempt_id": attempt.id,
        "codex_session_id": attempt.codex_session_id,
        "codex_transcript_start_line": attempt.codex_transcript_start_line,
        "codex_transcript_end_line": attempt.codex_transcript_end_line,
    }


def _compact_send_result_metadata(send_result_json: str) -> dict:
    if not send_result_json.strip():
        return {}
    try:
        parsed = json.loads(send_result_json)
    except json.JSONDecodeError:
        return {"parse_status": "invalid_json"}
    if not isinstance(parsed, dict):
        return {}

    metadata: dict[str, Any] = {}

    def collect(value: Any) -> None:
        if len(metadata) >= SEND_RESULT_FIELD_LIMIT:
            return
        if not isinstance(value, dict):
            return
        for key, child in value.items():
            if len(metadata) >= SEND_RESULT_FIELD_LIMIT:
                return
            if key in SEND_RESULT_METADATA_KEYS and isinstance(
                child, str | int | float | bool
            ):
                metadata[key] = (
                    _memory_text(str(child))[:SEND_RESULT_VALUE_LIMIT]
                    if isinstance(child, str)
                    else child
                )
            elif isinstance(child, dict):
                collect(child)

    collect(parsed)
    return metadata


def build_reply_sent_memory_payload(
    attempt: ReplyAttempt,
    sent_reply: SentReply | None = None,
    *,
    single_chat: bool | None = None,
    trigger_created_at: str | None = None,
) -> dict:
    effective_single_chat = (
        attempt.conversation_single_chat if single_chat is None else single_chat
    )
    effective_trigger_created_at = (
        trigger_created_at
        if trigger_created_at is not None
        else attempt.trigger_create_time or None
    )
    final_reply_text = _memory_text(attempt.final_reply_text)
    sent_at = attempt.updated_at
    provenance = _attempt_provenance_payload(attempt)

    if sent_reply is not None:
        final_reply_text = _memory_text(sent_reply.reply_text)
        sent_at = sent_reply.sent_at
        provenance.update(
            {
                "sent_reply_id": sent_reply.id,
                "recall_key": sent_reply.recall_key,
                "send_result_available": bool(sent_reply.send_result_json),
            }
        )
        send_result_metadata = _compact_send_result_metadata(sent_reply.send_result_json)
        if send_result_metadata:
            provenance["send_result"] = send_result_metadata

    return {
        "event": "reply_sent",
        "conversation": _conversation_payload(attempt, effective_single_chat),
        "trigger": _trigger_payload(attempt, effective_trigger_created_at),
        "decision": {
            "action": attempt.action,
            "sensitivity_kind": attempt.sensitivity_kind,
            "codex_reason": _memory_text(attempt.codex_reason),
            "audit_summary": _memory_text(attempt.audit_summary),
        },
        "result": {
            "final_reply_text": final_reply_text,
            "send_status": attempt.send_status,
            "sent_at": sent_at,
        },
        "provenance": provenance,
    }


def build_review_correction_memory_payload(
    attempt: ReplyAttempt,
    *,
    single_chat: bool | None = None,
    trigger_created_at: str | None = None,
) -> dict:
    effective_single_chat = (
        attempt.conversation_single_chat if single_chat is None else single_chat
    )
    effective_trigger_created_at = (
        trigger_created_at
        if trigger_created_at is not None
        else attempt.trigger_create_time or None
    )
    return {
        "event": "review_correction",
        "conversation": _conversation_payload(attempt, effective_single_chat),
        "trigger": _trigger_payload(attempt, effective_trigger_created_at),
        "original": {
            "action": attempt.action,
            "sensitivity_kind": attempt.sensitivity_kind,
            "codex_reason": _memory_text(attempt.codex_reason),
            "draft_reply_text": _memory_text(attempt.draft_reply_text),
            "final_reply_text": _memory_text(attempt.final_reply_text),
            "send_status": attempt.send_status,
        },
        "review": {
            "reviewer_feedback": _memory_text(attempt.reviewer_feedback),
            "corrected_reply_text": _memory_text(attempt.corrected_reply_text),
            "reviewed_at": attempt.reviewed_at,
        },
        "provenance": _attempt_provenance_payload(attempt),
    }


def enqueue_review_correction_memory_event(
    store: AutoReplyStore, attempt_id: int
) -> bool:
    attempt = store.get_reply_attempt(attempt_id)
    if attempt is None:
        return False
    if not attempt.reviewer_feedback.strip() and not attempt.corrected_reply_text.strip():
        return False
    store.enqueue_memory_write_event(
        attempt_id,
        "review_correction",
        memory_payload_json(build_review_correction_memory_payload(attempt)),
    )
    return True


def memory_payload_json(payload: dict) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True)
