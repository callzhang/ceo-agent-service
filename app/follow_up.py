import json
from datetime import datetime, timedelta, timezone

from app.dws_client import DwsError
from app.feedback_spike import prepare_outgoing_reply_text
from app.store import AutoReplyStore
from app.task_models import ProjectStatus, TodoStatus


MAX_FOLLOW_UP_AGE_SECONDS = 7 * 24 * 60 * 60
RECOVERABLE_AUTH_RETRY_DELAY = timedelta(minutes=15)


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


def _completion_supported_by_current_evidence(store: AutoReplyStore, draft) -> tuple[bool, str]:
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
    if _has_completion_evidence(todo.completion_evidence_json):
        return True, "todo has completion evidence"
    return False, ""


def _skip_completed_follow_up(store: AutoReplyStore, draft, *, now: str, reason: str) -> None:
    store.update_follow_up_draft(
        draft.id,
        status="skipped",
        sent_at=now,
        send_result_json=json.dumps(
            {
                "skipped": True,
                "reason": reason,
                "evidence_check": "completion_supported",
            },
            ensure_ascii=False,
        ),
    )


def _skip_stale_follow_up(store: AutoReplyStore, draft, *, now: str) -> None:
    store.update_follow_up_draft(
        draft.id,
        status="skipped",
        sent_at=now,
        send_result_json=json.dumps(
            {
                "skipped": True,
                "reason": "stale_due_follow_up",
                "scheduled_at": draft.scheduled_at,
                "max_age_days": 7,
            },
            ensure_ascii=False,
        ),
    )


def _recoverable_retry_at(now: str) -> str:
    current = _parse_follow_up_datetime(now) or datetime.now(timezone.utc).replace(
        tzinfo=None
    )
    return (current + RECOVERABLE_AUTH_RETRY_DELAY).strftime("%Y-%m-%d %H:%M:%S")


def _defer_recoverable_follow_up(
    store: AutoReplyStore,
    draft,
    *,
    now: str,
    reason: str,
    error: str,
) -> None:
    store.update_follow_up_draft(
        draft.id,
        status="draft",
        scheduled_at=_recoverable_retry_at(now),
        send_result_json=json.dumps(
            {
                "recoverable": True,
                "reason": reason,
                "error": error,
                "retry_delay_minutes": int(
                    RECOVERABLE_AUTH_RETRY_DELAY.total_seconds() // 60
                ),
            },
            ensure_ascii=False,
        ),
    )


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
        if not fallback_name:
            return "", "", ""
        cached_profiles = store.find_org_users_by_name(fallback_name)
        if len(cached_profiles) == 1:
            cached = cached_profiles[0]
            return (
                cached.user_id,
                cached.open_dingtalk_id or "",
                (cached.name or fallback_name).strip(),
            )
        profiles = dws.search_user_profiles(fallback_name)
        if len(profiles) != 1:
            return "", "", fallback_name
        profile = profiles[0]
        return (
            profile.user_id,
            profile.open_dingtalk_id or "",
            (profile.name or fallback_name).strip(),
        )
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
) -> int:
    sent = 0
    drafts = store.list_follow_up_drafts(
        statuses=("draft", "approved"),
        due_before=now,
        limit=limit,
    )
    for draft in drafts:
        if not auto_send:
            continue
        if _is_stale_follow_up(draft.scheduled_at, now):
            _skip_stale_follow_up(store, draft, now=now)
            continue
        completed, reason = _completion_supported_by_current_evidence(store, draft)
        if completed:
            _skip_completed_follow_up(store, draft, now=now, reason=reason)
            continue
        try:
            owner_user_id, open_dingtalk_id, at_name = _owner_dingtalk_target(
                store,
                dws,
                owner_user_id=draft.owner_user_id,
                fallback_name=draft.owner_name,
            )
            if draft.target_kind == "group" and not owner_user_id:
                raise ValueError(
                    f"follow-up owner is not resolvable: {draft.owner_name}"
                )
            at_users = (
                [owner_user_id]
                if draft.target_kind == "group" and owner_user_id
                else []
            )
            at_open_dingtalk_ids = [open_dingtalk_id] if open_dingtalk_id else []
            at_open_dingtalk_names = [at_name] if at_name else []
            outgoing_text = prepare_outgoing_reply_text(
                reply_text=draft.question_text,
                original_text=draft.question_text,
                feedback_base_url=feedback_base_url,
            )
            question_text = outgoing_text.text
            feedback_token = outgoing_text.feedback_token
            if draft.target_kind == "group" and draft.target_conversation_id:
                result = dws.send_message(
                    draft.target_conversation_id,
                    question_text,
                    at_users=at_users,
                    at_open_dingtalk_ids=at_open_dingtalk_ids,
                    at_open_dingtalk_names=at_open_dingtalk_names,
                )
            else:
                result = dws.send_message(
                    None,
                    question_text,
                    at_open_dingtalk_ids=at_open_dingtalk_ids,
                    user_id=owner_user_id or None,
                )
        except Exception as exc:
            if isinstance(exc, DwsError) and exc.needs_login:
                _defer_recoverable_follow_up(
                    store,
                    draft,
                    now=now,
                    reason="dws_login_required",
                    error=str(exc),
                )
                store.record_error(
                    draft.target_conversation_id,
                    None,
                    "follow_up",
                    str(exc),
                )
                continue
            store.update_follow_up_draft(
                draft.id,
                status="failed",
                send_result_json=json.dumps({"error": str(exc)}, ensure_ascii=False),
            )
            store.record_error(
                draft.target_conversation_id,
                None,
                "follow_up",
                str(exc),
            )
            continue
        store.update_follow_up_draft(
            draft.id,
            status="sent",
            send_result_json=json.dumps(
                {
                    "owner_user_id": owner_user_id,
                    "at_users": at_users,
                    "at_open_dingtalk_ids": at_open_dingtalk_ids,
                    "at_open_dingtalk_names": at_open_dingtalk_names,
                    "feedback_token": feedback_token,
                    "send_result": result or {},
                },
                ensure_ascii=False,
            ),
            sent_at=now,
        )
        sent += 1
    return sent
