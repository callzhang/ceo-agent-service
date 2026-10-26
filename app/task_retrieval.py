import json
import re
from dataclasses import dataclass
from itertools import islice
from typing import Any

from app.store import AutoReplyStore
from app.task_models import WorkItem, WorkProject, WorkTodo
from app.task_semantic_models import (
    AttentionStatus, BusinessAttentionItem, BusinessAttentionTask,
    BusinessActorKind, BusinessAnchor, BusinessProject, BusinessTask, BusinessTaskAnchorLink,
    BusinessTaskEvidence, BusinessTaskRelation,
    BusinessWorkCluster, BusinessWorkClusterTask, BusinessRelationStatus, FormalTaskBasis,
    BusinessProjectContextRevision, BusinessProjectEvidence, SourceCitation,
)
from app.task_source_documents import (
    source_bundle,
    source_contains_quote,
    source_has_external_locator,
)
from app.business_source_reader import (
    AvailableSource, BusinessTaskSignalFact, PreparedSources, RequestSourceReader,
)
from app.task_source_projection_sharing import share_source_document_projections


TOKEN_RE = re.compile(r"[A-Za-z0-9_]+|[\u4e00-\u9fff]")




@dataclass(frozen=True)
class ProjectTaskDetail:
    project: WorkProject
    todos: tuple[WorkTodo, ...]
    updates: tuple[object, ...]
    follow_ups_by_todo: dict[int, tuple[object, ...]]
    dingtalk_links_by_todo: dict[int, tuple[object, ...]]


@dataclass(frozen=True)
class TaskSemanticContext:
    task_candidates: tuple[BusinessTask, ...]
    formal_tasks: tuple[BusinessTask, ...]
    unverified_formal_tasks: tuple[BusinessTask, ...]
    task_evidence: tuple[BusinessTaskEvidence, ...]
    evidence_signals: tuple[BusinessTaskSignalFact, ...]
    prepared_sources: PreparedSources
    task_relations: tuple[BusinessTaskRelation, ...]
    task_anchor_links: tuple[BusinessTaskAnchorLink, ...]
    clusters: tuple[BusinessWorkCluster, ...]
    cluster_memberships: tuple[BusinessWorkClusterTask, ...]
    anchors: tuple[BusinessAnchor, ...]
    official_projects: tuple[BusinessProject, ...]
    project_contexts: tuple[BusinessProjectContextRevision, ...]
    project_evidence: tuple[BusinessProjectEvidence, ...]
    attention_items: tuple[BusinessAttentionItem, ...]
    attention_memberships: tuple[BusinessAttentionTask, ...]


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
    rows: tuple[BusinessTaskEvidence, ...], *, recent_limit: int,
    pinned_signal_id: int | None = None,
) -> tuple[BusinessTaskEvidence, ...]:
    """Keep each role's edge proofs, the cited owner proof, and recent rows."""
    first_by_role: dict[str, BusinessTaskEvidence] = {}
    latest_by_role: dict[str, BusinessTaskEvidence] = {}
    for row in rows:
        role = row.evidence_role.value
        first_by_role.setdefault(role, row)
        latest_by_role[role] = row
    kept = set(first_by_role.values()) | set(latest_by_role.values())
    kept.update(rows[-recent_limit:])
    if pinned_signal_id is not None:
        kept.update(row for row in rows if row.signal_id == pinned_signal_id)
    return tuple(row for row in rows if row in kept)


def _source_backed_owner_signal_id(
    store: AutoReplyStore, task: BusinessTask, evidence: tuple[BusinessTaskEvidence, ...],
    prepared_sources: PreparedSources,
) -> int | None:
    if not (task.owner_name.strip() or task.owner_user_id.strip()):
        return None
    try:
        owner_evidence = json.loads(task.owner_evidence_json)
    except (TypeError, json.JSONDecodeError):
        return None
    if not isinstance(owner_evidence, dict):
        return None
    source_ref = owner_evidence.get("source_ref")
    excerpt = owner_evidence.get("excerpt")
    if not isinstance(source_ref, str) or not isinstance(excerpt, str) or not excerpt.strip():
        return None
    if task.owner_name and task.owner_name not in excerpt:
        return None
    required_role = (
        "commitment" if task.formal_basis is FormalTaskBasis.EXPLICIT_COMMITMENT else "assignment"
    )
    for row in evidence:
        if row.evidence_role.value != required_role:
            continue
        signal = store.get_business_task_signal(row.signal_id)
        if signal is None:
            continue
        prepared_sources.add(signal)
        reading = prepared_sources.reading_for(signal)
        if (signal.source_ref != source_ref or not isinstance(reading, AvailableSource)
                or not source_contains_quote(reading.body, excerpt)):
            continue
        if not task.owner_user_id:
            return signal.id if task.owner_name else None
        if not task.owner_name and task.owner_user_id in excerpt:
            return signal.id
        try:
            mapped = json.loads(signal.context_json).get("owner_identity", {})
        except (TypeError, json.JSONDecodeError):
            mapped = {}
        if not isinstance(mapped, dict):
            mapped = {}
        author_matches = (
            signal.author_kind is BusinessActorKind.HUMAN
            and signal.author_user_id == task.owner_user_id
            and (not task.owner_name or signal.author_name == task.owner_name)
        )
        context_matches = (
            mapped.get("user_id") == task.owner_user_id
            and (not task.owner_name or mapped.get("name") == task.owner_name)
        )
        if author_matches or context_matches:
            return signal.id
    return None


