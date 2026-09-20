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
    from app.codex_history import find_codex_session_path

    result = []
    for run in agent_runs:
        role = _run_role(run)
        for item in store.list_agent_runtime_attempts(run.id):
            session_id = str(getattr(item, "session_id", "") or "").strip()
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
                        f"/codex/{quote(session_id, safe='')}" if session_id else ""
                    ),
                    "proposal_revision": int(getattr(run, "proposal_revision", 0) or 0),
                    "turn_attempt": int(getattr(run, "turn_attempt", 0) or 0),
                    "route": normalize_display_value(getattr(item, "route_name", "")),
                    "runtime": normalize_display_value(getattr(item, "runtime_kind", "")),
                    "credential_mode": normalize_display_value(getattr(item, "credential_mode", "")),
                    "model": normalize_display_value(getattr(item, "model", "")),
                    "session_available": bool(
                        session_id and find_codex_session_path(session_id) is not None
                    ),
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
    if not str(getattr(consumer_run, "final_result_json", "") or "").strip():
        return "Consumer 未保存最终结果"
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
        raw_result = str(getattr(consumer_run, "final_result_json", "") or "")
        if raw_result.strip():
            from app.agent_contracts import ConsumerAgentResult

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
                    result = ConsumerAgentResult.model_validate(hydrated_result)
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
    attempt: Any, reply_task: Any, email_store: Any
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
    if email_store is None:
        return payload
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


from app.attempt_what_happened import build_what_happened


def _external_effect_sentence(what_happened: dict[str, Any]) -> str:
    """Say plainly that the action completed, without reciting identifiers.

    The service records a write by the identifiers it returned -- a task id, a
    process instance, a content hash. Those are evidence, not something a
    person reads, so the sentence names the commands and counts the rest.
    """

    actions = what_happened.get("external_actions") or []
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
    when = f"（{at}）" if at else ""
    return "外部动作已完成" + when + ("：" + "；".join(parts) if parts else "")


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


def build_attempt_detail(
    store: Any, attempt_id: int, *, email_store: Any = None
) -> tuple[int, dict[str, Any] | None]:
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
    terminal_run = None
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
    # Some historical/manual rerun Attempts have no agent_run_id.  In that
    # case the Attempt key still identifies the owning task, so load its runs
    # directly instead of incorrectly rendering "no Consumer run".
    if reply_task is not None and not agent_runs and hasattr(store, "_connect"):
        with store._connect() as db:
            rows = db.execute(
                "select * from agent_runs where reply_task_id=? order by id",
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
            reply_task.id, reply_task.execution_generation
        )
        if not current_agent_runs:
            # A manual rerun may close without creating a run.  Keep the
            # historical Consumer evidence readable, while current state
            # remains governed by the task itself.
            current_agent_runs = agent_runs
    # The stored Attempt row is historical.  Render the current projection
    # from the task generation's last effective run so an old pending row
    # cannot mask a later done/skipped result.
    from app.attempt_projection import project_attempt_status

    attempt = attempt.model_copy(
        update={
            "send_status": project_attempt_status(
                attempt, reply_task, current_agent_runs
            )
        }
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
    what_happened = build_what_happened(agent_runs, store=store)
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
                # Say whether the action completed when the record knows. The
                # stock sentence ("whether the external action completed is
                # decided by the current result and the business system") is
                # what the page showed for a leave that was already approved.
                "external_effect": (
                    _external_effect_sentence(what_happened)
                    if what_happened["reached_the_outside_world"]
                    else normalize_display_value(getattr(attention, "external_effect", "")) if attention else ""
                ),
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
        # The scores shown are the ones the action was taken on. Reading them
        # off the last run made the page say "approved on 86% complete
        # material" for an approval decided at 100%.
        "consumer_result": _consumer_result_with_deciding_scores(
            _consumer_result_payload(
                terminal_run, agent_runs, current_agent_runs, reply_task
            ),
            what_happened,
        ),
        "trigger": {
            "title": "Trigger",
            "text": normalize_display_value(f"{attempt.trigger_sender}: {attempt.trigger_text}"),
        },
        "audit_explanation": {"title": "审计说明", "text": normalize_display_value(audit_explanation)},
        "generated_reply": {"title": "生成回复", "text": normalize_display_value(_attempt_detail_reply_text(attempt, sent_reply))},
        "email": _email_payload(attempt, reply_task, email_store),
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
        # The three questions a person opens this page with, answered from
        # the generation's own record rather than any one channel's fields.
        "what_happened": what_happened,
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
