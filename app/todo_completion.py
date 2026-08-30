import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from app.store import AutoReplyStore
from app.task_models import ProjectPriority, ProjectStatus, TodoStatus, WorkItem

FOLLOW_UP_COMPLETION_CHECK_SCANNER = "follow_up_completion_check"
TODO_COMPLETION_EVIDENCE_SCANNER = "todo_completion_evidence"
FOLLOW_UP_COMPLETION_CHECK_LOOKBACK_DAYS = 14
TODO_COMPLETION_CANDIDATE_LIMIT = 50
WORKSPACE_FILE_SUFFIXES = {".md", ".markdown", ".txt", ".json"}


def close_todo_with_completion_evidence(
    store: AutoReplyStore,
    *,
    todo_id: int,
    evidence: dict[str, Any],
    now: str,
    source_type: str,
    source_ref: str,
    merge_reason: str,
    confidence: float = 1.0,
) -> bool:
    todo = store.get_work_todo(todo_id)
    if todo is None or str(todo.status) == TodoStatus.DONE.value:
        return False
    normalized_evidence = _completion_evidence(evidence, now=now)
    store.update_work_todo(
        todo.id,
        status=TodoStatus.DONE.value,
        completion_evidence_json=json.dumps(normalized_evidence, ensure_ascii=False),
        completed_at=now,
    )
    complete_follow_ups_for_todo(
        store,
        todo_id=todo.id,
        evidence=normalized_evidence,
        now=now,
    )
    store.create_work_update(
        project_id=todo.project_id,
        source_type=source_type,
        source_ref=source_ref,
        summary=f"Todo completed: {todo.title}",
        changes_json=json.dumps(
            {
                "todo_id": todo.id,
                "status": TodoStatus.DONE.value,
                "completion_evidence": normalized_evidence,
            },
            ensure_ascii=False,
        ),
        merge_reason=merge_reason,
        confidence=confidence,
    )
    return True


def complete_follow_ups_for_todo(
    store: AutoReplyStore,
    *,
    todo_id: int,
    evidence: dict[str, Any],
    now: str,
    _db: sqlite3.Connection | None = None,
) -> int:
    normalized_evidence = _completion_evidence(evidence, now=now)
    completed = 0
    for draft in store.list_follow_up_drafts_for_todo(
        todo_id,
        statuses=("draft", "approved", "sent"),
        _db=_db,
    ):
        payload = {
            "completed": True,
            "reason": normalized_evidence["reason"],
            "source": normalized_evidence["source"],
            "checked_at": now,
            "completion_evidence": normalized_evidence,
        }
        store.update_follow_up_draft(
            draft.id,
            status="completed",
            evidence_check_json=json.dumps(payload, ensure_ascii=False),
            suppressed_reason=normalized_evidence["reason"],
            _db=_db,
        )
        completed += 1
    return completed


def enqueue_follow_up_completion_checks(
    store: AutoReplyStore,
    dws: Any,
    *,
    now: str,
    limit: int = 1,
) -> int:
    return enqueue_todo_completion_evidence_checks(
        store,
        dws,
        workspace=None,
        now=now,
        limit=limit,
        require_follow_up=True,
    )


