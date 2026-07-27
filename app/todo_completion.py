import json
from datetime import datetime, timedelta, timezone
from typing import Any

from app.store import AutoReplyStore
from app.task_models import TodoStatus, WorkItem

FOLLOW_UP_COMPLETION_CHECK_SCANNER = "follow_up_completion_check"
FOLLOW_UP_COMPLETION_CHECK_LOOKBACK_DAYS = 14


def close_todo_with_completion_evidence(
    store: AutoReplyStore,
    *,
    todo_id: int,
    evidence: dict[str, Any],
    now: str,
    source_type: str,
    source_ref: str,
    merge_reason: str,
    confidence: float = 1.0,
) -> bool:
    todo = store.get_work_todo(todo_id)
    if todo is None or str(todo.status) == TodoStatus.DONE.value:
        return False
    normalized_evidence = _completion_evidence(evidence, now=now)
    store.update_work_todo(
        todo.id,
        status=TodoStatus.DONE.value,
        completion_evidence_json=json.dumps(normalized_evidence, ensure_ascii=False),
        completed_at=now,
    )
    complete_follow_ups_for_todo(
        store,
        todo_id=todo.id,
        evidence=normalized_evidence,
        now=now,
    )
    store.create_work_update(
        project_id=todo.project_id,
        source_type=source_type,
        source_ref=source_ref,
        summary=f"Todo completed: {todo.title}",
        changes_json=json.dumps(
            {
                "todo_id": todo.id,
                "status": TodoStatus.DONE.value,
                "completion_evidence": normalized_evidence,
            },
            ensure_ascii=False,
        ),
        merge_reason=merge_reason,
        confidence=confidence,
    )
    return True


def complete_follow_ups_for_todo(
    store: AutoReplyStore,
    *,
    todo_id: int,
    evidence: dict[str, Any],
    now: str,
) -> int:
    normalized_evidence = _completion_evidence(evidence, now=now)
    completed = 0
    for draft in store.list_follow_up_drafts_for_todo(
        todo_id,
        statuses=("draft", "approved", "sent"),
    ):
        payload = {
            "completed": True,
            "reason": normalized_evidence["reason"],
            "source": normalized_evidence["source"],
            "checked_at": now,
            "completion_evidence": normalized_evidence,
        }
        store.update_follow_up_draft(
            draft.id,
            status="completed",
            evidence_check_json=json.dumps(payload, ensure_ascii=False),
            send_result_json=json.dumps(payload, ensure_ascii=False),
            suppressed_reason=normalized_evidence["reason"],
        )
        completed += 1
    return completed


def enqueue_follow_up_completion_checks(
    store: AutoReplyStore,
    dws: Any,
    *,
    now: str,
    limit: int = 1,
) -> int:
    if limit <= 0:
        return 0
    checked = 0
    for draft in _completion_check_candidates(store, now=now):
        if checked >= limit:
            break
        todo = store.get_work_todo(draft.todo_id)
        if todo is None:
            continue
        if str(todo.status) == TodoStatus.DONE.value:
            evidence = _completion_evidence_from_todo(todo, now=now)
            complete_follow_ups_for_todo(
                store,
                todo_id=todo.id,
                evidence=evidence,
                now=now,
            )
            checked += 1
            continue
        sources = _external_completion_sources(store, dws, draft, todo, now=now)
        source_ref = f"follow-up:{draft.id}:{_date_part(now)}"
        if sources:
            work_item = _completion_check_work_item(
                store,
                draft=draft,
                todo=todo,
                source_ref=source_ref,
                sources=sources,
                now=now,
            )
            store.enqueue_work_summary_input(
                source_type=work_item.source.type.value,
                source_ref=work_item.source.ref,
                payload_json=work_item.model_dump_json(),
            )
        _record_completion_check(
            store,
            draft_id=draft.id,
            now=now,
            source_ref=source_ref,
            sources=sources,
            enqueued=bool(sources),
        )
        checked += 1
    store.set_daily_scan_state(
        FOLLOW_UP_COMPLETION_CHECK_SCANNER,
        last_success_at=now,
        cursor_json=json.dumps({"last_checked_at": now}, sort_keys=True),
        last_error="",
    )
    return checked


