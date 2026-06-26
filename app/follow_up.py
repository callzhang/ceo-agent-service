import json
from datetime import datetime, timedelta, timezone

from app.dws_client import DwsError
from app.feedback_spike import prepare_outgoing_reply_text
from app.store import AutoReplyStore
from app.task_models import ProjectStatus, TodoStatus


MAX_FOLLOW_UP_AGE_SECONDS = 7 * 24 * 60 * 60
RECOVERABLE_AUTH_RETRY_DELAY = timedelta(minutes=15)
FOLLOW_UP_REACTION_LOOKBACK_SECONDS = 2 * 24 * 60 * 60
MAX_FOLLOW_UPS_PER_OWNER_PER_DAY = 3
MAX_FOLLOW_UPS_PER_GROUP_PER_DAY = 8

COMPLETION_REACTION_PHRASES = (
    "完成了",
    "已完成",
    "已经完成",
    "已发",
    "已结束",
    "已经结束",
)
REDIRECT_REACTION_PHRASES = (
    "请联系",
    "负责整理",
    "不是我负责",
    "没法获取",
    "权限范围",
)
SOURCE_REQUEST_REACTION_PHRASES = (
    "看了什么材料",
    "什么材料",
    "当前背景",
    "这个是什么",
    "什么需求",
)
NEGATIVE_REACTION_PHRASES = (
    "乱发消息",
    "乱发",
    "很懵",
    "懵逼",
    "分发新任务",
    "谁接茬谁接活",
)


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


def _start_of_day(now: str) -> str:
    current = _parse_follow_up_datetime(now) or datetime.now(timezone.utc).replace(
        tzinfo=None
    )
    return current.replace(hour=0, minute=0, second=0, microsecond=0).strftime(
        "%Y-%m-%d %H:%M:%S"
    )


def _tomorrow_morning(now: str) -> str:
    current = _parse_follow_up_datetime(now) or datetime.now(timezone.utc).replace(
        tzinfo=None
    )
    tomorrow = current + timedelta(days=1)
    return tomorrow.replace(hour=9, minute=0, second=0, microsecond=0).strftime(
        "%Y-%m-%d %H:%M:%S"
    )


def _reaction_status_for_text(text: str) -> tuple[str, str]:
    compact = " ".join(text.strip().split())
    if not compact:
        return "", ""
    for phrase in NEGATIVE_REACTION_PHRASES:
        if phrase in compact:
            return "negative", compact
    for phrase in SOURCE_REQUEST_REACTION_PHRASES:
        if phrase in compact:
            return "asks_source", compact
    for phrase in REDIRECT_REACTION_PHRASES:
        if phrase in compact:
            return "redirect_owner", compact
    for phrase in COMPLETION_REACTION_PHRASES:
        if phrase in compact:
            return "completed", compact
    return "", ""


def _reaction_evidence_for_draft(store: AutoReplyStore, draft) -> tuple[str, str, str]:
    if draft.sent_at.strip():
        since = draft.sent_at
    elif draft.scheduled_at.strip():
        since = draft.scheduled_at
    else:
        since = draft.created_at
    if not draft.target_conversation_id.strip() or not since.strip():
        return "", "", ""
    for attempt in store.list_recent_reply_attempts_for_follow_up(
        conversation_id=draft.target_conversation_id,
        since=since,
        limit=30,
    ):
        status, summary = _reaction_status_for_text(attempt.trigger_text)
        if status:
            return status, summary, f"reply_attempt:{attempt.id}"
    return "", "", ""


def _refresh_recent_sent_reactions(store: AutoReplyStore, *, now: str) -> None:
    current = _parse_follow_up_datetime(now) or datetime.now(timezone.utc).replace(
        tzinfo=None
    )
    since = (current - timedelta(seconds=FOLLOW_UP_REACTION_LOOKBACK_SECONDS)).strftime(
        "%Y-%m-%d %H:%M:%S"
    )
    for draft in store.list_sent_follow_ups_since(since, limit=100):
        if draft.reaction_status:
            continue
        status, summary, source = _reaction_evidence_for_draft(store, draft)
        if not status:
            continue
        store.update_follow_up_draft(
            draft.id,
            reaction_status=status,
            reaction_summary=summary,
            evidence_check_json=json.dumps(
                {
                    "reaction_source": source,
                    "reaction_status": status,
                    "reaction_summary": summary,
                    "checked_at": now,
                },
                ensure_ascii=False,
            ),
        )
        if status == "completed" and draft.todo_id > 0:
            todo = store.get_work_todo(draft.todo_id)
            if todo is not None and str(todo.status) != TodoStatus.DONE.value:
                store.update_work_todo(
                    draft.todo_id,
                    status=TodoStatus.DONE.value,
                    completion_evidence_json=json.dumps(
                        {
                            "source": source,
                            "summary": summary,
                            "follow_up_id": draft.id,
                            "checked_at": now,
                        },
                        ensure_ascii=False,
                    ),
                )


def _risk_check(draft) -> dict:
    return _json_dict(draft.risk_check_json)


def _is_sensitive_follow_up(project, draft) -> bool:
    risk_check = _risk_check(draft)
    if bool(risk_check.get("sensitive")):
        return True
    return project is not None and str(project.category) == "HR"


def _source_context_prefix(project, todo) -> str:
    parts: list[str] = []
    if project is not None and project.title.strip():
        parts.append(f"项目「{project.title.strip()}」")
    if todo is not None and todo.title.strip():
        parts.append(f"TODO「{todo.title.strip()}」")
    if not parts:
        return ""
    return f"基于{' / '.join(parts)}的未完成事项：\n"


