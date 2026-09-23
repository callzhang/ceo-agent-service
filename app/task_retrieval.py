import json
import math
import re
from dataclasses import dataclass
from typing import Any

from app.store import AutoReplyStore
from app.task_models import WorkItem, WorkProject, WorkTodo
from app.task_semantic_models import (
    BusinessAnchor, BusinessProject, BusinessTask, BusinessTaskAnchorLink,
    BusinessTaskEvidence, BusinessTaskRelation, BusinessTaskSignal,
    BusinessWorkCluster,
)


TOKEN_RE = re.compile(r"[A-Za-z0-9_]+|[\u4e00-\u9fff]")


@dataclass(frozen=True)
class ProjectCandidate:
    project: WorkProject
    score: float
    document: str


@dataclass(frozen=True)
class ProjectTaskDetail:
    project: WorkProject
    score: float
    match_reasons: tuple[str, ...]
    todos: tuple[WorkTodo, ...]
    updates: tuple[object, ...]
    follow_ups_by_todo: dict[int, tuple[object, ...]]
    dingtalk_links_by_todo: dict[int, tuple[object, ...]]


@dataclass(frozen=True)
class TaskSemanticContext:
    task_candidates: tuple[BusinessTask, ...]
    formal_tasks: tuple[BusinessTask, ...]
    task_evidence: tuple[BusinessTaskEvidence, ...]
    evidence_signals: tuple[BusinessTaskSignal, ...]
    task_relations: tuple[BusinessTaskRelation, ...]
    task_anchor_links: tuple[BusinessTaskAnchorLink, ...]
    clusters: tuple[BusinessWorkCluster, ...]
    anchors: tuple[BusinessAnchor, ...]
    official_projects: tuple[BusinessProject, ...]


def _all_pages(fetch, *, page_size: int = 100):
    offset = 0
    while True:
        page = fetch(limit=page_size, offset=offset)
        yield from page
        if len(page) < page_size:
            return
        offset += len(page)


def _semantic_score(query_terms: set[str], document: str) -> int:
    return len(query_terms.intersection(tokenize(document)))


def _bounded_task_evidence(
    rows: tuple[BusinessTaskEvidence, ...], *, recent_limit: int
) -> tuple[BusinessTaskEvidence, ...]:
    """Keep each role's original and latest proof plus a bounded recent sample."""
    first_by_role: dict[str, BusinessTaskEvidence] = {}
    latest_by_role: dict[str, BusinessTaskEvidence] = {}
    for row in rows:
        role = row.evidence_role.value
        first_by_role.setdefault(role, row)
        latest_by_role[role] = row
    kept = set(first_by_role.values()) | set(latest_by_role.values())
    kept.update(rows[-recent_limit:])
    return tuple(row for row in rows if row in kept)


