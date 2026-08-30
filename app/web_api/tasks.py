"""Task management DTOs and read-only console payload builders."""

import json
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.task_progress import (
    task_progress_summary,
    task_state as resolved_task_state,
    todo_is_done,
    todo_is_open,
)
from app.task_retrieval import load_project_task_detail, resolve_task_owner_display
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
    owner: str = ""
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
    done: bool = False
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


class ConsoleEvidenceCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: int
    project_id: int
    todo_id: int
    source_type: str
    source_ref: str
    source_created_at: str = ""
    evidence_text: str = ""
    reason: str = ""
    confidence: float = 0.0
    status: str
    work_summary_input_id: int = 0
    decision: dict[str, Any] = Field(default_factory=dict)
    created_at: str = ""
    updated_at: str = ""
    detail_url: str = ""


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
    evidence_candidates: list[ConsoleEvidenceCandidate] = Field(default_factory=list)
    unlinked_follow_ups: list[dict[str, Any]] = Field(default_factory=list)


class ConsoleTaskFilters(BaseModel):
    model_config = ConfigDict(extra="forbid")

    categories: list[str] = Field(default_factory=list)
    task_states: list[str] = Field(default_factory=list)


class ConsoleTaskListEnvelope(ApiListEnvelope):
    items: list[ConsoleTaskSummary] = Field(default_factory=list)
    filters: ConsoleTaskFilters = Field(default_factory=ConsoleTaskFilters)


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


def _todo_done(
    todo: Any,
    *,
    follow_ups: list[Any] | tuple[Any, ...] = (),
    dingtalk_links: list[Any] | tuple[Any, ...] = (),
) -> bool:
    return todo_is_done(todo, follow_ups=follow_ups, dingtalk_links=dingtalk_links)


def _todo_open(
    todo: Any,
    *,
    follow_ups: list[Any] | tuple[Any, ...] = (),
    dingtalk_links: list[Any] | tuple[Any, ...] = (),
) -> bool:
    return todo_is_open(todo, follow_ups=follow_ups, dingtalk_links=dingtalk_links)


def _todo_overdue(
    todo: Any,
    *,
    follow_ups: list[Any] | tuple[Any, ...] = (),
    dingtalk_links: list[Any] | tuple[Any, ...] = (),
) -> bool:
    if not _todo_open(todo, follow_ups=follow_ups, dingtalk_links=dingtalk_links) or not todo.deadline_at:
        return False
    try:
        deadline = datetime.fromisoformat(todo.deadline_at.replace("Z", "+00:00"))
    except ValueError:
        return False
    if deadline.tzinfo is None:
        deadline = deadline.replace(tzinfo=timezone.utc)
    return deadline < datetime.now(timezone.utc)


def _task_state(
    project: Any,
    todos: list[Any],
    *,
    follow_ups_by_todo: dict[int, tuple[Any, ...]] | None = None,
    dingtalk_links_by_todo: dict[int, tuple[Any, ...]] | None = None,
) -> str:
    return resolved_task_state(
        project,
        todos,
        follow_ups_by_todo=follow_ups_by_todo,
        dingtalk_links_by_todo=dingtalk_links_by_todo,
        overdue_checker=lambda todo: _todo_overdue(
            todo,
            follow_ups=(follow_ups_by_todo or {}).get(todo.id, ()),
            dingtalk_links=(dingtalk_links_by_todo or {}).get(todo.id, ()),
        ),
    )


def task_summary(
    project: Any,
    todos: list[Any],
    *,
    follow_ups_by_todo: dict[int, tuple[Any, ...]] | None = None,
    dingtalk_links_by_todo: dict[int, tuple[Any, ...]] | None = None,
) -> dict[str, Any]:
    progress = task_progress_summary(
        todos,
        follow_ups_by_todo=follow_ups_by_todo,
        dingtalk_links_by_todo=dingtalk_links_by_todo,
    )
    state = _task_state(
        project,
        todos,
        follow_ups_by_todo=follow_ups_by_todo,
        dingtalk_links_by_todo=dingtalk_links_by_todo,
    )
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
        "owner": resolve_task_owner_display(project, todos),
        "current_state": normalize_display_value(project.current_state),
        "next_step": normalize_display_value(project.next_step),
        "open_count": progress["open_count"],
        "open_ratio": progress["open_ratio"],
        "progress_count": progress["done_count"],
        "progress_total": progress["total"],
        "progress_ratio": progress["done_ratio"],
        "todo_count": progress["total"],
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
    follow_ups = tuple(detail.follow_ups_by_todo.get(todo.id, ()))
    dingtalk_links = tuple(detail.dingtalk_links_by_todo.get(todo.id, ()))
    payload = {
        "id": todo.id,
        "project_id": todo.project_id,
        "title": normalize_display_value(todo.title),
        "description": normalize_display_value(todo.description),
        "owner_user_id": normalize_display_value(todo.owner_user_id),
        "owner_name": normalize_display_value(todo.owner_name),
        "status": _enum_text(todo.status),
        "done": _todo_done(todo, follow_ups=follow_ups, dingtalk_links=dingtalk_links),
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
            for follow_up in follow_ups
        ],
        "dingtalk_todos": [json_safe(link) for link in dingtalk_links],
    }
    return payload


