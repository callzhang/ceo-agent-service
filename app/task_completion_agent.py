"""Service-side lifecycle operations for linked TODOs and follow-ups.

The Task Agent returns the unified ``TaskAgentDecision`` contract; this module
validates and atomically applies its existing-TODO and follow-up transitions.
"""

import json
import sqlite3
from contextlib import nullcontext
from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

from app.store import AutoReplyStore
from app.task_models import (
    CompletionFollowUpChange,
    CompletionSearchTrace,
    TaskAgentDecision,
    WorkItem,
    WorkItemSourceType,
    owner_identity_is_supported,
)
from app.task_agent_session import TASK_AGENT_SESSION_SCOPE_ID
from app.todo_completion import (
    close_business_task_with_completion_evidence,
    close_todo_with_completion_evidence,
)

TaskCompletionDecision = TaskAgentDecision


COMPLETION_SOURCE_TYPES = frozenset(
    {
        WorkItemSourceType.TODO_COMPLETION_EVIDENCE_CANDIDATE,
        WorkItemSourceType.TODO_COMPLETION_CHECK,
        WorkItemSourceType.FOLLOW_UP_COMPLETION_CHECK,
    }
)
FOLLOW_UP_WORK_START_HOUR = 9
FOLLOW_UP_WORK_END_HOUR = 18
FOLLOW_UP_WORK_TZ = ZoneInfo("Asia/Shanghai")


def encode_task_completion_result(raw: str) -> str:
    from app.task_agent import _encode_task_agent_result

    return _encode_task_agent_result(raw)


def validate_task_completion_decision(
    work_item: WorkItem,
    decision: TaskCompletionDecision,
    *,
    now: str = "",
    audit_tool_events: list[dict[str, str]] | None = None,
) -> None:
    source_type = work_item.source.type
    if source_type not in COMPLETION_SOURCE_TYPES:
        raise ValueError(f"unsupported completion source type: {source_type}")
    summary = _json_dict(work_item.summary)
    policy = _completion_search_policy(summary)
    todo = summary.get("todo")
    expected_todo_id = _positive_id(todo.get("id")) if isinstance(todo, dict) else 0
    business_task = summary.get("business_task")
    expected_business_task_id = (
        _positive_id(business_task.get("id")) if isinstance(business_task, dict) else 0
    )
    follow_up_ids = _linked_follow_up_ids(summary)

    if source_type == WorkItemSourceType.FOLLOW_UP_COMPLETION_CHECK:
        if decision.todo_changes:
            raise ValueError("follow-up completion check cannot mutate TODO")
        primary = summary.get("follow_up")
        expected_ids = {_positive_id(primary.get("id"))} if isinstance(primary, dict) else set()
        expected_ids.discard(0)
        if not expected_ids:
            raise ValueError("follow-up completion check lacks linked follow-up")
        if any(change.follow_up_id not in expected_ids for change in decision.follow_up_changes):
            raise ValueError("decision targets an unlinked follow-up")
        if any(
            change.todo_id is not None and change.todo_id != expected_todo_id
            for change in decision.follow_up_changes
        ):
            raise ValueError("follow-up operation targets an unlinked TODO")
    elif expected_business_task_id:
        if any(
            change.business_task_id != expected_business_task_id
            for change in decision.todo_changes
        ):
            raise ValueError("decision targets an unlinked Task")
        if decision.follow_up_changes:
            raise ValueError("Task completion check cannot change an unlinked follow-up")
    else:
        if not expected_todo_id:
            raise ValueError("TODO completion check lacks linked TODO")
        if any(change.todo_id != expected_todo_id for change in decision.todo_changes):
            raise ValueError("decision targets an unlinked TODO")
        if any(change.follow_up_id not in follow_up_ids for change in decision.follow_up_changes):
            raise ValueError("decision targets an unlinked follow-up")

    if source_type == WorkItemSourceType.TODO_COMPLETION_EVIDENCE_CANDIDATE:
        candidate = summary.get("evidence_candidate")
        if not isinstance(candidate, dict) or not _positive_id(candidate.get("id")):
            raise ValueError("TODO completion candidate lacks candidate identity")
        if work_item.source.ref != f"todo-evidence:{_positive_id(candidate.get('id'))}":
            raise ValueError("TODO completion candidate source reference does not match candidate")
        if decision.follow_up_changes:
            raise ValueError("evidence candidate decision cannot mutate follow-up")

    if source_type in {
        WorkItemSourceType.TODO_COMPLETION_EVIDENCE_CANDIDATE,
        WorkItemSourceType.TODO_COMPLETION_CHECK,
    } or decision.todo_changes or decision.follow_up_changes:
        if not decision.search_trace:
            raise ValueError("completion decision requires search_trace")
        _validate_search_trace(
            decision.search_trace,
            policy=policy,
            now=now,
            audit_tool_events=audit_tool_events,
        )
    if source_type in {
        WorkItemSourceType.TODO_COMPLETION_EVIDENCE_CANDIDATE,
        WorkItemSourceType.TODO_COMPLETION_CHECK,
    }:
        if not decision.update_summary.strip():
            raise ValueError("TODO completion decision requires a check summary")
        if source_type == WorkItemSourceType.TODO_COMPLETION_EVIDENCE_CANDIDATE:
            candidate = summary["evidence_candidate"]
            candidate_source_ref = str(candidate.get("source_ref") or "").strip()
            if candidate_source_ref and candidate_source_ref not in _search_trace_sources(decision):
                raise ValueError("search_trace must cite the candidate source")
            if candidate_source_ref and candidate.get("source_created_at"):
                linked_trace = next(
                    (trace for trace in decision.search_trace if trace.source_ref == candidate_source_ref),
                    None,
                )
                if linked_trace is None or linked_trace.source_created_at != candidate["source_created_at"]:
                    raise ValueError("candidate source_created_at must match the persisted candidate")

    for change in decision.todo_changes:
        _require_fields(
            change.completion_evidence,
            ("source", "reason", "description", "completed_at", "checked_at"),
            "completion_evidence",
        )
        _parse_evidence_time(
            str(change.completion_evidence["completed_at"]), "completed_at", now=now
        )
        if not str(change.completion_evidence.get("reason") or "").strip():
            raise ValueError("TODO close requires a non-empty reason")
        _require_current_evidence(change.completion_evidence, "completion_evidence")
        evidence_source = str(change.completion_evidence.get("source") or "").strip()
        if evidence_source not in _search_trace_sources(decision):
            raise ValueError("search_trace must cite the completion evidence source")
        _require_non_memory_evidence_source(decision, evidence_source)
    for change in decision.follow_up_changes:
        if change.action in {"suppress", "close"}:
            if not change.reason.strip():
                raise ValueError(f"follow-up {change.action} requires a non-empty reason")
            _require_current_evidence(change.evidence_check, "evidence_check")
            if str(change.evidence_check.get("source") or "").strip() not in _search_trace_sources(decision):
                raise ValueError("evidence_check source must match a search_trace entry")
            _require_non_memory_evidence_source(
                decision, str(change.evidence_check["source"]).strip()
            )
        if change.action in {"reschedule", "keep_open"}:
            _validate_future_follow_up_time(change.next_due_at or "", now=now)
        if change.action == "reassign":
            if not ((change.owner_user_id or "").strip() or (change.owner_name or "").strip()):
                raise ValueError("reassign requires an owner identity")
            _require_fields(change.owner_evidence, ("source", "reason", "description"), "owner_evidence")