def enqueue_todo_completion_evidence_checks(
    store: AutoReplyStore,
    dws: Any,
    *,
    workspace: Path | None = None,
    now: str,
    limit: int = TODO_COMPLETION_CANDIDATE_LIMIT,
    require_follow_up: bool = False,
) -> int:
    if limit <= 0:
        return 0
    todos_checked = 0
    candidates_enqueued = 0
    workspace_changed_since = _last_todo_completion_checked_at(store)
    for project, todo, drafts, dingtalk_links in _todo_completion_check_candidates(
        store,
        now=now,
        require_follow_up=require_follow_up,
    ):
        if candidates_enqueued >= limit:
            break
        if str(todo.status) == TodoStatus.DONE.value:
            evidence = _completion_evidence_from_todo(todo, now=now)
            complete_follow_ups_for_todo(
                store,
                todo_id=todo.id,
                evidence=evidence,
                now=now,
            )
            todos_checked += 1
            continue
        sources = _dingtalk_todo_sources(dingtalk_links, now=now)
        enqueued_sources: list[dict[str, Any]] = []
        todo_work_item_enqueued = False
        for source in sources:
            if candidates_enqueued >= limit:
                break
            source_type = str(source.get("type") or "").strip()
            if not source_type or source_type.endswith("_error"):
                continue
            source_ref = str(source.get("source") or "").strip()
            if not source_ref:
                continue
            candidate = store.upsert_todo_evidence_candidate(
                project_id=todo.project_id,
                todo_id=todo.id,
                source_type=source_type,
                source_ref=source_ref,
                source_created_at=str(source.get("created_at") or ""),
                evidence_text=_source_evidence_text(source),
                reason=str(source.get("reason") or "environment evidence candidate"),
                confidence=float(source.get("confidence") or 0.0),
            )
            if str(candidate.status) not in {"candidate", "error"}:
                continue
            work_item = _todo_completion_evidence_work_item(
                store,
                project=project,
                todo=todo,
                candidate=candidate,
                source=source,
                drafts=drafts,
                dingtalk_links=dingtalk_links,
                now=now,
            )
            input_id = store.enqueue_work_summary_input(
                source_type=work_item.source.type.value,
                source_ref=work_item.source.ref,
                payload_json=work_item.model_dump_json(),
            )
            store.mark_todo_evidence_candidate_enqueued(candidate.id, input_id)
            enqueued_sources.append(source)
            candidates_enqueued += 1
            todo_work_item_enqueued = True
        if not enqueued_sources and candidates_enqueued < limit:
            work_item = _todo_completion_check_work_item(
                store,
                project=project,
                todo=todo,
                drafts=drafts,
                dingtalk_links=dingtalk_links,
                workspace=workspace,
                workspace_changed_since=workspace_changed_since,
                now=now,
            )
            input_id = store.enqueue_work_summary_input(
                source_type=work_item.source.type.value,
                source_ref=work_item.source.ref,
                payload_json=work_item.model_dump_json(),
            )
            if input_id > 0:
                candidates_enqueued += 1
                todo_work_item_enqueued = True
        follow_up_source_ref = f"todo-completion:{todo.id}:{_date_part(now)}"
        for draft in drafts:
            _record_completion_check(
                store,
                draft_id=draft.id,
                now=now,
                source_ref=follow_up_source_ref,
                sources=enqueued_sources,
                enqueued=todo_work_item_enqueued,
            )
        todos_checked += 1
    store.set_daily_scan_state(
        TODO_COMPLETION_EVIDENCE_SCANNER,
        last_success_at=now,
        cursor_json=json.dumps({"last_checked_at": now}, sort_keys=True),
        last_error="",
    )
    store.set_daily_scan_state(
        FOLLOW_UP_COMPLETION_CHECK_SCANNER,
        last_success_at=now,
        cursor_json=json.dumps({"last_checked_at": now}, sort_keys=True),
        last_error="",
    )
    return todos_checked


def _todo_completion_check_candidates(
    store: AutoReplyStore,
    *,
    now: str,
    require_follow_up: bool = False,
):
    today = _date_part(now)
    projects = store.list_work_projects(
        statuses=(ProjectStatus.ACTIVE.value, ProjectStatus.WAITING.value),
        limit=500,
    )
    todos_by_project = store.list_work_todos_for_projects(
        [project.id for project in projects],
        statuses=(TodoStatus.OPEN.value, TodoStatus.WAITING_OWNER.value),
    )
    rows: list[tuple[Any, Any, list[Any], list[Any]]] = []
    for project in projects:
        for todo in todos_by_project.get(project.id, []):
            drafts = store.list_follow_up_drafts(
                todo_id=todo.id,
                statuses=("draft", "approved", "sent"),
                limit=20,
            )
            if require_follow_up and not drafts:
                continue
            if drafts and all(
                _last_completion_check_date(draft.evidence_check_json) == today
                for draft in drafts
            ):
                continue
            dingtalk_links = store.list_work_todo_dingtalk_links_for_todo(todo.id)
            rows.append((project, todo, drafts, dingtalk_links))
    rows.sort(
        key=lambda row: _todo_candidate_sort_key(
            row[0],
            row[1],
            row[2],
            row[3],
            now=now,
        )
    )
    yield from rows


