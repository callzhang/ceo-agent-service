"""Task management DTOs and read-only console payload builders."""

import json
from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.task_models import ProjectStatus, TodoStatus
from app.task_retrieval import load_project_task_detail
from app.web_api.common import (
    ApiItemEnvelope,
    ApiListEnvelope,
    ApiListMeta,
    json_safe,
    normalize_display_value,
    snapshot_at,
)


class ConsoleTaskSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: int
    title: str
    status: str
    project_status: str
    category: str
    priority: str
    risk_level: str
    owner_user_id: str
    owner_name: str
    current_state: str
    next_step: str
    open_count: int
    open_ratio: int
    progress_count: int
    progress_total: int
    progress_ratio: int
    todo_count: int
    detail_url: str


class ConsoleFact(BaseModel):
    model_config = ConfigDict(extra="forbid")

    description: str
    source: str = ""
    created: str = ""
    updated: str = ""


class ConsoleTodo(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: int
    project_id: int
    title: str
    description: str = ""
    owner_user_id: str = ""
    owner_name: str = ""
    status: str
    priority: str
    deadline_at: str = ""
    next_follow_up_at: str = ""
    follow_up_question: str = ""
    blocker: str = ""
    completion_evidence: dict[str, Any] = Field(default_factory=dict)
    created_at: str = ""
    updated_at: str = ""
    completed_at: str = ""
    detail_url: str
    follow_ups: list[dict[str, Any]] = Field(default_factory=list)
    dingtalk_todos: list[dict[str, Any]] = Field(default_factory=list)


class ConsoleUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: int
    project_id: int
    source_type: str
    source_ref: str
    summary: str
    changes: dict[str, Any] = Field(default_factory=dict)
    merge_reason: str = ""
    confidence: float = 0.0
    created_at: str = ""


class ConsoleProject(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: int
    title: str
    category: str
    status: str
    priority: str
    risk_level: str
    needs_derek_attention: bool = False
    owner_user_id: str = ""
    owner_name: str = ""
    tags: list[str] = Field(default_factory=list)
    related_people: list[Any] = Field(default_factory=list)
    goal: str = ""
    background: str = ""
    current_state: str = ""
    blocker: str = ""
    next_step: str = ""
    next_follow_up_at: str = ""
    follow_up_mode: str = ""
    source_conversations: list[Any] = Field(default_factory=list)
    facts: list[ConsoleFact] = Field(default_factory=list)
    memory_context: dict[str, Any] = Field(default_factory=dict)
    created_at: str = ""
    updated_at: str = ""
    last_activity_at: str = ""


class ConsoleTaskDetail(BaseModel):
    model_config = ConfigDict(extra="forbid")

    project: ConsoleProject
    todos: list[ConsoleTodo] = Field(default_factory=list)
    updates: list[ConsoleUpdate] = Field(default_factory=list)
    unlinked_follow_ups: list[dict[str, Any]] = Field(default_factory=list)


class ConsoleTaskListEnvelope(ApiListEnvelope):
    items: list[ConsoleTaskSummary] = Field(default_factory=list)


class ConsoleTaskDetailEnvelope(ApiItemEnvelope):
    item: ConsoleTaskDetail


class ConsoleSentTodo(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    kind: str
    kind_label: str
    sent_at: str
    status: str
    owner: str
    project_title: str
    todo_title: str
    description: str = ""
    original_text: str = ""
    deadline: str = ""
    priority: str = ""
    target: str = ""
    external_id: str = ""
    detail_url: str = ""


class ConsoleSentTodoListEnvelope(ApiListEnvelope):
    items: list[ConsoleSentTodo] = Field(default_factory=list)


def _enum_text(value: Any) -> str:
    return normalize_display_value(getattr(value, "value", value))


def _parse_json(value: str, default: Any) -> Any:
    try:
        parsed = json.loads(value or "")
    except (TypeError, json.JSONDecodeError):
        return default
    return parsed


def _list_json(value: str) -> list[Any]:
    parsed = _parse_json(value, [])
    return parsed if isinstance(parsed, list) else []


def _object_json(value: str) -> dict[str, Any]:
    parsed = _parse_json(value, {})
    return parsed if isinstance(parsed, dict) else {}


def _memory_context_payload(value: str) -> dict[str, Any]:
    payload = json_safe(_object_json(value))
    if isinstance(payload, dict) and "summary" in payload:
        payload["summary"] = normalize_display_value(payload["summary"])
    return payload


def _todo_done(todo: Any) -> bool:
    return _enum_text(todo.status) == TodoStatus.DONE.value


def _todo_open(todo: Any) -> bool:
    return _enum_text(todo.status) not in {
        TodoStatus.DONE.value,
        TodoStatus.CANCELLED.value,
    }


def _todo_overdue(todo: Any) -> bool:
    if not _todo_open(todo) or not todo.deadline_at:
        return False
    try:
        deadline = datetime.fromisoformat(todo.deadline_at.replace("Z", "+00:00"))
    except ValueError:
        return False
    if deadline.tzinfo is None:
        deadline = deadline.replace(tzinfo=timezone.utc)
    return deadline < datetime.now(timezone.utc)


def _task_state(project: Any, todos: list[Any]) -> str:
    if _enum_text(project.status) == ProjectStatus.DONE.value:
        return "completed"
    if todos and not any(_todo_open(todo) for todo in todos):
        return "completed"
    if any(_todo_overdue(todo) for todo in todos):
        return "over due"
    if any(_todo_open(todo) for todo in todos):
        return "in progress"
    return "not started"


def task_summary(project: Any, todos: list[Any]) -> dict[str, Any]:
    done_count = sum(_todo_done(todo) for todo in todos)
    todo_count = len(todos)
    progress_ratio = round(done_count * 100 / todo_count) if todo_count else 0
    open_count = sum(_todo_open(todo) for todo in todos)
    open_ratio = round(open_count * 100 / todo_count) if todo_count else 0
    state = _task_state(project, todos)
    return {
        "id": project.id,
        "title": normalize_display_value(project.title),
        "status": state,
        "project_status": _enum_text(project.status),
        "category": _enum_text(project.category),
        "priority": _enum_text(project.priority),
        "risk_level": _enum_text(project.risk_level),
        "owner_user_id": normalize_display_value(project.owner_user_id),
        "owner_name": normalize_display_value(project.owner_name),
        "current_state": normalize_display_value(project.current_state),
        "next_step": normalize_display_value(project.next_step),
        "open_count": open_count,
        "open_ratio": open_ratio,
        "progress_count": done_count,
        "progress_total": todo_count,
        "progress_ratio": progress_ratio,
        "todo_count": todo_count,
        "detail_url": f"/tasks/{project.id}",
    }


def _fact_payload(value: Any) -> dict[str, str]:
    if isinstance(value, dict):
        return {
            "description": normalize_display_value(value.get("description")),
            "source": normalize_display_value(value.get("source")),
            "created": normalize_display_value(value.get("created")),
            "updated": normalize_display_value(value.get("updated")),
        }
    return {"description": normalize_display_value(value), "source": "", "created": "", "updated": ""}


def _todo_payload(todo: Any, detail: Any) -> dict[str, Any]:
    payload = {
        "id": todo.id,
        "project_id": todo.project_id,
        "title": normalize_display_value(todo.title),
        "description": normalize_display_value(todo.description),
        "owner_user_id": normalize_display_value(todo.owner_user_id),
        "owner_name": normalize_display_value(todo.owner_name),
        "status": _enum_text(todo.status),
        "priority": _enum_text(todo.priority),
        "deadline_at": normalize_display_value(todo.deadline_at),
        "next_follow_up_at": normalize_display_value(todo.next_follow_up_at),
        "follow_up_question": normalize_display_value(todo.follow_up_question),
        "blocker": normalize_display_value(todo.blocker),
        "completion_evidence": json_safe(_object_json(todo.completion_evidence_json)),
        "created_at": normalize_display_value(todo.created_at),
        "updated_at": normalize_display_value(todo.updated_at),
        "completed_at": normalize_display_value(todo.completed_at),
        "detail_url": f"/tasks/{todo.project_id}#todo-{todo.id}",
        "follow_ups": [
            {
                **json_safe(follow_up),
                "detail_url": f"/tasks/{todo.project_id}#follow-up-{follow_up.id}",
            }
            for follow_up in detail.follow_ups_by_todo.get(todo.id, ())
        ],
        "dingtalk_todos": [json_safe(link) for link in detail.dingtalk_links_by_todo.get(todo.id, ())],
    }
    return payload


def task_detail(store: Any, project_id: int) -> dict[str, Any] | None:
    detail = load_project_task_detail(store, project_id)
    if detail is None:
        return None
    project = detail.project
    todos = list(detail.todos)
    all_follow_ups = store.list_follow_up_drafts(project_id=project.id, limit=100)
    todo_ids = {todo.id for todo in todos}
    return {
        "project": {
            "id": project.id,
            "title": normalize_display_value(project.title),
            "category": _enum_text(project.category),
            "status": _enum_text(project.status),
            "priority": _enum_text(project.priority),
            "risk_level": _enum_text(project.risk_level),
            "needs_derek_attention": bool(project.needs_derek_attention),
            "owner_user_id": normalize_display_value(project.owner_user_id),
            "owner_name": normalize_display_value(project.owner_name),
            "tags": [normalize_display_value(item) for item in _list_json(project.tags_json)],
            "related_people": json_safe(_list_json(project.related_people_json)),
            "goal": normalize_display_value(project.goal),
            "background": normalize_display_value(project.background),
            "current_state": normalize_display_value(project.current_state),
            "blocker": normalize_display_value(project.blocker),
            "next_step": normalize_display_value(project.next_step),
            "next_follow_up_at": normalize_display_value(project.next_follow_up_at),
            "follow_up_mode": _enum_text(project.follow_up_mode),
            "source_conversations": json_safe(_list_json(project.source_conversations_json)),
            "facts": [_fact_payload(item) for item in _list_json(project.facts_json)],
            "memory_context": _memory_context_payload(project.memory_context_json),
            "created_at": normalize_display_value(project.created_at),
            "updated_at": normalize_display_value(project.updated_at),
            "last_activity_at": normalize_display_value(project.last_activity_at),
        },
        "todos": [_todo_payload(todo, detail) for todo in todos],
        "updates": [
            {
                "id": update.id,
                "project_id": update.project_id,
                "source_type": normalize_display_value(update.source_type),
                "source_ref": normalize_display_value(update.source_ref),
                "summary": normalize_display_value(update.summary),
                "changes": json_safe(_object_json(update.changes_json)),
                "merge_reason": normalize_display_value(update.merge_reason),
                "confidence": update.confidence,
                "created_at": normalize_display_value(update.created_at),
            }
            for update in detail.updates
        ],
        "unlinked_follow_ups": [
            json_safe(follow_up)
            for follow_up in all_follow_ups
            if follow_up.todo_id not in todo_ids
        ],
    }


def task_list_response(
    store: Any,
    *,
    page: int,
    page_size: int,
    query: str = "",
    category: str = "",
    task_state: str = "",
    sort: str = "",
    row_builder=None,
) -> ConsoleTaskListEnvelope:
    rows = []
    needle = query.strip().casefold()
    for project in store.list_work_projects(limit=None):
        todos = store.list_work_todos(project_id=project.id)
        row = task_summary(project, todos)
        if row_builder is not None:
            built = json_safe(row_builder(project, todos))
            if isinstance(built, dict):
                row.update(
                    {
                        "title": normalize_display_value(built.get("title", row["title"])),
                        "status": normalize_display_value(built.get("status", row["status"])),
                        "category": normalize_display_value(built.get("category", row["category"])),
                        "priority": normalize_display_value(built.get("priority", row["priority"])),
                        "risk_level": normalize_display_value(built.get("riskLevel", row["risk_level"])),
                        "owner_name": normalize_display_value(built.get("owner", row["owner_name"])),
                        "current_state": normalize_display_value(built.get("currentState", row["current_state"])),
                        "next_step": normalize_display_value(built.get("nextStep", row["next_step"])),
                        "open_count": int(built.get("openCount", row["open_count"])),
                        "open_ratio": int(built.get("openRatio", row["open_ratio"])),
                        "progress_count": int(built.get("progressCount", row["progress_count"])),
                        "progress_total": int(built.get("progressTotal", row["progress_total"])),
                        "progress_ratio": int(built.get("progressRatio", row["progress_ratio"])),
                        "todo_count": int(built.get("todoCount", row["todo_count"])),
                    }
                )
        row["title"] = row["title"].strip() or f"Project {row['id']}"
        haystack = " ".join(
            [
                row["title"], row["category"], row["project_status"], row["owner_name"],
                row["current_state"], row["next_step"],
                *[normalize_display_value(todo.title) for todo in todos],
            ]
        ).casefold()
        if needle and needle not in haystack:
            continue
        if category.strip() and row["category"] != category.strip():
            continue
        if task_state.strip() and row["status"] != task_state.strip():
            continue
        rows.append(row)
    if sort == "project_asc":
        rows.sort(key=lambda row: row["title"].casefold())
    elif sort == "project_desc":
        rows.sort(key=lambda row: row["title"].casefold(), reverse=True)
    elif sort == "priority_desc":
        priority_rank = {
            "p0": 60,
            "critical": 60,
            "urgent": 50,
            "high": 40,
            "p1": 40,
            "medium": 30,
            "p2": 30,
            "low": 20,
            "p3": 20,
        }
        rows.sort(
            key=lambda row: (
                priority_rank.get(row["priority"].strip().casefold(), 0),
                row["title"].casefold(),
            ),
            reverse=True,
        )
    elif sort == "progress_desc":
        rows.sort(
            key=lambda row: (row["progress_ratio"], row["title"].casefold()),
            reverse=True,
        )
    elif sort == "todos_desc":
        rows.sort(
            key=lambda row: (row["todo_count"], row["title"].casefold()),
            reverse=True,
        )
    total = len(rows)
    start = (page - 1) * page_size
    page_rows = rows[start : start + page_size]
    has_more = start + page_size < total
    return ConsoleTaskListEnvelope(
        items=[ConsoleTaskSummary.model_validate(row) for row in page_rows],
        meta=ApiListMeta(
            snapshot_at=snapshot_at(),
            page=page,
            page_size=page_size,
            total=total,
            next_cursor=str(page + 1) if has_more else "",
            has_more=has_more,
        ),
    )


def sent_todo_payload(record: Any) -> dict[str, str]:
    target = normalize_display_value(getattr(record, "target_kind", ""))
    conversation_id = normalize_display_value(getattr(record, "target_conversation_id", ""))
    external_id = normalize_display_value(getattr(record, "external_id", ""))
    if conversation_id:
        target = f"{target}:{conversation_id}".strip(":")
    elif external_id:
        target = external_id
    project_id = int(getattr(record, "project_id", 0) or 0)
    todo_id = int(getattr(record, "todo_id", 0) or 0)
    detail_url = (
        f"/tasks/{project_id}#todo-{todo_id}" if project_id and todo_id
        else f"/tasks/{project_id}" if project_id else ""
    )
    kind = normalize_display_value(getattr(record, "kind", ""))
    return {
        "id": f"{kind}:{getattr(record, 'source_id', '')}",
        "kind": kind,
        "kind_label": "DingTalk Todo" if kind == "dingtalk_todo" else "Follow-up",
        "sent_at": normalize_display_value(getattr(record, "sent_at", "")),
        "status": normalize_display_value(getattr(record, "status", "")),
        "owner": normalize_display_value(getattr(record, "owner_name", "") or getattr(record, "owner_user_id", "")),
        "project_title": normalize_display_value(getattr(record, "project_title", "")),
        "todo_title": normalize_display_value(getattr(record, "todo_title", "") or getattr(record, "title", "")),
        "description": normalize_display_value(getattr(record, "todo_description", "") or getattr(record, "description", "")),
        "original_text": normalize_display_value(getattr(record, "original_text", "")),
        "deadline": normalize_display_value(getattr(record, "deadline_at", "")),
        "priority": normalize_display_value(getattr(record, "priority", "")),
        "target": target,
        "external_id": external_id,
        "detail_url": detail_url,
    }
