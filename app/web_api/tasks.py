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
from app.task_semantic_models import (
    BusinessAttentionEvent,
    BusinessProjectCandidate,
    BusinessProjectContextRevision,
    BusinessTaskAnchorLink,
    BusinessTaskDateEvidence,
    BusinessTaskEvent,
    BusinessTaskRelation,
    BusinessProjectEvidence,
    BusinessTaskSignal,
    ProjectContext,
    ProjectCrmCustomerCandidate,
    ProjectResponsibility,
    TaskSuggestion,
)
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
    integrity_issues: list[str] = Field(default_factory=list)


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
        "detail_url": f"/tasks/legacy-project/{project.id}",
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
        "detail_url": f"/tasks/legacy-project/{todo.project_id}#todo-{todo.id}",
        "follow_ups": [
            {
                **json_safe(follow_up),
                "detail_url": f"/tasks/legacy-project/{todo.project_id}#follow-up-{follow_up.id}",
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
        "detail_url": f"/tasks/legacy-project/{candidate.project_id}#evidence-{candidate.id}",
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
        integrity_issues: list[str] = []
        if not normalize_display_value(project.title).strip():
            integrity_issues.append("missing_title")
            row["title"] = f"[数据异常：标题缺失，Project {row['id']}]"
        else:
            row["title"] = row["title"].strip()
        row["integrity_issues"] = integrity_issues
        if row["project_status"] == "archived":
            row["status"] = "archived"
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
        if not task_state.strip() and row["project_status"] == "archived":
            continue
        rows.append(row)
    if sort == "project_asc":
        rows.sort(
            key=lambda row: (
                bool(row["integrity_issues"]),
                row["title"].casefold(),
            )
        )
    elif sort == "project_desc":
        rows.sort(
            key=lambda row: (
                not bool(row["integrity_issues"]),
                row["title"].casefold(),
            ),
            reverse=True,
        )
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
        f"/tasks/legacy-project/{project_id}#todo-{todo_id}" if project_id and todo_id
        else f"/tasks/legacy-project/{project_id}" if project_id else ""
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


def business_task_sent_todo_payload(store: Any, link: Any) -> dict[str, str] | None:
    external_id = normalize_display_value(link["dingtalk_task_id"])
    if not external_id:
        return None
    task_id = int(link["business_task_id"])
    task = store.get_business_task(task_id)
    if task is None:
        return None
    return {
        "id": f"business_task_dingtalk:{link['id']}",
        "kind": "dingtalk_todo",
        "kind_label": "DingTalk Todo",
        "sent_at": normalize_display_value(link["last_push_at"] or link["created_at"]),
        "status": normalize_display_value(link["status"]),
        "owner": normalize_display_value(link["executor_name"] or link["executor_user_id"]),
        "project_title": "",
        "todo_title": normalize_display_value(link["title_snapshot"] or task.title),
        "description": normalize_display_value(task.description),
        "original_text": normalize_display_value(task.title),
        "deadline": normalize_display_value(link["deadline_at_snapshot"]),
        "priority": normalize_display_value(link["priority_snapshot"]),
        "target": external_id,
        "external_id": external_id,
        "detail_url": f"/tasks/item/{task_id}",
    }


class ConsoleBusinessAttentionSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: int
    category: str
    business_area: str
    title: str
    why_attention: str
    current_state: str
    ceo_action: str
    anchor_label: str
    linked_task_count: int
    updated_at: str
    detail_url: str


class ConsoleBusinessTaskSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: int
    title: str
    stage: str
    status: str
    commitment_status: str
    owner: str
    deadline_at: str
    business_relevance: str
    anchor_labels: list[str]
    updated_at: str
    detail_url: str
    origin: str
    suggested_owner: str
    suggestion_reason: str
    deadline_type: str
    project_customer_names: list[str] = Field(default_factory=list)


class ConsoleBusinessProjectSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: int
    title: str
    registry_source: str
    canonical_anchor_id: int
    confirmed_task_count: int
    detail_url: str
    overall_owner: str
    overall_responsibility: str
    attention_reason: str
    updated_at: str
    responsible_content: str = ""
    goal: str = ""
    deadline: str = ""
    current_status: str = ""
    source_title: str = ""
    reporting_period: str = ""
    source_url: str = ""
    source_excerpt: str = ""
    open_task_count: int = 0
    done_task_count: int = 0
    crm_customer_id: str = ""
    crm_customer_name: str = ""
    crm_customer_lookup_status: str = "not_requested"
    crm_customer_candidates: list[ProjectCrmCustomerCandidate] = Field(default_factory=list)
    crm_customer_label: str = ""


class ConsoleBusinessProjectCustomerGroup(BaseModel):
    model_config = ConfigDict(extra="forbid")
    crm_customer_id: str
    crm_customer_name: str
    projects: list[ConsoleBusinessProjectSummary]


class ConsoleBusinessProjectCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: int
    title: str
    reason: str
    status: str
    cluster_id: int
    provisional: bool
    confirmed_project_id: int | None


class ConsoleBusinessAttentionListEnvelope(ApiListEnvelope):
    items: list[ConsoleBusinessAttentionSummary] = Field(default_factory=list)


class ConsoleBusinessTaskListEnvelope(ApiListEnvelope):
    items: list[ConsoleBusinessTaskSummary] = Field(default_factory=list)


class ConsoleBusinessProjectListEnvelope(ApiListEnvelope):
    items: list[ConsoleBusinessProjectSummary] = Field(default_factory=list)
    customer_groups: list[ConsoleBusinessProjectCustomerGroup] = Field(default_factory=list)
    candidates: list[ConsoleBusinessProjectCandidate] = Field(default_factory=list)
    candidate_meta: ApiListMeta


class ConsoleBusinessAttentionDetail(BaseModel):
    model_config = ConfigDict(extra="forbid")
    summary: ConsoleBusinessAttentionSummary
    anchor: dict[str, Any]
    assessment: dict[str, Any]
    evidence_signals: list[BusinessTaskSignal]
    linked_tasks: list[ConsoleBusinessTaskSummary]
    events: list[BusinessAttentionEvent]


class ConsoleBusinessTaskDetail(BaseModel):
    model_config = ConfigDict(extra="forbid")
    summary: ConsoleBusinessTaskSummary
    description: str
    formal_basis: str
    owner_user_id: str
    missing_evidence: list[Any]
    evidence: list[dict[str, Any]]
    date_evidence: list[BusinessTaskDateEvidence]
    events: list[BusinessTaskEvent]
    relations: list[BusinessTaskRelation]
    clusters: list[dict[str, Any]]
    anchors: list[dict[str, Any]]
    official_projects: list[ConsoleBusinessProjectSummary]
    follow_ups: list[dict[str, Any]]
    dingtalk_todos: list[dict[str, Any]]
    suggestion: TaskSuggestion | None


class ConsoleBusinessProjectDetail(BaseModel):
    model_config = ConfigDict(extra="forbid")
    summary: ConsoleBusinessProjectSummary
    anchor: dict[str, Any]
    confirmed_tasks: list[ConsoleBusinessTaskSummary]
    context: ProjectContext | None
    responsibilities: list[ProjectResponsibility]
    suggestions: list[ConsoleBusinessTaskSummary]
    evidence_signals: list[BusinessTaskSignal]
    context_revisions: list[BusinessProjectContextRevision]
    evidence_meta: ApiListMeta
    context_revision_meta: ApiListMeta


class ConsoleBusinessAttentionDetailEnvelope(ApiItemEnvelope):
    item: ConsoleBusinessAttentionDetail


class ConsoleBusinessTaskDetailEnvelope(ApiItemEnvelope):
    item: ConsoleBusinessTaskDetail


class ConsoleBusinessProjectDetailEnvelope(ApiItemEnvelope):
    item: ConsoleBusinessProjectDetail


class ConsoleBusinessProjectEvidenceListEnvelope(ApiListEnvelope):
    items: list[BusinessProjectEvidence] = Field(default_factory=list)


class ConsoleBusinessTaskSignalDetailEnvelope(ApiItemEnvelope):
    item: BusinessTaskSignal


def _page(items: list[Any], *, page: int, page_size: int) -> tuple[list[Any], ApiListMeta]:
    total = len(items)
    start = (page - 1) * page_size
    return items[start : start + page_size], ApiListMeta(
        snapshot_at=snapshot_at(), page=page, page_size=page_size, total=total,
        next_cursor=str(page + 1) if start + page_size < total else "",
        has_more=start + page_size < total,
    )


def _all_business_tasks(store: Any) -> list[Any]:
    rows = []
    offset = 0
    while batch := store.list_business_tasks(limit=100, offset=offset):
        rows.extend(batch)
        offset += len(batch)
    return rows


def _all_pages(list_method: Any, **kwargs: Any) -> list[Any]:
    rows = []
    offset = 0
    while batch := list_method(limit=100, offset=offset, **kwargs):
        rows.extend(batch)
        offset += len(batch)
    return rows


def _anchors_by_id(store: Any) -> dict[int, Any]:
    rows = []
    offset = 0
    while batch := store.list_business_anchors(limit=100, offset=offset):
        rows.extend(batch)
        offset += len(batch)
    return {row.id: row for row in rows}


def _clusters_by_id(store: Any) -> dict[int, Any]:
    rows = []
    offset = 0
    while batch := store.list_business_work_clusters(limit=100, offset=offset):
        rows.extend(batch)
        offset += len(batch)
    return {row.id: row for row in rows}


def _task_summary_payload(store: Any, task: Any, anchors: dict[int, Any]) -> dict[str, Any]:
    links = _all_pages(store.list_business_task_anchor_links, task_id=task.id)
    labels = [anchors[link.anchor_id].title for link in links
              if link.status.value == "confirmed" and link.active and link.anchor_id in anchors and anchors[link.anchor_id].active]
    suggestion = TaskSuggestion.model_validate_json(task.suggestion_json) if task.origin == "agent_suggestion" and task.stage.value == "candidate" else None
    project_customer_names = list(
        store.list_business_task_project_customer_names(task_id=task.id)
    )
    matching_dates = [row for row in store.list_business_task_date_evidence(task.id)
                      if task.deadline_at and row.value_at == task.deadline_at
                      and row.date_type.value in {"requested_deadline_at", "committed_deadline_at", "external_deadline_at"}]
    return {
        "id": task.id, "title": task.title, "stage": task.stage.value,
        "status": task.status.value, "commitment_status": task.commitment_status.value,
        "owner": task.owner_name or task.owner_user_id, "deadline_at": task.deadline_at,
        "business_relevance": task.business_relevance.value,
        "anchor_labels": labels, "updated_at": task.updated_at,
        "detail_url": f"/tasks/item/{task.id}",
        "origin": task.origin,
        "suggested_owner": (suggestion.suggested_owner_name or suggestion.suggested_owner_user_id) if suggestion else "",
        "suggestion_reason": suggestion.reason if suggestion else "",
        "deadline_type": matching_dates[-1].date_type.value if matching_dates else "",
        "project_customer_names": project_customer_names,
    }


def business_task_list_response(store: Any, *, page: int, page_size: int,
                                query: str = "", stage: str = "", status: str = "",
                                business_relevance: str = "", owner: str = "",
                                sort: str = "updated") -> ConsoleBusinessTaskListEnvelope:
    if owner not in ("", "assigned", "unassigned"):
        raise ValueError(f"unknown owner filter: {owner}")
    if sort not in ("updated", "created"):
        raise ValueError(f"unknown sort: {sort}")
    needle = query.strip().casefold()
    tasks = [task for task in _all_business_tasks(store)
             if (not stage or task.stage.value == stage)
             and (not status or task.status.value == status)
             and (not owner or bool(task.owner_name or task.owner_user_id) == (owner == "assigned"))
             and (not business_relevance or task.business_relevance.value == business_relevance)
             and (not needle or needle in f"{task.title} {task.description} {task.owner_name}".casefold())]
    tasks.sort(key=lambda task: (task.created_at if sort == "created" else task.updated_at, task.id), reverse=True)
    selected, meta = _page(tasks, page=page, page_size=page_size)
    anchors = _anchors_by_id(store)
    return ConsoleBusinessTaskListEnvelope(
        items=[ConsoleBusinessTaskSummary.model_validate(_task_summary_payload(store, task, anchors)) for task in selected],
        meta=meta,
    )


def _attention_summary_payload(store: Any, item: Any, anchors: dict[int, Any]) -> dict[str, Any]:
    anchor = anchors.get(item.anchor_id)
    return {
        "id": item.id, "category": item.category.value, "business_area": item.business_area,
        "title": item.title, "why_attention": item.why_attention,
        "current_state": item.current_state, "ceo_action": item.ceo_action,
        "anchor_label": anchor.title if anchor else "",
        "linked_task_count": len(store.list_business_attention_tasks(item.id)),
        "updated_at": item.updated_at, "detail_url": f"/tasks/attention/{item.id}",
    }


def business_attention_list_response(store: Any, *, page: int, page_size: int,
                                     query: str = "", category: str = "", status: str = "active") -> ConsoleBusinessAttentionListEnvelope:
    needle = query.strip().casefold()
    items = [item for item in store.list_business_attention_items()
             if (not category or item.category.value == category)
             and (not status or item.status.value == status)
             and (not needle or needle in f"{item.title} {item.business_area} {item.why_attention} {item.current_state}".casefold())]
    ranks = {"decision": 0, "push": 1, "watch": 2, "fyi": 3}
    items.sort(key=lambda item: (-ranks[item.category.value], item.updated_at, item.id), reverse=True)
    selected, meta = _page(items, page=page, page_size=page_size)
    anchors = _anchors_by_id(store)
    return ConsoleBusinessAttentionListEnvelope(
        items=[ConsoleBusinessAttentionSummary.model_validate(_attention_summary_payload(store, item, anchors)) for item in selected],
        meta=meta,
    )


def _confirmed_project_ids_by_task(store: Any) -> dict[int, set[int]]:
    """Load active Task→Project membership once for summary projections."""
    with store._connect() as db:
        rows = db.execute(
            """
            select link.task_id, project.id
            from business_task_anchor_links link
            join business_projects project on project.canonical_anchor_id=link.anchor_id
            join business_anchors anchor on anchor.id=link.anchor_id
            where link.status='confirmed' and link.active=1 and anchor.active=1
            """
        ).fetchall()
    result: dict[int, set[int]] = {}
    for row in rows:
        result.setdefault(int(row[0]), set()).add(int(row[1]))
    return result


def _project_summary_payload(
    store: Any,
    project: Any,
    tasks: list[Any],
    project_ids_by_task: dict[int, set[int]] | None = None,
) -> dict[str, Any]:
    if project_ids_by_task is None:
        project_ids_by_task = _confirmed_project_ids_by_task(store)
    linked_tasks = [
        task for task in tasks
        if not (task.origin == "agent_suggestion" and task.stage.value == "candidate")
        and project.id in project_ids_by_task.get(task.id, set())
    ]
    context = store.get_business_project_context(project.id)
    owner = context.overall_owner if context else None
    with store._connect() as db:
        revision_time = db.execute("select max(created_at) from business_project_context_revisions where project_id=?", (project.id,)).fetchone()[0]
        evidence_time = db.execute("select max(created_at) from business_project_evidence where project_id=?", (project.id,)).fetchone()[0]
        attention_reasons = [row[0] for row in db.execute(
            "select why_attention from business_attention_items where anchor_id=? and status='active' order by id", (project.canonical_anchor_id,)
        )]
    return {
        "id": project.id,
        "title": project.title,
        "registry_source": project.registry_source,
        "canonical_anchor_id": project.canonical_anchor_id,
        "confirmed_task_count": len(linked_tasks),
        "detail_url": f"/tasks/project/{project.id}",
        "overall_owner": owner.person_name if owner else "",
        "overall_responsibility": owner.responsibility if owner else "",
        "responsible_content": owner.responsibility if owner else "",
        "goal": context.goal if context else "",
        "current_status": "\n".join(fact.text for fact in context.facts) if context else "",
        "attention_reason": "\n".join(attention_reasons),
        "updated_at": max(time for time in (project.created_at, revision_time, evidence_time) if time),
        "open_task_count": sum(task.status.value not in {"done", "cancelled"} for task in linked_tasks),
        "done_task_count": sum(task.status.value == "done" for task in linked_tasks),
        "crm_customer_id": project.crm_customer_id,
        "crm_customer_name": project.crm_customer_name,
        "crm_customer_lookup_status": project.crm_customer_lookup_status,
        "crm_customer_candidates": project.crm_customer_candidates,
        "crm_customer_label": project.crm_customer_label,
    }


def business_project_list_response(store: Any, *, page: int, page_size: int, query: str = "",
                                   candidate_page: int = 1, candidate_page_size: int = 20,
                                   group_by_customer: bool = False) -> ConsoleBusinessProjectListEnvelope:
    needle = query.strip().casefold()
    projects = [row for row in store.list_business_projects(limit=None)
                if (not needle or needle in f"{row.title} {row.crm_customer_name}".casefold())]
    tasks = _all_business_tasks(store)
    project_ids_by_task = _confirmed_project_ids_by_task(store)
    project_summaries = {
        project.id: ConsoleBusinessProjectSummary.model_validate(
            _project_summary_payload(store, project, tasks, project_ids_by_task)
        )
        for project in projects
    }
    customer_groups = []
    if group_by_customer:
        grouped: dict[str, list[ConsoleBusinessProjectSummary]] = {}
        labels: dict[str, str] = {}
        for project in projects:
            if not project.crm_customer_id:
                continue
            grouped.setdefault(project.crm_customer_id, []).append(project_summaries[project.id])
            labels[project.crm_customer_id] = project.crm_customer_name or project.crm_customer_id
        ordered_groups = [
            ConsoleBusinessProjectCustomerGroup(
                crm_customer_id=customer_id,
                crm_customer_name=labels[customer_id],
                projects=sorted(rows, key=lambda row: (row.title.casefold(), row.id)),
            )
            for customer_id, rows in sorted(
                grouped.items(), key=lambda pair: (labels[pair[0]].casefold(), pair[0])
            )
        ]
        selected_groups, meta = _page(ordered_groups, page=page, page_size=page_size)
        selected = [project for group in selected_groups for project in group.projects]
        customer_groups = selected_groups
    else:
        selected, meta = _page(projects, page=page, page_size=page_size)
    candidate_start = (candidate_page - 1) * candidate_page_size
    with store._connect() as db:
        if needle:
            candidate_total = int(db.execute(
                "select count(*) from business_project_candidates where status='proposed' and instr(lower(title), lower(?)) > 0",
                (query.strip(),),
            ).fetchone()[0])
            candidate_rows = db.execute(
                "select * from business_project_candidates where status='proposed' and instr(lower(title), lower(?)) > 0 order by id limit ? offset ?",
                (query.strip(), candidate_page_size, candidate_start),
            ).fetchall()
        else:
            candidate_total = int(db.execute(
                "select count(*) from business_project_candidates where status='proposed'"
            ).fetchone()[0])
            candidate_rows = db.execute(
                "select * from business_project_candidates where status='proposed' order by id limit ? offset ?",
                (candidate_page_size, candidate_start),
            ).fetchall()
    candidates = [BusinessProjectCandidate.model_validate(dict(row)) for row in candidate_rows]
    return ConsoleBusinessProjectListEnvelope(
        items=[project_summaries[project.id] for project in selected],
        customer_groups=customer_groups,
        candidates=[ConsoleBusinessProjectCandidate(id=row.id, title=row.title, reason=row.reason,
                    status=row.status.value, cluster_id=row.cluster_id, provisional=row.status.value == "proposed",
                    confirmed_project_id=row.confirmed_project_id) for row in candidates],
        meta=meta,
        candidate_meta=ApiListMeta(
            snapshot_at=snapshot_at(), page=candidate_page, page_size=candidate_page_size,
            total=candidate_total,
            next_cursor=str(candidate_page + 1) if candidate_start + candidate_page_size < candidate_total else "",
            has_more=candidate_start + candidate_page_size < candidate_total,
        ),
    )


def business_attention_detail(store: Any, attention_id: int) -> ConsoleBusinessAttentionDetail | None:
    item = store.get_business_attention_item(attention_id)
    if item is None:
        return None
    anchors = _anchors_by_id(store)
    linked = [store.get_business_task(link.task_id) for link in store.list_business_attention_tasks(attention_id)]
    evidence_ids = {item.evidence_signal_id}
    if item.resolution_signal_id:
        evidence_ids.add(item.resolution_signal_id)
    events = list(store.list_business_attention_events(attention_id))
    evidence_ids.update(event.signal_id for event in events)
    assessment = _object_json(item.assessment_json)
    evidence_ids.update(evidence["signal_id"] for evidence in assessment.get("evidence", []))
    return ConsoleBusinessAttentionDetail(
        summary=ConsoleBusinessAttentionSummary.model_validate(_attention_summary_payload(store, item, anchors)),
        anchor=json_safe(anchors[item.anchor_id]) if item.anchor_id in anchors else {},
        assessment=assessment,
        evidence_signals=[signal for signal_id in sorted(evidence_ids) if (signal := store.get_business_task_signal(signal_id)) is not None],
        linked_tasks=[ConsoleBusinessTaskSummary.model_validate(_task_summary_payload(store, task, anchors)) for task in linked if task is not None],
        events=events,
    )


def business_task_detail(store: Any, task_id: int) -> ConsoleBusinessTaskDetail | None:
    task = store.get_business_task(task_id)
    if task is None:
        return None
    anchors = _anchors_by_id(store)
    evidence = []
    for link in store.list_business_task_evidence(task_id):
        signal = store.get_business_task_signal(link.signal_id)
        evidence.append({"role": link.evidence_role.value, "signal": json_safe(signal) if signal else None})
    cluster_links = []
    offset = 0
    while batch := store.list_business_work_cluster_tasks(task_id=task_id, limit=100, offset=offset):
        cluster_links.extend(batch)
        offset += len(batch)
    clusters = _clusters_by_id(store)
    projects = store.list_business_task_project_links(task_id=task_id)
    project_members = _all_business_tasks(store) if projects else []
    project_ids_by_task = _confirmed_project_ids_by_task(store) if projects else {}
    anchor_links: list[BusinessTaskAnchorLink] = _all_pages(store.list_business_task_anchor_links, task_id=task_id)
    follow_ups = [dict(row) for row in _all_pages(store.list_business_task_follow_ups, business_task_id=task_id)]
    dingtalk_todos = [dict(row) for row in _all_pages(store.list_business_task_dingtalk_links, business_task_id=task_id)]
    return ConsoleBusinessTaskDetail(
        summary=ConsoleBusinessTaskSummary.model_validate(_task_summary_payload(store, task, anchors)),
        description=task.description, formal_basis=task.formal_basis.value if task.formal_basis else "",
        owner_user_id=task.owner_user_id, missing_evidence=_list_json(task.missing_evidence_json),
        evidence=evidence, date_evidence=list(store.list_business_task_date_evidence(task_id)),
        events=list(store.list_business_task_events(task_id)),
        relations=_all_pages(store.list_business_task_relations, task_id=task_id),
        clusters=[{"membership": json_safe(link), "cluster": json_safe(clusters.get(link.cluster_id))} for link in cluster_links],
        anchors=[{"link": json_safe(link), "anchor": json_safe(anchors.get(link.anchor_id))} for link in anchor_links],
        official_projects=[ConsoleBusinessProjectSummary.model_validate(
            _project_summary_payload(store, project, project_members, project_ids_by_task)
        ) for project in projects],
        follow_ups=follow_ups, dingtalk_todos=dingtalk_todos,
        suggestion=TaskSuggestion.model_validate_json(task.suggestion_json) if task.origin == "agent_suggestion" else None,
    )


def business_project_detail(store: Any, project_id: int) -> ConsoleBusinessProjectDetail | None:
    project = store.get_business_project(project_id)
    if project is None:
        return None
    all_tasks = _all_business_tasks(store)
    project_ids_by_task = _confirmed_project_ids_by_task(store)
    tasks = [task for task in all_tasks if project.id in project_ids_by_task.get(task.id, set())]
    anchors = _anchors_by_id(store)
    context = store.get_business_project_context(project_id)
    citations = (
        [citation for responsibility in ([context.overall_owner] if context.overall_owner else []) + context.responsibilities for citation in responsibility.evidence]
        + [citation for fact in context.facts for citation in fact.evidence]
    ) if context else []
    proof_ids = tuple(sorted({citation.signal_id for citation in citations if citation.signal_id is not None}))
    evidence = store.list_business_project_evidence(project_id, limit=20, pinned_signal_ids=proof_ids)
    revisions = store.list_business_project_context_revisions(project_id, limit=20)
    with store._connect() as db:
        evidence_count = db.execute("select count(*) from business_project_evidence where project_id=?", (project_id,)).fetchone()[0]
        revision_count = db.execute("select count(*) from business_project_context_revisions where project_id=?", (project_id,)).fetchone()[0]
    def metadata(total: int, visible: int) -> ApiListMeta:
        return ApiListMeta(snapshot_at=snapshot_at(), page=1, page_size=20, total=total,
                           has_more=total > visible, next_cursor="")
    return ConsoleBusinessProjectDetail(
        summary=ConsoleBusinessProjectSummary.model_validate(
            _project_summary_payload(store, project, tasks, project_ids_by_task)
        ),
        anchor=json_safe(anchors[project.canonical_anchor_id]) if project.canonical_anchor_id in anchors else {},
        confirmed_tasks=[ConsoleBusinessTaskSummary.model_validate(_task_summary_payload(store, task, anchors)) for task in tasks if not (task.origin == "agent_suggestion" and task.stage.value == "candidate")],
        context=context,
        responsibilities=list(context.responsibilities) if context else [],
        suggestions=[ConsoleBusinessTaskSummary.model_validate(_task_summary_payload(store, task, anchors)) for task in tasks if task.origin == "agent_suggestion" and task.stage.value == "candidate"],
        evidence_signals=[signal for proof in evidence if (signal := store.get_business_task_signal(proof.signal_id)) is not None],
        context_revisions=list(revisions), evidence_meta=metadata(evidence_count, len(evidence)), context_revision_meta=metadata(revision_count, len(revisions)),
    )


def business_project_evidence_response(
    store: Any, project_id: int, *, page: int, page_size: int
) -> ConsoleBusinessProjectEvidenceListEnvelope | None:
    if store.get_business_project(project_id) is None:
        return None
    offset = (page - 1) * page_size
    evidence = store.list_business_project_evidence(
        project_id,
        limit=page_size,
        offset=offset,
    )
    with store._connect() as db:
        total = int(
            db.execute(
                "select count(*) from business_project_evidence where project_id=?",
                (project_id,),
            ).fetchone()[0]
        )
    has_more = offset + len(evidence) < total
    return ConsoleBusinessProjectEvidenceListEnvelope(
        items=list(evidence),
        meta=ApiListMeta(
            snapshot_at=snapshot_at(),
            page=page,
            page_size=page_size,
            total=total,
            next_cursor=str(page + 1) if has_more else "",
            has_more=has_more,
        ),
    )
