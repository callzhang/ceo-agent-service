import json
from datetime import datetime
from typing import Any

from app.dws_client import DwsError
from app.store import AutoReplyStore
from app.task_models import ProjectCategory, ProjectPriority, TodoStatus


WEAK_TITLES = {"跟进一下", "同步进展", "确认进展", "问一下", "推进一下"}
DINGTALK_TODO_TITLE_LIMIT = 80
DINGTALK_TODO_CONTEXT_LIMIT = 42


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


def _dingtalk_todo_title(todo: Any) -> str:
    title = _trim_inline_text(todo.title, DINGTALK_TODO_TITLE_LIMIT)
    if not title:
        return ""
    for field in ("description", "follow_up_question", "blocker"):
        detail = _first_context_sentence(str(getattr(todo, field, "") or ""))
        if not detail:
            continue
        if detail in title or title in detail:
            continue
        return _trim_inline_text(
            f"{title}：{detail}",
            DINGTALK_TODO_TITLE_LIMIT,
        )
    return title


def _is_project_sensitive(store: AutoReplyStore, project_id: int) -> bool:
    project = store.get_work_project(project_id)
    return project is not None and project.category == ProjectCategory.HR


def _todo_is_eligible(store: AutoReplyStore, todo: Any) -> bool:
    if todo.status not in {TodoStatus.OPEN, TodoStatus.WAITING_OWNER}:
        return False
    if not todo.owner_user_id.strip():
        return False
    if not _deadline_to_iso(todo.deadline_at):
        return False
    if not _is_actionable_title(todo.title):
        return False
    if not _is_actionable_title(_dingtalk_todo_title(todo)):
        return False
    if _has_completion_evidence(todo.completion_evidence_json):
        return False
    if _is_project_sensitive(store, todo.project_id):
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

    if not _todo_is_eligible(store, todo):
        return None

    dingtalk_title = _dingtalk_todo_title(todo)
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

    try:
        create_payload = dws.create_todo_task(
            title=dingtalk_title,
            executor_user_id=todo.owner_user_id,
            due=_deadline_to_iso(todo.deadline_at),
            priority=_priority_to_dingtalk(str(todo.priority)),
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
        "summary": "DingTalk Todo marked done",
        "synced_at": now,
    }
    store.update_work_todo(
        todo.id,
        status=TodoStatus.DONE.value,
        completion_evidence_json=json.dumps(evidence, ensure_ascii=False),
        completed_at=now,
    )
    store.create_work_update(
        project_id=todo.project_id,
        source_type="dingtalk_todo",
        source_ref=task_id,
        summary=f"Todo completed in DingTalk: {todo.title}",
        changes_json=json.dumps(
            {"todo_id": todo.id, "status": TodoStatus.DONE.value},
            ensure_ascii=False,
        ),
        merge_reason="dingtalk_todo_status_pull",
        confidence=1.0,
    )
    return True
