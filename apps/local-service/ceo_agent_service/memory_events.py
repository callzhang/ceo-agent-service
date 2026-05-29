import json

from ceo_agent_service.store import ReplyAttempt, SentReply


def _conversation_payload(attempt: ReplyAttempt) -> dict:
    return {
        "conversation_id": attempt.conversation_id,
        "title": attempt.conversation_title,
    }


def _trigger_payload(attempt: ReplyAttempt) -> dict:
    return {
        "message_id": attempt.trigger_message_id,
        "sender": attempt.trigger_sender,
        "text": attempt.trigger_text,
    }


def _attempt_provenance_payload(attempt: ReplyAttempt) -> dict:
    return {
        "attempt_id": attempt.id,
        "codex_session_id": attempt.codex_session_id,
        "codex_transcript_start_line": attempt.codex_transcript_start_line,
        "codex_transcript_end_line": attempt.codex_transcript_end_line,
    }


def build_reply_sent_memory_payload(
    attempt: ReplyAttempt,
    sent_reply: SentReply | None = None,
) -> dict:
    final_reply_text = attempt.final_reply_text
    sent_at = attempt.updated_at
    provenance = _attempt_provenance_payload(attempt)

    if sent_reply is not None:
        final_reply_text = sent_reply.reply_text
        sent_at = sent_reply.sent_at
        provenance.update(
            {
                "sent_reply_id": sent_reply.id,
                "recall_key": sent_reply.recall_key,
                "send_result_json": sent_reply.send_result_json,
            }
        )

    return {
        "event": "reply_sent",
        "conversation": _conversation_payload(attempt),
        "trigger": _trigger_payload(attempt),
        "decision": {
            "action": attempt.action,
            "sensitivity_kind": attempt.sensitivity_kind,
            "codex_reason": attempt.codex_reason,
            "audit_summary": attempt.audit_summary,
        },
        "result": {
            "final_reply_text": final_reply_text,
            "send_status": attempt.send_status,
            "sent_at": sent_at,
        },
        "provenance": provenance,
    }


def build_review_correction_memory_payload(attempt: ReplyAttempt) -> dict:
    return {
        "event": "review_correction",
        "conversation": _conversation_payload(attempt),
        "trigger": _trigger_payload(attempt),
        "original": {
            "action": attempt.action,
            "sensitivity_kind": attempt.sensitivity_kind,
            "codex_reason": attempt.codex_reason,
            "draft_reply_text": attempt.draft_reply_text,
            "final_reply_text": attempt.final_reply_text,
            "send_status": attempt.send_status,
        },
        "review": {
            "reviewer_feedback": attempt.reviewer_feedback,
            "corrected_reply_text": attempt.corrected_reply_text,
            "reviewed_at": attempt.reviewed_at,
        },
        "provenance": _attempt_provenance_payload(attempt),
    }


def memory_payload_json(payload: dict) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True)