def apply_task_completion_decision(
    store: AutoReplyStore,
    *,
    summary_input_id: int,
    work_item: WorkItem,
    decision: TaskCompletionDecision,
    now: str = "",
    sync_external_todo: bool = False,
    _db: sqlite3.Connection | None = None,
    audit_tool_events: list[dict[str, str]] | None = None,
) -> bool:
    effective_now = now or datetime.now(timezone.utc).isoformat()
    transaction = store.task_agent_domain_apply_transaction() if _db is None else nullcontext(_db)
    with transaction as db:
        _validate_queued_source_and_persisted_links(
            store, summary_input_id, work_item, db
        )
        validate_task_completion_decision(
            work_item, decision, now=effective_now, audit_tool_events=audit_tool_events
        )
        return _apply_task_completion_decision_in_transaction(
            store, summary_input_id=summary_input_id, work_item=work_item,
            decision=decision, effective_now=effective_now,
            sync_external_todo=sync_external_todo, db=db,
        )


def _apply_task_completion_decision_in_transaction(
    store: AutoReplyStore, *, summary_input_id: int, work_item: WorkItem,
    decision: TaskCompletionDecision, effective_now: str,
    sync_external_todo: bool, db: sqlite3.Connection,
) -> bool:
    closed_todo = 0
    closed_business_task = 0
    closed_evidence: dict[str, Any] = {}
    search_trace = [entry.model_dump(mode="json") for entry in decision.search_trace]
    for change in decision.todo_changes:
        evidence = {
            **change.completion_evidence,
            "checked_at": effective_now,
            "search_trace": search_trace,
        }
        if change.business_task_id is not None:
            if close_business_task_with_completion_evidence(
                store, business_task_id=change.business_task_id, evidence=evidence,
                source_type=work_item.source.type.value, _db=db,
            ):
                closed_business_task = change.business_task_id
                closed_evidence = evidence
            continue
        if close_todo_with_completion_evidence(
            store,
            todo_id=change.todo_id,
            evidence=evidence,
            now=effective_now,
            source_type=work_item.source.type.value,
            source_ref=work_item.source.ref,
            merge_reason="task_completion_evidence_confirmed",
            _db=db,
        ):
            closed_todo = change.todo_id
            closed_evidence = evidence

    for change in decision.follow_up_changes:
        summary = _json_dict(work_item.summary)
        task_blob = summary.get("business_task")
        task_id = _positive_id(task_blob.get("id")) if isinstance(task_blob, dict) else 0
        if task_id:
            _apply_business_task_follow_up_change(
                store, change, business_task_id=task_id, now=effective_now, db=db,
            )
        else:
            _apply_follow_up_change(store, change, now=effective_now, _db=db)

    if closed_todo and sync_external_todo:
        store.enqueue_task_todo_sync_outbox(
            operation_key=f"task-agent:{summary_input_id}:todo:{closed_todo}:complete",
            work_todo_id=closed_todo,
            operation="complete",
            evidence_json=json.dumps(closed_evidence, ensure_ascii=False, separators=(",", ":")),
            _db=db,
        )
    if closed_business_task and sync_external_todo:
        link = db.execute(
            "select 1 from business_task_dingtalk_links where business_task_id=? "
            "and status in ('creating','active') limit 1", (closed_business_task,),
        ).fetchone()
        if link is not None:
            store.enqueue_business_task_todo_sync_outbox(
                operation_key=f"task-agent:{summary_input_id}:business-task:{closed_business_task}:complete",
                business_task_id=closed_business_task,
                operation="complete",
                evidence_json=json.dumps(closed_evidence, ensure_ascii=False, separators=(",", ":")),
                _db=db,
            )

    if work_item.source.type == WorkItemSourceType.TODO_COMPLETION_EVIDENCE_CANDIDATE:
        candidate = store.get_todo_evidence_candidate_by_work_summary_input(
            summary_input_id, _db=db
        )
        if candidate is not None:
            store.mark_todo_evidence_candidate(
                candidate.id,
                status="accepted" if closed_todo else "rejected",
                decision_json=json.dumps(decision.model_dump(mode="json"), ensure_ascii=False),
                _db=db,
            )
    return bool(closed_todo or closed_business_task or decision.follow_up_changes)


