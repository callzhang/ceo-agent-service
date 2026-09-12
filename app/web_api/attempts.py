"""Structured Attempt detail payloads for the React console."""

import json
from types import SimpleNamespace
from typing import Any
from urllib.parse import quote

from app.web_api.common import json_safe, normalize_display_value


def _stored_json(value: str, fallback: Any) -> Any:
    try:
        return json.loads(value or "")
    except (TypeError, json.JSONDecodeError):
        return fallback


def _permission_display(attempt: Any) -> str:
    action = str(getattr(attempt, "permission_action", "") or "").strip()
    reason = str(getattr(attempt, "permission_reason", "") or "").strip()
    if action and reason:
        return f"{action} · {reason}"
    return action or reason


def _status_message(attempt: Any, attention: Any) -> tuple[str, bool]:
    status = str(getattr(attempt, "send_status", "") or "").strip().lower()
    if status == "sent":
        return "这条回复已发送，无需你操作。", False
    if status == "completed":
        return "这条事项已完成，无需你操作。", False
    if status == "skipped":
        return "这条事项已判定无需回复，无需你操作。", False
    if status == "needs_human":
        return "这条事项等待你的决策。请阅读已核验事实后提交处理指令。", True
    if status == "failed":
        return "这次处理没有完成，可重新处理当前事项。", False
    if attention is not None:
        return "系统正在处理这条事项。", False
    return f"当前状态：{status or '未提供'}。", False


def _run_role(run: Any) -> str:
    role = getattr(run, "role", None)
    return str(getattr(role, "value", role) or "")


_AGENT_ROLE_LABELS = {"consumer": "处理过程", "audit": "审计过程"}

_CONVERSATION_LABELS = {
    "email": "邮件",
    "wechat": "微信会话",
    "dingtalk": "群名",
}


def _conversation_label(attempt: Any) -> str:
    channel = str(getattr(attempt, "channel", "") or "").strip()
    return _CONVERSATION_LABELS.get(channel, "会话")


def _transcript_owner(run: Any) -> Any:
    """Address an agent run's transcript slice the way an Attempt row is read.

    An agent run records the same three values an Attempt row does - session id
    and the transcript line range it owns - under its own column names.
    """
    return SimpleNamespace(
        codex_session_id=str(getattr(run, "codex_session_id", "") or ""),
        codex_transcript_start_line=int(getattr(run, "transcript_start_line", 0) or 0),
        codex_transcript_end_line=int(getattr(run, "transcript_end_line", 0) or 0),
        audit_tool_events_json="",
    )


def _agent_sessions(attempt: Any, agent_runs: list[Any]) -> list[dict[str, Any]]:
    """Return each role's readable transcript, with the calls it made.

    The Attempt row stores a single session id, which is the last role that
    ran. Linking only that one hides the Consumer transcript even though it is
    on disk, so every run with a readable transcript gets its own entry.
    """
    from app.audit_web import _audit_event_uses_for_attempt
    from app.codex_history import find_codex_session_path

    sessions: list[dict[str, Any]] = []
    seen: set[str] = set()
    candidates = [(_run_role(run), _transcript_owner(run)) for run in agent_runs]
    candidates.append(("", attempt))
    for role, owner in candidates:
        session_id = str(getattr(owner, "codex_session_id", "") or "").strip()
        if not session_id or session_id in seen:
            continue
        if find_codex_session_path(session_id) is None:
            continue
        seen.add(session_id)
        sessions.append(
            {
                "role": role,
                "label": _AGENT_ROLE_LABELS.get(role, "Agent session"),
                "session_id": session_id,
                "url": f"/codex/{quote(session_id, safe='')}",
                "tool_uses": json_safe(_audit_event_uses_for_attempt(owner)),
            }
        )
    return sessions


def _runtime_payload(agent_runs: list[Any], store: Any) -> list[dict[str, Any]]:
    result = []
    for run in agent_runs:
        role = _run_role(run)
        for item in store.list_agent_runtime_attempts(run.id):
            session_id = str(getattr(item, "session_id", "") or "").strip()
            result.append(
                {
                    "role": normalize_display_value(role),
                    "session_url": (
                        f"/codex/{quote(session_id, safe='')}" if session_id else ""
                    ),
                    "proposal_revision": int(getattr(run, "proposal_revision", 0) or 0),
                    "turn_attempt": int(getattr(run, "turn_attempt", 0) or 0),
                    "route": normalize_display_value(getattr(item, "route_name", "")),
                    "runtime": normalize_display_value(getattr(item, "runtime_kind", "")),
                    "credential_mode": normalize_display_value(getattr(item, "credential_mode", "")),
                    "model": normalize_display_value(getattr(item, "model", "")),
                    "session_available": bool(session_id),
                    "status": normalize_display_value(getattr(item, "status", "")),
                    "failure_code": normalize_display_value(getattr(item, "failure_code", "")),
                    "failover_permitted": bool(getattr(item, "failover_permitted", False)),
                    "transcript_start": int(getattr(item, "transcript_start", 0) or 0),
                    "transcript_end": int(getattr(item, "transcript_end", 0) or 0),
                    "effect_started_at": normalize_display_value(
                        getattr(item, "first_effect_started_at", "")
                    ),
                }
            )
    return result


