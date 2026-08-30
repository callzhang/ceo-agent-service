"""Read-only progress projection for work projects and TODOs."""

import json
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from app.task_models import (
    DingTalkTodoLinkStatus,
    FollowUpDraftStatus,
    ProjectStatus,
    TodoStatus,
)


def todo_is_done(
    todo: Any,
    *,
    follow_ups: Iterable[Any] = (),
    dingtalk_links: Iterable[Any] = (),
) -> bool:
    if _status(todo) == TodoStatus.DONE.value:
        return True
    if _status(todo) == TodoStatus.CANCELLED.value:
        return False
    if _has_completion_evidence(getattr(todo, "completion_evidence_json", "")):
        return True
    if any(_status(follow_up) == FollowUpDraftStatus.COMPLETED.value for follow_up in follow_ups):
        return True
    return any(_dingtalk_link_done(link) for link in dingtalk_links)


def todo_is_open(
    todo: Any,
    *,
    follow_ups: Iterable[Any] = (),
    dingtalk_links: Iterable[Any] = (),
) -> bool:
    if _status(todo) == TodoStatus.CANCELLED.value:
        return False
    return not todo_is_done(todo, follow_ups=follow_ups, dingtalk_links=dingtalk_links)


def task_progress_summary(
    todos: Sequence[Any],
    *,
    follow_ups_by_todo: Mapping[int, Iterable[Any]] | None = None,
    dingtalk_links_by_todo: Mapping[int, Iterable[Any]] | None = None,
) -> dict[str, int]:
    follow_ups_by_todo = follow_ups_by_todo or {}
    dingtalk_links_by_todo = dingtalk_links_by_todo or {}
    total = len(todos)
    done_count = sum(
        1
        for todo in todos
        if todo_is_done(
            todo,
            follow_ups=follow_ups_by_todo.get(todo.id, ()),
            dingtalk_links=dingtalk_links_by_todo.get(todo.id, ()),
        )
    )
    open_count = sum(
        1
        for todo in todos
        if todo_is_open(
            todo,
            follow_ups=follow_ups_by_todo.get(todo.id, ()),
            dingtalk_links=dingtalk_links_by_todo.get(todo.id, ()),
        )
    )
    return {
        "done_count": done_count,
        "done_ratio": round(done_count * 100 / total) if total else 0,
        "open_count": open_count,
        "open_ratio": round(open_count * 100 / total) if total else 0,
        "total": total,
    }


def task_state(
    project: Any,
    todos: Sequence[Any],
    *,
    follow_ups_by_todo: Mapping[int, Iterable[Any]] | None = None,
    dingtalk_links_by_todo: Mapping[int, Iterable[Any]] | None = None,
    overdue_checker=None,
) -> str:
    if _status(project) == ProjectStatus.DONE.value:
        return "completed"
    summary = task_progress_summary(
        todos,
        follow_ups_by_todo=follow_ups_by_todo,
        dingtalk_links_by_todo=dingtalk_links_by_todo,
    )
    if todos and summary["open_count"] == 0:
        return "completed"
    if overdue_checker is not None:
        follow_ups_by_todo = follow_ups_by_todo or {}
        dingtalk_links_by_todo = dingtalk_links_by_todo or {}
        if any(
            todo_is_open(
                todo,
                follow_ups=follow_ups_by_todo.get(todo.id, ()),
                dingtalk_links=dingtalk_links_by_todo.get(todo.id, ()),
            )
            and overdue_checker(todo)
            for todo in todos
        ):
            return "over due"
    if summary["open_count"] > 0:
        return "in progress"
    return "not started"


def _status(value: Any) -> str:
    return str(getattr(getattr(value, "status", ""), "value", getattr(value, "status", "")))


def _dingtalk_link_done(link: Any) -> bool:
    if _status(link) == DingTalkTodoLinkStatus.DONE.value:
        return True
    return getattr(link, "last_dingtalk_done", None) is True


def _has_completion_evidence(value: str) -> bool:
    text = str(value or "").strip()
    if not text or text == "{}":
        return False
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return bool(text)
    if isinstance(payload, dict):
        return any(str(payload.get(key) or "").strip() for key in ("source", "reason", "completed_at"))
    return bool(payload)