def process_task_completion_work_item(
    store: AutoReplyStore,
    codex_runner,
    work_input,
    *,
    now: str = "",
    dws=None,
    session_lease=None,
) -> None:
    active_run_id: int | None = None
    work_item = None
    try:
        if session_lease is not None:
            session_lease.assert_owned()
        work_item = WorkItem.model_validate_json(work_input.payload_json)
        if work_item.source.type not in COMPLETION_SOURCE_TYPES:
            raise ValueError(f"unsupported completion source type: {work_item.source.type}")
        active_run_id = store.begin_task_agent_run(work_input.id)
        decision = codex_runner.decide(
            prompt=build_task_completion_prompt(work_item),
            workload_key=str(active_run_id),
            session_scope_id=TASK_AGENT_SESSION_SCOPE_ID,
        )
        audit_events = list(getattr(codex_runner, "last_audit_tool_events", []) or [])
        from app.task_agent import _validate_task_agent_decision

        _validate_task_agent_decision(decision, work_item=work_item, now=now)
        validate_task_completion_decision(
            work_item, decision, now=now, audit_tool_events=audit_events
        )
        session_id = getattr(codex_runner, "last_session_id", None) or ""
        if session_lease is not None:
            session_lease.assert_owned()
        with store.task_agent_domain_apply_transaction() as db:
            from app.task_agent import apply_task_agent_decision

            task_result = apply_task_agent_decision(
                store,
                summary_input_id=work_input.id,
                work_item=work_item,
                decision=decision,
                codex_session_id=session_id,
                record_run=False,
                now=now,
                _db=db,
            )
            completion_changed = apply_task_completion_decision(
                store,
                summary_input_id=work_input.id,
                work_item=work_item,
                decision=decision,
                now=now,
                sync_external_todo=dws is not None,
                _db=db,
                audit_tool_events=audit_events,
            )
            changed = bool(
                completion_changed or task_result.task_ids or task_result.affected_task_ids
            )
            if changed:
                store.mark_work_summary_input_done(work_input.id, _db=db)
            else:
                store.mark_work_summary_input_skipped(
                    work_input.id,
                    decision.update_summary or "No completion or follow-up transition.",
                    _db=db,
                )
            store.finish_task_agent_run(
                active_run_id,
                status="completed",
                codex_session_id=session_id,
                decision_json=json.dumps(decision.model_dump(mode="json"), ensure_ascii=False),
                audit_summary="; ".join(
                    filter(
                        None,
                        (
                            _completion_audit_summary(decision),
                            *(item.update_summary for item in decision.task_decisions),
                            *task_result.skipped_reasons,
                        ),
                    )
                ),
                memory_recall_used=decision.memory_recall_used
                or any(item.memory_recall_used for item in decision.task_decisions),
                _db=db,
            )
        active_run_id = None
        from app.task_agent import _project_task_attention

        _project_task_attention(
            store,
            task_result.attention_proposals,
            tuple(dict.fromkeys((
                *task_result.affected_task_ids,
                *(change.business_task_id for change in decision.todo_changes if change.business_task_id is not None),
            ))),
        )
    except Exception as exc:
        _mark_candidate_error(store, work_input.id, str(exc))
        if active_run_id is not None:
            store.finish_task_agent_run(active_run_id, status="failed", error=str(exc))
        store.mark_work_summary_input_failed(work_input.id, str(exc))
        raise


