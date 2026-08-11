import json
from datetime import datetime, timedelta, timezone
from uuid import NAMESPACE_URL, uuid4, uuid5
from zoneinfo import ZoneInfo

from app.dws_client import DwsError
from app.external_retry import is_external_dependency_error
from app.feedback_spike import prepare_outgoing_reply_text
from app.store import AutoReplyStore
from app.task_models import ProjectStatus, TodoStatus, WorkItem
from app.todo_sync import (
    refresh_dingtalk_todo_before_follow_up,
)


MAX_FOLLOW_UP_AGE_SECONDS = 7 * 24 * 60 * 60
RECOVERABLE_AUTH_RETRY_DELAY = timedelta(minutes=15)
FOLLOW_UP_SEND_LEASE = timedelta(minutes=5)
FOLLOW_UP_RECONCILIATION_LEASE = timedelta(minutes=5)
FOLLOW_UP_RECONCILIATION_DELAY = timedelta(minutes=15)
PRIOR_DELIVERY_REVIEW_REASON = "prior_revision_delivered_requires_agent_review"
LOCAL_WORK_TZ = ZoneInfo("Asia/Shanghai")
LOCAL_WORK_START_HOUR = 9
LOCAL_WORK_END_HOUR = 18
FOLLOW_UP_FIELD_LIMIT = 96
FOLLOW_UP_DESCRIPTION_LIMIT = 240
FOLLOW_UP_QUESTION_LIMIT = 140


def _parse_follow_up_datetime(value: str) -> datetime | None:
    text = value.strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is not None:
        return parsed.astimezone(timezone.utc).replace(tzinfo=None)
    return parsed


def _is_stale_follow_up(scheduled_at: str, now: str) -> bool:
    scheduled = _parse_follow_up_datetime(scheduled_at)
    current = _parse_follow_up_datetime(now)
    if scheduled is None or current is None:
        return False
    return (current - scheduled).total_seconds() > MAX_FOLLOW_UP_AGE_SECONDS


def _has_completion_evidence(completion_evidence_json: str) -> bool:
    try:
        evidence = json.loads(completion_evidence_json or "{}")
    except json.JSONDecodeError:
        return bool(completion_evidence_json.strip())
    return bool(evidence)


def _json_dict(value: str) -> dict:
    try:
        parsed = json.loads(value or "{}")
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _tomorrow_morning(now: str) -> str:
    current = _now_as_utc(now).astimezone(LOCAL_WORK_TZ)
    tomorrow = current + timedelta(days=1)
    while tomorrow.weekday() >= 5:
        tomorrow = tomorrow + timedelta(days=1)
    local_morning = tomorrow.replace(
        hour=LOCAL_WORK_START_HOUR,
        minute=0,
        second=0,
        microsecond=0,
    )
    return local_morning.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _now_as_utc(value: str) -> datetime:
    parsed = _parse_follow_up_datetime(value) or datetime.now(timezone.utc).replace(
        tzinfo=None
    )
    return parsed.replace(tzinfo=timezone.utc)


def _local_work_time(value: str) -> datetime:
    return _now_as_utc(value).astimezone(LOCAL_WORK_TZ)


def _is_local_working_time(value: str) -> bool:
    local_now = _local_work_time(value)
    return (
        local_now.weekday() < 5
        and LOCAL_WORK_START_HOUR <= local_now.hour < LOCAL_WORK_END_HOUR
    )


def _next_local_work_start_utc(value: str) -> str:
    local_next = _local_work_time(value)
    if local_next.weekday() >= 5 or local_next.hour >= LOCAL_WORK_END_HOUR:
        local_next = local_next + timedelta(days=1)
        while local_next.weekday() >= 5:
            local_next = local_next + timedelta(days=1)
        local_next = local_next.replace(
            hour=LOCAL_WORK_START_HOUR,
            minute=0,
            second=0,
            microsecond=0,
        )
    elif local_next.hour < LOCAL_WORK_START_HOUR:
        local_next = local_next.replace(
            hour=LOCAL_WORK_START_HOUR,
            minute=0,
            second=0,
            microsecond=0,
        )
    else:
        local_next = local_next.replace(microsecond=0)
    return local_next.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _risk_check(draft) -> dict:
    return _json_dict(draft.risk_check_json)


def _is_sensitive_follow_up(draft) -> bool:
    return bool(_risk_check(draft).get("sensitive"))


def _compact_follow_up_text(value: str, *, limit: int) -> str:
    text = " ".join(str(value or "").strip().split())
    if len(text) <= limit:
        return text
    return f"{text[: max(0, limit - 4)].rstrip()}..."