def _completion_evidence(evidence: dict[str, Any], *, now: str) -> dict[str, Any]:
    source = str(evidence.get("source") or "").strip()
    reason = str(
        evidence.get("reason")
        or evidence.get("description")
        or evidence.get("summary")
        or ""
    ).strip()
    completed_at = str(evidence.get("completed_at") or now).strip()
    normalized = dict(evidence)
    normalized["source"] = source or "unknown"
    normalized["reason"] = reason or "completion confirmed"
    normalized["completed_at"] = completed_at
    normalized.setdefault("checked_at", now)
    return normalized


def _completion_evidence_from_todo(todo: Any, *, now: str) -> dict[str, Any]:
    try:
        evidence = json.loads(todo.completion_evidence_json or "{}")
    except json.JSONDecodeError:
        evidence = {
            "source": "work_todo",
            "reason": todo.completion_evidence_json,
        }
    if not isinstance(evidence, dict):
        evidence = {"source": "work_todo", "reason": str(evidence)}
    return _completion_evidence(evidence, now=now)


def _completion_check_candidates(store: AutoReplyStore, *, now: str):
    today = _date_part(now)
    for draft in store.list_follow_up_drafts(
        statuses=("draft", "approved", "sent"),
        limit=500,
    ):
        if draft.todo_id <= 0:
            continue
        if _last_completion_check_date(draft.evidence_check_json) == today:
            continue
        yield draft


def _last_completion_check_date(value: str) -> str:
    try:
        payload = json.loads(value or "{}")
    except json.JSONDecodeError:
        return ""
    checked_at = str(
        payload.get("completion_check_checked_at")
        or payload.get("checked_at")
        or ""
    )
    return _date_part(checked_at)


def _external_completion_sources(
    store: AutoReplyStore,
    dws: Any,
    draft: Any,
    todo: Any,
    *,
    now: str,
) -> list[dict[str, Any]]:
    query = _completion_query(store, draft, todo)
    if not query:
        return []
    sources: list[dict[str, Any]] = []
    sources.extend(_search_dws_messages(dws, query=query, draft=draft, now=now))
    sources.extend(_search_recent_minutes(dws, query=query, now=now))
    return sources[:8]


def _completion_query(store: AutoReplyStore, draft: Any, todo: Any) -> str:
    project = store.get_work_project(todo.project_id)
    parts = [
        getattr(project, "title", "") if project is not None else "",
        todo.title,
        todo.description,
        draft.title,
        draft.question_text,
    ]
    return " ".join(part.strip() for part in parts if str(part or "").strip())[:240]


def _search_dws_messages(
    dws: Any,
    *,
    query: str,
    draft: Any,
    now: str,
) -> list[dict[str, Any]]:
    search = getattr(dws, "search_messages", None)
    if search is None:
        return []
    start = _lookback_start(draft.sent_at or draft.scheduled_at or now, now=now)
    try:
        messages = search(query, start=start, end=now, limit=5)
    except Exception as exc:
        return [{"type": "dws_message_search_error", "error": str(exc)}]
    results = []
    for message in messages[:5]:
        results.append(
            {
                "type": "dws_message",
                "source": f"dws_message:{getattr(message, 'open_message_id', '')}",
                "conversation_id": getattr(message, "open_conversation_id", ""),
                "conversation_title": getattr(message, "conversation_title", ""),
                "sender": getattr(message, "sender_name", ""),
                "created_at": getattr(message, "create_time", ""),
                "content": getattr(message, "content", ""),
            }
        )
    return results