def build_task_completion_prompt(work_item: WorkItem) -> str:
    from pathlib import Path

    from app.business_skills import bundled_business_skills_root
    from app.structured_agent import load_skill_text

    schema = json.dumps(TaskAgentDecision.model_json_schema(), ensure_ascii=False, indent=2)
    tracking_skill = load_skill_text(
        [bundled_business_skills_root() / "ceo-work-tracking" / "SKILL.md"]
    )
    todo_skill = load_skill_text(
        [Path.home() / ".agents" / "skills" / "dingtalk-todo" / "SKILL.md"]
    )
    tracking_section = _skill_section(tracking_skill, "## TODO Completion Discovery")
    todo_sections = "\n\n".join(
        filter(
            None,
            (
                _skill_section(todo_skill, "## 执行契约"),
                _skill_section(todo_skill, "## 路由优先级"),
                _skill_section(todo_skill, "## 关键约束"),
            ),
        )
    )
    return f"""You are the CEO Agent's Task Agent handling an existing-work lifecycle check. Return exactly one TaskAgentDecision JSON object, with task_decisions (0..N) and any applicable linked TODO or follow-up transitions in the same decision.

For TODO completion checks, close only the linked TODO when current evidence directly satisfies its stated completion condition. Include source, reason, description, completed_at, and checked_at in completion_evidence. Always return a compact top-level search_trace of at most three checked evidence sources, including when no completion is found; the source that supports completion_evidence.source must appear in that trace. Add a concise update_summary explaining the check result. If evidence is insufficient, return empty mutation arrays and leave the TODO open.

For follow-up completion checks, modify only the linked existing follow-up. Suppress or close it only when the supplied current state/evidence supports that transition. If it remains open, provide a future local work-hours schedule. Never create a replacement follow-up during repair.

Respect the source Work Item's search_policy. For every trace entry, return source_kind exactly from allowed_sources, source_ref, a concise result and reason, and source_created_at when available. The service stamps retrieved_at and binds audit_call_ids from the current run's read-tool events; never invent receipt IDs. Source timestamps must fit the supplied time window; the service-stamped retrieval time must not predate the window. Return no more sources or tool calls than the supplied budgets. If the current supplied linked TODO/follow-up state itself is the evidence, use its exact linked source reference and still cite the available read receipt where applicable. Memory recall is background only and cannot justify a transition.

Tool-use boundary (prompt guidance): use connected tools only for read-only discovery of source facts, identity, and context. Do not use CLI, API, or MCP tools to create, update, delete, send, or complete external records or messages. Return proposed Task and lifecycle changes only in this structured result; the service validates and applies supported operations. This prompt does not technically disable write-capable tools, so do not claim that it is an enforced permission boundary. The service applies any accepted local TODO completion through its existing outbox sync, preventing a double-write.

Prior session turns are background only. Decide this turn from the current
Work Item, current retrieved state, and fresh source evidence. Never cite an
earlier turn as proof of a current assignment, status, deadline, or completion.

Do not guess ownership, completion time, or source linkage. A transition requires non-empty reason and current evidence_check/source/reason/description tied to a trace entry.

Work Item JSON:\n{work_item.model_dump_json(indent=2)}

Current ceo-work-tracking completion contract:\n{tracking_section}

Current dingtalk-todo operation contract:\n{todo_sections}

TaskAgentDecision schema:\n{schema}
"""