def retrieve_task_semantic_context(
    store: AutoReplyStore, work_item: WorkItem, *, limit_per_kind: int = 20
) -> TaskSemanticContext:
    if limit_per_kind < 1:
        raise ValueError("limit_per_kind must be positive")
    query_terms = set(tokenize(work_item.summary))
    conversation_task_ids = set(_all_pages(
        lambda *, limit, offset: store.list_business_task_ids_for_conversation(
            conversation_id=work_item.source.conversation_id, limit=limit, offset=offset
        )
    ))
    tasks = _all_pages(store.list_business_tasks)
    ranked_tasks = sorted(
        (
            (
                _semantic_score(query_terms, f"{task.id} {task.title} {task.description} {task.owner_name}")
                + (2 if task.id in conversation_task_ids else 0),
                task,
            )
            for task in tasks
            if task.status.value != "merged"
        ),
        key=lambda pair: (-pair[0], -pair[1].id),
    )
    candidates = tuple(
        task for score, task in ranked_tasks
        if score > 0 and task.stage.value == "candidate"
    )[:limit_per_kind]
    formal = tuple(
        task for score, task in ranked_tasks
        if score > 0 and task.stage.value == "formal"
    )[:limit_per_kind]
    selected = candidates + formal
    evidence = tuple(
        row for task in selected
        for row in _bounded_task_evidence(
            store.list_business_task_evidence(task.id), recent_limit=limit_per_kind
        )
    )
    signals = tuple(
        signal for signal_id in sorted({row.signal_id for row in evidence})
        if (signal := store.get_business_task_signal(signal_id)) is not None
    )
    relations = tuple(dict.fromkeys(
        row for task in selected
        for row in _all_pages(
            lambda *, limit, offset, task_id=task.id: store.list_business_task_relations(
                task_id=task_id, limit=limit, offset=offset
            )
        )
    ))[:limit_per_kind]
    links = tuple(
        row for task in selected
        for row in _all_pages(
            lambda *, limit, offset, task_id=task.id: store.list_business_task_anchor_links(
                task_id=task_id, limit=limit, offset=offset
            )
        )
    )[:limit_per_kind]
    clusters = sorted(
        _all_pages(store.list_business_work_clusters),
        key=lambda cluster: (-_semantic_score(query_terms, cluster.title), cluster.id),
    )
    anchors = sorted(
        _all_pages(store.list_business_anchors),
        key=lambda anchor: (-_semantic_score(query_terms, anchor.title), anchor.id),
    )
    projects = sorted(
        _all_pages(store.list_business_projects),
        key=lambda project: (-_semantic_score(query_terms, project.title), project.id),
    )
    return TaskSemanticContext(
        task_candidates=candidates,
        formal_tasks=formal,
        task_evidence=evidence,
        evidence_signals=signals,
        task_relations=relations,
        task_anchor_links=links,
        clusters=tuple(clusters[:limit_per_kind]),
        anchors=tuple(anchors[:limit_per_kind]),
        official_projects=tuple(projects[:limit_per_kind]),
    )