def _references_payload(attempt: Any) -> list[dict[str, str]]:
    """Return human-readable materials, never raw tool calls, for an Attempt."""
    from app.audit_web import _audit_document_uses_for_attempt

    references = []
    for document in _audit_document_uses_for_attempt(attempt):
        title = normalize_display_value(document.get("title"))
        source = normalize_display_value(document.get("source"))
        relevance = normalize_display_value(document.get("relevance"))
        if title or source or relevance:
            references.append({"title": title, "source": source, "relevance": relevance})
    return references


def _feedback_payload(events: list[Any]) -> list[dict[str, str]]:
    from app.audit_web import _feedback_rating_stars_for_rating

    payload = []
    for event in events:
        rating = str(getattr(event, "rating", "") or "")
        rating_count = len(_feedback_rating_stars_for_rating(rating))
        payload.append(
            {
                "rating": normalize_display_value(rating),
                "rating_label": normalize_display_value(getattr(event, "rating_label", "") or rating),
                "rating_stars": f"{'★' * rating_count}{'☆' * (5 - rating_count)} · {rating_count}/5" if rating_count else "",
                "comment": normalize_display_value(getattr(event, "comment", "")),
                "source": normalize_display_value(getattr(event, "source", "")),
                "received_at": normalize_display_value(getattr(event, "received_at", "") or getattr(event, "updated_at", "")),
            }
        )
    return payload


def _execution_url(
    attempt: Any, agent_sessions: list[dict[str, Any]], role: str
) -> str:
    if not any(session["role"] == role for session in agent_sessions):
        return ""
    return f"/attempts/{int(attempt.id)}/execution/{role}"


def _action_links(
    attempt: Any,
    agent_sessions: list[dict[str, Any]],
    reply_task: Any,
    sent_reply: Any,
    wechat_delivery: Any,
) -> dict[str, Any]:
    from app.audit_web import _sent_reply_has_recall_target

    status = str(getattr(attempt, "send_status", "") or "").strip().lower()
    terminal = status in {"sent", "skipped", "completed", "commented", "calendar", "document", "reacted"}
    service_task = False
    if reply_task is not None:
        trigger = _stored_json(getattr(reply_task, "trigger_message_json", ""), {})
        raw = trigger.get("raw_payload") if isinstance(trigger, dict) else None
        service_task = isinstance(raw, dict) and (
            bool(raw.get("service_task")) or str(raw.get("source") or "").strip() == "oa_pending_scan"
        )
    dingtalk_url = ""
    if service_task and str(getattr(attempt, "oa_url", "") or "").strip():
        dingtalk_url = str(attempt.oa_url).strip()
    elif not service_task and str(getattr(attempt, "channel", "") or "") == "dingtalk":
        # Only a DingTalk conversation id can open a DingTalk conversation. An
        # email or WeChat attempt carries its own channel identity, and sending
        # that to the popup produces a link that cannot resolve.
        conversation_id = str(getattr(attempt, "conversation_id", "") or "").strip()
        if conversation_id:
            dingtalk_url = f"/open-dingtalk-popup?conversation_id={quote(conversation_id, safe='')}"
    delivery_action_label = ""
    delivery_action_url = ""
    if wechat_delivery is not None:
        delivery_status = str(getattr(wechat_delivery, "status", "") or "").strip()
        delivery_id = int(getattr(wechat_delivery, "id", 0) or 0)
        if delivery_status == "ready_to_send" and delivery_id:
            delivery_action_label = "发送"
            delivery_action_url = f"/api/console/wechat/deliveries/{delivery_id}/approve"
        elif (
            delivery_status == "skipped"
            and str(getattr(wechat_delivery, "action_started_at", "") or "").strip()
            and delivery_id
        ):
            delivery_action_label = "重试发送"
            delivery_action_url = f"/api/console/wechat/deliveries/{delivery_id}/retry"
    terminal = terminal and not delivery_action_url
    return {
        "can_rerun": status == "failed",
        "can_recall": _sent_reply_has_recall_target(sent_reply),
        "can_submit_feedback": True,
        "rerun_url": f"/api/console/history/{int(attempt.id)}/rerun",
        "recall_url": f"/api/console/history/{int(attempt.id)}/recall",
        "feedback_url": f"/api/console/history/{int(attempt.id)}/feedback",
        "consumer_url": _execution_url(attempt, agent_sessions, "consumer"),
        "audit_url": _execution_url(attempt, agent_sessions, "audit"),
        "agent_url": next(
            (
                str(session["url"])
                for session in agent_sessions
                if session["session_id"] == str(getattr(attempt, "codex_session_id", "") or "").strip()
            ),
            "",
        ),
        "dingtalk_url": dingtalk_url,
        "wechat_open_url": (
            f"/api/console/history/{int(attempt.id)}/open-wechat-message"
            if str(getattr(attempt, "channel", "") or "") == "wechat" and reply_task is not None
            else ""
        ),
        "delivery_action_label": delivery_action_label,
        "delivery_action_url": delivery_action_url,
        "terminal": terminal,
        "action_label": "无需操作" if terminal else "需要处理",
    }