def _apply_follow_up_change(
    store: AutoReplyStore,
    change: CompletionFollowUpChange,
    *,
    now: str,
    _db: sqlite3.Connection | None,
) -> None:
    current = store.get_follow_up_draft(change.follow_up_id, _db=_db)
    if current is None:
        raise ValueError(f"follow-up not found: {change.follow_up_id}")
    values: dict[str, object] = {
        "evidence_check_json": json.dumps(
            {
                "source": "task_completion_agent",
                "action": change.action,
                "reason": change.reason,
                "evidence": change.evidence_check,
                "checked_at": now,
            },
            ensure_ascii=False,
        )
    }
    if change.todo_id is not None:
        values["todo_id"] = change.todo_id
    if change.action == "suppress":
        values.update(status="skipped", suppressed_reason=change.reason or "completion_check_suppressed")
    elif change.action == "close":
        if str(current.status) in {"draft", "approved"}:
            values.update(status="skipped", suppressed_reason=change.reason or "completion_check_closed")
        values.update(reaction_status="completed", reaction_summary=change.reason)
    elif change.action == "reschedule":
        values.update(status="draft", suppressed_reason="", scheduled_at=change.next_due_at.strip())
    elif change.action == "keep_open":
        values.update(
            status="draft",
            suppressed_reason="",
            scheduled_at=change.next_due_at.strip(),
            reaction_summary=change.reason,
        )
        todo_id = change.todo_id or current.todo_id
        todo = store.get_work_todo(todo_id, _db=_db) if todo_id > 0 else None
        if todo is not None and todo.follow_up_question.strip():
            values["question_text"] = todo.follow_up_question.strip()
    elif change.action == "reassign":
        if change.owner_user_id is not None:
            values["owner_user_id"] = change.owner_user_id.strip()
        if change.owner_name is not None:
            values["owner_name"] = change.owner_name.strip()
        risk = _json_dict(current.risk_check_json)
        final_owner = {
            "owner_user_id": (
                change.owner_user_id
                if change.owner_user_id is not None
                else current.owner_user_id
            ),
            "owner_name": (
                change.owner_name
                if change.owner_name is not None
                else current.owner_name
            ),
        }
        owner_evidence = change.owner_evidence or risk.get("owner_evidence")
        _require_supported_owner(final_owner, owner_evidence)
        risk["owner_evidence"] = owner_evidence
        values.update(
            suppressed_reason="",
            reaction_status="redirect_owner",
            reaction_summary=change.reason,
            risk_check_json=json.dumps(risk, ensure_ascii=False),
        )
    store.update_follow_up_draft(change.follow_up_id, _db=_db, **values)


def _apply_business_task_follow_up_change(
    store: AutoReplyStore,
    change: CompletionFollowUpChange,
    *,
    business_task_id: int,
    now: str,
    db: sqlite3.Connection,
) -> None:
    current = db.execute(
        "select * from business_task_follow_ups where id=? and business_task_id=?",
        (change.follow_up_id, business_task_id),
    ).fetchone()
    if current is None:
        raise ValueError("business Task follow-up is not linked to this Task")
    if db.execute(
        "select 1 from business_task_follow_up_send_attempts where draft_id=? "
        "and draft_revision=? and state in ('claimed','sending') limit 1",
        (change.follow_up_id, current["revision"]),
    ).fetchone() is not None:
        raise ValueError("business Task follow-up send is still in progress")
    values: dict[str, object] = {
        "evidence_check_json": json.dumps({
            "source": "task_agent", "action": change.action,
            "reason": change.reason, "evidence": change.evidence_check,
            "checked_at": now,
        }, ensure_ascii=False),
    }
    if change.action == "suppress":
        values.update(status="skipped", suppressed_reason=change.reason)
    elif change.action == "close":
        values.update(status="completed", suppressed_reason=change.reason)
    elif change.action in {"reschedule", "keep_open"}:
        values.update(
            status="draft", scheduled_at=change.next_due_at.strip(),
            suppressed_reason="", send_result_json="{}",
        )
    elif change.action == "reassign":
        final_owner = {
            "owner_user_id": change.owner_user_id or current["owner_user_id"],
            "owner_name": change.owner_name or current["owner_name"],
        }
        _require_supported_owner(final_owner, change.owner_evidence)
        values.update(
            owner_user_id=final_owner["owner_user_id"],
            owner_name=final_owner["owner_name"],
            status="draft", suppressed_reason="",
        )
    db.execute(
        "update business_task_follow_ups set "
        + ", ".join(f"{column}=?" for column in values)
        + ", revision=revision+1, updated_at=current_timestamp where id=? and business_task_id=?",
        [*values.values(), change.follow_up_id, business_task_id],
    )