def render_task_semantic_context(context: TaskSemanticContext) -> str:
    payload = {
        "notice": "Similarity rank is context only; never authority or confirmation for identity, acceptance, anchor match, or official Project creation.",
        "task_candidates": [_model_payload(value) for value in context.task_candidates],
        "existing_formal_tasks": [_model_payload(value) for value in context.formal_tasks],
        "task_evidence": [_model_payload(value) for value in context.task_evidence],
        "source_signals": [_model_payload(value) for value in context.evidence_signals],
        "task_relations": [_model_payload(value) for value in context.task_relations],
        "task_anchor_links": [_model_payload(value) for value in context.task_anchor_links],
        "work_clusters": [_model_payload(value) for value in context.clusters],
        "registered_anchors": [_model_payload(value) for value in context.anchors],
        "official_project_registry": [_model_payload(value) for value in context.official_projects],
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


def tokenize(text: str) -> list[str]:
    return [match.group(0).casefold() for match in TOKEN_RE.finditer(text or "")]


def resolve_task_owner_display(project: WorkProject, todos: tuple[WorkTodo, ...] | list[WorkTodo]) -> str:
    for value in (project.owner_name, project.owner_user_id):
        owner = str(value or "").strip()
        if owner:
            return owner

    todo_owners: list[str] = []
    seen: set[str] = set()
    for todo in todos:
        owner = str(todo.owner_name or todo.owner_user_id or "").strip()
        if not owner or owner in seen:
            continue
        seen.add(owner)
        todo_owners.append(owner)
    if len(todo_owners) == 1:
        return todo_owners[0]
    if len(todo_owners) > 1:
        visible = "、".join(todo_owners[:3])
        if len(todo_owners) > 3:
            return f"多人：{visible} 等 {len(todo_owners)} 人"
        return f"多人：{visible}"
    return ""


def project_document(project: WorkProject) -> str:
    fields = [
        project.title,
        _enum_value(project.category),
        project.tags_json,
        project.owner_name,
        project.owner_user_id,
        project.goal,
        project.background,
        project.facts_json,
        project.current_state,
        project.blocker,
        project.next_step,
        project.source_conversations_json,
    ]
    return "\n".join(str(field) for field in fields if field)


def _structured_project_candidate(
    store: AutoReplyStore,
    summary: str,
) -> WorkProject | None:
    try:
        payload = json.loads(summary)
    except (TypeError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None

    project_payload = payload.get("project")
    if not isinstance(project_payload, dict):
        return None
    project_id = project_payload.get("id")
    if (
        isinstance(project_id, bool)
        or not isinstance(project_id, int)
        or project_id <= 0
    ):
        return None

    project = store.get_work_project(project_id)
    if project is None or project.status.value not in {"active", "waiting"}:
        return None
    return project


def retrieve_project_candidates(
    store: AutoReplyStore,
    *,
    summary: str,
    project_name: str = "",
    limit: int = 5,
) -> list[ProjectCandidate]:
    if limit <= 0:
        return []

    query_terms = tokenize(f"{project_name}\n{summary}")
    if not query_terms:
        return []

    # Candidate ranking must see the complete active corpus. A recency cap makes
    # older valid projects impossible to match and encourages duplicate creates.
    projects = store.list_work_projects(statuses=("active", "waiting"))
    if not projects:
        return []

    documents: list[tuple[WorkProject, str, list[str], dict[str, int]]] = []
    document_frequency: dict[str, int] = {}
    for project in projects:
        document = project_document(project)
        terms = tokenize(document)
        term_counts: dict[str, int] = {}
        for term in terms:
            term_counts[term] = term_counts.get(term, 0) + 1
        for term in term_counts:
            document_frequency[term] = document_frequency.get(term, 0) + 1
        documents.append((project, document, terms, term_counts))

    doc_count = len(documents)
    average_length = sum(len(terms) for _, _, terms, _ in documents) / doc_count
    query_vocabulary = set(query_terms)
    candidates: list[ProjectCandidate] = []
    k1 = 1.2
    b = 0.75

    for project, document, terms, term_counts in documents:
        document_length = len(terms)
        score = 0.0
        for term in query_vocabulary:
            term_frequency = term_counts.get(term, 0)
            if term_frequency == 0:
                continue
            df = document_frequency[term]
            idf = math.log(1 + (doc_count - df + 0.5) / (df + 0.5))
            denominator = term_frequency + k1 * (
                1 - b + b * document_length / average_length
            )
            score += idf * term_frequency * (k1 + 1) / denominator
        if score > 0:
            candidates.append(
                ProjectCandidate(project=project, score=score, document=document)
            )

    candidates.sort(key=lambda candidate: (-candidate.score, candidate.project.id))
    structured_project = _structured_project_candidate(store, summary)
    if structured_project is not None:
        structured_score = max(
            (candidate.score for candidate in candidates), default=0.0
        ) + 1.0
        candidates = [
            candidate
            for candidate in candidates
            if candidate.project.id != structured_project.id
        ]
        candidates.insert(
            0,
            ProjectCandidate(
                project=structured_project,
                score=structured_score,
                document=project_document(structured_project),
            ),
        )
    return candidates[:limit]


def render_candidate_prompt(candidates: list[ProjectCandidate]) -> str:
    payload = []
    for candidate in candidates:
        project = candidate.project
        try:
            memory_context = json.loads(project.memory_context_json or "{}")
        except (TypeError, json.JSONDecodeError):
            memory_context = {}
        payload.append(
            {
                "id": project.id,
                "score": round(candidate.score, 4),
                "title": project.title,
                "category": _enum_value(project.category),
                "tags": _parse_json_list(project.tags_json),
                "owner": project.owner_name or project.owner_user_id,
                "owner_name": project.owner_name,
                "owner_user_id": project.owner_user_id,
                "goal": project.goal,
                "background": project.background,
                "facts": _parse_json_list(project.facts_json),
                "current_state": project.current_state,
                "blocker": project.blocker,
                "next_step": project.next_step,
                "memory_context": memory_context,
                "source_conversations": _parse_json_list(
                    project.source_conversations_json
                ),
            }
        )
    return json.dumps(payload, ensure_ascii=False, indent=2)


def retrieve_project_task_details(
    store: AutoReplyStore,
    *,
    query: str,
    conversation_id: str = "",
    owner_user_id: str = "",
    limit: int = 3,
    todos_per_project: int = 8,
    updates_per_project: int = 5,
    follow_ups_per_todo: int = 5,
) -> list[ProjectTaskDetail]:
    if limit <= 0:
        return []

    query = query.strip()
    conversation_id = conversation_id.strip()
    owner_user_id = owner_user_id.strip()
    if not query and not conversation_id and not owner_user_id:
        return []

    bm25_candidates = retrieve_project_candidates(
        store,
        summary=query,
        limit=max(limit * 4, limit),
    )
    project_scores: dict[int, float] = {
        candidate.project.id: candidate.score for candidate in bm25_candidates
    }
    project_reasons: dict[int, set[str]] = {
        candidate.project.id: {"text_match"} for candidate in bm25_candidates
    }
    projects_by_id: dict[int, WorkProject] = {
        candidate.project.id: candidate.project for candidate in bm25_candidates
    }
    todo_owner_project_ids = (
        store.list_work_project_ids_for_todo_owner(owner_user_id)
        if owner_user_id
        else set()
    )

    for project in store.list_work_projects(statuses=("active", "waiting"), limit=500):
        reasons = project_reasons.setdefault(project.id, set())
        if _source_conversations_include(project.source_conversations_json, conversation_id):
            project_scores[project.id] = project_scores.get(project.id, 0.0) + 100.0
            reasons.add("source_conversation_match")
        if owner_user_id and project.owner_user_id == owner_user_id:
            project_scores[project.id] = project_scores.get(project.id, 0.0) + 40.0
            reasons.add("project_owner_match")
        if project.id in todo_owner_project_ids:
            project_scores[project.id] = project_scores.get(project.id, 0.0) + 30.0
            reasons.add("todo_owner_match")
        if project.id in project_scores:
            projects_by_id[project.id] = project

    ranked_project_ids = sorted(
        project_scores,
        key=lambda project_id: (-project_scores[project_id], project_id),
    )[:limit]
    details: list[ProjectTaskDetail] = []
    for project_id in ranked_project_ids:
        project = projects_by_id[project_id]
        todos = tuple(store.list_work_todos(project_id=project.id)[:todos_per_project])
        todo_ids = [todo.id for todo in todos]
        links_by_todo = {
            todo_id: tuple(links)
            for todo_id, links in store.list_work_todo_dingtalk_links_for_todos(
                todo_ids
            ).items()
        }
        follow_up_rows_by_todo = store.list_follow_up_drafts_for_todos(todo_ids)
        follow_ups_by_todo = {
            todo.id: tuple(follow_up_rows_by_todo.get(todo.id, ())[:follow_ups_per_todo])
            for todo in todos
        }
        details.append(
            ProjectTaskDetail(
                project=project,
                score=project_scores[project_id],
                match_reasons=tuple(sorted(project_reasons.get(project_id, ()))),
                todos=todos,
                updates=tuple(store.list_work_updates(project.id, limit=updates_per_project)),
                follow_ups_by_todo=follow_ups_by_todo,
                dingtalk_links_by_todo=links_by_todo,
            )
        )
    return details


def load_project_task_detail(
    store: AutoReplyStore,
    project_id: int,
    *,
    todos_per_project: int | None = None,
    updates_per_project: int = 50,
    follow_ups_per_todo: int = 20,
) -> ProjectTaskDetail | None:
    project = store.get_work_project(project_id)
    if project is None:
        return None
    todos = store.list_work_todos(project_id=project.id)
    if todos_per_project is not None:
        todos = todos[:todos_per_project]
    todo_ids = [todo.id for todo in todos]
    links_by_todo = {
        todo_id: tuple(links)
        for todo_id, links in store.list_work_todo_dingtalk_links_for_todos(
            todo_ids
        ).items()
    }
    follow_up_rows_by_todo = store.list_follow_up_drafts_for_todos(todo_ids)
    follow_ups_by_todo = {
        todo.id: tuple(follow_up_rows_by_todo.get(todo.id, ())[:follow_ups_per_todo])
        for todo in todos
    }
    return ProjectTaskDetail(
        project=project,
        score=0.0,
        match_reasons=("project_id",),
        todos=tuple(todos),
        updates=tuple(store.list_work_updates(project.id, limit=updates_per_project)),
        follow_ups_by_todo=follow_ups_by_todo,
        dingtalk_links_by_todo=links_by_todo,
    )


def render_project_task_details(details: list[ProjectTaskDetail]) -> str:
    if not details:
        return ""

    payload = []
    for detail in details:
        project = detail.project
        payload.append(
            {
                "match": {
                    "score": round(detail.score, 4),
                    "reasons": list(detail.match_reasons),
                },
                "project": {
                    "id": project.id,
                    "detail_url": f"/tasks/{project.id}",
                    "title": project.title,
                    "category": _enum_value(project.category),
                    "status": _enum_value(project.status),
                    "priority": _enum_value(project.priority),
                    "risk_level": _enum_value(project.risk_level),
                    "owner": resolve_task_owner_display(project, detail.todos),
                    "owner_user_id": project.owner_user_id,
                    "owner_name": project.owner_name,
                    "goal": project.goal,
                    "background": project.background,
                    "facts": _parse_json_list(project.facts_json),
                    "current_state": project.current_state,
                    "blocker": project.blocker,
                    "next_step": project.next_step,
                    "next_follow_up_at": project.next_follow_up_at,
                    "source_conversations": _parse_json_list(
                        project.source_conversations_json
                    ),
                },
                "todos": [
                    {
                        "id": todo.id,
                        "detail_url": f"/tasks/{project.id}#todo-{todo.id}",
                        "title": todo.title,
                        "description": todo.description,
                        "status": _enum_value(todo.status),
                        "priority": _enum_value(todo.priority),
                        "owner": todo.owner_name or todo.owner_user_id,
                        "owner_user_id": todo.owner_user_id,
                        "owner_name": todo.owner_name,
                        "deadline_at": todo.deadline_at,
                        "next_follow_up_at": todo.next_follow_up_at,
                        "follow_up_question": todo.follow_up_question,
                        "blocker": todo.blocker,
                        "completion_evidence": _parse_json_object(
                            todo.completion_evidence_json
                        ),
                        "dingtalk_todos": [
                            _model_payload(link)
                            for link in detail.dingtalk_links_by_todo.get(todo.id, ())
                        ],
                        "follow_ups": [
                            {
                                **_model_payload(follow_up),
                                "detail_url": (
                                    f"/tasks/{project.id}#follow-up-{follow_up.id}"
                                ),
                            }
                            for follow_up in detail.follow_ups_by_todo.get(todo.id, ())
                        ],
                    }
                    for todo in detail.todos
                ],
                "recent_updates": [
                    {
                        **_model_payload(update),
                        "changes": _parse_json_object(
                            str(getattr(update, "changes_json", "{}"))
                        ),
                    }
                    for update in detail.updates
                ],
            }
        )
    return json.dumps(payload, ensure_ascii=False, indent=2)


def _parse_json_list(value: str) -> list[object]:
    try:
        parsed = json.loads(value or "[]")
    except json.JSONDecodeError:
        return []
    return parsed if isinstance(parsed, list) else []


def _parse_json_object(value: str) -> dict[str, object]:
    try:
        parsed = json.loads(value or "{}")
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _source_conversations_include(value: str, conversation_id: str) -> bool:
    if not conversation_id:
        return False
    for item in _parse_json_list(value):
        if isinstance(item, dict) and str(item.get("id") or "").strip() == conversation_id:
            return True
    return False


def _model_payload(value: object) -> dict[str, Any]:
    dump = getattr(value, "model_dump", None)
    if callable(dump):
        payload = dump(mode="json")
        return payload if isinstance(payload, dict) else {}
    if hasattr(value, "__dict__"):
        return dict(value.__dict__)
    return {}


def _enum_value(value: object) -> object:
    return getattr(value, "value", value)
