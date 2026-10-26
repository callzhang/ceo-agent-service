"""Structured Attempt detail payloads for the React console."""

import json
from collections.abc import Callable
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any
from urllib.parse import quote

from app.wechat.models import principal_reply_supersession
from app.attempt_rerun_presentation import RerunPresentation, attempt_rerun_presentation
from app.attempt_projection import terminal_oa_source_closure_is_skipped
from app.attempt_what_happened import build_what_happened, deciding_scores, stopped_because
from app.history_actions import can_reopen_skipped_oa_review
from app.web_api.common import json_safe, normalize_display_value
from app.agent_contracts import parse_persisted_consumer_result



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


def resolve_attempt_detail_reference(store: Any, requested_id: int) -> int | None:
    """Resolve a public Attempt ID or a historical Actions physical-ID URL."""
    if store.get_reply_attempt(requested_id) is not None:
        return requested_id
    with store._connect() as db:
        row = db.execute(
            "select cast(legacy_key as integer) as public_id from actions "
            "where record_type='reply_attempts' and id=?",
            (requested_id,),
        ).fetchone()
    return int(row["public_id"]) if row is not None else None


def _status_message(
    attempt: Any, attention: Any, *, rerun_block_reason: str = ""
) -> tuple[str, bool]:
    status = str(getattr(attempt, "send_status", "") or "").strip().lower()
    if terminal_oa_source_closure_is_skipped(attempt):
        return (
            "原 OA 流程已终止，此跟进已跳过；原消息送达情况仍未核实。",
            False,
        )
    if getattr(attempt, "resolved_at", "") and getattr(attempt, "resolution", ""):
        evidence = _stored_json(attempt.resolution, {})
        if isinstance(evidence, dict) and evidence.get("source") == "exact original DingTalk OA process and task readback":
            return "原 OA 流程或节点已结束，此历史处理已跳过；该收口记录未包含具体审批决定。", False
        return str(attempt.resolution), False
    if status == "sent":
        return "这条回复已发送，无需你操作。", False
    if status == "completed":
        return "这条事项已完成，无需你操作。", False
    if status == "skipped":
        return "这条事项已判定无需回复，无需你操作。", False
    if status == "needs_human":
        return "这条事项等待你的决策。请阅读已核验事实后提交处理指令。", True
    if status == "blocked" and getattr(attempt, "send_error", "") == "consumer_purpose_retry_exhausted":
        return (
            "权限申请后的自动重读次数已用完，审批仍待处理。权限开通后可重新读取并审核；"
            "此状态不代表审批失败，也不会自动执行审批动作。",
            False,
        )
    if status == "failed":
        if rerun_block_reason:
            return rerun_block_reason, False
        return "这次处理没有完成，可重新处理当前事项。", False
    if attention is not None:
        return "系统正在处理这条事项。", False
    return f"当前状态：{status or '未提供'}。", False


def _execution_status_message(
    attempt: Any, execution_status: str, fallback: str
) -> str:
    """Keep a verified source-terminal skip distinct from delivery uncertainty."""
    if terminal_oa_source_closure_is_skipped(attempt):
        return _status_message(attempt, None)[0]
    return fallback


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
    """Return each role's readable transcript.

    The Attempt row stores a single session id, which is the last role that
    ran. Linking only that one hides the Consumer transcript even though it is
    on disk, so every run with a readable transcript gets its own entry. The
    calls themselves are not copied here: the Agent record at that URL already
    carries them with their inputs, outputs and the reasoning around them.
    """
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
            }
        )
    return sessions