def _todo_candidate_sort_key(
    project: Any,
    todo: Any,
    drafts: list[Any],
    dingtalk_links: list[Any],
    *,
    now: str,
) -> tuple[int, int, int, int, str]:
    priority = str(todo.priority or project.priority or "").strip()
    priority_rank = {
        ProjectPriority.P0.value: 0,
        ProjectPriority.P1.value: 1,
        ProjectPriority.P2.value: 2,
    }.get(priority, 3)
    overdue_rank = 0 if _todo_is_overdue(todo, now=now) else 1
    recent_rank = 0 if _recent_project_activity(project, now=now) else 1
    linked_rank = 0 if drafts or dingtalk_links else 1
    return (
        priority_rank,
        overdue_rank,
        recent_rank,
        linked_rank,
        str(todo.updated_at or todo.created_at or ""),
    )


def _todo_is_overdue(todo: Any, *, now: str) -> bool:
    if not str(getattr(todo, "deadline_at", "") or "").strip():
        return False
    due = _parse_datetime(todo.deadline_at)
    current = _parse_datetime(now)
    return due is not None and current is not None and due <= current


def _recent_project_activity(project: Any, *, now: str) -> bool:
    activity = _parse_datetime(getattr(project, "last_activity_at", "") or "")
    current = _parse_datetime(now)
    if activity is None or current is None:
        return False
    return activity >= current - timedelta(days=FOLLOW_UP_COMPLETION_CHECK_LOOKBACK_DAYS)


def _last_todo_completion_checked_at(store: AutoReplyStore) -> str:
    state = store.get_daily_scan_state(TODO_COMPLETION_EVIDENCE_SCANNER)
    if not state:
        return ""
    cursor = _json_dict(str(state.get("cursor_json") or "{}"))
    return str(cursor.get("last_checked_at") or state.get("last_success_at") or "")


def _dingtalk_todo_sources(
    dingtalk_links: list[Any],
    *,
    now: str,
) -> list[dict[str, Any]]:
    sources: list[dict[str, Any]] = []
    for link in dingtalk_links:
        if bool(link.last_dingtalk_done) is not True:
            continue
        source_ref = link.dingtalk_task_id or f"link-{link.id}"
        sources.append(
            {
                "type": "dingtalk_todo",
                "source": f"dingtalk_todo:{source_ref}",
                "created_at": link.last_pull_at or now,
                "content": link.last_dingtalk_payload_json,
                "reason": "DingTalk TODO is marked done.",
                "confidence": 1.0,
            }
        )
    return sources


def _source_lookback_anchor(todo: Any, drafts: list[Any], *, now: str) -> str:
    candidates = [
        draft.sent_at or draft.scheduled_at
        for draft in drafts
        if str(draft.sent_at or draft.scheduled_at or "").strip()
    ]
    candidates.extend(
        [
            getattr(todo, "updated_at", ""),
            getattr(todo, "created_at", ""),
            now,
        ]
    )
    return next((str(candidate) for candidate in candidates if str(candidate).strip()), now)


def _source_evidence_text(source: dict[str, Any]) -> str:
    for key in ("content", "summary", "text", "title"):
        value = str(source.get(key) or "").strip()
        if value:
            return value[:3000]
    return json.dumps(source, ensure_ascii=False)[:3000]