def _mark_candidate_error(store: AutoReplyStore, work_input_id: int, error: str) -> None:
    candidate = store.get_todo_evidence_candidate_by_work_summary_input(work_input_id)
    if candidate is not None:
        store.mark_todo_evidence_candidate(
            candidate.id,
            status="error",
            decision_json=json.dumps({"error": error}, ensure_ascii=False),
        )


def _validate_queued_source_and_persisted_links(
    store: AutoReplyStore,
    work_input_id: int,
    work_item: WorkItem,
    db: sqlite3.Connection,
) -> None:
    row = db.execute(
        "select source_type, source_ref, payload_json from work_summary_inputs where id=?",
        (work_input_id,),
    ).fetchone()
    if row is None:
        raise ValueError("completion queue input no longer exists")
    if row["source_type"] != work_item.source.type.value or row["source_ref"] != work_item.source.ref:
        raise ValueError("queued source_type/ref does not match the Work Item source")
    try:
        queued_item = WorkItem.model_validate_json(row["payload_json"])
    except ValueError as exc:
        raise ValueError("queued completion payload is invalid") from exc
    if queued_item.source.type != work_item.source.type or queued_item.source.ref != work_item.source.ref:
        raise ValueError("queued payload source does not match the processed Work Item")
    if queued_item.model_dump(mode="json") != work_item.model_dump(mode="json"):
        raise ValueError("processed Work Item differs from the persisted queue payload")

    summary = _json_dict(work_item.summary)
    task_blob = summary.get("business_task")
    task_id = _positive_id(task_blob.get("id")) if isinstance(task_blob, dict) else 0
    if task_id:
        task = store.get_business_task_in_transaction(task_id=task_id, _db=db)
        if task is None or task.status.value == "merged":
            raise ValueError("linked Business Task does not exist in persisted state")
        if work_item.source.type == WorkItemSourceType.FOLLOW_UP_COMPLETION_CHECK:
            follow_up = summary.get("follow_up")
            draft_id = _positive_id(follow_up.get("id")) if isinstance(follow_up, dict) else 0
            draft = db.execute(
                "select business_task_id, revision from business_task_follow_ups where id=?",
                (draft_id,),
            ).fetchone()
            if (
                draft is None or draft["business_task_id"] != task_id
                or work_item.source.ref
                != f"business-task-follow-up-repair:{draft_id}:{draft['revision']}"
            ):
                raise ValueError("follow-up source is not bound to the current Task and revision")
        elif not work_item.source.ref.startswith(f"business-task-completion-check:{task_id}:"):
            raise ValueError("completion source is not bound to linked Business Task")
        return
    todo_blob = summary.get("todo")
    todo_id = _positive_id(todo_blob.get("id")) if isinstance(todo_blob, dict) else 0
    todo = store.get_work_todo(todo_id, _db=db) if todo_id else None
    if todo is None:
        raise ValueError("linked TODO does not exist in persisted state")
    project_blob = summary.get("project")
    project_id = _positive_id(project_blob.get("id")) if isinstance(project_blob, dict) else 0
    if not project_id or todo.project_id != project_id:
        raise ValueError("linked TODO does not match the Work Item project")
    project = store.get_work_project(project_id, _db=db)
    if project is None:
        raise ValueError("linked project does not exist in persisted state")

    if work_item.source.type == WorkItemSourceType.TODO_COMPLETION_EVIDENCE_CANDIDATE:
        _validate_persisted_candidate(store, work_input_id, work_item, _db=db)
    elif work_item.source.type == WorkItemSourceType.TODO_COMPLETION_CHECK:
        if not work_item.source.ref.startswith(f"todo-completion-check:{todo_id}:"):
            raise ValueError("TODO completion source is not bound to the linked TODO")

    linked_ids = _linked_follow_up_ids(summary)
    if work_item.source.type == WorkItemSourceType.FOLLOW_UP_COMPLETION_CHECK:
        primary = summary.get("follow_up")
        primary_id = _positive_id(primary.get("id")) if isinstance(primary, dict) else 0
        if primary_id:
            linked_ids = {primary_id}
        if not primary_id or not work_item.source.ref.startswith(f"follow-up-repair:{primary_id}"):
            raise ValueError("follow-up completion source is not bound to its primary draft")
    for draft_id in linked_ids:
        draft = store.get_follow_up_draft(draft_id, _db=db)
        if draft is None or draft.todo_id != todo_id or draft.project_id != project_id:
            raise ValueError("linked follow-up draft does not match the Work Item TODO/project")