def _evidence_candidate_payload(candidate: Any) -> dict[str, Any]:
    return {
        "id": candidate.id,
        "project_id": candidate.project_id,
        "todo_id": candidate.todo_id,
        "source_type": normalize_display_value(candidate.source_type),
        "source_ref": normalize_display_value(candidate.source_ref),
        "source_created_at": normalize_display_value(candidate.source_created_at),
        "evidence_text": normalize_display_value(candidate.evidence_text),
        "reason": normalize_display_value(candidate.reason),
        "confidence": float(candidate.confidence),
        "status": _enum_text(candidate.status),
        "work_summary_input_id": candidate.work_summary_input_id,
        "decision": json_safe(_object_json(candidate.decision_json)),
        "created_at": normalize_display_value(candidate.created_at),
        "updated_at": normalize_display_value(candidate.updated_at),
        "detail_url": f"/tasks/{candidate.project_id}#evidence-{candidate.id}",
    }


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
        "evidence_candidates": [
            _evidence_candidate_payload(candidate)
            for candidate in store.list_todo_evidence_candidates(
                project_id=project.id,
                limit=50,
            )
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
    categories: set[str] = set()
    task_states: set[str] = set()
    needle = query.strip().casefold()
    projects = store.list_work_projects(limit=None)
    project_ids = [project.id for project in projects]
    todos_by_project = store.list_work_todos_for_projects(project_ids)
    all_todo_ids = [
        todo.id
        for todos in todos_by_project.values()
        for todo in todos
    ]
    all_completed_follow_ups_by_todo = {
        todo_id: tuple(follow_ups)
        for todo_id, follow_ups in store.list_follow_up_drafts_for_todos(
            all_todo_ids,
            statuses=("completed",),
        ).items()
    }
    all_dingtalk_links_by_todo = {
        todo_id: tuple(links)
        for todo_id, links in store.list_work_todo_dingtalk_links_for_todos(
            all_todo_ids
        ).items()
    }
    for project in projects:
        todos = todos_by_project.get(project.id, [])
        todo_ids = [todo.id for todo in todos]
        follow_ups_by_todo = {
            todo_id: all_completed_follow_ups_by_todo.get(todo_id, ())
            for todo_id in todo_ids
        }
        dingtalk_links_by_todo = {
            todo_id: all_dingtalk_links_by_todo.get(todo_id, ())
            for todo_id in todo_ids
        }
        row_detail = SimpleNamespace(
            follow_ups_by_todo=follow_ups_by_todo,
            dingtalk_links_by_todo=dingtalk_links_by_todo,
        )
        row = task_summary(
            project,
            todos,
            follow_ups_by_todo=follow_ups_by_todo,
            dingtalk_links_by_todo=dingtalk_links_by_todo,
        )
        if row_builder is not None:
            built = json_safe(row_builder(project, todos, row_detail))
            if isinstance(built, dict):
                row.update(
                    {
                        "title": normalize_display_value(built.get("title", row["title"])),
                        "status": normalize_display_value(built.get("status", row["status"])),
                        "category": normalize_display_value(built.get("category", row["category"])),
                        "priority": normalize_display_value(built.get("priority", row["priority"])),
                        "risk_level": normalize_display_value(built.get("riskLevel", row["risk_level"])),
                        "owner": normalize_display_value(built.get("owner") or row["owner"]),
                        "owner_name": row["owner_name"],
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
        if row["category"].strip():
            categories.add(row["category"])
        if row["status"].strip():
            task_states.add(row["status"])
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
        filters=ConsoleTaskFilters(
            categories=sorted(categories, key=str.casefold),
            task_states=sorted(task_states, key=str.casefold),
        ),
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