def _todo_completion_check_work_item(
    store: AutoReplyStore,
    *,
    project: Any,
    todo: Any,
    drafts: list[Any],
    dingtalk_links: list[Any],
    workspace: Path | None,
    workspace_changed_since: str,
    now: str,
) -> WorkItem:
    updates = store.list_work_updates(todo.project_id, limit=10)
    summary = {
        "instruction": (
            "这是 TODO 完成状态的受限环境检查。service 只负责调度；"
            "请按 search_policy 使用可用只读工具检索 DWS、Lark、email、"
            "CEO_WORKSPACE 本地文件和 memory_recall 背景，判断 TODO 是否已有明确完成证据。"
            "只有当前证据直接满足 TODO 完成条件时才 close TODO，并写完整 "
            "completion_evidence.source/reason/description/completed_at/checked_at。"
            "找不到强证据时不要关闭，也不要扩大到 search_policy 之外。"
        ),
        "project": _project_context(project),
        "todo": _todo_context(todo),
        "follow_ups": _follow_up_context(drafts),
        "dingtalk_todos": _dingtalk_todo_context(dingtalk_links),
        "recent_updates": _recent_update_context(updates),
        "search_policy": _todo_completion_search_policy(
            todo=todo,
            drafts=drafts,
            workspace=workspace,
            workspace_changed_since=workspace_changed_since,
            now=now,
        ),
    }
    primary_draft = drafts[0] if drafts else None
    return WorkItem.model_validate(
        {
            "source": {
                "type": "todo_completion_check",
                "ref": f"todo-completion-check:{todo.id}:{_date_part(now)}",
                "title": f"TODO completion check #{todo.id}",
                "conversation_id": (
                    primary_draft.target_conversation_id if primary_draft is not None else ""
                ),
                "conversation_title": "",
                "created_at": now,
            },
            "summary": json.dumps(summary, ensure_ascii=False),
            "project_name": project.title,
            "context": {
                "sender": "CEO task completion checker",
                "sender_user_id": "",
                "participants": [todo.owner_name] if todo.owner_name else [],
                "source_conversation_kind": (
                    "group"
                    if primary_draft is not None and primary_draft.target_kind == "group"
                    else "direct"
                ),
                "source_conversation_title": "",
            },
            "task_signals": {
                "possible_task_update": True,
                "mentions_follow_up": bool(drafts),
                "progress_claim": True,
                "signal_reason": "todo completion check",
            },
        }
    )


def _project_context(project: Any) -> dict[str, Any]:
    return {
        "id": project.id,
        "title": project.title,
        "category": str(project.category),
        "status": str(project.status),
        "priority": str(project.priority),
        "risk_level": str(project.risk_level),
        "background": project.background,
        "current_state": project.current_state,
        "next_step": project.next_step,
    }


def _todo_context(todo: Any) -> dict[str, Any]:
    return {
        "id": todo.id,
        "title": todo.title,
        "description": todo.description,
        "owner_user_id": todo.owner_user_id,
        "owner_name": todo.owner_name,
        "status": str(todo.status),
        "priority": str(todo.priority),
        "deadline_at": todo.deadline_at,
        "next_follow_up_at": todo.next_follow_up_at,
        "follow_up_question": todo.follow_up_question,
        "completion_evidence": _json_dict(todo.completion_evidence_json),
    }


def _follow_up_context(drafts: list[Any]) -> list[dict[str, Any]]:
    return [
        {
            "id": draft.id,
            "title": draft.title,
            "description": draft.description,
            "question_text": draft.question_text,
            "owner_user_id": draft.owner_user_id,
            "owner_name": draft.owner_name,
            "target_kind": draft.target_kind,
            "target_conversation_id": draft.target_conversation_id,
            "status": str(draft.status),
            "scheduled_at": draft.scheduled_at,
            "sent_at": draft.sent_at,
        }
        for draft in drafts
    ]


def _dingtalk_todo_context(dingtalk_links: list[Any]) -> list[dict[str, Any]]:
    return [
        {
            "id": link.id,
            "dingtalk_task_id": link.dingtalk_task_id,
            "status": str(link.status),
            "last_dingtalk_done": link.last_dingtalk_done,
            "last_pull_at": link.last_pull_at,
            "last_payload": _json_dict(link.last_dingtalk_payload_json),
            "last_error": link.last_error,
        }
        for link in dingtalk_links
    ]