def _validate_persisted_candidate(
    store: AutoReplyStore, work_input_id: int, work_item: WorkItem,
    *, _db: sqlite3.Connection | None = None,
) -> None:
    if work_item.source.type != WorkItemSourceType.TODO_COMPLETION_EVIDENCE_CANDIDATE:
        return
    summary = _json_dict(work_item.summary)
    todo = summary.get("todo")
    evidence = summary.get("evidence_candidate")
    candidate = store.get_todo_evidence_candidate_by_work_summary_input(work_input_id, _db=_db)
    if not isinstance(todo, dict) or not isinstance(evidence, dict) or candidate is None:
        raise ValueError("TODO evidence candidate is not linked to its source record")
    if (
        candidate.id != _positive_id(evidence.get("id"))
        or candidate.todo_id != _positive_id(todo.get("id"))
        or candidate.source_ref != str(evidence.get("source_ref") or "").strip()
        or work_item.source.ref != f"todo-evidence:{candidate.id}"
    ):
        raise ValueError("TODO evidence candidate identity does not match its linked TODO/source")


def _search_trace_sources(decision: TaskCompletionDecision) -> set[str]:
    return {
        trace.source_ref.strip()
        for trace in decision.search_trace
    }


def _completion_search_policy(summary: dict[str, Any]) -> dict[str, Any]:
    policy = summary.get("search_policy")
    if not isinstance(policy, dict):
        raise ValueError("completion Work Item lacks search_policy")
    allowed = policy.get("allowed_sources")
    window = policy.get("time_window")
    limits = policy.get("limits")
    if not isinstance(allowed, list) or not allowed or not all(isinstance(x, str) for x in allowed):
        raise ValueError("search_policy.allowed_sources must be a non-empty string list")
    if not isinstance(window, dict) or not isinstance(limits, dict):
        raise ValueError("search_policy requires time_window and limits")
    for key in ("max_tool_calls", "max_sources_to_return"):
        if not isinstance(limits.get(key), int) or limits[key] < 1:
            raise ValueError(f"search_policy.limits.{key} must be a positive integer")
    return policy


def _validate_search_trace(
    traces: list[CompletionSearchTrace], *, policy: dict[str, Any], now: str,
    audit_tool_events: list[dict[str, str]] | None,
) -> None:
    window = policy["time_window"]
    limits = policy["limits"]
    if len(traces) > min(3, int(limits["max_sources_to_return"])):
        raise ValueError("search_trace exceeds the allowed source budget")
    allowed_sources = set(policy["allowed_sources"])
    start = _parse_evidence_time(str(window.get("prefer_since") or ""), "search_policy.time_window.prefer_since", now=now)
    end = _parse_evidence_time(str(window.get("end") or ""), "search_policy.time_window.end", now=now)
    if start > end:
        raise ValueError("search_policy time window is inverted")
    observed_call_ids = {
        str(event.get("call_id") or "").strip()
        for event in (audit_tool_events or [])
        if str(event.get("call_id") or "").strip()
    }
    if len(observed_call_ids) > int(limits["max_tool_calls"]):
        raise ValueError("observed runtime tool calls exceed search_policy budget")
    for trace in traces:
        if trace.source_kind not in allowed_sources:
            raise ValueError("search_trace source_kind is outside search_policy.allowed_sources")
        if not trace.source_ref.strip() or not trace.result.strip() or not trace.reason.strip():
            raise ValueError("search_trace requires source_ref, result, and reason")
        # Retrieval is stamped by the service at decision validation time; the
        # policy window bounds source dates, not when the queued check executes.
        trace.retrieved_at = now or datetime.now(timezone.utc).isoformat()
        retrieved = _parse_evidence_time(trace.retrieved_at, "search_trace.retrieved_at", now=now)
        if retrieved < start:
            raise ValueError("search_trace retrieval time predates search_policy.time_window")
        if trace.source_created_at:
            created = _parse_evidence_time(trace.source_created_at, "search_trace.source_created_at", now=now)
            if not start <= created <= end:
                raise ValueError("search_trace source time is outside search_policy.time_window")
        matching_ids = _receipt_ids_for_source(trace.source_ref, audit_tool_events or [])
        if not matching_ids:
            raise ValueError("search_trace source_ref cannot be matched to a current tool receipt")
        # The model-provided IDs are ignored: only IDs bound by service-side
        # matching against this runner's current transcript are persisted.
        trace.audit_call_ids = matching_ids
    unique_receipts = {call_id for trace in traces for call_id in trace.audit_call_ids}
    if len(unique_receipts) > int(limits["max_tool_calls"]):
        raise ValueError("search_trace exceeds the allowed tool-call budget")