def _prioritized_registry_rows(rows, *, linked_ids: set[int], query_terms: set[str], limit: int):
    linked = [row for row in rows if row.id in linked_ids]
    remaining = sorted(
        (row for row in rows if row.id not in linked_ids),
        key=lambda row: (-_semantic_score(query_terms, row.title), row.id),
    )
    return tuple(linked + remaining[:limit])


def _project_document(project: BusinessProject) -> str:
    context = project.context
    return project.title + ("\n" + json.dumps(context.model_dump(mode="json"), ensure_ascii=False) if context else "")


def _source_citations(value: object) -> tuple[SourceCitation, ...]:
    """Collect structured citation fields, never infer proof from free prose."""
    found: list[SourceCitation] = []
    if isinstance(value, dict):
        id = value.get("signal_id")
        ref, quote = value.get("source_ref"), value.get("source_excerpt")
        if isinstance(id, int) and not isinstance(id, bool) and id > 0 and isinstance(ref, str) and ref.strip() and isinstance(quote, str) and quote.strip():
            found.append(SourceCitation(signal_id=id, source_ref=ref, source_excerpt=quote))
        for child in value.values():
            found.extend(_source_citations(child))
    elif isinstance(value, list):
        for child in value:
            found.extend(_source_citations(child))
    return tuple(dict.fromkeys(found))


def _semantic_citations(project_contexts, tasks, attention_items) -> tuple[SourceCitation, ...]:
    values = [revision.context.model_dump(mode="json") for revision in project_contexts]
    values.extend(json.loads(task.suggestion_json) for task in tasks)
    values.extend(json.loads(item.assessment_json) for item in attention_items)
    return tuple(dict.fromkeys(citation for value in values for citation in _source_citations(value)))