def _search_recent_minutes(dws: Any, *, query: str, now: str) -> list[dict[str, Any]]:
    list_minutes = getattr(dws, "list_minutes", None)
    if list_minutes is None:
        return []
    start = _lookback_start(now, now=now)
    try:
        minutes_items = list_minutes(scope="all", limit=20, start=start, end=now)
    except Exception as exc:
        return [{"type": "dws_minutes_search_error", "error": str(exc)}]
    results = []
    normalized_query = _compact_text(query)
    for item in minutes_items:
        title = str(item.get("title") or "").strip()
        payload_text = json.dumps(item, ensure_ascii=False)
        if normalized_query and normalized_query not in _compact_text(payload_text):
            continue
        minutes_id = str(
            item.get("taskUuid")
            or item.get("minutesId")
            or item.get("id")
            or item.get("uuid")
            or ""
        ).strip()
        results.append(
            {
                "type": "dws_minutes",
                "source": f"dws_minutes:{minutes_id}",
                "title": title,
                "created_at": str(
                    item.get("createdAt")
                    or item.get("startTimeISO")
                    or item.get("startTime")
                    or ""
                ),
                "summary": payload_text[:3000],
            }
        )
        if len(results) >= 3:
            break
    return results


def _completion_check_work_item(
    store: AutoReplyStore,
    *,
    draft: Any,
    todo: Any,
    source_ref: str,
    sources: list[dict[str, Any]],
    now: str,
) -> WorkItem:
    project = store.get_work_project(todo.project_id)
    project_title = project.title if project is not None else ""
    summary = {
        "instruction": (
            "这是 follow-up 完成状态的每日检查。请判断 sources 是否明确证明 "
            "TODO 已完成；只有证据明确完成时才 close TODO，并在 "
            "completion_evidence 写完整 reason/source/completed_at。"
        ),
        "project": {"id": todo.project_id, "title": project_title},
        "todo": {
            "id": todo.id,
            "title": todo.title,
            "description": todo.description,
            "owner_user_id": todo.owner_user_id,
            "owner_name": todo.owner_name,
            "status": todo.status,
            "deadline_at": todo.deadline_at,
        },
        "follow_up": {
            "id": draft.id,
            "title": draft.title,
            "question_text": draft.question_text,
            "sent_at": draft.sent_at,
            "scheduled_at": draft.scheduled_at,
        },
        "sources": sources,
    }
    return WorkItem.model_validate(
        {
            "source": {
                "type": "follow_up_completion_check",
                "ref": source_ref,
                "title": f"Follow-up completion check #{draft.id}",
                "conversation_id": draft.target_conversation_id,
                "conversation_title": "",
                "created_at": now,
            },
            "summary": json.dumps(summary, ensure_ascii=False),
            "project_name": project_title,
            "context": {
                "sender": "CEO task completion checker",
                "sender_user_id": "",
                "participants": [draft.owner_name or todo.owner_name],
                "source_conversation_kind": (
                    "group" if draft.target_kind == "group" else "direct"
                ),
                "source_conversation_title": "",
            },
            "task_signals": {
                "possible_task_update": True,
                "mentions_follow_up": True,
                "progress_claim": True,
                "signal_reason": "daily follow-up completion evidence check",
            },
        }
    )


def _record_completion_check(
    store: AutoReplyStore,
    *,
    draft_id: int,
    now: str,
    source_ref: str,
    sources: list[dict[str, Any]],
    enqueued: bool,
) -> None:
    payload = {
        "completed": False,
        "reason": "completion_not_confirmed_by_automatic_check",
        "source": source_ref,
        "completion_check_checked_at": now,
        "sources": sources,
        "work_item_enqueued": enqueued,
    }
    store.update_follow_up_draft(
        draft_id,
        evidence_check_json=json.dumps(payload, ensure_ascii=False),
    )


def _lookback_start(value: str, *, now: str) -> str:
    parsed = _parse_datetime(value) or _parse_datetime(now) or datetime.now(timezone.utc)
    start = parsed - timedelta(days=FOLLOW_UP_COMPLETION_CHECK_LOOKBACK_DAYS)
    return start.isoformat()


def _parse_datetime(value: str) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        try:
            parsed = datetime.strptime(text, "%Y-%m-%d %H:%M:%S")
        except ValueError:
            return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed


def _date_part(value: str) -> str:
    parsed = _parse_datetime(value)
    return parsed.date().isoformat() if parsed is not None else ""


def _compact_text(value: str) -> str:
    return "".join(str(value or "").split()).casefold()