def _receipt_ids_for_source(source_ref: str, events: list[dict[str, str]]) -> list[str]:
    needle = source_ref.strip().casefold()
    matches: set[str] = set()
    if not needle:
        return []
    for event in events:
        call_id = str(event.get("call_id") or "").strip()
        if not call_id or not str(event.get("tool") or "").strip():
            continue
        evidence = "\n".join(
            str(event.get(key) or "")
            for key in ("output", "result", "source_refs")
        ).casefold()
        if needle in evidence:
            matches.add(call_id)
    return sorted(matches)


def _require_non_memory_evidence_source(
    decision: TaskCompletionDecision, source_ref: str
) -> None:
    source = next(
        (item.source_kind for item in decision.search_trace if item.source_ref == source_ref),
        "",
    )
    if source == "memory_recall_for_background_only":
        raise ValueError("memory_recall cannot be completion evidence")


def _parse_evidence_time(value: str, label: str, *, now: str = "") -> datetime:
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{label} must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    parsed = parsed.astimezone(timezone.utc)
    if now:
        try:
            effective_now = datetime.fromisoformat(now.strip().replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError("current time must be ISO datetime") from exc
        if effective_now.tzinfo is None:
            effective_now = effective_now.replace(tzinfo=timezone.utc)
        if parsed > effective_now.astimezone(timezone.utc):
            raise ValueError(f"{label} cannot be in the future")
    return parsed


def _require_current_evidence(evidence: dict[str, Any], label: str) -> None:
    _require_fields(evidence, ("source", "reason", "description"), label)


def _completion_audit_summary(decision: TaskCompletionDecision) -> str:
    trace = "; ".join(
        f"{item.source_kind} {item.source_ref} [{','.join(item.audit_call_ids)}]: {item.result}"
        for item in decision.search_trace
    )
    return "; ".join(filter(None, (decision.update_summary, f"search_trace: {trace}")))


def _linked_follow_up_ids(summary: dict[str, Any]) -> set[int]:
    items = summary.get("follow_ups")
    if not isinstance(items, list):
        return set()
    return {value for item in items if isinstance(item, dict) if (value := _positive_id(item.get("id")))}


def _json_dict(raw: str) -> dict[str, Any]:
    try:
        value = json.loads(raw or "{}")
    except (TypeError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _positive_id(value: object) -> int:
    try:
        number = int(value or 0)
    except (TypeError, ValueError):
        return 0
    return number if number > 0 else 0


def _require_fields(value: dict[str, Any], fields: tuple[str, ...], label: str) -> None:
    for field in fields:
        if not str(value.get(field) or "").strip():
            raise ValueError(f"{label}.{field} is required")


def _validate_future_follow_up_time(value: str, *, now: str = "") -> None:
    try:
        scheduled = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("follow-up reschedule requires ISO datetime") from exc
    local_scheduled = (
        scheduled.replace(tzinfo=FOLLOW_UP_WORK_TZ)
        if scheduled.tzinfo is None
        else scheduled.astimezone(FOLLOW_UP_WORK_TZ)
    )
    if local_scheduled.weekday() >= 5 or not (
        FOLLOW_UP_WORK_START_HOUR
        <= local_scheduled.hour
        < FOLLOW_UP_WORK_END_HOUR
    ):
        raise ValueError("follow-up reschedule must be within local work hours")
    try:
        current = datetime.fromisoformat((now or datetime.now(timezone.utc).isoformat()).replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("current time must be ISO datetime") from exc
    current_aware = (
        current.replace(tzinfo=timezone.utc)
        if current.tzinfo is None
        else current.astimezone(timezone.utc)
    )
    if local_scheduled.astimezone(timezone.utc) <= current_aware:
        raise ValueError("follow-up reschedule must be in the future")


def _require_supported_owner(assigned: dict[str, object], evidence: object) -> None:
    if not any(str(value or "").strip() for value in assigned.values()):
        return
    if not str(assigned.get("owner_user_id") or "").strip():
        raise ValueError("follow-up owner requires a stable user ID")
    if not isinstance(evidence, dict):
        raise ValueError("follow-up owner evidence is required")
    _require_fields(evidence, ("source", "reason", "description"), "owner_evidence")
    if not owner_identity_is_supported(assigned, evidence):
        raise ValueError("follow-up owner evidence does not support assigned identity")


def _skill_section(skill: str, heading: str) -> str:
    start = skill.find(heading)
    if start < 0:
        raise ValueError(f"required operation Skill section missing: {heading}")
    next_heading = skill.find("\n## ", start + len(heading))
    return skill[start:] if next_heading < 0 else skill[start:next_heading]