def _recent_update_context(updates: list[Any]) -> list[dict[str, Any]]:
    return [
        {
            "id": update.id,
            "source_type": update.source_type,
            "source_ref": update.source_ref,
            "summary": update.summary,
            "changes": _json_dict(update.changes_json),
            "merge_reason": update.merge_reason,
            "created_at": update.created_at,
        }
        for update in updates
    ]


def _todo_completion_search_policy(
    *,
    todo: Any,
    drafts: list[Any],
    workspace: Path | None,
    workspace_changed_since: str,
    now: str,
) -> dict[str, Any]:
    search_since = _source_lookback_anchor(todo, drafts, now=now)
    return {
        "time_window": {
            "default_days": FOLLOW_UP_COMPLETION_CHECK_LOOKBACK_DAYS,
            "prefer_since": search_since,
            "end": now,
            "changed_files_since": workspace_changed_since,
        },
        "limits": {
            "max_tool_calls": 8,
            "max_raw_reads": 3,
            "max_sources_to_return": 3,
        },
        "allowed_sources": [
            "dws_message",
            "dws_minutes",
            "lark_message",
            "lark_doc",
            "lark_task",
            "email",
            "local_file_under_CEO_WORKSPACE",
            "memory_recall_for_background_only",
        ],
        "local_files": {
            "root": "CEO_WORKSPACE" if workspace is not None else "",
            "changed_since": workspace_changed_since,
            "allowed_suffixes": sorted(WORKSPACE_FILE_SUFFIXES),
        },
    }


def _todo_completion_evidence_work_item(
    store: AutoReplyStore,
    *,
    project: Any,
    todo: Any,
    candidate: Any,
    source: dict[str, Any],
    drafts: list[Any],
    dingtalk_links: list[Any],
    now: str,
) -> WorkItem:
    updates = store.list_work_updates(todo.project_id, limit=10)
    summary = {
        "instruction": (
            "这是 TODO 完成状态的环境证据候选。请结合候选证据、项目/TODO 上下文、"
            "相关 follow-up、钉钉 TODO 和最近 updates 判断 TODO 是否已经明确完成。"
            "只有证据明确完成时才 close TODO；自动关闭必须写完整 "
            "completion_evidence.source/reason/description/completed_at/checked_at。"
            "弱证据、进展信息或上下文不足时不要关闭，输出更新后的 task JSON 或 skip。"
            "需要历史背景时按现有权限使用 memory_recall。"
        ),
        "project": {
            "id": project.id,
            "title": project.title,
            "category": str(project.category),
            "status": str(project.status),
            "priority": str(project.priority),
            "risk_level": str(project.risk_level),
            "background": project.background,
            "current_state": project.current_state,
            "next_step": project.next_step,
        },
        "todo": {
            "id": todo.id,
            "title": todo.title,
            "description": todo.description,
            "owner_user_id": todo.owner_user_id,
            "owner_name": todo.owner_name,
            "status": str(todo.status),
            "priority": str(todo.priority),
            "deadline_at": todo.deadline_at,
            "next_follow_up_at": todo.next_follow_up_at,
            "follow_up_question": todo.follow_up_question,
            "completion_evidence": _json_dict(todo.completion_evidence_json),
        },
        "evidence_candidate": {
            "id": candidate.id,
            "source_type": candidate.source_type,
            "source_ref": candidate.source_ref,
            "source_created_at": candidate.source_created_at,
            "evidence_text": candidate.evidence_text,
            "reason": candidate.reason,
            "confidence": candidate.confidence,
            "raw_source": source,
        },
        "follow_ups": [
            {
                "id": draft.id,
                "title": draft.title,
                "description": draft.description,
                "question_text": draft.question_text,
                "owner_user_id": draft.owner_user_id,
                "owner_name": draft.owner_name,
                "target_kind": draft.target_kind,
                "target_conversation_id": draft.target_conversation_id,
                "status": str(draft.status),
                "scheduled_at": draft.scheduled_at,
                "sent_at": draft.sent_at,
            }
            for draft in drafts
        ],
        "dingtalk_todos": [
            {
                "id": link.id,
                "dingtalk_task_id": link.dingtalk_task_id,
                "status": str(link.status),
                "last_dingtalk_done": link.last_dingtalk_done,
                "last_pull_at": link.last_pull_at,
                "last_payload": _json_dict(link.last_dingtalk_payload_json),
                "last_error": link.last_error,
            }
            for link in dingtalk_links
        ],
        "recent_updates": [
            {
                "id": update.id,
                "source_type": update.source_type,
                "source_ref": update.source_ref,
                "summary": update.summary,
                "changes": _json_dict(update.changes_json),
                "merge_reason": update.merge_reason,
                "created_at": update.created_at,
            }
            for update in updates
        ],
    }
    primary_draft = drafts[0] if drafts else None
    return WorkItem.model_validate(
        {
            "source": {
                "type": "todo_completion_evidence_candidate",
                "ref": f"todo-evidence:{candidate.id}",
                "title": f"TODO completion evidence #{candidate.id}",
                "conversation_id": (
                    primary_draft.target_conversation_id if primary_draft is not None else ""
                ),
                "conversation_title": "",
                "created_at": now,
            },
            "summary": json.dumps(summary, ensure_ascii=False),
            "project_name": project.title,
            "context": {
                "sender": "CEO task completion checker",
                "sender_user_id": "",
                "participants": [todo.owner_name] if todo.owner_name else [],
                "source_conversation_kind": (
                    "group"
                    if primary_draft is not None and primary_draft.target_kind == "group"
                    else "direct"
                ),
                "source_conversation_title": "",
            },
            "task_signals": {
                "possible_task_update": True,
                "mentions_follow_up": bool(drafts),
                "progress_claim": True,
                "signal_reason": "todo completion evidence candidate",
            },
        }
    )