def _runtime_payload(agent_runs: list[Any], store: Any) -> list[dict[str, Any]]:
    from app.native_trajectory import (
        _native_records,
        claude_session_path,
        find_codex_session_path,
    )

    run_ids = [int(getattr(run, "id", 0) or 0) for run in agent_runs]
    batch_reader = getattr(store, "list_agent_runtime_attempts_for_runs", None)
    attempts_by_run = (
        batch_reader(run_ids)
        if callable(batch_reader)
        else {run_id: store.list_agent_runtime_attempts(run_id) for run_id in run_ids}
    )
    result = []
    for run in agent_runs:
        role = _run_role(run)
        for item in attempts_by_run.get(int(getattr(run, "id", 0) or 0), []):
            session_id = str(getattr(item, "session_id", "") or "").strip()
            runtime_kind = str(getattr(item, "runtime_kind", "") or "")
            start = int(getattr(item, "transcript_start", 0) or 0)
            end = int(getattr(item, "transcript_end", 0) or 0)
            path = (
                find_codex_session_path(session_id)
                if runtime_kind == "codex_cli" and session_id else
                claude_session_path(session_id)
                if runtime_kind == "claude_cli" and session_id else None
            )
            native_available = bool(_native_records(path, start, end))
            native_reason = (
                "" if native_available else
                "friday_artifact_remote" if runtime_kind == "friday_runtime" else
                "native_session_unavailable" if session_id else
                "native_session_not_started"
            )
            result.append(
                {
                    "role": normalize_display_value(role),
                    "run_id": int(getattr(run, "id", 0) or 0),
                    "execution_generation": normalize_display_value(
                        getattr(run, "execution_generation", "")
                    ),
                    "attempt_number": int(getattr(item, "attempt_number", 0) or 0),
                    "created_at": normalize_display_value(
                        getattr(item, "created_at", "")
                    ),
                    "finished_at": normalize_display_value(
                        getattr(item, "finished_at", "")
                    ),
                    "session_url": (
                        f"/codex/{quote(session_id, safe='')}"
                        if runtime_kind == "codex_cli" and native_available else ""
                    ),
                    "proposal_revision": int(getattr(run, "proposal_revision", 0) or 0),
                    "turn_attempt": int(getattr(run, "turn_attempt", 0) or 0),
                    "route": normalize_display_value(getattr(item, "route_name", "")),
                    "runtime": normalize_display_value(runtime_kind),
                    "credential_mode": normalize_display_value(getattr(item, "credential_mode", "")),
                    "model": normalize_display_value(getattr(item, "model", "")),
                    "session_available": native_available,
                    "native_reason": native_reason,
                    "status": normalize_display_value(getattr(item, "status", "")),
                    "run_status": normalize_display_value(run.status),
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


def _linked_consumer_run(terminal_run: Any, agent_runs: list[Any]) -> Any | None:
    """Resolve the current-generation Consumer for an Attempt's terminal run."""
    if terminal_run is not None and _run_role(terminal_run) == "consumer":
        return terminal_run
    parent_id = getattr(terminal_run, "parent_agent_run_id", None) if terminal_run else None
    parent = next(
        (run for run in agent_runs if getattr(run, "id", None) == parent_id),
        None,
    )
    if _run_role(parent) == "consumer":
        return parent
    consumers = [run for run in agent_runs if _run_role(run) == "consumer"]
    if not consumers:
        return None
    return max(
        consumers,
        key=lambda run: (
            int(getattr(run, "turn_attempt", 0) or 0),
            int(getattr(run, "proposal_revision", 0) or 0),
            int(getattr(run, "id", 0) or 0),
        ),
    )


def _consumer_error_reason(consumer_run: Any | None) -> str:
    if consumer_run is None:
        return "未找到当前 Attempt 关联的 Consumer run"
    if str(getattr(consumer_run, "status", "") or "") == "failed":
        error = _stored_json(getattr(consumer_run, "structured_error_json", ""), {})
        if isinstance(error, dict):
            for key in ("detail", "code"):
                value = error.get(key)
                if isinstance(value, str) and value.strip():
                    from app.history import safe_observability_error

                    return safe_observability_error(value, limit=180)
        return "Consumer 运行失败"
    if not str(getattr(consumer_run, "adopted_result_json", "") or "").strip():
        return "Consumer 已采用业务结果不可用"
    return "Consumer 结果不符合当前契约"


def _consumer_result_payload(
    terminal_run: Any | None,
    agent_runs: list[Any],
    current_agent_runs: list[Any],
    reply_task: Any | None,
) -> dict[str, Any]:
    """Build the read-only Consumer result DTO for one Attempt.

    The terminal run is the only authority for the historic metrics. Current
    generation activity is deliberately projected separately, so a rerun never
    substitutes unfinished values for the Attempt's existing result.
    """
    # A historical Attempt may not retain agent_run_id. In that case the
    # complete run list can contain older generations with larger retry
    # counters; prefer the task's current generation before falling back to
    # the historical list, otherwise an old failed Consumer masks a newer
    # completed result.
    runs_for_link = current_agent_runs if terminal_run is None and current_agent_runs else agent_runs
    consumer_run = _linked_consumer_run(terminal_run, runs_for_link)
    result = None
    raw_result = ""
    if consumer_run is not None and str(getattr(consumer_run, "status", "") or "") != "failed":
        raw_result = str(getattr(consumer_run, "adopted_result_json", "") or "")
        if raw_result.strip():
            try:
                parsed_result = _stored_json(raw_result, {})
                if isinstance(parsed_result, dict):
                    hydrated_result = dict(parsed_result)
                    # Results written before the coverage fields were added
                    # remain valid historical judgements. Match the worker's
                    # delivery-reconstruction compatibility policy: coverage
                    # defaults are safe, while risk and confidence remain
                    # required evidence.
                    if "confidence" in hydrated_result and "risk" in hydrated_result:
                        hydrated_result.setdefault("rule_coverage", 1.0)
                        hydrated_result.setdefault("information_completeness", 1.0)
                    result = parse_persisted_consumer_result(hydrated_result)
                else:
                    result = None
            except ValueError:
                result = None

    if result is None:
        partial: dict[str, Any] = {}
        parsed = _stored_json(raw_result, {})
        if isinstance(parsed, dict):
            partial = parsed
        def metric(name: str, formatter: Any = str) -> str:
            value = partial.get(name)
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                try:
                    return formatter(value)
                except (TypeError, ValueError):
                    return "—"
            if isinstance(value, str) and value.strip():
                return value.strip()
            return "—"

        partial_error = "Consumer 结果不符合当前契约" if partial and raw_result.strip() else _consumer_error_reason(consumer_run)
        payload: dict[str, Any] = {
            "confidence": metric("confidence", lambda value: f"{value:.0%}"),
            "information_completeness": metric(
                "information_completeness", lambda value: f"{value:.0%}"
            ),
            "rule_coverage": metric("rule_coverage", lambda value: f"{value:.0%}"),
            "risk": metric("risk"),
            "error_reason": partial_error,
            "current_run": None,
        }
    else:
        payload = {
            "confidence": f"{result.confidence:.0%}",
            "information_completeness": f"{result.information_completeness:.0%}",
            "rule_coverage": f"{result.rule_coverage:.0%}",
            "risk": result.risk.value,
            "error_reason": "",
            "current_run": None,
        }

    active_run = next(
        (
            run
            for run in current_agent_runs
            if _run_role(run) == "consumer"
            and str(getattr(run, "status", "") or "") in {"pending", "running"}
        ),
        None,
    )
    if active_run is not None:
        payload["current_run"] = {
            "id": int(getattr(active_run, "id", 0) or 0) or None,
            "status": str(getattr(active_run, "status", "")),
        }
    elif reply_task is not None and str(getattr(reply_task, "status", "") or "") == "pending":
        payload["current_run"] = {"id": None, "status": "pending"}
    return payload


def _email_payload(
    attempt: Any, reply_task: Any, email_store_factory: Callable[[], Any] | None
) -> dict[str, Any] | None:
    """Return the email an email-channel Attempt acted on, and what it did.

    An email Attempt addresses its work by action identity, so the message it
    came from, the plan that authorized the action and the receipt the action
    earned are all reachable but none of them were on the page.
    """
    if str(getattr(attempt, "channel", "") or "") != "email":
        return None
    trigger = _stored_json(getattr(reply_task, "trigger_message_json", ""), {})
    if not isinstance(trigger, dict):
        trigger = {}
    classification_id = str(trigger.get("classification_id") or "").strip()
    payload: dict[str, Any] = {
        "classification_id": classification_id,
        "classification_url": (
            f"/email?tab=list&selected={quote(classification_id, safe='')}"
            if classification_id
            else ""
        ),
        "account_id": normalize_display_value(trigger.get("account_id")),
        "action_type": normalize_display_value(trigger.get("action_type")),
        "category": normalize_display_value(trigger.get("category")),
        "action_plan_id": normalize_display_value(trigger.get("action_plan_id")),
        "stable_message_identity": normalize_display_value(
            trigger.get("stable_message_identity")
        ),
        "subject": "",
        "sender": normalize_display_value(getattr(attempt, "trigger_sender", "")),
        "folder": "",
        "received_at": normalize_display_value(
            getattr(attempt, "trigger_create_time", "")
        ),
        "rfc_message_id": "",
        "candidate_source": "",
        "unsubscribe": None,
    }
    parameters = trigger.get("action_parameters")
    if isinstance(parameters, dict):
        payload["candidate_source"] = normalize_display_value(
            parameters.get("candidate_source")
        )
    if email_store_factory is None:
        return payload
    email_store = email_store_factory()
    if classification_id.isdigit():
        classification = email_store.get_classification(int(classification_id))
        if classification is not None:
            payload.update(
                {
                    "subject": normalize_display_value(classification.get("subject")),
                    "sender": normalize_display_value(classification.get("sender")),
                    "folder": normalize_display_value(classification.get("folder")),
                    "received_at": normalize_display_value(
                        classification.get("received_at")
                    ),
                    "rfc_message_id": normalize_display_value(
                        classification.get("rfc_message_id")
                    ),
                }
            )
    action_identity = str(getattr(attempt, "trigger_message_id", "") or "").strip()
    receipt = email_store.get_email_unsubscribe_receipt(action_identity)
    if receipt is not None:
        payload["unsubscribe"] = {
            "outcome": normalize_display_value(receipt.get("outcome")),
            "evidence": normalize_display_value(receipt.get("evidence")),
            "result_text": normalize_display_value(receipt.get("result_text")),
            "receipt_id": normalize_display_value(receipt.get("receipt_id")),
            "entry_reference": normalize_display_value(receipt.get("entry_reference")),
            "started_at": normalize_display_value(receipt.get("started_at")),
            "completed_at": normalize_display_value(receipt.get("completed_at")),
            "steps": [
                {
                    "sequence": int(step.get("sequence") or 0),
                    "operation": normalize_display_value(step.get("operation")),
                    "state": normalize_display_value(step.get("state")),
                }
                for step in email_store.list_email_unsubscribe_steps(action_identity)
            ],
        }
    return payload


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


def _oa_url(attempt: Any, reply_task: Any) -> str:
    from app.oa_approval import resolve_oa_url

    return resolve_oa_url(
        stored_url=str(getattr(attempt, "oa_url", "") or ""),
        trigger_text=str(getattr(reply_task, "trigger_text", "") or "")
        if reply_task is not None
        else "",
        trigger_message_json=str(getattr(reply_task, "trigger_message_json", "") or "")
        if reply_task is not None
        else "",
    )


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
    rerun: RerunPresentation,
    *,
    rerun_block_reason: str = "",
    source_send_status: str = "",
    source_is_latest: bool = False,
) -> dict[str, Any]:
    from app.audit_web import _sent_reply_has_recall_target

    status = str(getattr(attempt, "send_status", "") or "").strip().lower()
    terminal = status in {"done", "sent", "skipped", "completed", "commented", "calendar", "document", "reacted"}
    dingtalk_url = ""
    approval_url = _oa_url(attempt, reply_task)
    if approval_url:
        dingtalk_url = (
            "/open-dingtalk-oa-popup?url=" + quote(approval_url, safe="")
        )
    elif str(getattr(attempt, "channel", "") or "") == "dingtalk":
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
            (
                delivery_status == "skipped"
                or (
                    delivery_status == "failed"
                    and bool(getattr(wechat_delivery, "pre_action_failure", False))
                )
            )
            and not principal_reply_supersession(str(getattr(wechat_delivery, "error", "") or ""))
            and str(getattr(wechat_delivery, "action_started_at", "") or "").strip()
            and delivery_id
        ):
            delivery_action_label = "重试发送"
            delivery_action_url = f"/api/console/wechat/deliveries/{delivery_id}/retry"
    terminal = terminal and not delivery_action_url
    can_resend_wechat = (
        source_send_status == "sent"
        and source_is_latest
        and str(getattr(attempt, "channel", "") or "") == "wechat"
        and reply_task is not None
        and not bool(getattr(reply_task, "input_compacted", False))
        and str(getattr(reply_task, "trigger_message_id", "") or "") == str(attempt.trigger_message_id)
        and int(getattr(reply_task, "manual_rerun_attempt_id", 0) or 0) != int(attempt.id)
    )
    return {
        "can_rerun": (
            (status == "failed" and not rerun_block_reason)
            or (status == "blocked" and attempt.send_error == "consumer_purpose_retry_exhausted" and not rerun_block_reason)
            or can_reopen_skipped_oa_review(attempt, reply_task)
        ),
        "rerun_label": rerun.label,
        "rerun_confirmation": rerun.confirmation,
        "rerun_block_reason": rerun_block_reason,
        "can_recall": _sent_reply_has_recall_target(sent_reply),
        "can_submit_feedback": True,
        "rerun_url": f"/api/console/history/{int(attempt.id)}/rerun",
        "can_resend_wechat": can_resend_wechat,
        "resend_wechat_url": f"/api/console/history/{int(attempt.id)}/resend-wechat",
        "resend_message_id": str(attempt.trigger_message_id) if can_resend_wechat else "",
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

def _external_effect_sentence(what_happened: dict[str, Any]) -> str:
    """Say plainly that the action completed, without reciting identifiers.

    The service records a write by the identifiers it returned -- a task id, a
    process instance, a content hash. Those are evidence, not something a
    person reads, so the sentence names the commands and counts the rest.
    """

    actions = what_happened.get("external_actions") or []
    reused_actions = what_happened.get("reused_external_actions") or []
    commands = [
        action["what"] for action in actions if action.get("recorded_by") == "turn"
    ]
    recorded = sum(1 for action in actions if action.get("recorded_by") == "service")
    parts = []
    if commands:
        parts.append("、".join(dict.fromkeys(commands)))
    if recorded:
        parts.append(f"服务记录了 {recorded} 项写操作回执")
    at = next((action.get("at") for action in actions if action.get("at")), "")
    when = f"（本轮操作时间：{at}）" if at else ""
    sentences = []
    if actions:
        sentences.append("本轮外部动作已完成" + when + ("：" + "；".join(parts) if parts else ""))
    if reused_actions:
        reused_count = len([action for action in reused_actions if action.get("recorded_by") == "system"])
        reused_times = list(dict.fromkeys(
            action["at"] for action in reused_actions if action.get("at")
        ))
        original_time = f"（原始操作时间：{'、'.join(reused_times)}）" if reused_times else ""
        sentences.append(f"已复用此前完成的 {reused_count} 项外部动作回执{original_time}")
    return "；".join(sentences)


def _consumer_result_with_deciding_scores(
    payload: dict[str, Any], what_happened: dict[str, Any]
) -> dict[str, Any]:
    """Replace the shown scores with the ones the action was taken on."""

    scores = what_happened.get("deciding_scores") or {}
    if not scores:
        return payload
    updated = dict(payload)
    for field in ("confidence", "rule_coverage", "information_completeness"):
        value = scores.get(field)
        if isinstance(value, (int, float)):
            updated[field] = f"{round(float(value) * 100)}%"
    risk = scores.get("risk")
    if isinstance(risk, str) and risk:
        updated["risk"] = risk
    updated["from_run_id"] = scores.get("from_run_id")
    return updated


def _human_decision_payload(reviewed: dict[str, Any] | None) -> dict[str, Any] | None:
    """Show persisted reviewed context, including historical answered questions."""
    if reviewed is None or reviewed["decision"] != "approve":
        return None
    from app.agent_contracts import parse_persisted_consumer_result

    try:
        decision = parse_persisted_consumer_result(reviewed["candidate_json"])
    except (TypeError, ValueError):
        return None
    if decision.outcome != "needs_human":
        return None
    return {
        "reason": normalize_display_value(decision.needs_human_reason),
        "basis": json_safe(decision.decision_basis.model_dump(mode="json")),
        "requested_input": normalize_display_value(decision.requested_input) if decision.requested_input else "",
        "candidate_id": reviewed["id"],
        "candidate_digest": reviewed["candidate_digest"],
        "review_id": reviewed["review_id"],
        "selection": {"option_key": reviewed["option_key"], "status": reviewed["selection_status"]}
        if reviewed["selection_id"] else None,
        "authorization_plan": None,
    }


def _system_execution_payload(
    store: Any, reviewed: dict[str, Any] | None, *, is_current: bool,
) -> dict[str, Any] | None:
    """Project the bound plan and canonical verified receipts independently."""
    if reviewed is None:
        return None
    from app.model_action_schema import candidate_option

    candidate = _stored_json(reviewed["candidate_json"], {})
    branch = (candidate_option(reviewed["candidate_json"], reviewed["option_key"])
              if reviewed.get("selection_id") else None)
    if candidate.get("outcome") == "needs_human" and not reviewed.get("selection_id"):
        return None
    plan = branch.get("plan") if branch else candidate.get("proposal")
    actions = plan.get("actions", []) if isinstance(plan, dict) else []
    execution = store.get_candidate_execution(reviewed["id"])
    if not is_current and execution is None:
        return None
    if candidate.get("outcome") == "no_action" and execution is None:
        return None
    attempts = {
        item["action_index"]: item
        for item in store.list_candidate_action_attempts(execution["id"])
    } if execution else {}
    result = _stored_json(execution["result_json"], {}) if execution else {}
    projected_actions = []
    for index, action in enumerate(actions):
        attempt = attempts.get(index)
        receipt = (
            store.get_candidate_external_action(attempt["external_action_key"])
            if attempt and attempt["status"] == "verified" else None
        )
        projected_actions.append({
            "action_index": index,
            "action_identity": action["action_identity"],
            "description": normalize_display_value(action.get("description")),
            "operation": action["operation"],
            "status": (
                "uncertain" if attempt and attempt["status"] == "verified" and receipt is None
                else attempt["status"] if attempt else "pending"
            ),
            "result": json_safe(_stored_json(attempt["result_json"], {})) if attempt else {},
            "receipt": {
                "external_action_key": receipt["external_action_key"],
                "provider_result": json_safe(_stored_json(receipt["provider_result_json"], {})),
            } if receipt else None,
        })
    return {
        "candidate_id": reviewed["id"], "review_id": reviewed["review_id"],
        "stage_index": candidate.get("stage_index", 0), "is_current": is_current,
        "execution_id": execution["id"] if execution else None,
        "status": execution["status"] if execution else "pending",
        "summary": normalize_display_value(result.get("summary")) if execution else "",
        "error": json_safe(result.get("error", {})),
        "verified_actions": sum(action["receipt"] is not None for action in projected_actions),
        "total_actions": len(actions), "actions": projected_actions,
    }


def _oa_decision_payload(
    attempt: Any, reviewed: dict[str, Any] | None,
    execution: dict[str, Any] | None,
) -> dict[str, Any] | None:
    """Show the reviewed OA decision with its own action receipt, not Task status."""
    if reviewed is None or execution is None:
        return None
    from app.model_action_schema import candidate_option

    candidate = _stored_json(reviewed["candidate_json"], {})
    branch = (candidate_option(reviewed["candidate_json"], reviewed["option_key"])
              if reviewed.get("selection_id") else None)
    plan = branch.get("plan") if branch else candidate.get("proposal")
    if not isinstance(plan, dict):
        return None
    labels = {"approve": "同意", "reject": "拒绝", "revert_task": "退回"}
    decisions = []
    for index, action in enumerate(plan.get("actions", [])):
        target = action.get("target", {})
        if (
            action.get("capability") != "dingtalk-oa"
            or action.get("operation") not in labels
            or str(target.get("process_instance_id") or "") != attempt.oa_process_instance_id
            or str(target.get("task_id") or "") != attempt.oa_task_id
        ):
            continue
        result = execution["actions"][index]
        decisions.append({
            "operation": action["operation"], "label": labels[action["operation"]],
            "status": result["status"], "receipt": result["receipt"],
            "reason": str(plan.get("authored_judgment") or candidate.get("summary") or ""),
            "remark": str(action.get("payload", {}).get("remark") or ""),
        })
    return decisions[0] if len(decisions) == 1 else None


def _oa_permission_wait_projection(
    store: Any,
    task: Any,
    reviewed: dict[str, Any] | None,
    execution: dict[str, Any] | None,
    *,
    historical_error: str,
    audit_note: str,
) -> dict[str, str] | None:
    """Separate a verified OA access-request receipt from the current wait state."""
    from app.consumer_purpose_retry import RETRY_REQUESTED, PurposeRetryBudget

    if (
        task is None
        or str(getattr(task, "status", "") or "") not in {"pending", "processing"}
        or str(getattr(task, "error", "") or "") != RETRY_REQUESTED
        or reviewed is None
        or execution is None
    ):
        return None
    candidate = _stored_json(str(reviewed.get("candidate_json") or ""), {})
    purpose_output = candidate.get("purpose_output") or {}
    if purpose_output.get("purpose") != "oa" or purpose_output.get("action") != "request_access":
        return None
    business_key = str(getattr(task, "business_object_key", "") or "").strip()
    retry_key = business_key or f"reply-task:{int(getattr(task, 'id', 0) or 0)}"
    retry_state = PurposeRetryBudget(store, retry_key, "oa").state()
    if not retry_state or retry_state.get("closed"):
        return None
    verified = [
        action for action in execution.get("actions", [])
        if action.get("operation") == "comment"
        and action.get("status") == "verified"
        and action.get("receipt")
        and action["receipt"].get("external_action_key") == retry_state.get("receipt")
    ]
    if len(verified) != 1:
        return None
    provider_result = verified[0]["receipt"].get("provider_result") or {}
    operation_record = provider_result.get("operation_record") or {}
    raw_timestamp = operation_record.get("date")
    operation_time = "未由回执提供"
    if isinstance(raw_timestamp, (int, float)) and raw_timestamp > 0:
        operation_time = datetime.fromtimestamp(
            raw_timestamp / 1000, tz=timezone.utc
        ).isoformat(timespec="seconds").replace("+00:00", "Z")
    note = str(audit_note or "").strip()
    audit_text = (
        f"此前 Audit 说明（针对执行前候选）：{note}"
        if note else "此前 Audit 说明针对执行前候选。"
    )
    return {
        "current_status": str(getattr(task, "status", "") or ""),
        "current_error": RETRY_REQUESTED,
        "historical_error": str(historical_error or "").strip(),
        "operation_time": operation_time,
        "status_message": (
            f"权限申请评论已由系统回执核验送达（原操作时间：{operation_time}）；"
            "当前等待权限开通后重新读取材料，尚不表示权限已开通或审批已完成。"
        ),
        "audit_explanation": (
            f"{audit_text} 系统执行回执随后确认评论已送达，"
            f"原操作时间为 {operation_time}。当前处理状态为等待权限开通后重读。"
        ),
    }


def build_attempt_detail(
    store: Any, attempt_id: int, *, email_store_factory: Callable[[], Any] | None = None
) -> tuple[int, dict[str, Any] | None]:
    """Build a rich, JSON-safe Attempt DTO without rendering HTML."""
    canonical_id = resolve_attempt_detail_reference(store, attempt_id)
    attempt = store.get_reply_attempt(canonical_id) if canonical_id is not None else None
    historical_attempt = attempt
    if attempt is None:
        return 404, None

    from app.audit_web import (
        _agent_failure_reason_text,
        _attempt_action_label_text,
        _attempt_detail_reply_text,
        _attempt_info_tooltip,
        _feedback_token_for_sent_reply,
        _sent_reply_recall_message_id,
        _attempt_reason_text,
        _needs_human_decision_options,
        _quality_warnings,
        _attempt_rerun_block_reason,
        _route_failure_recovery_state,
        reply_history_attention,
    )

    sent_reply = store.get_sent_reply(attempt.conversation_id, attempt.trigger_message_id)
    terminal_run = None
    agent_runs: list[Any] = []
    if attempt.agent_run_id:
        terminal_run = store.get_agent_run(attempt.agent_run_id)
        if terminal_run is not None:
            agent_runs = store.list_agent_runs_for_task_generation(
                terminal_run.reply_task_id, terminal_run.execution_generation
            )
    # A run owns a stable task ID even when a newer input changes the OA scan key.
    reply_task = (
        store.get_reply_task(terminal_run.reply_task_id)
        if terminal_run is not None and terminal_run.reply_task_id is not None
        else store.get_reply_task_for_message(
            attempt.conversation_id, attempt.trigger_message_id, channel=attempt.channel
        )
    )
    current_reviewed = (
        store.current_reviewed_candidate(reply_task.id, reply_task.execution_generation)
        if reply_task is not None else None
    )
    current_task_reviewed = current_reviewed
    history_reviewed = (
        store.reviewed_candidate_for_audit_run(attempt.agent_run_id)
        if attempt.agent_run_id else None
    )
    if current_reviewed is not None and current_reviewed["audit_run_id"] != attempt.agent_run_id:
        current_reviewed = None
    current_needs_human_decision = None
    if current_reviewed is not None:
        from app.decision_quality import parse_stored_needs_human_decision

        current_needs_human_decision = parse_stored_needs_human_decision(
            current_reviewed["candidate_json"]
        )
    decision_reviewed = current_reviewed or history_reviewed
    # Some historical/manual rerun Attempts have no agent_run_id.  In that
    # case the Attempt key still identifies the owning task, so load its runs
    # directly instead of incorrectly rendering "no Consumer run".
    if reply_task is not None and not agent_runs and hasattr(store, "_connect"):
        from app.store import (
            NATIVE_CANDIDATE_REVIEW_SQL, NATIVE_REVIEW_CANDIDATE_SQL,
        )
        with store._connect() as db:
            rows = db.execute(
                f"""select agent_runs.*,
                          case agent_runs.role
                            when 'consumer' then candidate.candidate_json
                            when 'audit' then review.result_json
                          end as adopted_result_json
                   from runs agent_runs
                   left join ({NATIVE_REVIEW_CANDIDATE_SQL}) candidate on candidate.candidate_id=agent_runs.id
                   left join ({NATIVE_CANDIDATE_REVIEW_SQL}) review on review.audit_run_id=agent_runs.id
                   where agent_runs.task_id=? order by agent_runs.id""",
                (reply_task.id,),
            ).fetchall()
            agent_runs = [
                store._agent_run_from_row(row, db=db, load_events=False)
                for row in rows
            ]
    current_agent_runs = agent_runs
    if (
        reply_task is not None
        and reply_task.execution_generation
        and (
            terminal_run is None
            or reply_task.execution_generation != terminal_run.execution_generation
        )
    ):
        current_agent_runs = store.list_agent_runs_for_task_generation(
            reply_task.id,
            reply_task.execution_generation,
            load_events=False,
        )
    # The stored Attempt row is historical.  Render the current projection
    # from the task generation's last effective run so an old pending row
    # cannot mask a later done/skipped result.
    from app.attempt_projection import current_wechat_delivery_matches, project_attempt_status

    wechat_delivery = (
        store.get_wechat_delivery_for_task(reply_task.id)
        if reply_task is not None and str(attempt.channel or "") == "wechat"
        else None
    )
    current_execution = (
        store.get_candidate_execution(current_task_reviewed["id"])
        if current_task_reviewed is not None else None
    )
    projected_status = project_attempt_status(
        attempt,
        reply_task,
        current_agent_runs,
        delivery=wechat_delivery,
        current_execution_status=str((current_execution or {}).get("status") or ""),
    )
    if current_needs_human_decision is not None and reply_task is not None:
        if reply_task.status == "needs_human" and current_reviewed["selection_id"] is None:
            projected_status = "needs_human"
        elif current_reviewed["selection_id"] is not None and reply_task.status in ("pending", "processing"):
            projected_status = reply_task.status
    current_generation_changed = (
        reply_task is not None and terminal_run is not None
        and reply_task.execution_generation != terminal_run.execution_generation
    )
    if current_generation_changed and current_execution is not None and current_execution["status"] == "uncertain":
        projected_status = "uncertain"
    display_runs = current_agent_runs if current_generation_changed else agent_runs
    display_reviewed = current_task_reviewed if current_generation_changed else decision_reviewed
    display_terminal_run = (
        next((run for run in reversed(display_runs)
              if display_reviewed is not None and run.id == display_reviewed["audit_run_id"]), None)
        if current_generation_changed else terminal_run
    )
    historical_record = None
    if current_generation_changed:
        historical_record = {
            "run_id": historical_attempt.agent_run_id,
            "status": historical_attempt.send_status,
            "error": historical_attempt.send_error,
            "resolution": _stored_json(historical_attempt.resolution, historical_attempt.resolution),
            "session_url": (
                f"/codex/{quote(historical_attempt.codex_session_id, safe='')}"
                if historical_attempt.codex_session_id else ""
            ),
        }
    current_stop_execution = (
        store.get_candidate_execution(current_task_reviewed["id"])
        if current_generation_changed and current_task_reviewed is not None else None
    )
    if current_stop_execution is not None and current_stop_execution["status"] not in {"failed", "uncertain", "retry"}:
        current_stop_execution = None
    display_updates = {"send_status": projected_status}
    if current_generation_changed:
        latest = current_agent_runs[-1] if current_agent_runs else None
        error = _stored_json(latest.structured_error_json, {}) if latest else {}
        result = _stored_json(latest.adopted_result_json, {}) if latest else {}
        execution_result = _stored_json(current_stop_execution["result_json"], {}) if current_stop_execution else {}
        execution_error = execution_result.get("error") or {}
        display_updates.update({
            "send_error": execution_error.get("code") or reply_task.error,
            "codex_reason": execution_error.get("code") or reply_task.error,
            "audit_summary": str(execution_result.get("summary") or error.get("detail")
                                 or error.get("reported_summary") or result.get("summary")
                                 or reply_task.error),
        })
    attempt = attempt.model_copy(update=display_updates)
    attention = reply_history_attention(
        attempt,
        task=reply_task,
        decision_options=_needs_human_decision_options(store, attempt),
    )
    rerun_block_reason = _attempt_rerun_block_reason(historical_attempt, agent_runs)
    if rerun_block_reason and rerun_block_reason == historical_attempt.resolution:
        rerun_block_reason = _status_message(historical_attempt, None)[0]
    runtime_attempts = _runtime_payload(display_runs, store)
    agent_sessions = _agent_sessions(
        attempt.model_copy(update={"codex_session_id": ""}) if current_generation_changed else attempt,
        display_runs,
    )
    feedback_token = _feedback_token_for_sent_reply(sent_reply)
    feedback_events = store.list_feedback_events_for_tokens([feedback_token]).get(feedback_token, [])
    delivery = None
    if sent_reply is not None:
        source_available = sent_reply.operation is not None and sent_reply.target is not None
        delivery = {
            "action_id": sent_reply.id,
            "conversation_id": sent_reply.conversation_id,
            "trigger_message_id": sent_reply.trigger_message_id,
            "body": sent_reply.reply_text,
            "receipt_id": sent_reply.receipt_id,
            "operation": sent_reply.operation,
            "target": sent_reply.target,
            "sent_at": sent_reply.sent_at,
            "source_available": source_available,
            "source_label": "来源事实已记录" if source_available else "来源事实未提供",
            "provider_message_id": sent_reply.provider_result_id or _sent_reply_recall_message_id(sent_reply),
            "provider_receipt": {
                "id": sent_reply.provider_receipt_id,
                "available": sent_reply.provider_receipt_id is not None,
                "body": sent_reply.send_result_json,
                "result_digest": sent_reply.result_digest,
            },
            "recall_status": sent_reply.recall_status,
            "recalled_at": sent_reply.recalled_at,
        }
    rerun = (attempt_rerun_presentation(store, reply_task, agent_runs)
             if attempt.send_status == "failed" else RerunPresentation())
    latest_attempt = store.get_latest_reply_attempt_for_trigger(
        historical_attempt.conversation_id, historical_attempt.trigger_message_id,
    ) if historical_attempt.channel == "wechat" else None
    status_message, requires_decision = _status_message(attempt, attention, rerun_block_reason=rerun_block_reason)
    # A sent-reply ledger is direct evidence that a DingTalk response reached
    # the provider. Prefer it to the task projection (which can simply be
    # "done") so the person viewing the Attempt is not left guessing whether
    # anything was actually delivered.
    if sent_reply is not None and str(attempt.channel or "") == "dingtalk" and decision_reviewed is None:
        recipient = str(attempt.conversation_title or "").strip() or "对方"
        status_message = f"已向 {recipient} 发送回复，并已记录投递回执。"
        requires_decision = False
    if wechat_delivery is not None:
        delivery_status = str(getattr(wechat_delivery, "status", "") or "").strip()
        delivery_started = str(
            getattr(wechat_delivery, "action_started_at", "") or ""
        ).strip()
        if delivery_status == "ready_to_send":
            status_message = "这条微信回复已准备好，确认后即可发送。"
        elif delivery_status == "skipped" and principal_reply_supersession(str(getattr(wechat_delivery, "error", "") or "")):
            if current_wechat_delivery_matches(attempt, reply_task, wechat_delivery):
                status_message = "你已在后续消息中亲自回复；这条旧候选已跳过，未发送，无需重试。"
        elif delivery_started and (
            delivery_status == "skipped"
            or (
                delivery_status == "failed"
                and bool(getattr(wechat_delivery, "pre_action_failure", False))
            )
        ):
            status_message = "这条微信回复在发送前失败，尚未发送；你可以重试。"
    decision_options = []
    decision_actionable = (
        attempt.send_status == "needs_human"
        and reply_task is not None and reply_task.status == "needs_human"
        and current_reviewed is not None and current_reviewed["selection_id"] is None
        and current_needs_human_decision is not None
    )
    if attempt.send_status == "needs_human":
        requires_decision = bool(decision_actionable)
    if decision_actionable:
        decision = current_needs_human_decision
        for option in decision.decision_options if decision is not None else ():
            decision_options.append(
                {
                    "key": option.key,
                    "label": normalize_display_value(getattr(option, "label", "")),
                    "instruction": normalize_display_value(getattr(option, "instruction", "")),
                    "consequence": normalize_display_value(getattr(option, "consequence", "")),
                    "plan": json_safe(option.plan.model_dump(mode="json")) if option.plan else None,
                    "terminal_outcome": option.terminal_outcome,
                    "reason": option.reason,
                    "url": f"/api/console/history/{int(attempt.id)}/human-decision",
                }
            )
    human_decision = _human_decision_payload(decision_reviewed)
    system_execution = _system_execution_payload(
        store, display_reviewed, is_current=display_reviewed is not None and display_reviewed == current_task_reviewed,
    )
    if display_reviewed is not None and display_reviewed == current_task_reviewed:
        # A previous stage's send is evidence for that stage, never completion
        # of the current reviewed question or selected plan.
        status_message, _ = _status_message(attempt, attention)
        if system_execution is not None:
            execution_messages = {
                "pending": "已审核方案等待系统执行；尚未核验业务完成。",
                "running": "系统正在执行已审核方案，完成状态以核验回执为准。",
                "retry": "已审核方案没有全部核验完成，系统将按原方案恢复。",
                "uncertain": "已审核方案没有全部核验完成，外部结果仍待核验。",
                "failed": "已审核方案执行失败；已完成动作的回执仍保留。",
                "done": "已审核方案的全部动作已核验完成。",
                "skipped": "已按审核通过的停止方案结束，原因已记录。",
            }
            status_message = _execution_status_message(
                attempt,
                str(system_execution["status"]),
                execution_messages.get(system_execution["status"], status_message),
            )
    if rerun_block_reason:
        status_message = rerun_block_reason
    elif attempt.send_status == "failed" and rerun.explanation:
        status_message = rerun.explanation
    if reply_task is not None and reply_task.status == "failed" and reply_task.error in {"external_action_uncertain", "external_action_failed"}:
        # Historical sent projections and Agent summaries precede the current
        # delivery readback and cannot override its failure/uncertainty.
        status_message = attention.reason if attention else reply_task.error
        requires_decision = False
    try:
        audit_explanation = _attempt_reason_text(attempt)
    except RuntimeError:
        audit_explanation = attempt.codex_reason or attempt.send_error
    if current_generation_changed and display_terminal_run is not None:
        audit_explanation = str(_stored_json(display_terminal_run.adopted_result_json, {}).get("summary") or "")
    oa_decision = _oa_decision_payload(attempt, display_reviewed, system_execution)
    if oa_decision is not None:
        if oa_decision["receipt"] is not None:
            status_message = f"审批已{oa_decision['label']}，操作结果已核验。"
        else:
            status_message = f"审批方案：{oa_decision['label']}；尚未核验操作完成。"
        if historical_record is not None and historical_attempt.resolved_at:
            rerun_block_reason = "原始处理已结束；当前审批决定和执行结果见下方。"
    oa_permission_wait = _oa_permission_wait_projection(
        store,
        reply_task,
        history_reviewed,
        system_execution,
        historical_error=attempt.send_error,
        audit_note=audit_explanation,
    )
    if oa_permission_wait is not None:
        status_message = oa_permission_wait["status_message"]
        audit_explanation = oa_permission_wait["audit_explanation"]
        # The immutable Attempt row may still say "running" or carry the
        # executor's earlier uncertainty. Project the current task wait state
        # for this detail response, while retaining the original error below.
        attempt = attempt.model_copy(update={
            "send_status": oa_permission_wait["current_status"],
            "send_error": oa_permission_wait["current_error"],
            "audit_summary": oa_permission_wait["audit_explanation"],
        })
    stop_reason = stopped_because(current_agent_runs)
    if reply_task is not None and reply_task.status in {"done", "skipped"}:
        stop_reason = {"kind": "none", "code": "", "sentence": ""}
    elif attempt.send_status == "failed" and (not current_agent_runs or current_agent_runs[-1].status != "failed"):
        stop_reason = {
            "kind": "execution" if current_stop_execution is not None or (
                system_execution and system_execution["is_current"]
                and system_execution["status"] in {"failed", "uncertain", "retry"}
            ) else "rule",
            "code": attempt.send_error or attempt.codex_reason,
            "sentence": attempt.audit_summary or audit_explanation,
        }
    current_failure_reason = _agent_failure_reason_text(attempt, current_agent_runs)
    if current_generation_changed and not current_agent_runs and attempt.send_status == "failed":
        current_failure_reason = attempt.audit_summary or attempt.send_error
    what_happened = build_what_happened(display_runs, store=store, stop_reason=stop_reason)
    if current_generation_changed and display_reviewed is not None:
        displayed_candidate = _stored_json(display_reviewed["candidate_json"], {})
        what_happened["deciding_scores"] = {
            **{field: displayed_candidate.get(field) for field in (
                "confidence", "information_completeness", "rule_coverage", "risk",
            )},
            "from_run_id": display_reviewed["candidate_id"],
        }
    metric_evidence = (
        {"deciding_scores": deciding_scores(agent_runs)}
        if current_generation_changed and display_reviewed is None else what_happened
    )
    oa_action = oa_decision["operation"] if oa_decision is not None else attempt.oa_action
    action_pills = [{"label": _attempt_action_label_text(attempt), "status": attempt.send_status}]
    if oa_action.strip():
        action_pills.append({"label": f"🧾 {oa_action.strip()}", "status": oa_action})
    if attempt.calendar_response_status.strip():
        action_pills.append({"label": f"📆 {attempt.calendar_response_status.strip()}", "status": attempt.calendar_response_status})
    if _route_failure_recovery_state(attempt, reply_task):
        action_pills.append({"label": "↻ Recovery", "status": _route_failure_recovery_state(attempt, reply_task)})
    # Attempt rows are service state; native sessions own tool-call detail.
    tool_uses: list[dict[str, Any]] = []
    metadata = [
        {"label": "trigger message id", "value": normalize_display_value(attempt.trigger_message_id)},
        {"label": "action", "value": normalize_display_value(attempt.action)},
        {"label": "sensitivity", "value": normalize_display_value(attempt.sensitivity_kind)},
        {"label": "permission", "value": normalize_display_value(_permission_display(attempt))},
        {"label": "send status", "value": normalize_display_value(attempt.send_status)},
        {
            "label": "send error",
            "value": normalize_display_value(
                oa_permission_wait["current_error"]
                if oa_permission_wait is not None else attempt.send_error
            ),
        },
        {"label": "retry count", "value": str(attempt.retry_count)},
        {"label": "created", "value": normalize_display_value(attempt.created_at)},
        {"label": "updated", "value": normalize_display_value(attempt.updated_at)},
        {"label": "reviewed", "value": normalize_display_value(attempt.reviewed_at)},
    ]
    if (
        oa_permission_wait is not None
        and oa_permission_wait["historical_error"]
        and oa_permission_wait["historical_error"] != oa_permission_wait["current_error"]
    ):
        metadata.append({
            "label": "原始 Attempt 错误（历史）",
            "value": normalize_display_value(oa_permission_wait["historical_error"]),
        })
    if (
        reply_task is not None
        and int(getattr(reply_task, "manual_rerun_attempt_id", 0) or 0) > 0
    ):
        metadata.append(
            {
                "label": "当前批次",
                "value": "人工重新处理：不是审核反馈后的复审。",
            }
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
                # Say whether the action completed when the record knows. The
                # stock sentence ("whether the external action completed is
                # decided by the current result and the business system") is
                # what the page showed for a leave that was already approved.
                "external_effect": (
                    _external_effect_sentence(what_happened)
                    if (
                        what_happened["reached_the_outside_world"]
                        or what_happened.get("reused_external_actions")
                    )
                    else normalize_display_value(getattr(attention, "external_effect", "")) if attention else ""
                ),
                "retry_at": normalize_display_value(getattr(attention, "retry_at", "")) if attention else "",
            },
        },
        "metadata": metadata,
        # The scores shown are the ones the action was taken on. Reading them
        # off the last run made the page say "approved on 86% complete
        # material" for an approval decided at 100%.
        "consumer_result": _consumer_result_with_deciding_scores(
            _consumer_result_payload(
                display_terminal_run or terminal_run, display_runs or agent_runs, current_agent_runs, reply_task
            ),
            metric_evidence,
        ),
        "trigger": {
            "title": "Trigger",
            "text": normalize_display_value(f"{attempt.trigger_sender}: {attempt.trigger_text}"),
        },
        "audit_explanation": {"title": "审计说明", "text": normalize_display_value(audit_explanation)},
        # Say what the turn wrote when it wrote something. "No generated reply
        # recorded" was shown for a turn that had composed a clarifying
        # question and already sent it.
        "generated_reply": {
            "title": "生成回复",
            "text": normalize_display_value(
                sent_reply.reply_text if sent_reply is not None and sent_reply.reply_text.strip()
                else what_happened.get("proposed_text")
                or _attempt_detail_reply_text(attempt, sent_reply)
            ),
        },
        "delivery": delivery,
        "email": _email_payload(attempt, reply_task, email_store_factory),
        "references": _references_payload(attempt),
        "feedback": {
            "reviewer_feedback": normalize_display_value(attempt.reviewer_feedback),
            "corrected_reply": normalize_display_value(attempt.corrected_reply_text),
            "feedback_url": f"/api/console/history/{int(attempt.id)}/feedback",
            "events": _feedback_payload(feedback_events),
        },
        "decision_options": decision_options,
        "human_decision": human_decision,
        "system_execution": system_execution,
        "audit_summary": normalize_display_value(attempt.audit_summary),
        "draft_reply": normalize_display_value(attempt.draft_reply_text),
        "failure_reason": normalize_display_value(current_failure_reason),
        "recovery_state": normalize_display_value(_route_failure_recovery_state(attempt, reply_task)),
        "action_pills": action_pills,
        "quality_warnings": [normalize_display_value(item) for item in _quality_warnings(attempt)],
        "context_only_info": normalize_display_value(_attempt_info_tooltip(attempt)),
        "tool_uses": tool_uses,
        "historical_attempt": historical_record,
        # The three questions a person opens this page with, answered from
        # the generation's own record rather than any one channel's fields.
        "what_happened": what_happened,
        "agent_execution_record": bool(display_runs or attempt.codex_session_id),
        "revision_count": len({getattr(run, "proposal_revision", "") for run in display_runs if getattr(run, "role", None) and getattr(run, "proposal_revision", "")}),
        "oa": {
            "process_instance_id": normalize_display_value(attempt.oa_process_instance_id),
            "task_id": normalize_display_value(attempt.oa_task_id),
            "url": normalize_display_value(_oa_url(attempt, reply_task)),
            "action": normalize_display_value(oa_action),
            "remark": oa_decision["remark"] if oa_decision is not None else normalize_display_value(attempt.oa_remark),
            "result": (
                oa_decision["receipt"]["provider_result"] if oa_decision["receipt"] else {}
            ) if oa_decision is not None else _stored_json(attempt.oa_action_result_json, {}),
            "decision": oa_decision,
        },
        "calendar": {
            "event_id": normalize_display_value(attempt.calendar_event_id),
            "response_status": normalize_display_value(attempt.calendar_response_status),
            "result": _stored_json(attempt.calendar_response_result_json, {}),
        },
        "actions": _action_links(
            attempt, agent_sessions, reply_task, sent_reply, wechat_delivery, rerun,
            rerun_block_reason=rerun_block_reason,
            source_send_status=historical_attempt.send_status,
            source_is_latest=(
                latest_attempt is not None
                and latest_attempt.channel == "wechat"
                and latest_attempt.id == historical_attempt.id
            ),
        ),
        "agent_sessions": agent_sessions,
        "runtime_attempts": runtime_attempts,
        "created_at": normalize_display_value(attempt.created_at),
        "updated_at": normalize_display_value(attempt.updated_at),
    }
