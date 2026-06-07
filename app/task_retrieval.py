import json
import math
import re
from dataclasses import dataclass

from app.store import AutoReplyStore
from app.task_models import WorkProject


TOKEN_RE = re.compile(r"[A-Za-z0-9_]+|[\u4e00-\u9fff]")


@dataclass(frozen=True)
class ProjectCandidate:
    project: WorkProject
    score: float
    document: str


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


def _parse_json_list(value: str) -> list[object]:
    try:
        parsed = json.loads(value or "[]")
    except json.JSONDecodeError:
        return []
    return parsed if isinstance(parsed, list) else []


def _enum_value(value: object) -> object:
    return getattr(value, "value", value)
