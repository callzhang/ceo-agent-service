import json
from datetime import datetime
from typing import Any

from app.dws_client import DwsClient, DwsError
from app.store import AutoReplyStore
from app.task_models import ProjectPriority, TodoStatus
from app.todo_completion import close_todo_with_completion_evidence


WEAK_TITLES = {"跟进一下", "同步进展", "确认进展", "问一下", "推进一下"}
DINGTALK_TODO_TITLE_LIMIT = 80
DINGTALK_TODO_CONTEXT_LIMIT = 42
MAX_DINGTALK_TODO_CREATE_RETRIES = 1
DINGTALK_TODO_CREATE_RETRYABLE_ERROR_CODES = frozenset({
    "TOKEN_VERIFIED_FAILED",
    "SECURITY_CHECK_INVOKE_FAILED",
})


def _parse_datetime(value: str) -> datetime | None:
    text = (value or "").strip()
    if not text:
        return None
    candidates = [text]
    if text.endswith("Z"):
        candidates.append(f"{text[:-1]}+00:00")
    for candidate in candidates:
        try:
            return datetime.fromisoformat(candidate)
        except ValueError:
            pass
    try:
        return datetime.strptime(text, "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None


def _deadline_to_iso(value: str) -> str:
    deadline = _parse_datetime(value)
    if deadline is None:
        return ""
    if deadline.tzinfo is None:
        return f"{deadline.isoformat()}+08:00"
    return deadline.isoformat()


def _priority_to_dingtalk(priority: str) -> int:
    priorities = {
        ProjectPriority.P0.value: 40,
        ProjectPriority.P1.value: 30,
        ProjectPriority.P2.value: 20,
        ProjectPriority.NONE.value: 20,
    }
    return priorities.get((priority or "").strip(), 20)


def _payload_candidate_dicts(payload: dict[str, Any]) -> list[dict[str, Any]]:
    values = [payload]
    result = payload.get("result")
    if isinstance(result, dict):
        values.append(result)
        detail = result.get("todoDetailModel")
        if isinstance(detail, dict):
            values.append(detail)
    return values


def _payload_task_id(payload: dict[str, Any]) -> str:
    values = _payload_candidate_dicts(payload)
    for item in values:
        for key in ("todoTaskId", "taskId", "task_id", "id"):
            value = item.get(key)
            if value is not None and str(value).strip():
                return str(value).strip()
    return ""


def _payload_done_value(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "done", "completed"}:
            return True
        if normalized in {"false", "open", "pending", "active"}:
            return False
    return None


def _payload_done(payload: dict[str, Any]) -> bool:
    values = _payload_candidate_dicts(payload)
    for item in values:
        for key in ("done", "isDone", "completed", "isCompleted", "status"):
            parsed = _payload_done_value(item.get(key))
            if parsed is not None:
                return parsed
    return False


def _has_completion_evidence(value: str) -> bool:
    text = (value or "").strip()
    if not text:
        return False
    try:
        evidence = json.loads(text)
    except json.JSONDecodeError:
        return True
    if isinstance(evidence, (dict, list)):
        return bool(evidence)
    return bool(evidence)


def _is_actionable_title(title: str) -> bool:
    compact = "".join((title or "").split())
    return len(compact) >= 6 and compact not in WEAK_TITLES


def _normalize_inline_text(value: str) -> str:
    return " ".join((value or "").split())


def _trim_inline_text(value: str, limit: int) -> str:
    text = _normalize_inline_text(value)
    if len(text) <= limit:
        return text
    return f"{text[: max(0, limit - 1)]}…"


def _first_context_sentence(value: str) -> str:
    text = _normalize_inline_text(value)
    if not text:
        return ""
    for separator in ("。", "；", ";", "！", "!", "？", "?"):
        index = text.find(separator)
        if index > 0:
            text = text[:index]
            break
    return _trim_inline_text(text, DINGTALK_TODO_CONTEXT_LIMIT)


def _project_context(project: Any) -> str:
    title = _normalize_inline_text(str(getattr(project, "title", "") or ""))
    return f"项目：{title}" if title else ""


def _json_list(value: str) -> list:
    try:
        parsed = json.loads(value or "[]")
    except json.JSONDecodeError:
        return []
    return parsed if isinstance(parsed, list) else []


def _dingtalk_todo_description(todo: Any, project: Any = None) -> str:
    parts: list[str] = []
    if project is not None:
        project_title = _normalize_inline_text(str(getattr(project, "title", "") or ""))
        if project_title:
            parts.append(f"项目：{project_title}")
        for label, field in (
            ("项目背景", "background"),
            ("当前状态", "current_state"),
            ("下一步", "next_step"),
            ("阻塞", "blocker"),
        ):
            value = _normalize_inline_text(str(getattr(project, field, "") or ""))
            if value:
                parts.append(f"{label}：{value}")
    for label, field in (
        ("待办", "title"),
        ("待办背景", "description"),
        ("完成标准/确认问题", "follow_up_question"),
        ("待办阻塞", "blocker"),
        ("截止时间", "deadline_at"),
    ):
        value = _normalize_inline_text(str(getattr(todo, field, "") or ""))
        if value:
            parts.append(f"{label}：{value}")
    owner = _normalize_inline_text(str(getattr(todo, "owner_name", "") or ""))
    if owner:
        parts.append(f"负责人：{owner}")
    priority = _normalize_inline_text(str(getattr(todo, "priority", "") or ""))
    if priority:
        parts.append(f"优先级：{priority}")
    return "\n".join(dict.fromkeys(parts))


def _dingtalk_todo_tags(todo: Any, project: Any = None) -> list[str]:
    tags: list[str] = []
    if project is not None:
        tags.extend(str(item).strip() for item in _json_list(project.tags_json))
        category = str(getattr(project, "category", "") or "").strip()
        risk_level = str(getattr(project, "risk_level", "") or "").strip()
        if category:
            tags.append(category)
        if risk_level:
            tags.append(f"risk:{risk_level}")
    priority = str(getattr(todo, "priority", "") or "").strip()
    if priority:
        tags.append(priority)
    return [tag for tag in dict.fromkeys(tags) if tag]


def _dingtalk_todo_participants(todo: Any, project: Any = None) -> list[dict[str, str]]:
    participants: list[dict[str, str]] = []
    owner_user_id = str(getattr(todo, "owner_user_id", "") or "").strip()
    owner_name = str(getattr(todo, "owner_name", "") or "").strip()
    if owner_user_id:
        participants.append(
            {"user_id": owner_user_id, "name": owner_name, "role": "owner"}
        )
    if project is not None:
        for item in _json_list(project.related_people_json):
            if not isinstance(item, dict):
                continue
            user_id = str(item.get("user_id") or "").strip()
            name = str(item.get("name") or "").strip()
            role = str(item.get("role") or "").strip()
            if user_id:
                participants.append(
                    {"user_id": user_id, "name": name, "role": role}
                )
    unique: dict[str, dict[str, str]] = {}
    for item in participants:
        unique.setdefault(item["user_id"], item)
    return list(unique.values())


def _dingtalk_todo_title(todo: Any, project: Any = None) -> str:
    title = _trim_inline_text(todo.title, DINGTALK_TODO_TITLE_LIMIT)
    if not title:
        return ""
    for field in ("description", "follow_up_question", "blocker"):
        detail = _first_context_sentence(str(getattr(todo, field, "") or ""))
        if not detail:
            continue
        project_context = _project_context(project)
        if (
            project_context
            and project_context not in title
            and project_context not in detail
            and "来源" not in detail
            and "基于" not in detail
        ):
            detail = f"{project_context}；{detail}"
        if detail in title or title in detail:
            continue
        return _trim_inline_text(
            f"{title}：{detail}",
            DINGTALK_TODO_TITLE_LIMIT,
        )
    project_context = _project_context(project)
    if project_context and project_context not in title:
        return _trim_inline_text(
            f"{title}：{project_context}",
            DINGTALK_TODO_TITLE_LIMIT,
        )
    return title


def _todo_is_eligible(store: AutoReplyStore, todo: Any) -> bool:
    project = store.get_work_project(todo.project_id)
    if todo.status not in {TodoStatus.OPEN, TodoStatus.WAITING_OWNER}:
        return False
    if not todo.owner_user_id.strip():
        return False
    if not _deadline_to_iso(todo.deadline_at):
        return False
    if not _is_actionable_title(todo.title):
        return False
    if not _is_actionable_title(_dingtalk_todo_title(todo, project)):
        return False
    if _has_completion_evidence(todo.completion_evidence_json):
        return False
    if project is not None and str(project.category) == "HR":
        return False
    return store.get_active_work_todo_dingtalk_link(todo.id) is None


def _find_existing_link_with_task_id(store: AutoReplyStore, work_todo_id: int) -> Any:
    links = store.list_work_todo_dingtalk_links(
        statuses=("failed",),
        limit=1,
        work_todo_id=work_todo_id,
        with_dingtalk_task_id=True,
    )
    for link in links:
        return link
    return None


def _is_retryable_dingtalk_todo_error(error: str) -> bool:
    normalized = error.casefold()
    return any(
        code.casefold() in normalized
        for code in DINGTALK_TODO_CREATE_RETRYABLE_ERROR_CODES
    )


def _find_retryable_failed_create_link(
    store: AutoReplyStore,
    work_todo_id: int,
) -> Any:
    links = store.list_work_todo_dingtalk_links(
        statuses=("failed",),
        limit=10,
        work_todo_id=work_todo_id,
    )
    for link in links:
        if link.dingtalk_task_id.strip():
            continue
        if _is_retryable_dingtalk_todo_error(link.last_error):
            return link
    return None


def _refresh_existing_dingtalk_link(
    store: AutoReplyStore,
    dws: Any,
    link: Any,
    *,
    now: str,
):
    task_id = link.dingtalk_task_id.strip()
    try:
        payload = dws.get_todo_task(task_id)
    except (DwsError, RuntimeError) as exc:
        store.update_work_todo_dingtalk_link(
            link.id,
            status="failed",
            last_pull_at=now,
            last_error=str(exc),
        )
        return store.get_work_todo_dingtalk_link(link.id)

    done = _payload_done(payload)
    store.update_work_todo_dingtalk_link(
        link.id,
        status="done" if done else "active",
        last_dingtalk_done=done,
        last_dingtalk_payload_json=json.dumps(payload, ensure_ascii=False),
        last_pull_at=now,
        last_error="",
    )
    if done:
        _close_internal_todo_from_dingtalk(store, link, task_id, now)
    return store.get_work_todo_dingtalk_link(link.id)


def _create_dingtalk_todo_for_link(
    store: AutoReplyStore,
    dws: Any,
    *,
    todo: Any,
    link_id: int,
    now: str,
):
    project = store.get_work_project(todo.project_id)
    dingtalk_title = _dingtalk_todo_title(todo, project)
    dingtalk_description = _dingtalk_todo_description(todo, project)
    dingtalk_tags = _dingtalk_todo_tags(todo, project)
    dingtalk_participants = _dingtalk_todo_participants(todo, project)
    store.update_work_todo_dingtalk_link(
        link_id,
        executor_user_id=todo.owner_user_id,
        executor_name=todo.owner_name,
        title_snapshot=dingtalk_title,
        deadline_at_snapshot=todo.deadline_at,
        priority_snapshot=todo.priority.value,
        status="creating",
        last_error="",
    )
    try:
        create_payload = dws.create_todo_task(
            title=dingtalk_title,
            executor_user_id=todo.owner_user_id,
            due=_deadline_to_iso(todo.deadline_at),
            priority=_priority_to_dingtalk(str(todo.priority)),
            description=dingtalk_description,
            tags=dingtalk_tags,
            participants=dingtalk_participants,
            files=[],
        )
    except (DwsError, RuntimeError) as exc:
        store.update_work_todo_dingtalk_link(
            link_id,
            status="failed",
            last_error=str(exc),
        )
        return store.get_work_todo_dingtalk_link(link_id)

    task_id = _payload_task_id(create_payload)
    if not task_id:
        store.update_work_todo_dingtalk_link(
            link_id,
            status="failed",
            last_error="DingTalk todo create response did not include task id",
        )
        return store.get_work_todo_dingtalk_link(link_id)

    store.update_work_todo_dingtalk_link(
        link_id,
        dingtalk_task_id=task_id,
        last_push_at=now,
        last_error="",
    )
    try:
        get_payload = dws.get_todo_task(task_id)
    except (DwsError, RuntimeError) as exc:
        store.update_work_todo_dingtalk_link(
            link_id,
            status="failed",
            last_error=str(exc),
        )
        return store.get_work_todo_dingtalk_link(link_id)

    done = _payload_done(get_payload)
    store.update_work_todo_dingtalk_link(
        link_id,
        status="active",
        last_dingtalk_done=done,
        last_dingtalk_payload_json=json.dumps(get_payload, ensure_ascii=False),
        last_pull_at=now,
        last_error="",
    )
    return store.get_work_todo_dingtalk_link(link_id)


def maybe_create_dingtalk_todo(
    store: AutoReplyStore,
    dws: Any,
    *,
    work_todo_id: int,
    now: str,
):
    todo = store.get_work_todo(work_todo_id)
    if todo is None:
        return None

    active_link = store.get_active_work_todo_dingtalk_link(work_todo_id)
    if active_link is not None:
        return active_link

    existing_link = _find_existing_link_with_task_id(store, work_todo_id)
    if existing_link is not None:
        return _refresh_existing_dingtalk_link(store, dws, existing_link, now=now)

    retryable_create_link = _find_retryable_failed_create_link(store, work_todo_id)
    if retryable_create_link is not None and _todo_is_eligible(store, todo):
        if retryable_create_link.retry_count >= MAX_DINGTALK_TODO_CREATE_RETRIES:
            return retryable_create_link
        store.update_work_todo_dingtalk_link(
            retryable_create_link.id,
            retry_count=retryable_create_link.retry_count + 1,
        )
        return _create_dingtalk_todo_for_link(
            store,
            dws,
            todo=todo,
            link_id=retryable_create_link.id,
            now=now,
        )

    if not _todo_is_eligible(store, todo):
        return None

    project = store.get_work_project(todo.project_id)
    dingtalk_title = _dingtalk_todo_title(todo, project)
    link_id = store.create_work_todo_dingtalk_link(
        work_todo_id=todo.id,
        executor_user_id=todo.owner_user_id,
        executor_name=todo.owner_name,
        title_snapshot=dingtalk_title,
        deadline_at_snapshot=todo.deadline_at,
        priority_snapshot=todo.priority.value,
        status="creating",
    )
    link = store.get_work_todo_dingtalk_link(link_id)
    if link is None:
        raise RuntimeError(f"created DingTalk todo link {link_id} was not found")
    if link.status != "creating" or link.dingtalk_task_id.strip():
        return link
    return _create_dingtalk_todo_for_link(
        store,
        dws,
        todo=todo,
        link_id=link_id,
        now=now,
    )


def pull_dingtalk_todo_statuses(
    store: AutoReplyStore,
    dws: Any,
    *,
    now: str,
    limit: int = 100,
) -> int:
    closed_count = 0
    links = store.list_work_todo_dingtalk_links(statuses=("active",), limit=limit)
    for link in links:
        task_id = link.dingtalk_task_id.strip()
        if not task_id:
            store.update_work_todo_dingtalk_link(
                link.id,
                last_pull_at=now,
                last_error="active DingTalk todo link has no task id",
            )
            continue
        try:
            payload = dws.get_todo_task(task_id)
            done = _payload_done(payload)
            store.update_work_todo_dingtalk_link(
                link.id,
                last_dingtalk_done=done,
                last_dingtalk_payload_json=json.dumps(payload, ensure_ascii=False),
                last_pull_at=now,
                last_error="",
            )
            if done:
                if _close_internal_todo_from_dingtalk(store, link, task_id, now):
                    closed_count += 1
                store.update_work_todo_dingtalk_link(link.id, status="done")
        except (DwsError, RuntimeError) as exc:
            store.update_work_todo_dingtalk_link(link.id, last_error=str(exc))
    return closed_count


def retry_failed_dingtalk_todo_links(
    store: AutoReplyStore,
    dws: Any,
    *,
    now: str,
    limit: int = 20,
) -> int:
    recovered = 0
    links = store.list_work_todo_dingtalk_links(statuses=("failed",), limit=limit)
    for link in links:
        if link.dingtalk_task_id.strip():
            updated = _refresh_existing_dingtalk_link(store, dws, link, now=now)
        else:
            todo = store.get_work_todo(link.work_todo_id)
            if todo is None:
                continue
            if _has_completion_evidence(todo.completion_evidence_json):
                # The internal work is already evidenced as complete. Retrying a
                # historical create would produce an overdue duplicate task.
                store.update_work_todo_dingtalk_link(
                    link.id,
                    status="cancelled",
                    last_error="internal_todo_completed_before_dingtalk_delivery",
                )
                continue
            if (
                not _is_retryable_dingtalk_todo_error(link.last_error)
                or not _todo_is_eligible(store, todo)
                or link.retry_count >= MAX_DINGTALK_TODO_CREATE_RETRIES
            ):
                continue
            store.update_work_todo_dingtalk_link(
                link.id,
                retry_count=link.retry_count + 1,
            )
            updated = _create_dingtalk_todo_for_link(
                store,
                dws,
                todo=todo,
                link_id=link.id,
                now=now,
            )
        if updated is not None and updated.status != "failed":
            recovered += 1
    return recovered


def refresh_dingtalk_todo_before_follow_up(
    store: AutoReplyStore,
    dws: Any,
    *,
    work_todo_id: int,
    now: str,
) -> tuple[bool, str]:
    link = store.get_active_work_todo_dingtalk_link(work_todo_id)
    if link is None or not link.dingtalk_task_id.strip():
        return False, ""

    task_id = link.dingtalk_task_id.strip()
    try:
        payload = dws.get_todo_task(task_id)
        done = _payload_done(payload)
        store.update_work_todo_dingtalk_link(
            link.id,
            last_dingtalk_done=done,
            last_dingtalk_payload_json=json.dumps(payload, ensure_ascii=False),
            last_pull_at=now,
            last_error="",
        )
        if done:
            _close_internal_todo_from_dingtalk(store, link, task_id, now)
            store.update_work_todo_dingtalk_link(link.id, status="done")
            return True, "dingtalk_todo_done"
    except (DwsError, RuntimeError) as exc:
        store.update_work_todo_dingtalk_link(link.id, last_error=str(exc))
    return False, ""


def sync_completed_todo_to_dingtalk(
    store: AutoReplyStore,
    dws: Any,
    *,
    work_todo_id: int,
    evidence: dict[str, Any],
    now: str,
) -> bool:
    del evidence
    link = store.get_active_work_todo_dingtalk_link(work_todo_id)
    if link is None:
        return False
    if not link.dingtalk_task_id.strip():
        store.update_work_todo_dingtalk_link(
            link.id,
            last_error="active DingTalk todo link has no task id",
        )
        return False
    try:
        payload = dws.mark_todo_task_done(link.dingtalk_task_id, done=True)
        store.update_work_todo_dingtalk_link(
            link.id,
            status="done",
            last_dingtalk_done=True,
            last_dingtalk_payload_json=json.dumps(payload, ensure_ascii=False),
            last_push_at=now,
            last_error="",
        )
        return True
    except (DwsError, RuntimeError) as exc:
        store.update_work_todo_dingtalk_link(link.id, last_error=str(exc))
        return False


def _close_internal_todo_from_dingtalk(
    store: AutoReplyStore,
    link: Any,
    task_id: str,
    now: str,
) -> bool:
    todo = store.get_work_todo(link.work_todo_id)
    if todo is None or todo.status == TodoStatus.DONE:
        return False
    evidence = {
        "source": f"dingtalk_todo:{task_id}",
        "reason": "DingTalk Todo marked done by owner",
        "completed_at": now,
        "checked_at": now,
    }
    return close_todo_with_completion_evidence(
        store,
        todo_id=todo.id,
        evidence=evidence,
        now=now,
        source_type="dingtalk_todo",
        source_ref=task_id,
        merge_reason="dingtalk_todo_status_pull",
        confidence=1.0,
    )