def _follow_up_message_text(store: AutoReplyStore, draft) -> str:
    project = store.get_work_project(draft.project_id)
    todo = store.get_work_todo(draft.todo_id) if draft.todo_id > 0 else None
    text = draft.question_text.strip()
    if text.startswith("基于"):
        return text
    return f"{_source_context_prefix(project, todo)}{text}".strip()


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
    status, summary, source = _reaction_evidence_for_draft(store, draft)
    if status == "completed":
        store.update_work_todo(
            draft.todo_id,
            status=TodoStatus.DONE.value,
            completion_evidence_json=json.dumps(
                {
                    "source": source,
                    "summary": summary,
                    "follow_up_id": draft.id,
                },
                ensure_ascii=False,
            ),
        )
        return True, f"completion reaction: {summary}"
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


def _defer_policy_follow_up(
    store: AutoReplyStore,
    draft,
    *,
    now: str,
    reason: str,
    detail: dict,
) -> None:
    store.update_follow_up_draft(
        draft.id,
        status="draft",
        scheduled_at=_tomorrow_morning(now),
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


def _skip_policy_follow_up(
    store: AutoReplyStore,
    draft,
    *,
    now: str,
    reason: str,
    detail: dict,
) -> None:
    store.update_follow_up_draft(
        draft.id,
        status="skipped",
        sent_at=now,
        suppressed_reason=reason,
        send_result_json=json.dumps(
            {
                "skipped": True,
                "reason": reason,
                "checked_at": now,
                **detail,
            },
            ensure_ascii=False,
        ),
    )


def _recent_reaction_should_suppress(
    store: AutoReplyStore,
    draft,
    *,
    now: str,
) -> tuple[bool, str, dict]:
    since = _start_of_day(now)
    for previous in store.list_recent_follow_up_reactions(
        project_id=draft.project_id,
        owner_user_id=draft.owner_user_id,
        since=since,
        limit=5,
    ):
        if previous.reaction_status in {
            "negative",
            "asks_source",
            "redirect_owner",
            "confused",
        }:
            return True, f"recent_reaction_{previous.reaction_status}", {
                "previous_follow_up_id": previous.id,
                "reaction_summary": previous.reaction_summary,
            }
    return False, "", {}


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
    _refresh_recent_sent_reactions(store, now=now)
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
        status, summary, source = _reaction_evidence_for_draft(store, draft)
        if status in {"negative", "asks_source", "redirect_owner"}:
            _skip_policy_follow_up(
                store,
                draft,
                now=now,
                reason=f"reaction_{status}",
                detail={"reaction_summary": summary, "reaction_source": source},
            )
            continue
        suppress, suppress_reason, suppress_detail = _recent_reaction_should_suppress(
            store,
            draft,
            now=now,
        )
        if suppress:
            _skip_policy_follow_up(
                store,
                draft,
                now=now,
                reason=suppress_reason,
                detail=suppress_detail,
            )
            continue
        try:
            owner_user_id, open_dingtalk_id, at_name = _owner_dingtalk_target(
                store,
                dws,
                owner_user_id=draft.owner_user_id,
                fallback_name=draft.owner_name,
            )
            if not owner_user_id:
                raise ValueError(
                    f"follow-up owner is not resolvable: {draft.owner_name}"
                )
            day_start = _start_of_day(now)
            owner_sent_today = store.count_sent_follow_ups_for_owner_since(
                owner_user_id,
                day_start,
            )
            if owner_sent_today >= MAX_FOLLOW_UPS_PER_OWNER_PER_DAY:
                _defer_policy_follow_up(
                    store,
                    draft,
                    now=now,
                    reason="owner_daily_cap",
                    detail={
                        "owner_user_id": owner_user_id,
                        "sent_today": owner_sent_today,
                        "cap": MAX_FOLLOW_UPS_PER_OWNER_PER_DAY,
                    },
                )
                continue
            project = store.get_work_project(draft.project_id)
            sensitive = _is_sensitive_follow_up(project, draft)
            send_to_group = (
                draft.target_kind == "group"
                and bool(draft.target_conversation_id)
                and not sensitive
            )
            if send_to_group:
                group_sent_today = store.count_sent_follow_ups_for_conversation_since(
                    draft.target_conversation_id,
                    day_start,
                )
                if group_sent_today >= MAX_FOLLOW_UPS_PER_GROUP_PER_DAY:
                    _defer_policy_follow_up(
                        store,
                        draft,
                        now=now,
                        reason="group_daily_cap",
                        detail={
                            "target_conversation_id": draft.target_conversation_id,
                            "sent_today": group_sent_today,
                            "cap": MAX_FOLLOW_UPS_PER_GROUP_PER_DAY,
                        },
                    )
                    continue
            at_users = (
                [owner_user_id]
                if send_to_group and owner_user_id
                else []
            )
            at_open_dingtalk_ids = [open_dingtalk_id] if open_dingtalk_id else []
            at_open_dingtalk_names = [at_name] if at_name else []
            original_text = _follow_up_message_text(store, draft)
            outgoing_text = prepare_outgoing_reply_text(
                reply_text=original_text,
                original_text=original_text,
                feedback_base_url=feedback_base_url,
            )
            question_text = outgoing_text.text
            feedback_token = outgoing_text.feedback_token
            if send_to_group:
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
                    user_id=None if open_dingtalk_id else owner_user_id or None,
                    open_dingtalk_id=open_dingtalk_id or None,
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
                    "sensitive": sensitive,
                    "target_kind_used": "group" if send_to_group else "direct",
                    "send_result": result or {},
                },
                ensure_ascii=False,
            ),
            evidence_check_json=json.dumps(
                {
                    "checked_at": now,
                    "completion_supported": False,
                    "reaction_status": status,
                    "reaction_summary": summary,
                    "sensitive": sensitive,
                },
                ensure_ascii=False,
            ),
            sent_at=now,
        )
        sent += 1
    return sent