def retrieve_task_semantic_context(
    store: AutoReplyStore, work_item: WorkItem, *, limit_per_kind: int = 20,
    source_reader: RequestSourceReader | None = None,
) -> TaskSemanticContext:
    if limit_per_kind < 1:
        raise ValueError("limit_per_kind must be positive")
    query_terms = set(tokenize(work_item.summary))
    all_projects = tuple(_all_pages(store.list_business_projects))
    same_source_project_ids = set(_all_pages(
        lambda *, limit, offset: store.list_business_project_ids_for_source(
            source_type=work_item.source.type.value, source_id=work_item.source.ref.split("#", 1)[0],
            limit=limit, offset=offset,
        )
    ))
    ranked_projects = sorted(all_projects, key=lambda project: (
        project.id not in same_source_project_ids,
        -_semantic_score(query_terms, _project_document(project)), project.id,
    ))
    initial_projects = tuple(project for project in ranked_projects if project.id in same_source_project_ids)
    initial_projects += tuple(project for project in ranked_projects if project.id not in same_source_project_ids)[:max(0, limit_per_kind - len(initial_projects))]
    project_task_ids = {
        task_id for project in initial_projects
        if project.id in same_source_project_ids or _semantic_score(query_terms, _project_document(project)) > 0
        for task_id in store.list_business_task_ids_for_project(project_id=project.id, limit=limit_per_kind)
    }
    conversation_task_ids = set(_all_pages(
        lambda *, limit, offset: store.list_business_task_ids_for_conversation(
            conversation_id=work_item.source.conversation_id, limit=limit, offset=offset
        )
    ))
    # What this source produced before is what it most likely updates now, however
    # the words rank: a meeting read again must see its own earlier Tasks, or the
    # Agent records them a second time.
    same_source_task_ids = set(_all_pages(
        lambda *, limit, offset: store.list_business_task_ids_for_source(
            source_type=work_item.source.type.value,
            source_id=work_item.source.ref.split("#", 1)[0],
            limit=limit, offset=offset,
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
        key=lambda pair: (pair[1].id not in project_task_ids, -pair[0], -pair[1].id),
    )
    same_source_candidates = tuple(
        task for _score, task in ranked_tasks
        if task.id in same_source_task_ids and task.stage.value == "candidate"
    )
    candidates = same_source_candidates + tuple(
        task for score, task in ranked_tasks
        if (score > 0 or task.id in project_task_ids) and task.stage.value == "candidate" and task.id not in same_source_task_ids
    )[:max(0, limit_per_kind - len(same_source_candidates))]
    formal_rows: list[BusinessTask] = []
    unverified_rows: list[BusinessTask] = []
    evidence_by_task: dict[int, tuple[BusinessTaskEvidence, ...]] = {}
    owner_signal_by_task: dict[int, int] = {}
    prepared_sources = PreparedSources(source_reader or store.business_source_reader())
    # Reserve the bucket for all known same-source Tasks before filling its
    # remaining lexical/Project budget; a forced overflow must not retain noise.
    for score, task in sorted(ranked_tasks, key=lambda pair: pair[1].id not in same_source_task_ids):
        if (len(formal_rows) >= limit_per_kind
                and len(unverified_rows) >= limit_per_kind
                and task.id not in same_source_task_ids):
            break
        if (score <= 0 and task.id not in same_source_task_ids and task.id not in project_task_ids) or task.stage.value != "formal":
            continue
        task_evidence = store.list_business_task_evidence(task.id)
        owner_signal_id = _source_backed_owner_signal_id(
            store, task, task_evidence, prepared_sources
        )
        bucket = formal_rows if owner_signal_id is not None else unverified_rows
        if len(bucket) < limit_per_kind or task.id in same_source_task_ids:
            bucket.append(task)
            evidence_by_task[task.id] = task_evidence
            if owner_signal_id is not None:
                owner_signal_by_task[task.id] = owner_signal_id
    formal = tuple(formal_rows)
    unverified = tuple(unverified_rows)
    selected = candidates + formal + unverified
    evidence = tuple(
        row for task in selected
        for row in _bounded_task_evidence(
            evidence_by_task.get(task.id) or store.list_business_task_evidence(task.id),
            recent_limit=limit_per_kind,
            pinned_signal_id=owner_signal_by_task.get(task.id),
        )
    )
    relations = tuple(dict.fromkeys(
        row for task in selected
        for row in islice(_all_pages(
            lambda *, limit, offset, task_id=task.id: store.list_business_task_relations(
                task_id=task_id, limit=limit, offset=offset
            )
        ), limit_per_kind)
    ))
    links = tuple(
        row for task in selected
        for row in sorted(_all_pages(
            lambda *, limit, offset, task_id=task.id: store.list_business_task_anchor_links(
                task_id=task_id, limit=limit, offset=offset
            )
        ), key=lambda link: (
            not (link.status is BusinessRelationStatus.CONFIRMED and link.active),
            link.id,
        ))[:limit_per_kind]
    )
    signal_ids = (
        {row.signal_id for row in evidence}
        | {row.supporting_signal_id for row in relations}
        | {row.evidence_signal_id for row in links}
    )
    selected_memberships = tuple(dict.fromkeys(
        row for task in selected
        for row in _all_pages(
            lambda *, limit, offset, task_id=task.id: store.list_business_work_cluster_tasks(
                task_id=task_id, limit=limit, offset=offset
            )
        )
    ))
    all_clusters = tuple(_all_pages(store.list_business_work_clusters))
    clusters = _prioritized_registry_rows(
        all_clusters,
        linked_ids={row.cluster_id for row in selected_memberships},
        query_terms=query_terms,
        limit=limit_per_kind,
    )
    cluster_memberships = tuple(dict.fromkeys(tuple(
        row for cluster in clusters
        for row in islice(_all_pages(
            lambda *, limit, offset, cluster_id=cluster.id: store.list_business_work_cluster_tasks(
                cluster_id=cluster_id, limit=limit, offset=offset
            )
        ), max(20, limit_per_kind))
    ) + selected_memberships))
    all_anchors = tuple(_all_pages(store.list_business_anchors))
    anchors = _prioritized_registry_rows(
        all_anchors,
        linked_ids={row.anchor_id for row in links} | {project.canonical_anchor_id for project in initial_projects},
        query_terms=query_terms,
        limit=limit_per_kind,
    )
    linked_anchor_ids = {row.anchor_id for row in links}
    projects = tuple({project.id: project for project in initial_projects + tuple(project for project in all_projects if project.canonical_anchor_id in linked_anchor_ids)}.values())
    project_contexts = tuple(
        revision for project in projects
        for revision in store.list_business_project_context_revisions(project.id, limit=1)
    )
    project_evidence = tuple(
        proof for project in projects
        for proof in store.list_business_project_evidence(
            project.id, limit=limit_per_kind,
            pinned_signal_ids=tuple({
                citation.signal_id for revision in project_contexts if revision.project_id == project.id
                for citation in _source_citations(revision.context.model_dump(mode="json"))
            }),
        )
    )
    project_anchor_ids = {project.canonical_anchor_id for project in projects}
    attention_items = tuple(
        item for item in store.list_business_attention_items()
        if item.status is AttentionStatus.ACTIVE and item.anchor_id in project_anchor_ids
    )[:limit_per_kind]
    attention_memberships = tuple(
        member for item in attention_items
        for member in store.list_business_attention_tasks(item.id)
    )
    citations = _semantic_citations(project_contexts, selected, attention_items)
    signal_ids |= {proof.signal_id for proof in project_evidence}
    signal_ids |= {citation.signal_id for citation in citations}
    signal_ids |= {item.evidence_signal_id for item in attention_items}
    signals = tuple(
        signal for signal_id in sorted(signal_ids)
        if (signal := store.get_business_task_signal(signal_id)) is not None
    )
    # Only a source the Agent cannot fetch itself is read to build the input;
    # decisions read the sources they cite when they are prepared, before writing.
    for signal in signals:
        if not source_has_external_locator(signal.source):
            prepared_sources.add(signal)
    return TaskSemanticContext(
        task_candidates=candidates,
        formal_tasks=formal,
        unverified_formal_tasks=unverified,
        task_evidence=evidence,
        evidence_signals=signals,
        prepared_sources=prepared_sources,
        task_relations=relations,
        task_anchor_links=links,
        clusters=clusters,
        cluster_memberships=cluster_memberships,
        anchors=anchors,
        official_projects=projects,
        project_contexts=project_contexts,
        project_evidence=project_evidence,
        attention_items=attention_items,
        attention_memberships=attention_memberships,
    )


def render_task_semantic_context(context: TaskSemanticContext) -> str:
    citations = _semantic_citations(
        context.project_contexts,
        context.task_candidates + context.formal_tasks + context.unverified_formal_tasks,
        context.attention_items,
    )
    for task in context.formal_tasks:
        proof = json.loads(task.owner_evidence_json)
        for signal in context.evidence_signals:
            if signal.source_ref == proof.get("source_ref") and isinstance(proof.get("excerpt"), str):
                citations += (SourceCitation(signal_id=signal.id, source_ref=signal.source_ref, source_excerpt=proof["excerpt"]),)
    payload = {
        "notice": "Similarity rank is context only; never authority or confirmation for identity, acceptance, anchor match, or official Project creation. Legacy formal rows without source-backed owner evidence are unverified, not owner commitments.",
        "official_project_registry": [_model_payload(value) for value in context.official_projects],
        "project_contexts": [_model_payload(value) for value in context.project_contexts],
        "project_evidence": [_model_payload(value) for value in context.project_evidence],
        "task_candidates": [_model_payload(value) for value in context.task_candidates],
        "existing_formal_tasks": [_model_payload(value) for value in context.formal_tasks],
        "unverified_formal_tasks": [_model_payload(value) for value in context.unverified_formal_tasks],
        "task_evidence": [_model_payload(value) for value in context.task_evidence],
        **share_source_document_projections(
            source_bundle(
                context.evidence_signals,
                prepared_sources=context.prepared_sources,
                citations=citations,
            )
        ),
        "task_relations": [_model_payload(value) for value in context.task_relations],
        "task_anchor_links": [_model_payload(value) for value in context.task_anchor_links],
        "work_clusters": [_model_payload(value) for value in context.clusters],
        "cluster_memberships": [_model_payload(value) for value in context.cluster_memberships],
        "registered_anchors": [_model_payload(value) for value in context.anchors],
        "current_project_attention": [{
            "id": value.id, "anchor_id": value.anchor_id,
            "task_ids": [member.task_id for member in context.attention_memberships
                         if member.attention_item_id == value.id],
            "why_attention": value.why_attention, "current_state": value.current_state,
            "assessment_json": value.assessment_json, "updated_at": value.updated_at,
        } for value in context.attention_items],
    }
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


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
        todos=tuple(todos),
        updates=tuple(store.list_work_updates(project.id, limit=updates_per_project)),
        follow_ups_by_todo=follow_ups_by_todo,
        dingtalk_links_by_todo=links_by_todo,
    )


def _model_payload(value: object) -> dict[str, Any]:
    dump = getattr(value, "model_dump", None)
    if callable(dump):
        payload = dump(mode="json")
        return payload if isinstance(payload, dict) else {}
    if hasattr(value, "__dict__"):
        return dict(value.__dict__)
    return {}