def _follow_up_context_lines(project, todo, draft) -> list[str]:
    lines: list[str] = []
    project_title = project.title.strip() if project is not None else ""
    todo_title = todo.title.strip() if todo is not None else ""
    draft_title = str(getattr(draft, "title", "") or "").strip()
    title = draft_title or todo_title
    if title:
        lines.append(
            f"- 事项：{_compact_follow_up_text(title, limit=FOLLOW_UP_FIELD_LIMIT)}"
        )
    if project_title:
        lines.append(
            f"- 项目：{_compact_follow_up_text(project_title, limit=FOLLOW_UP_FIELD_LIMIT)}"
        )
    if todo_title and todo_title != title:
        lines.append(
            f"- TODO：{_compact_follow_up_text(todo_title, limit=FOLLOW_UP_FIELD_LIMIT)}"
        )
    priority = str(getattr(draft, "priority", "") or "").strip()
    if not priority and todo is not None:
        priority = str(todo.priority).strip()
    if priority:
        lines.append(f"- 优先级：{priority}")
    deadline = ""
    if todo is not None:
        deadline = todo.deadline_at.strip()
    if deadline:
        lines.append(f"- DDL：{deadline}")
    return lines


def _follow_up_background_text(project, todo, draft) -> str:
    candidates = [
        str(getattr(draft, "description", "") or "").strip(),
        todo.description.strip() if todo is not None else "",
        project.background.strip() if project is not None else "",
    ]
    for candidate in candidates:
        if candidate:
            return _compact_follow_up_text(
                candidate,
                limit=FOLLOW_UP_DESCRIPTION_LIMIT,
            )
    return ""


def _is_open_conversation_id(value: str) -> bool:
    return value.strip().startswith("cid")


def _follow_up_message_text(store: AutoReplyStore, draft) -> str:
    project = store.get_work_project(draft.project_id)
    todo = store.get_work_todo(draft.todo_id) if draft.todo_id > 0 else None
    question = _compact_follow_up_text(
        draft.question_text.strip(),
        limit=FOLLOW_UP_QUESTION_LIMIT,
    )
    parts: list[str] = []
    if question:
        parts.append(f"**请确认：** {question}")
    context_lines = _follow_up_context_lines(project, todo, draft)
    if context_lines:
        parts.append("**事项**\n" + "\n".join(context_lines))
    background = _follow_up_background_text(project, todo, draft)
    if background:
        parts.append(f"**背景**\n{background}")
    return "\n\n".join(parts).strip()


def _completion_supported_by_current_evidence(
    store: AutoReplyStore,
    draft,
) -> tuple[bool, str]:
    project = store.get_work_project(draft.project_id)
    if project is not None and str(project.status) == ProjectStatus.DONE.value:
        return True, "project status is done"

    if draft.todo_id <= 0:
        return False, ""

    todo = store.get_work_todo(draft.todo_id)
    if todo is None:
        return False, ""
    if str(todo.status) == TodoStatus.DONE.value:
        return True, "todo status is done"
    if str(todo.status) == TodoStatus.CANCELLED.value:
        return True, "todo status is cancelled"
    if _has_completion_evidence(todo.completion_evidence_json):
        return True, "todo has completion evidence"
    return False, ""


def _skip_completed_follow_up(
    store: AutoReplyStore,
    draft,
    *,
    now: str,
    reason: str,
    completed: bool = True,
) -> bool:
    status = "completed" if completed else "skipped"
    payload = {
        "completed": completed,
        "skipped": not completed,
        "reason": reason,
        "source": reason,
        "checked_at": now,
        "evidence_check": "completion_supported",
    }
    return store.update_follow_up_draft_if_revision(
        draft.id,
        draft.revision,
        status=status,
        sent_at=now,
        send_result_json=json.dumps(payload, ensure_ascii=False),
        evidence_check_json=json.dumps(payload, ensure_ascii=False),
        suppressed_reason=reason,
    )


def _recoverable_retry_at(now: str) -> str:
    current = _parse_follow_up_datetime(now) or datetime.now(timezone.utc).replace(
        tzinfo=None
    )
    return (current + RECOVERABLE_AUTH_RETRY_DELAY).strftime("%Y-%m-%d %H:%M:%S")


def _lease_until(now: str, duration: timedelta) -> str:
    current = _parse_follow_up_datetime(now) or datetime.now(timezone.utc).replace(
        tzinfo=None
    )
    return (current + duration).strftime("%Y-%m-%d %H:%M:%S")


def _attempt_lease_is_active(attempt: dict[str, object], *, now: str) -> bool:
    lease_until = _parse_follow_up_datetime(str(attempt.get("lease_until") or ""))
    current = _parse_follow_up_datetime(now)
    return lease_until is not None and current is not None and lease_until > current


