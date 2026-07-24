import json
import math
import re
from dataclasses import dataclass
from typing import Any

from app.store import AutoReplyStore
from app.task_models import WorkProject, WorkTodo


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


def tokenize(text: str) -> list[str]:
    return [match.group(0).casefold() for match in TOKEN_RE.finditer(text or "")]


def project_document(project: WorkProject) -> str:
    fields = [
        project.title,
        _enum_value(project.category),
        project.tags_json,
        project.owner_name,
        project.goal,
        project.background,
        project.facts_json,
        project.current_state,
        project.blocker,
        project.next_step,
        project.source_conversations_json,
    ]
    return "\n".join(str(field) for field in fields if field)


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

    projects = store.list_work_projects(statuses=("active", "waiting"), limit=500)
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
    return candidates[:limit]


def render_candidate_prompt(candidates: list[ProjectCandidate]) -> str:
    payload = []
    for candidate in candidates:
        project = candidate.project
        payload.append(
            {
                "id": project.id,
                "score": round(candidate.score, 4),
                "title": project.title,
                "category": _enum_value(project.category),
                "tags": _parse_json_list(project.tags_json),
                "owner_name": project.owner_name,
                "goal": project.goal,
                "background": project.background,
                "facts": _parse_json_list(project.facts_json),
                "current_state": project.current_state,
                "blocker": project.blocker,
                "next_step": project.next_step,
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
        follow_ups_by_todo = {
            todo.id: tuple(
                store.list_follow_up_drafts(
                    project_id=project.id,
                    todo_id=todo.id,
                    limit=follow_ups_per_todo,
                )
            )
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