def _json_dict(value: str) -> dict[str, Any]:
    try:
        parsed = json.loads(value or "{}")
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _completion_evidence(evidence: dict[str, Any], *, now: str) -> dict[str, Any]:
    source = str(evidence.get("source") or "").strip()
    reason = str(
        evidence.get("reason")
        or evidence.get("description")
        or evidence.get("summary")
        or ""
    ).strip()
    completed_at = str(evidence.get("completed_at") or now).strip()
    normalized = dict(evidence)
    normalized["source"] = source or "unknown"
    normalized["reason"] = reason or "completion confirmed"
    normalized["completed_at"] = completed_at
    normalized.setdefault("checked_at", now)
    return normalized


def _completion_evidence_from_todo(todo: Any, *, now: str) -> dict[str, Any]:
    try:
        evidence = json.loads(todo.completion_evidence_json or "{}")
    except json.JSONDecodeError:
        evidence = {
            "source": "work_todo",
            "reason": todo.completion_evidence_json,
        }
    if not isinstance(evidence, dict):
        evidence = {"source": "work_todo", "reason": str(evidence)}
    return _completion_evidence(evidence, now=now)


def _last_completion_check_date(value: str) -> str:
    try:
        payload = json.loads(value or "{}")
    except json.JSONDecodeError:
        return ""
    checked_at = str(
        payload.get("completion_check_checked_at")
        or payload.get("checked_at")
        or ""
    )
    return _date_part(checked_at)


def _record_completion_check(
    store: AutoReplyStore,
    *,
    draft_id: int,
    now: str,
    source_ref: str,
    sources: list[dict[str, Any]],
    enqueued: bool,
) -> None:
    payload = {
        "completed": False,
        "reason": "completion_not_confirmed_by_automatic_check",
        "source": source_ref,
        "completion_check_checked_at": now,
        "sources": sources,
        "work_item_enqueued": enqueued,
    }
    store.update_follow_up_draft(
        draft_id,
        evidence_check_json=json.dumps(payload, ensure_ascii=False),
    )


def _parse_datetime(value: str) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        try:
            parsed = datetime.strptime(text, "%Y-%m-%d %H:%M:%S")
        except ValueError:
            return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed


def _date_part(value: str) -> str:
    parsed = _parse_datetime(value)
    return parsed.date().isoformat() if parsed is not None else ""