def _follow_up_revision_uuid(draft, message_text: str) -> str:
    revision = json.dumps(
        {
            "draft_id": draft.id,
            "todo_id": draft.todo_id,
            "owner_user_id": draft.owner_user_id,
            "target_kind": draft.target_kind,
            "target_conversation_id": draft.target_conversation_id,
            "message_text": message_text,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return str(uuid5(NAMESPACE_URL, f"ceo-agent-service:follow-up:{revision}"))


def _defer_recoverable_follow_up(
    store: AutoReplyStore,
    draft,
    *,
    now: str,
    reason: str,
    error: str,
    claim_token: str = "",
    idempotency_uuid: str = "",
    lease_owner: str = "",
) -> bool:
    result_json = json.dumps(
        {
            "recoverable": True,
            "reason": reason,
            "error": error,
            "claimed_revision": draft.revision,
            "idempotency_uuid": idempotency_uuid,
            "retry_delay_minutes": int(
                RECOVERABLE_AUTH_RETRY_DELAY.total_seconds() // 60
            ),
        },
        ensure_ascii=False,
    )
    update = {
        "status": "draft",
        "scheduled_at": _recoverable_retry_at(now),
        "send_result_json": result_json,
    }
    if claim_token:
        return store.update_claimed_follow_up_draft(
            draft.id,
            claimed_revision=draft.revision,
            claim_token=claim_token,
            lease_owner=lease_owner,
            now=now,
            attempt_state=(
                "unknown" if reason == "dws_send_outcome_unknown" else "retryable"
            ),
            attempt_result_json=result_json,
            **update,
        )
    return store.update_follow_up_draft_if_revision(
        draft.id,
        draft.revision,
        **update,
    )


def _dws_send_outcome_is_unknown(exc: BaseException) -> bool:
    if not isinstance(exc, DwsError):
        return False
    if exc.needs_login or exc.needs_authorization:
        return False
    return exc.code in {None, "1"}


def _reconcile_unknown_follow_up_attempt(
    store: AutoReplyStore,
    dws,
    draft,
    attempt: dict[str, object],
    *,
    now: str,
    reconciliation_owner: str,
) -> str:
    attempt_revision = int(attempt.get("draft_revision") or 0)
    payload = _json_dict(str(attempt.get("result_json") or "{}"))
    send_result = payload.get("send_result")
    verification: dict[str, object] = {
        "state": "ambiguous",
        "reason": "no persisted send result supports read-only status lookup",
    }
    if isinstance(send_result, dict) and hasattr(dws, "verify_message_send_result"):
        try:
            checked = dws.verify_message_send_result(send_result)
            if isinstance(checked, dict):
                verification = checked
        except Exception as exc:
            verification = {
                "state": "ambiguous",
                "reason": "send status readback failed",
                "error": str(exc),
            }
    reconciled = {
        **payload,
        "claimed_revision": attempt_revision,
        "idempotency_uuid": str(attempt.get("idempotency_uuid") or ""),
        "reconciliation_from_state": str(
            attempt.get("reconciliation_from_state") or "unknown"
        ),
        "reconciliation": verification,
        "reconciled_at": now,
    }
    result_json = json.dumps(reconciled, ensure_ascii=False)
    state = str(verification.get("state") or "").casefold()
    claim_token = str(attempt.get("claim_token") or "")
    if state == "sent":
        resolved = store.resolve_unknown_follow_up_attempt_sent(
            draft.id,
            draft_revision=attempt_revision,
            claim_token=claim_token,
            lease_owner=reconciliation_owner,
            now=now,
            sent_at=now,
            result_json=result_json,
        )
        return "sent" if resolved else "stale"
    if state == "failed":
        resolved = store.resolve_unknown_follow_up_attempt_not_sent(
            draft.id,
            draft_revision=attempt_revision,
            claim_token=claim_token,
            lease_owner=reconciliation_owner,
            now=now,
            result_json=result_json,
        )
        return "not_sent" if resolved else "stale"
    deferred = store.defer_unknown_follow_up_attempt(
        draft.id,
        draft_revision=attempt_revision,
        claim_token=claim_token,
        lease_owner=reconciliation_owner,
        now=now,
        lease_until=_lease_until(now, FOLLOW_UP_RECONCILIATION_DELAY),
        result_json=result_json,
    )
    return "unknown" if deferred else "stale"


def _recover_follow_up_send_attempt(
    store: AutoReplyStore,
    draft,
    *,
    now: str,
) -> bool:
    attempt = store.get_follow_up_send_attempt(
        draft_id=draft.id,
        draft_revision=draft.revision,
    )
    if attempt is None or str(attempt.get("state") or "") == "retryable":
        return False
    state = str(attempt.get("state") or "")
    if state == "claimed":
        return _attempt_lease_is_active(attempt, now=now)
    return True


def _defer_policy_follow_up(
    store: AutoReplyStore,
    draft,
    *,
    now: str,
    reason: str,
    detail: dict,
) -> bool:
    next_scheduled_at = str(detail.get("next_scheduled_at") or "").strip()
    return store.update_follow_up_draft_if_revision(
        draft.id,
        draft.revision,
        status="draft",
        scheduled_at=next_scheduled_at or _tomorrow_morning(now),
        suppressed_reason=reason,
        evidence_check_json=json.dumps(
            {
                "deferred": True,
                "reason": reason,
                "checked_at": now,
                **detail,
            },
            ensure_ascii=False,
        ),
    )


def _work_tracking_review_item(
    store: AutoReplyStore,
    draft,
    *,
    now: str,
    reason: str,
    repair_source_ref: str = "",
    additional_evidence: dict[str, object] | None = None,
) -> tuple[WorkItem, str]:
    project = store.get_work_project(draft.project_id)
    todo = store.get_work_todo(draft.todo_id) if draft.todo_id > 0 else None
    dingtalk_link = (
        store.get_active_work_todo_dingtalk_link(todo.id) if todo is not None else None
    )
    existing_check = _json_dict(draft.evidence_check_json)
    if (
        not repair_source_ref
        and reason == "stale_follow_up_requires_agent_review"
        and draft.suppressed_reason == reason
    ):
        repair_source_ref = str(existing_check.get("repair_source_ref") or "").strip()
    if reason == "stale_follow_up_requires_agent_review" and not repair_source_ref:
        revision = json.dumps(
            {
                "draft_id": draft.id,
                "todo_id": draft.todo_id,
                "owner_user_id": draft.owner_user_id,
                "target_kind": draft.target_kind,
                "target_conversation_id": draft.target_conversation_id,
                "question_text": draft.question_text,
                "scheduled_at": draft.scheduled_at,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        repair_revision = uuid5(
            NAMESPACE_URL,
            f"ceo-agent-service:follow-up-repair:{revision}",
        )
        repair_source_ref = f"follow-up-repair:{draft.id}:{repair_revision}"
    if not repair_source_ref:
        repair_source_ref = f"follow-up-repair:{draft.id}"
    work_item = WorkItem.model_validate(
        {
            "source": {
                "type": "follow_up_completion_check",
                "ref": repair_source_ref,
                "title": f"Follow-up repair #{draft.id}",
                "conversation_id": draft.target_conversation_id,
                "conversation_title": "",
                "created_at": now,
            },
            "summary": json.dumps(
                {
                    "reason": reason,
                    "project": (
                        {"id": project.id, "title": project.title}
                        if project is not None
                        else {"id": draft.project_id}
                    ),
                    "todo": (
                        {
                            "id": todo.id,
                            "title": todo.title,
                            "owner_user_id": todo.owner_user_id,
                            "owner_name": todo.owner_name,
                            "status": str(todo.status),
                            "completion_evidence": _json_dict(
                                todo.completion_evidence_json
                            ),
                            "dingtalk": (
                                {
                                    "task_id": dingtalk_link.dingtalk_task_id,
                                    "done": dingtalk_link.last_dingtalk_done,
                                    "last_pull_at": dingtalk_link.last_pull_at,
                                    "last_payload": _json_dict(
                                        dingtalk_link.last_dingtalk_payload_json
                                    ),
                                    "last_error": dingtalk_link.last_error,
                                }
                                if dingtalk_link is not None
                                else None
                            ),
                        }
                        if todo is not None
                        else {"id": draft.todo_id, "missing": True}
                    ),
                    "follow_up": {
                        "id": draft.id,
                        "owner_user_id": draft.owner_user_id,
                        "owner_name": draft.owner_name,
                        "target_kind": draft.target_kind,
                        "target_conversation_id": draft.target_conversation_id,
                        "question_text": draft.question_text,
                        "risk_check": _risk_check(draft),
                    },
                    "delivery_evidence": additional_evidence,
                },
                ensure_ascii=False,
            ),
            "project_name": project.title if project is not None else "",
            "context": {
                "sender": "CEO follow-up dispatcher",
                "sender_user_id": "",
                "participants": [draft.owner_name] if draft.owner_name else [],
                "source_conversation_kind": (
                    "group" if draft.target_kind == "group" else "direct"
                ),
                "source_conversation_title": "",
            },
            "task_signals": {
                "possible_task_update": True,
                "mentions_follow_up": True,
                "signal_reason": reason,
            },
        }
    )
    return work_item, repair_source_ref


def _defer_follow_up_for_agent_review(
    store: AutoReplyStore,
    draft,
    *,
    now: str,
    reason: str,
    repair_source_ref: str = "",
    additional_evidence: dict[str, object] | None = None,
) -> bool:
    work_item, resolved_source_ref = _work_tracking_review_item(
        store,
        draft,
        now=now,
        reason=reason,
        repair_source_ref=repair_source_ref,
        additional_evidence=additional_evidence,
    )
    deferred = _defer_policy_follow_up(
        store,
        draft,
        now=now,
        reason=reason,
        detail={
            "agent_review_enqueued": True,
            "repair_source_ref": resolved_source_ref,
        },
    )
    if not deferred:
        return False
    store.enqueue_work_summary_input(
        source_type=work_item.source.type.value,
        source_ref=work_item.source.ref,
        payload_json=work_item.model_dump_json(),
    )
    return True


def _enqueue_prior_delivery_agent_review(
    store: AutoReplyStore,
    draft,
    attempt: dict[str, object],
    *,
    now: str,
) -> bool:
    attempt_revision = int(attempt.get("draft_revision") or 0)
    attempt_result = _json_dict(str(attempt.get("result_json") or "{}"))
    evidence = {
        "prior_revision": attempt_revision,
        "prior_idempotency_uuid": str(attempt.get("idempotency_uuid") or ""),
        "prior_attempt_state": str(attempt.get("state") or ""),
        "prior_send_result": attempt_result,
        "prior_delivered_text": str(attempt_result.get("delivered_text") or ""),
        "current_revision": draft.revision,
        "current_question_text": draft.question_text,
        "current_scheduled_at": draft.scheduled_at,
        "old_content_delivery_proven": True,
        "corrected_revision_exists": True,
    }
    source_ref = (
        f"follow-up-repair:{draft.id}:prior-delivery:{attempt_revision}:"
        f"current:{draft.revision}"
    )
    work_item, _ = _work_tracking_review_item(
        store,
        draft,
        now=now,
        reason=PRIOR_DELIVERY_REVIEW_REASON,
        repair_source_ref=source_ref,
        additional_evidence=evidence,
    )
    return store.enqueue_follow_up_delivery_review(
        draft_id=draft.id,
        draft_revision=attempt_revision,
        claim_token=str(attempt.get("claim_token") or ""),
        current_revision=draft.revision,
        source_type=work_item.source.type.value,
        source_ref=work_item.source.ref,
        payload_json=work_item.model_dump_json(),
    )


def _recover_prior_follow_up_send_attempt(
    store: AutoReplyStore,
    draft,
    *,
    now: str,
) -> bool:
    attempt = store.get_prior_unresolved_follow_up_send_attempt(
        draft_id=draft.id,
        before_revision=draft.revision,
    )
    if attempt is None:
        return False
    state = str(attempt.get("state") or "")
    if state == "sent":
        review_source_ref = str(attempt.get("review_source_ref") or "")
        if review_source_ref:
            review_status = store.get_work_summary_input_status(
                source_type="follow_up_completion_check",
                source_ref=review_source_ref,
            )
            if review_status in {"done", "discarded"}:
                return False
            if int(attempt.get("review_enqueued_revision") or 0) >= draft.revision:
                return True
        _enqueue_prior_delivery_agent_review(store, draft, attempt, now=now)
        return True
    return True


def _process_expired_follow_up_reconciliations(
    store: AutoReplyStore,
    dws,
    *,
    now: str,
    limit: int,
) -> set[int]:
    lease_owner = f"follow-up-reconciliation:{uuid4()}"
    attempts = store.claim_expired_follow_up_reconciliation_attempts(
        now=now,
        lease_owner=lease_owner,
        lease_until=_lease_until(now, FOLLOW_UP_RECONCILIATION_LEASE),
        limit=limit,
    )
    handled_draft_ids: set[int] = set()
    for attempt in attempts:
        draft_id = int(attempt.get("draft_id") or 0)
        draft = store.get_follow_up_draft(draft_id)
        if draft is None:
            payload = _json_dict(str(attempt.get("result_json") or "{}"))
            payload["reconciliation"] = {
                "state": "ambiguous",
                "reason": "follow-up draft is unavailable",
            }
            store.defer_unknown_follow_up_attempt(
                draft_id,
                draft_revision=int(attempt.get("draft_revision") or 0),
                claim_token=str(attempt.get("claim_token") or ""),
                lease_owner=lease_owner,
                now=now,
                lease_until=_lease_until(now, FOLLOW_UP_RECONCILIATION_DELAY),
                result_json=json.dumps(payload, ensure_ascii=False),
            )
            continue
        handled_draft_ids.add(draft.id)
        outcome = _reconcile_unknown_follow_up_attempt(
            store,
            dws,
            draft,
            attempt,
            now=now,
            reconciliation_owner=lease_owner,
        )
        attempt_revision = int(attempt.get("draft_revision") or 0)
        if outcome == "sent":
            delivered = store.get_follow_up_send_attempt(
                draft_id=draft.id,
                draft_revision=attempt_revision,
            )
            current = store.get_follow_up_draft(draft.id)
            current_result = (
                _json_dict(current.send_result_json) if current is not None else {}
            )
            exact_revision_finalized = (
                current is not None
                and current.status == "sent"
                and str(current_result.get("idempotency_uuid") or "")
                == str(attempt.get("idempotency_uuid") or "")
            )
            if (
                delivered is not None
                and current is not None
                and not exact_revision_finalized
            ):
                _enqueue_prior_delivery_agent_review(
                    store,
                    current,
                    delivered,
                    now=now,
                )
    return handled_draft_ids


def _owner_dingtalk_target(
    store: AutoReplyStore,
    dws,
    *,
    owner_user_id: str,
    fallback_name: str,
) -> tuple[str, str, str]:
    owner_user_id = owner_user_id.strip()
    fallback_name = fallback_name.strip()
    if not owner_user_id:
        return "", "", fallback_name
    cached = store.get_org_user_profile(owner_user_id)
    if cached is not None and (cached.open_dingtalk_id or cached.name):
        return owner_user_id, cached.open_dingtalk_id or "", (
            cached.name or fallback_name
        ).strip()
    profile = dws.get_user_profile(owner_user_id)
    return owner_user_id, profile.open_dingtalk_id or "", (
        profile.name or fallback_name
    ).strip()


def process_due_follow_ups(
    store: AutoReplyStore,
    dws,
    *,
    now: str,
    auto_send: bool,
    feedback_base_url: str = "",
    limit: int = 50,
    draft_ids: tuple[int, ...] | None = None,
) -> int:
    sent = 0
    reconciled_draft_ids = (
        _process_expired_follow_up_reconciliations(
            store,
            dws,
            now=now,
            limit=limit,
        )
        if auto_send
        else set()
    )
    if draft_ids is None:
        drafts = store.list_follow_up_drafts(
            statuses=("draft", "approved"),
            due_before=now,
            limit=limit,
        )
    else:
        current = _parse_follow_up_datetime(now)
        drafts = []
        for draft_id in draft_ids[:limit]:
            draft = store.get_follow_up_draft(draft_id)
            scheduled = (
                _parse_follow_up_datetime(draft.scheduled_at) if draft is not None else None
            )
            if (
                draft is not None
                and draft.status in {"draft", "approved"}
                and scheduled is not None
                and current is not None
                and scheduled <= current
            ):
                drafts.append(draft)
    for draft in drafts:
        if not auto_send:
            continue
        if draft.id in reconciled_draft_ids:
            continue
        if _recover_prior_follow_up_send_attempt(store, draft, now=now):
            continue
        if draft.suppressed_reason == PRIOR_DELIVERY_REVIEW_REASON:
            continue
        if _recover_follow_up_send_attempt(store, draft, now=now):
            continue
        if not _is_local_working_time(now):
            _defer_policy_follow_up(
                store,
                draft,
                now=now,
                reason="outside_local_working_hours",
                detail={
                    "local_timezone": str(LOCAL_WORK_TZ),
                    "next_scheduled_at": _next_local_work_start_utc(now),
                },
            )
            continue
        if not draft.owner_user_id.strip():
            _defer_follow_up_for_agent_review(
                store,
                draft,
                now=now,
                reason="owner_requires_agent_review",
            )
            continue
        sensitive = _is_sensitive_follow_up(draft)
        if (sensitive and draft.target_kind != "direct") or (
            draft.target_kind == "group"
            and not _is_open_conversation_id(draft.target_conversation_id)
        ):
            _defer_follow_up_for_agent_review(
                store,
                draft,
                now=now,
                reason="target_requires_agent_review",
            )
            continue
        if draft.todo_id <= 0 or store.get_work_todo(draft.todo_id) is None:
            _defer_follow_up_for_agent_review(
                store,
                draft,
                now=now,
                reason="todo_binding_requires_agent_review",
            )
            continue
        dingtalk_done, _ = refresh_dingtalk_todo_before_follow_up(
            store,
            dws,
            work_todo_id=draft.todo_id,
            now=now,
        )
        if dingtalk_done:
            continue
        completed, reason = _completion_supported_by_current_evidence(
            store,
            draft,
        )
        if completed:
            _skip_completed_follow_up(
                store,
                draft,
                now=now,
                reason=reason,
                completed=reason != "todo status is cancelled",
            )
            continue
        if (
            draft.suppressed_reason == "stale_follow_up_requires_agent_review"
            or _is_stale_follow_up(draft.scheduled_at, now)
        ):
            _defer_follow_up_for_agent_review(
                store,
                draft,
                now=now,
                reason="stale_follow_up_requires_agent_review",
            )
            continue
        claim_token = ""
        lease_owner = ""
        sending = False
        revision_uuid = ""
        try:
            owner_user_id, open_dingtalk_id, at_name = _owner_dingtalk_target(
                store,
                dws,
                owner_user_id=draft.owner_user_id,
                fallback_name=draft.owner_name,
            )
            if not owner_user_id:
                _defer_follow_up_for_agent_review(
                    store,
                    draft,
                    now=now,
                    reason="owner_requires_agent_review",
                )
                continue
            group_conversation_id = draft.target_conversation_id.strip()
            send_to_group = draft.target_kind == "group"
            at_users = (
                [owner_user_id]
                if send_to_group and owner_user_id
                else []
            )
            at_open_dingtalk_ids = [open_dingtalk_id] if open_dingtalk_id else []
            at_open_dingtalk_names = [at_name] if at_name else []
            original_text = _follow_up_message_text(store, draft)
            revision_uuid = _follow_up_revision_uuid(draft, original_text)
            outgoing_text = prepare_outgoing_reply_text(
                reply_text=original_text,
                original_text=original_text,
                feedback_base_url=feedback_base_url,
                feedback_token=f"spike_{revision_uuid.replace('-', '')}",
            )
            question_text = outgoing_text.text
            feedback_token = outgoing_text.feedback_token
            claim_token = str(uuid4())
            lease_owner = f"follow-up-dispatch:{uuid4()}"
            if not store.claim_follow_up_draft_revision(
                draft.id,
                expected_revision=draft.revision,
                claim_token=claim_token,
                idempotency_uuid=revision_uuid,
                lease_owner=lease_owner,
                claimed_at=now,
                lease_until=_lease_until(now, FOLLOW_UP_SEND_LEASE),
            ):
                # A correction won the revision race; leave its revision queued.
                store.get_follow_up_draft(draft.id)
                continue
            if not store.transition_follow_up_attempt_to_sending(
                draft.id,
                claimed_revision=draft.revision,
                claim_token=claim_token,
                lease_owner=lease_owner,
                now=now,
                lease_until=_lease_until(now, FOLLOW_UP_SEND_LEASE),
            ):
                continue
            sending = True
            if send_to_group:
                result = dws.send_message(
                    group_conversation_id,
                    question_text,
                    at_users=at_users,
                    at_open_dingtalk_ids=at_open_dingtalk_ids,
                    at_open_dingtalk_names=at_open_dingtalk_names,
                    idempotency_uuid=revision_uuid,
                )
            else:
                result = dws.send_message(
                    None,
                    question_text,
                    at_open_dingtalk_ids=at_open_dingtalk_ids,
                    user_id=None if open_dingtalk_id else owner_user_id or None,
                    open_dingtalk_id=open_dingtalk_id or None,
                    idempotency_uuid=revision_uuid,
                )
            persisted_send_result = json.dumps(
                {
                    "send_result": result or {},
                    "delivered_text": question_text,
                    "claimed_revision": draft.revision,
                    "idempotency_uuid": revision_uuid,
                },
                ensure_ascii=False,
            )
            store.record_follow_up_sending_result(
                draft.id,
                draft_revision=draft.revision,
                claim_token=claim_token,
                lease_owner=lease_owner,
                now=now,
                result_json=persisted_send_result,
            )
        except Exception as exc:
            if sending and (
                _dws_send_outcome_is_unknown(exc)
                or is_external_dependency_error(exc)
            ):
                unknown_result_json = json.dumps(
                    {
                        "recoverable": True,
                        "reason": "dws_send_outcome_unknown",
                        "error": str(exc),
                        "claimed_revision": draft.revision,
                        "idempotency_uuid": revision_uuid,
                    },
                    ensure_ascii=False,
                )
                store.mark_follow_up_sending_unknown(
                    draft.id,
                    draft_revision=draft.revision,
                    claim_token=claim_token,
                    lease_owner=lease_owner,
                    lease_until=_lease_until(now, FOLLOW_UP_RECONCILIATION_DELAY),
                    result_json=unknown_result_json,
                )
                store.record_error(
                    draft.target_conversation_id,
                    None,
                    "follow_up",
                    str(exc),
                )
                continue
            if sending and isinstance(exc, DwsError) and exc.needs_login:
                retryable_result_json = json.dumps(
                    {
                        "recoverable": True,
                        "reason": "dws_login_required",
                        "error": str(exc),
                        "claimed_revision": draft.revision,
                        "idempotency_uuid": revision_uuid,
                    },
                    ensure_ascii=False,
                )
                store.mark_follow_up_sending_retryable(
                    draft.id,
                    draft_revision=draft.revision,
                    claim_token=claim_token,
                    lease_owner=lease_owner,
                    result_json=retryable_result_json,
                )
                store.record_error(
                    draft.target_conversation_id,
                    None,
                    "follow_up",
                    str(exc),
                )
                continue
            if (isinstance(exc, DwsError) and exc.needs_login) or (
                is_external_dependency_error(exc)
            ):
                reason = (
                    "dws_login_required"
                    if isinstance(exc, DwsError) and exc.needs_login
                    else "external_dependency_unavailable"
                )
                _defer_recoverable_follow_up(
                    store,
                    draft,
                    now=now,
                    reason=reason,
                    error=str(exc),
                    claim_token=claim_token,
                    idempotency_uuid=revision_uuid,
                    lease_owner=lease_owner,
                )
                store.record_error(
                    draft.target_conversation_id,
                    None,
                    "follow_up",
                    str(exc),
                )
                continue
            if _dws_send_outcome_is_unknown(exc):
                _defer_recoverable_follow_up(
                    store,
                    draft,
                    now=now,
                    reason="dws_send_outcome_unknown",
                    error=str(exc),
                    claim_token=claim_token,
                    idempotency_uuid=revision_uuid,
                    lease_owner=lease_owner,
                )
                store.record_error(
                    draft.target_conversation_id,
                    None,
                    "follow_up",
                    str(exc),
                )
                continue
            if claim_token:
                failed_result_json = json.dumps(
                    {
                        "error": str(exc),
                        "claimed_revision": draft.revision,
                        "idempotency_uuid": revision_uuid,
                    },
                    ensure_ascii=False,
                )
                store.update_claimed_follow_up_draft(
                    draft.id,
                    claimed_revision=draft.revision,
                    claim_token=claim_token,
                    lease_owner=lease_owner,
                    now=now,
                    attempt_state="failed",
                    attempt_result_json=failed_result_json,
                    status="failed",
                    send_result_json=failed_result_json,
                )
            else:
                store.update_follow_up_draft_if_revision(
                    draft.id,
                    draft.revision,
                    status="failed",
                    send_result_json=json.dumps(
                        {"error": str(exc)},
                        ensure_ascii=False,
                    ),
                )
            store.record_error(
                draft.target_conversation_id,
                None,
                "follow_up",
                str(exc),
            )
            continue
        sent_result_json = json.dumps(
            {
                "owner_user_id": owner_user_id,
                "at_users": at_users,
                "at_open_dingtalk_ids": at_open_dingtalk_ids,
                "at_open_dingtalk_names": at_open_dingtalk_names,
                "feedback_token": feedback_token,
                "sensitive": sensitive,
                "target_kind_used": "group" if send_to_group else "direct",
                "claimed_revision": draft.revision,
                "idempotency_uuid": revision_uuid,
                "send_result": result or {},
                "delivered_text": question_text,
            },
            ensure_ascii=False,
        )
        finalized = store.update_claimed_follow_up_draft(
            draft.id,
            claimed_revision=draft.revision,
            claim_token=claim_token,
            lease_owner=lease_owner,
            now=now,
            attempt_state="sent",
            attempt_result_json=sent_result_json,
            status="sent",
            send_result_json=sent_result_json,
            evidence_check_json=json.dumps(
                {
                    "checked_at": now,
                    "completion_supported": False,
                    "sensitive": sensitive,
                },
                ensure_ascii=False,
            ),
            sent_at=now,
        )
        if finalized:
            sent += 1
            continue
        delivered = store.get_follow_up_send_attempt(
            draft_id=draft.id,
            draft_revision=draft.revision,
        )
        current = store.get_follow_up_draft(draft.id)
        if (
            delivered is not None
            and delivered.get("state") == "sent"
            and delivered.get("idempotency_uuid") == revision_uuid
            and current is not None
            and current.revision > draft.revision
        ):
            _enqueue_prior_delivery_agent_review(
                store,
                current,
                delivered,
                now=now,
            )
    return sent