def build_attempt_detail(store: Any, attempt_id: int) -> tuple[int, dict[str, Any] | None]:
    """Build a rich, JSON-safe Attempt DTO without rendering HTML."""
    attempt = store.get_reply_attempt(attempt_id)
    if attempt is None:
        return 404, None

    from app.audit_web import (
        _agent_failure_reason_text,
        _attempt_action_label_text,
        _attempt_detail_reply_text,
        _attempt_info_tooltip,
        _audit_tool_uses_for_attempt,
        _feedback_token_for_sent_reply,
        _attempt_reason_text,
        _needs_human_decision_options,
        _quality_warnings,
        _route_failure_recovery_state,
        reply_history_attention,
    )

    sent_reply = store.get_sent_reply(attempt.conversation_id, attempt.trigger_message_id)
    agent_runs: list[Any] = []
    if attempt.agent_run_id:
        terminal_run = store.get_agent_run(attempt.agent_run_id)
        if terminal_run is not None:
            agent_runs = store.list_agent_runs_for_task_generation(
                terminal_run.reply_task_id, terminal_run.execution_generation
            )
    reply_task = store.get_reply_task_for_message(
        attempt.conversation_id, attempt.trigger_message_id, channel=attempt.channel
    )
    wechat_delivery = (
        store.get_wechat_delivery_for_task(reply_task.id)
        if reply_task is not None and str(attempt.channel or "") == "wechat"
        else None
    )
    attention = reply_history_attention(
        attempt,
        task=reply_task,
        decision_options=_needs_human_decision_options(attempt, agent_runs),
    )
    runtime_attempts = _runtime_payload(agent_runs, store)
    agent_sessions = _agent_sessions(attempt, agent_runs)
    feedback_token = _feedback_token_for_sent_reply(sent_reply)
    feedback_events = store.list_feedback_events_for_tokens([feedback_token]).get(feedback_token, [])
    status_message, requires_decision = _status_message(attempt, attention)
    if wechat_delivery is not None:
        delivery_status = str(getattr(wechat_delivery, "status", "") or "").strip()
        delivery_started = str(
            getattr(wechat_delivery, "action_started_at", "") or ""
        ).strip()
        if delivery_status == "ready_to_send":
            status_message = "这条微信回复已准备好，确认后即可发送。"
        elif delivery_status == "skipped" and delivery_started:
            status_message = "这条微信回复此前未能打开会话，尚未发送；你可以重试。"
    decision_options = []
    if attempt.send_status == "needs_human":
        for option in _needs_human_decision_options(attempt, agent_runs):
            decision_options.append(
                {
                    "label": normalize_display_value(getattr(option, "label", "")),
                    "instruction": normalize_display_value(getattr(option, "instruction", "")),
                    "consequence": normalize_display_value(getattr(option, "consequence", "")),
                    "url": f"/api/console/history/{int(attempt.id)}/human-decision",
                }
            )
    try:
        audit_explanation = _attempt_reason_text(attempt)
    except RuntimeError:
        audit_explanation = attempt.codex_reason or attempt.send_error
    action_pills = [{"label": _attempt_action_label_text(attempt), "status": attempt.send_status}]
    if attempt.oa_action.strip():
        action_pills.append({"label": f"🧾 {attempt.oa_action.strip()}", "status": attempt.oa_action})
    if attempt.calendar_response_status.strip():
        action_pills.append({"label": f"📆 {attempt.calendar_response_status.strip()}", "status": attempt.calendar_response_status})
    if _route_failure_recovery_state(attempt, reply_task):
        action_pills.append({"label": "↻ Recovery", "status": _route_failure_recovery_state(attempt, reply_task)})
    # The calls a run made are the only readable account of what it did. They
    # used to be dropped whenever agent runs existed, on the assumption that a
    # per-role page showed them instead; no such page ever rendered them, so
    # the process was missing from every agent Attempt. When the runs do have
    # readable transcripts their calls are carried per role by agent_sessions,
    # addressed by the run that made them; this field then holds nothing, and
    # it stays the only source for an Attempt whose transcript is gone.
    tool_uses = (
        [] if agent_sessions else json_safe(_audit_tool_uses_for_attempt(attempt))
    )
    return 200, {
        "id": attempt.id,
        "title": normalize_display_value(attempt.conversation_title),
        "type": normalize_display_value(attempt.action),
        "conversation": {
            "label": _conversation_label(attempt),
            "title": normalize_display_value(attempt.conversation_title),
            "trigger_sender": normalize_display_value(attempt.trigger_sender),
        },
        "status": {
            "raw": normalize_display_value(attempt.send_status),
            "subject": normalize_display_value(next((line.strip() for line in attempt.trigger_text.splitlines() if line.strip()), "未记录事项摘要")[:180]),
            "message": status_message,
            "requires_decision": requires_decision,
            "attention": {
                "kind": normalize_display_value(getattr(attention, "kind", "")) if attention else "",
                "reason": normalize_display_value(getattr(attention, "reason", "")) if attention else "",
                "external_effect": normalize_display_value(getattr(attention, "external_effect", "")) if attention else "",
                "retry_at": normalize_display_value(getattr(attention, "retry_at", "")) if attention else "",
            },
        },
        "metadata": [
            {"label": "trigger message id", "value": normalize_display_value(attempt.trigger_message_id)},
            {"label": "action", "value": normalize_display_value(attempt.action)},
            {"label": "sensitivity", "value": normalize_display_value(attempt.sensitivity_kind)},
            {"label": "permission", "value": normalize_display_value(_permission_display(attempt))},
            {"label": "send status", "value": normalize_display_value(attempt.send_status)},
            {"label": "send error", "value": normalize_display_value(attempt.send_error)},
            {"label": "retry count", "value": str(attempt.retry_count)},
            {"label": "created", "value": normalize_display_value(attempt.created_at)},
            {"label": "updated", "value": normalize_display_value(attempt.updated_at)},
            {"label": "reviewed", "value": normalize_display_value(attempt.reviewed_at)},
        ],
        "trigger": {
            "title": "Trigger",
            "text": normalize_display_value(f"{attempt.trigger_sender}: {attempt.trigger_text}"),
        },
        "audit_explanation": {"title": "审计说明", "text": normalize_display_value(audit_explanation)},
        "generated_reply": {"title": "生成回复", "text": normalize_display_value(_attempt_detail_reply_text(attempt, sent_reply))},
        "references": _references_payload(attempt),
        "feedback": {
            "reviewer_feedback": normalize_display_value(attempt.reviewer_feedback),
            "corrected_reply": normalize_display_value(attempt.corrected_reply_text),
            "feedback_url": f"/api/console/history/{int(attempt.id)}/feedback",
            "events": _feedback_payload(feedback_events),
        },
        "decision_options": decision_options,
        "audit_summary": normalize_display_value(attempt.audit_summary),
        "draft_reply": normalize_display_value(attempt.draft_reply_text),
        "failure_reason": normalize_display_value(_agent_failure_reason_text(attempt, agent_runs)),
        "recovery_state": normalize_display_value(_route_failure_recovery_state(attempt, reply_task)),
        "action_pills": action_pills,
        "quality_warnings": [normalize_display_value(item) for item in _quality_warnings(attempt)],
        "context_only_info": normalize_display_value(_attempt_info_tooltip(attempt)),
        "tool_uses": tool_uses,
        "agent_execution_record": bool(agent_runs or attempt.codex_session_id),
        "revision_count": len({getattr(run, "proposal_revision", "") for run in agent_runs if getattr(run, "role", None) and getattr(run, "proposal_revision", "")}),
        "oa": {
            "process_instance_id": normalize_display_value(attempt.oa_process_instance_id),
            "task_id": normalize_display_value(attempt.oa_task_id),
            "url": normalize_display_value(attempt.oa_url),
            "action": normalize_display_value(attempt.oa_action),
            "remark": normalize_display_value(attempt.oa_remark),
            "result": _stored_json(attempt.oa_action_result_json, {}),
        },
        "calendar": {
            "event_id": normalize_display_value(attempt.calendar_event_id),
            "response_status": normalize_display_value(attempt.calendar_response_status),
            "result": _stored_json(attempt.calendar_response_result_json, {}),
        },
        "actions": _action_links(
            attempt, agent_sessions, reply_task, sent_reply, wechat_delivery
        ),
        "agent_sessions": agent_sessions,
        "runtime_attempts": runtime_attempts,
        "created_at": normalize_display_value(attempt.created_at),
        "updated_at": normalize_display_value(attempt.updated_at),
    }
