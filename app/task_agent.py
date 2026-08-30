import json
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Protocol
from zoneinfo import ZoneInfo

from pydantic import ValidationError

from app.agent_runtime_router import (
    ApprovedCodexCommandFactory,
    READ_ONLY_BACKGROUND_AGENT_BOUNDARY,
    RoutedCodexExecution,
    RoutedCodexExecutionError,
    RoutedResultCodec,
    RoutedResultValidationError,
    RoutedResultValidationRetry,
)
from app.codex_runner import memory_connector_config_issue
from app.config import repo_root
from app.external_retry import ExternalDependencyError
from app.routed_result_privacy import audit_references_from_full_events
from app.store import AutoReplyStore, RecentFollowUpCandidate
from app.structured_agent import load_skill_text
from app.task_models import (
    FollowUpDraftChange,
    FollowUpDraftDecision,
    TaskAgentDecision,
    TodoChange,
    TodoStatus,
    WorkItem,
    WorkSummaryInput,
    owner_identity_is_supported,
)
from app.task_retrieval import (
    render_candidate_prompt,
    retrieve_project_candidates,
)
from app.todo_completion import complete_follow_ups_for_todo
from app.todo_sync import (
    dispatch_task_todo_sync_outbox,
    maybe_create_dingtalk_todo,
    sync_completed_todo_to_dingtalk,
)

TASK_AGENT_AUDIT_EVENT_LIMIT = 200
TASK_AGENT_MAX_TIMEOUT_SECONDS = 900
# A required live DWS read can legitimately take several minutes without
# producing Codex JSONL output. Keep a finite bound while matching launchd's
# task-agent timeout policy.
TASK_AGENT_MAX_IDLE_TIMEOUT_SECONDS = 300
RECENT_FOLLOW_UP_CONTEXT_WINDOW = timedelta(days=7)
FOLLOW_UP_WORK_START_HOUR = 9
FOLLOW_UP_WORK_END_HOUR = 18
FOLLOW_UP_WORK_TZ = ZoneInfo("Asia/Shanghai")
WORK_TRACKING_SKILL_PATH = repo_root() / "skills" / "ceo-work-tracking" / "SKILL.md"
TASK_RUNTIME_CAPABILITIES = frozenset(
    {
        "structured_output",
        "local_schema_validation",
        "reviewed_read_tools",
    }
)
TASK_RESULT_CODEC = RoutedResultCodec.text(
    schema_id="task_agent.decision.v1",
    allow_evidence_source_refs=True,
)


class RepairableTaskDecisionValidationError(ValueError):
    """A typed Agent decision can be corrected in one bounded follow-up turn."""


class TaskCodex(Protocol):
    last_session_id: str
    last_transcript_start_line: int
    last_transcript_end_line: int

    def decide(
        self,
        *,
        prompt: str,
        workload_key: str,
        session_scope_id: str | None = None,
    ) -> TaskAgentDecision: ...


class TaskAgentRunner:
    def __init__(self, codex: TaskCodex):
        self.codex = codex

    def decide(
        self,
        work_item: WorkItem,
        candidate_prompt: str,
        *,
        memory_issue: str = "",
        run_id: int,
        session_scope_id: str,
    ) -> TaskAgentDecision:
        return self.codex.decide(
            prompt=build_task_agent_prompt(
                work_item,
                candidate_prompt,
                memory_issue=memory_issue,
            ),
            workload_key=str(run_id),
            session_scope_id=session_scope_id,
        )

    def repair_owner_assignment(
        self,
        work_item: WorkItem,
        candidate_prompt: str,
        decision: TaskAgentDecision,
        *,
        validation_error: str,
        memory_issue: str = "",
        run_id: int,
        session_scope_id: str,
    ) -> TaskAgentDecision:
        return self.codex.decide(
            prompt=build_owner_resolution_prompt(
                work_item,
                candidate_prompt,
                decision,
                validation_error=validation_error,
                memory_issue=memory_issue,
            ),
            workload_key=str(run_id),
            session_scope_id=session_scope_id,
        )

    def repair_validation(
        self,
        work_item: WorkItem,
        candidate_prompt: str,
        decision: TaskAgentDecision,
        *,
        validation_error: str,
        memory_issue: str = "",
        run_id: int,
        session_scope_id: str,
    ) -> TaskAgentDecision:
        return self.codex.decide(
            prompt=build_task_agent_validation_repair_prompt(
                work_item,
                candidate_prompt,
                decision,
                validation_error=validation_error,
                memory_issue=memory_issue,
            ),
            workload_key=str(run_id),
            session_scope_id=session_scope_id,
        )


class TaskAgentCodexRunner:
    def __init__(
        self,
        *,
        routed_execution: RoutedCodexExecution,
    ):
        from app.codex_decision import extract_codex_audit_events
        from app.codex_history import (
            extract_codex_audit_events_from_session,
        )

        self.routed_execution = routed_execution
        self._extract_codex_audit_events = extract_codex_audit_events
        self._extract_codex_audit_events_from_session = (
            extract_codex_audit_events_from_session
        )
        self.last_session_id: str | None = None
        self.last_audit_tool_events: list[dict[str, str]] = []
        self.last_transcript_start_line = 0
        self.last_transcript_end_line = 0

    def decide(
        self,
        *,
        prompt: str,
        workload_key: str,
        session_scope_id: str | None = None,
    ) -> TaskAgentDecision:
        self.last_session_id = None
        self.last_audit_tool_events = []
        self.last_transcript_start_line = 0
        self.last_transcript_end_line = 0
        try:
            result = self.routed_execution.execute(
                workload_kind="task",
                workload_key=workload_key,
                prompt=prompt,
                command_factory=ApprovedCodexCommandFactory.read_only_task(
                    developer_instructions=(
                        "Return exactly one TaskAgentDecision JSON object. "
                        "Use only reviewed read tools.\n\n"
                        + READ_ONLY_BACKGROUND_AGENT_BOUNDARY
                    ),
                ),
                parser=_encode_task_agent_result,
                result_codec=TASK_RESULT_CODEC,
                conversation_id=session_scope_id,
                required_capabilities=TASK_RUNTIME_CAPABILITIES,
                result_validation_retry=RoutedResultValidationRetry.same_session_exactly_once(
                    correction_prompt=_task_result_validation_repair_prompt
                ),
            )
        except RoutedCodexExecutionError as exc:
            if not exc.retryable_external_dependency:
                raise
            raise ExternalDependencyError(
                "codex task agent", exc, dependency="codex"
            ) from exc
        payload = json.loads(result.value)
        decision = TaskAgentDecision.model_validate(payload["decision"])
        self.last_session_id = result.session_id or None
        self.last_transcript_start_line = result.transcript_start
        self.last_transcript_end_line = result.transcript_end
        session_events = []
        if self.last_session_id:
            session_events = self._extract_codex_audit_events_from_session(
                self.last_session_id,
                start_line=self.last_transcript_start_line,
                end_line=self.last_transcript_end_line,
                limit=TASK_AGENT_AUDIT_EVENT_LIMIT,
            )
        self.last_audit_tool_events = session_events or payload["audit_tool_events"]
        return decision


def _encode_task_agent_result(raw: str) -> str:
    from app.codex_decision import extract_codex_audit_events

    decision = _parse_task_agent_decision(raw)
    encoded = json.dumps(
        {
            "decision": decision.model_dump(mode="json"),
            "audit_tool_events": audit_references_from_full_events(
                extract_codex_audit_events(raw, limit=TASK_AGENT_AUDIT_EVENT_LIMIT),
                limit=TASK_AGENT_AUDIT_EVENT_LIMIT,
            ),
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )
    try:
        TASK_RESULT_CODEC.encode(encoded)
    except ValueError as exc:
        raise RoutedResultValidationError(
            "task result contains a runtime path outside an evidence source field",
            raw_output=raw,
        ) from exc
    return encoded


def _task_result_validation_repair_prompt(_raw_output: str) -> str:
    return (
        "Resume the same task decision and return exactly one valid "
        "TaskAgentDecision JSON object. Do not include local filesystem paths, "
        "session paths, lock paths, credentials, or runtime diagnostics in any "
        "business field. A source path may appear only in an evidence field "
        "whose key is exactly source or source_ref; summarize read failures "
        "without copying the runtime path."
    )


def build_task_agent_prompt(
    work_item: WorkItem,
    candidate_prompt: str,
    *,
    memory_issue: str = "",
    current_time: str = "",
) -> str:
    skill_text = load_skill_text([WORK_TRACKING_SKILL_PATH])
    work_item_json = json.dumps(
        work_item.model_dump(mode="json"),
        ensure_ascii=False,
        indent=2,
    )
    memory_status = _memory_connector_prompt_status(memory_issue)
    effective_current_time = current_time.strip() or datetime.now(
        timezone.utc
    ).isoformat()
    decision_schema = json.dumps(
        TaskAgentDecision.model_json_schema(),
        ensure_ascii=False,
        indent=2,
    )
    return f"""You are the CEO Agent task agent. Update tracked work only; do not
reply to the current message. Follow the loaded Skill and return exactly one
TaskAgentDecision JSON object that satisfies the supplied Pydantic schema.

Current execution time: {effective_current_time}
Any follow_up_change.next_due_at must be strictly later than this execution
time and must satisfy the documented local work-hours constraint. Do not reuse
the source creation time or an earlier scheduled time as a future deadline.

{skill_text}

Memory connector status facts:
{memory_status}

Required evidence sequence for every non-skip decision:
- When memory_recall is available, call it with a focused query about the
  work item, project, prior commitments, and owner context before deciding.
  Set memory_recall_used=true only after that tool call is present in the
  session receipt.
- Memory is stable background, not proof of the current external state. Use
  the applicable live DWS read for current people, ownership, task, meeting,
  or document state before creating, updating, or following up on work.
- When memory_recall is unavailable after tool discovery, set
  project.memory_context.memories[].source exactly to
  "memory_connector_runtime_unavailable", explain the unavailable condition
  in its summary, and use live evidence; set memory_recall_used=false and do
  not claim that recall ran.

Current Work Item JSON:
{work_item_json}

Current candidate context:
{candidate_prompt}

Existing follow-up repair rule:
- When the Work Item summary contains follow_up.id, it identifies an existing
  follow-up draft under review. Do not recreate that draft as a new
  follow_up_draft just to change its schedule, target, or state. Use a
  follow_up_change for that id after reading the linked TODO and, when present,
  original_work_update.source_type/source_ref source material.
- A persisted owner id or name is not owner evidence. Create or reassign a
  follow-up only when the original source independently supports the owner and
  you can provide owner evidence with source, reason, and description. For
  every non-empty owner assignment, the matching evidence object must also
  repeat that same stable user_id and name: project.owner_evidence,
  todo_changes[*].owner_evidence, and
  follow_up_drafts[*].risk_check.owner_evidence. Do not invent owner evidence
  from matching stored records.
- If the original source cannot support an owner, keep the existing follow-up
  suppressed with a clear evidence_check rather than emitting an invalid new
  follow_up_draft. Do not send a message as part of this repair decision.

Follow-up draft participant contract:
- Emit a follow_up_draft only when its participants list is non-empty and every
  participant has a stable user_id from a focused live directory/contact read.
  Include every identified recipient and responsible person relevant to that
  follow-up; names alone are not enough.
- If you cannot reliably identify at least one participant, do not emit a
  follow_up_draft. You may retain a supported project update, but must omit the
  owner-dependent TODO or follow-up rather than guessing an identity.

Runtime-data boundary:
- Do not copy local filesystem paths, session paths, lock paths, credentials, or
  other runtime diagnostics into business descriptions, summaries, titles,
  reasons, or risks. A source path may appear only in an evidence field whose
  key is exactly source or source_ref; describe failed reads without repeating
  the runtime path.

Material-to-task boundary:
- Decide first whether the source records a durable work update. A reference
  document, script, presentation, or other informational artifact is not a
  task merely because it has an author, speaker, or topic.
- When the source does not establish a concrete commitment, owner, deadline,
  progress change, or next step, return action="skip" with a clear reason.
  Do not create a project, TODO, or follow-up for material that lacks such a
  work signal.
- Never infer an owner from the author, speaker, participants, or a matching
  stored project. A non-skip decision may leave ownership empty only when
  the source establishes a real work update but does not assign an owner.

TaskAgentDecision Pydantic JSON schema:
{decision_schema}
"""


def build_candidate_context_prompt(
    *,
    project_candidates: str,
    follow_up_candidates: str,
) -> str:
    return (
        "候选项目:\n"
        f"{project_candidates}\n\n"
        "近期 follow-up 候选:\n"
        f"{follow_up_candidates}"
    )


def build_owner_resolution_prompt(
    work_item: WorkItem,
    candidate_prompt: str,
    decision: TaskAgentDecision,
    *,
    validation_error: str,
    memory_issue: str = "",
) -> str:
    skill_text = load_skill_text([WORK_TRACKING_SKILL_PATH])
    decision_schema = json.dumps(
        TaskAgentDecision.model_json_schema(),
        ensure_ascii=False,
        indent=2,
    )
    return f"""Repair the previous CEO Agent task decision. Return exactly one complete
TaskAgentDecision JSON object that satisfies the supplied Pydantic schema.

The previous decision was rejected before any project, TODO, follow-up, or
external message was created because: {validation_error}

{skill_text}

Use the existing memory context only as stable background. For any owner you
keep, perform a focused live directory/contact read and include the stable
owner_user_id plus source, reason, and description in owner_evidence. The
owner_evidence object itself must repeat that same identity as user_id and
name, so the persisted evidence can be verified independently. Do not infer an
owner from an author, speaker, participant, or name-only match.

If the source establishes a real work update but a responsible person cannot be
uniquely resolved, keep the project owner fields and owner_evidence empty. Do
not create a TODO or follow-up that depends on that unverified owner. Do not
send a message while repairing this decision.

For every follow_up_draft you keep, participants must be a non-empty list of
stable user identities returned by a focused live directory/contact read; each
entry must include user_id. If no participant can be reliably identified, omit
the follow-up draft rather than guessing from a display name or source text.

Memory connector status facts:
{_memory_connector_prompt_status(memory_issue)}

Current Work Item JSON:
{work_item.model_dump_json(indent=2)}

Current candidate context:
{candidate_prompt}

Previous rejected decision JSON:
{decision.model_dump_json(indent=2)}

TaskAgentDecision Pydantic JSON schema:
{decision_schema}
    """


def build_task_agent_validation_repair_prompt(
    work_item: WorkItem,
    candidate_prompt: str,
    decision: TaskAgentDecision,
    *,
    validation_error: str,
    memory_issue: str = "",
) -> str:
    return (
        build_task_agent_prompt(
            work_item,
            candidate_prompt,
            memory_issue=memory_issue,
        )
        + f"""

Validation repair:
The previous decision was rejected before any project, TODO, follow-up, or
external message was created because: {validation_error}

This repair is read-only. Call memory_recall now with a focused query about
this work item, its project, prior commitments, and owner context. Do not
reuse the previous decision's memory_recall_used flag unless the new session
receipt contains the tool call. Return a complete replacement decision after
the tool call; do not send messages or perform writes.

If the rejected decision used update_project but did not establish a stable
integer project ID, the replacement may use update_project only with an ID
from the current candidate context or a successful current task-management
read. If no such ID can be established, return skip; 不得改成 create_project
to bypass the missing-ID error or create a possible duplicate project.

Previous rejected decision JSON:
{decision.model_dump_json(indent=2)}
"""
    )


def render_follow_up_candidate_prompt(
    candidates: list[RecentFollowUpCandidate],
) -> str:
    payload = []
    for candidate in candidates:
        payload.append(
            {
                "id": candidate.follow_up_id,
                "follow_up_id": candidate.follow_up_id,
                "project_id": candidate.project_id,
                "project_title": candidate.project_title,
                "project_status": candidate.project_status,
                "project_priority": candidate.project_priority,
                "project_risk_level": candidate.project_risk_level,
                "todo_id": candidate.todo_id,
                "todo_title": candidate.todo_title,
                "todo_status": candidate.todo_status,
                "todo_priority": candidate.todo_priority,
                "todo_deadline_at": candidate.todo_deadline_at,
                "todo_next_follow_up_at": candidate.todo_next_follow_up_at,
                "owner_user_id": candidate.owner_user_id,
                "owner_name": candidate.owner_name,
                "target_conversation_id": candidate.target_conversation_id,
                "target_kind": candidate.target_kind,
                "question_text": candidate.question_text,
                "scheduled_at": candidate.scheduled_at,
                "sent_at": candidate.sent_at,
                "status": candidate.status,
                "reaction_status": candidate.reaction_status,
                "reaction_summary": candidate.reaction_summary,
                "suppressed_reason": candidate.suppressed_reason,
                "evidence_check_json": candidate.evidence_check_json,
                "risk_check_json": candidate.risk_check_json,
                "send_result_json": candidate.send_result_json,
            }
        )
    return json.dumps(payload, ensure_ascii=False, indent=2)


def _recent_follow_up_context_since(created_at: str) -> str:
    created_at = created_at.strip()
    if not created_at:
        return ""
    for parser in (
        lambda text: datetime.fromisoformat(text.replace("Z", "+00:00")),
        lambda text: datetime.strptime(text, "%Y-%m-%d %H:%M:%S"),
    ):
        try:
            parsed = parser(created_at)
            return (parsed - RECENT_FOLLOW_UP_CONTEXT_WINDOW).strftime(
                "%Y-%m-%d %H:%M:%S"
            )
        except ValueError:
            continue
    return ""


def _memory_connector_prompt_status(memory_issue: str) -> str:
    issue = memory_issue.strip()
    if issue:
        return f"不可用：{issue}"
    return "可用：memory_recall tool is configured."


def process_work_item(
    store: AutoReplyStore,
    runner: TaskAgentRunner,
    work_input: WorkSummaryInput,
    *,
    dws=None,
    now: str = "",
) -> None:
    active_run_id: int | None = None
    try:
        memory_issue = memory_connector_config_issue()
        work_item = WorkItem.model_validate_json(work_input.payload_json)
        candidates = retrieve_project_candidates(
            store,
            summary=work_item.summary,
            project_name=work_item.project_name,
        )
        follow_up_candidates = store.list_recent_follow_up_candidates(
            conversation_id=work_item.source.conversation_id,
            owner_user_id=work_item.context.sender_user_id,
            since=_recent_follow_up_context_since(work_item.source.created_at),
            limit=10,
        )
        candidate_prompt = build_candidate_context_prompt(
            project_candidates=render_candidate_prompt(candidates),
            follow_up_candidates=render_follow_up_candidate_prompt(
                follow_up_candidates
            ),
        )
        active_run_id = store.begin_task_agent_run(work_input.id)
        session_scope_id = f"task:{active_run_id}"
        decision = runner.decide(
            work_item,
            candidate_prompt,
            memory_issue=memory_issue,
            run_id=active_run_id,
            session_scope_id=session_scope_id,
        )
        decision = _normalize_follow_up_change_times(decision)
        codex_session_id = getattr(runner.codex, "last_session_id", None) or ""
        audit_tool_events = getattr(runner.codex, "last_audit_tool_events", None)
        memory_recall_attempted = _audit_events_include_memory_recall(
            audit_tool_events
        )
        memory_runtime_unavailable = (
            _decision_reports_memory_runtime_unavailable(decision)
        )
        try:
            _validate_memory_recall_tool_event(
                decision,
                audit_tool_events,
                memory_issue=memory_issue,
                memory_runtime_unavailable=memory_runtime_unavailable,
            )
            _validate_task_agent_decision(
                decision,
                memory_issue=memory_issue,
                memory_recall_attempted=memory_recall_attempted,
                memory_runtime_unavailable=memory_runtime_unavailable,
                now=now,
            )
        except ValueError as exc:
            repairable_validation_error = isinstance(
                exc, RepairableTaskDecisionValidationError
            ) or str(exc) in {
                "non-discard task decision requires memory_recall tool event",
                "non-skip task decision requires memory_recall tool event",
            }
            if not repairable_validation_error:
                raise
            rejected_decision = decision
            store.finish_task_agent_run(
                active_run_id,
                status="failed",
                codex_session_id=codex_session_id,
                decision_json=_json_dumps(decision.model_dump(mode="json")),
                audit_summary=decision.update_summary,
                memory_recall_used=decision.memory_recall_used,
                error=str(exc),
            )
            active_run_id = store.begin_task_agent_run(work_input.id)
            session_scope_id = f"task:{active_run_id}"
            decision = runner.repair_validation(
                work_item,
                candidate_prompt,
                decision,
                validation_error=str(exc),
                memory_issue=memory_issue,
                run_id=active_run_id,
                session_scope_id=session_scope_id,
            )
            decision = _normalize_follow_up_change_times(decision)
            _validate_task_agent_validation_repair(
                rejected_decision,
                decision,
            )
            codex_session_id = getattr(runner.codex, "last_session_id", None) or ""
            audit_tool_events = getattr(runner.codex, "last_audit_tool_events", None)
            memory_recall_attempted = _audit_events_include_memory_recall(
                audit_tool_events
            )
            memory_runtime_unavailable = (
                _decision_reports_memory_runtime_unavailable(decision)
            )
            _validate_memory_recall_tool_event(
                decision,
                audit_tool_events,
                memory_issue=memory_issue,
                memory_runtime_unavailable=memory_runtime_unavailable,
            )
            _validate_task_agent_decision(
                decision,
                memory_issue=memory_issue,
                memory_recall_attempted=memory_recall_attempted,
                memory_runtime_unavailable=memory_runtime_unavailable,
                now=now,
            )
        try:
            _validate_owner_changes(store, decision)
        except OwnerResolutionRequired as exc:
            store.finish_task_agent_run(
                active_run_id,
                status="failed",
                codex_session_id=codex_session_id,
                decision_json=_json_dumps(decision.model_dump(mode="json")),
                audit_summary=decision.update_summary,
                memory_recall_used=decision.memory_recall_used,
                error=str(exc),
            )
            active_run_id = store.begin_task_agent_run(work_input.id)
            decision = runner.repair_owner_assignment(
                work_item,
                candidate_prompt,
                decision,
                validation_error=str(exc),
                memory_issue=memory_issue,
                run_id=active_run_id,
                session_scope_id=session_scope_id,
            )
            decision = _normalize_follow_up_change_times(decision)
            codex_session_id = getattr(runner.codex, "last_session_id", None) or ""
            audit_tool_events = getattr(runner.codex, "last_audit_tool_events", None)
            memory_recall_attempted = (
                memory_recall_attempted
                or _audit_events_include_memory_recall(audit_tool_events)
            )
            memory_runtime_unavailable = (
                memory_runtime_unavailable
                or (
                    _audit_events_include_memory_tool_discovery(audit_tool_events)
                    and _decision_reports_memory_runtime_unavailable(decision)
                )
            )
            _validate_memory_recall_tool_event(
                decision,
                audit_tool_events,
                memory_issue=memory_issue,
                memory_runtime_unavailable=memory_runtime_unavailable,
            )
            _validate_task_agent_decision(
                decision,
                memory_issue=memory_issue,
                memory_recall_attempted=memory_recall_attempted,
                memory_runtime_unavailable=memory_runtime_unavailable,
                now=now,
            )
            _validate_owner_changes(store, decision)
        with store.task_agent_domain_apply_transaction() as db:
            apply_task_agent_decision(
                store,
                summary_input_id=work_input.id,
                work_item=work_item,
                decision=decision,
                codex_session_id=codex_session_id,
                memory_issue=memory_issue,
                memory_recall_attempted=memory_recall_attempted,
                memory_runtime_unavailable=memory_runtime_unavailable,
                record_run=False,
                dws=dws,
                now=now,
                _db=db,
            )
            if decision.action == "skip":
                store.mark_work_summary_input_skipped(
                    work_input.id,
                    decision.skip_reason or decision.update_summary,
                    _db=db,
                )
            else:
                store.mark_work_summary_input_done(work_input.id, _db=db)
            store.finish_task_agent_run(
                active_run_id,
                status="completed",
                codex_session_id=codex_session_id,
                decision_json=_json_dumps(decision.model_dump(mode="json")),
                audit_summary=decision.update_summary,
                memory_recall_used=decision.memory_recall_used,
                _db=db,
            )
        active_run_id = None
        if dws is not None:
            dispatch_task_todo_sync_outbox(
                store,
                dws,
                owner=f"task-agent-outbox:{work_input.id}",
                now=now or datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            )
    except Exception as exc:
        if active_run_id is not None:
            store.finish_task_agent_run(
                active_run_id,
                status="failed",
                error=str(exc),
            )
        store.mark_work_summary_input_failed(work_input.id, str(exc))
        raise


def apply_task_agent_decision(
    store: AutoReplyStore,
    *,
    summary_input_id: int,
    work_item: WorkItem,
    decision: TaskAgentDecision,
    codex_session_id: str = "",
    memory_issue: str = "",
    memory_recall_attempted: bool = False,
    memory_runtime_unavailable: bool = False,
    record_run: bool = True,
    dws=None,
    now: str = "",
    _db: sqlite3.Connection | None = None,
) -> int | None:
    decision = _normalize_follow_up_change_times(decision)
    _validate_task_agent_decision(
        decision,
        memory_issue=memory_issue,
        memory_recall_attempted=memory_recall_attempted,
        memory_runtime_unavailable=memory_runtime_unavailable,
        now=now,
    )
    _validate_owner_changes(store, decision)

    if record_run:
        store.record_task_agent_run(
            summary_input_id=summary_input_id,
            codex_session_id=codex_session_id,
            decision_json=_json_dumps(decision.model_dump(mode="json")),
            audit_summary=decision.update_summary,
            memory_recall_used=decision.memory_recall_used,
        )

    if decision.action == "skip":
        return None

    _validate_follow_up_change_targets(store, decision.follow_up_changes)

    if decision.project is None:
        raise ValueError(f"{decision.action} requires project")

    project_id = _apply_project(store, decision, _db=_db)
    update_id = store.create_work_update(
        project_id=project_id,
        source_type=work_item.source.type.value,
        source_ref=work_item.source.ref,
        summary=decision.update_summary,
        changes_json=_json_dumps(
            {
                "action": decision.action,
                "todo_changes": [
                    _todo_change_audit_payload(change)
                    for change in decision.todo_changes
                ],
                "follow_up_drafts": [
                    draft.model_dump(mode="json")
                    for draft in decision.follow_up_drafts
                ],
                "follow_up_changes": [
                    change.model_dump(mode="json")
                    for change in decision.follow_up_changes
                ],
            }
        ),
        merge_reason=decision.merge_reason,
        confidence=decision.confidence,
        _db=_db,
    )
    todo_refs: dict[str, int] = {}
    create_sync_todo_ids: list[int] = []
    sync_now = ""
    for todo_change in decision.todo_changes:
        todo_id = _apply_todo_change(
            store,
            project_id=project_id,
            update_id=update_id,
            change=todo_change,
            _db=_db,
        )
        if (
            dws is not None
            and todo_change.action == "close"
            and bool(todo_change.completion_evidence)
        ):
            sync_now = sync_now or now or datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            if _db is not None:
                store.enqueue_task_todo_sync_outbox(
                    operation_key=(
                        f"task-agent:{summary_input_id}:todo:{todo_id}:complete"
                    ),
                    work_todo_id=todo_id,
                    operation="complete",
                    evidence_json=_json_dumps(todo_change.completion_evidence),
                    _db=_db,
                )
            else:
                sync_completed_todo_to_dingtalk(
                    store, dws, work_todo_id=todo_id,
                    evidence=todo_change.completion_evidence, now=sync_now,
                )
        if todo_change.action in {"create", "update"}:
            create_sync_todo_ids.append(todo_id)
        if todo_change.action == "create" and todo_change.todo_ref.strip():
            todo_refs[todo_change.todo_ref.strip()] = todo_id
    for draft in decision.follow_up_drafts:
        _create_follow_up_draft(
            store,
            project_id=project_id,
            draft=draft,
            todo_refs=todo_refs,
            _db=_db,
        )
    for change in decision.follow_up_changes:
        _apply_follow_up_change(store, change, _db=_db)
    if dws is not None:
        sync_now = sync_now or now or datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        for todo_id in create_sync_todo_ids:
            if _db is not None:
                store.enqueue_task_todo_sync_outbox(
                    operation_key=f"task-agent:{summary_input_id}:todo:{todo_id}:create",
                    work_todo_id=todo_id,
                    operation="create",
                    _db=_db,
                )
            else:
                maybe_create_dingtalk_todo(
                    store, dws, work_todo_id=todo_id, now=sync_now
                )
    return project_id


def _validate_task_agent_decision(
    decision: TaskAgentDecision,
    *,
    memory_issue: str = "",
    memory_recall_attempted: bool = False,
    memory_runtime_unavailable: bool = False,
    now: str = "",
) -> None:
    for todo_change in decision.todo_changes:
        if todo_change.action != "create" and todo_change.todo_id is None:
            raise ValueError(f"{todo_change.action} requires todo_id")
        if todo_change.action == "close":
            evidence = todo_change.completion_evidence
            _require_evidence_fields(
                evidence,
                label="completion_evidence",
                fields=("source", "reason", "description", "completed_at"),
            )
    for draft in decision.follow_up_drafts:
        if not draft.title.strip():
            raise ValueError("follow_up_draft.title is required")
        if not draft.description.strip():
            raise ValueError("follow_up_draft.description is required")
        if not draft.owner_user_id.strip():
            raise ValueError(
                "follow_up_draft.owner_user_id is required as stable owner ID"
            )
        owner_ids = [
            str(owner.get("user_id") or "").strip()
            for owner in draft.owners
            if isinstance(owner, dict)
        ]
        if not owner_ids:
            raise ValueError("follow_up_draft.owners with user_id is required")
        if draft.owner_user_id.strip() not in owner_ids:
            raise ValueError("follow_up_draft.owner_user_id must be in owners")
        _require_evidence_fields(
            draft.risk_check.get("owner_evidence"),
            label="follow_up_draft.risk_check.owner_evidence",
            fields=("source", "reason", "description"),
        )
        if draft.todo_id is None and not draft.todo_ref.strip():
            raise ValueError("follow_up_draft requires todo_id or todo_ref")
        if not draft.scheduled_at.strip():
            raise ValueError("follow_up_draft.scheduled_at is required")
        if not str(draft.priority).strip():
            raise ValueError("follow_up_draft.priority is required")
        if not draft.tags:
            raise ValueError("follow_up_draft.tags is required")
        participant_ids = [
            str(participant.get("user_id") or "").strip()
            for participant in draft.participants
            if isinstance(participant, dict)
        ]
        if not participant_ids:
            raise ValueError("follow_up_draft.participants is required")
    for change in decision.follow_up_changes:
        if change.follow_up_id <= 0:
            raise ValueError("follow_up_change.follow_up_id is required")
        if change.action == "reschedule" and not (
            change.next_due_at and change.next_due_at.strip()
        ):
            raise ValueError(
                "follow_up_change.next_due_at is required for reschedule"
            )
        if change.action == "keep_open" and not (
            change.next_due_at and change.next_due_at.strip()
        ):
            raise ValueError(
                "follow_up_change.next_due_at is required for keep_open"
            )
        if change.action in {"reschedule", "keep_open"}:
            _validate_future_follow_up_time(change.next_due_at or "", now=now)
        if change.action == "reassign" and not (
            (change.owner_user_id and change.owner_user_id.strip())
            or (change.owner_name and change.owner_name.strip())
        ):
            raise ValueError(
                "follow_up_change.owner_user_id or owner_name is required for reassign"
            )
    if decision.action == "skip":
        return
    if (
        not memory_issue.strip()
        and not memory_recall_attempted
        and not memory_runtime_unavailable
        and not decision.memory_recall_used
    ):
        raise ValueError("non-skip task decision requires memory_recall_used")
    if decision.project is None:
        raise ValueError(f"{decision.action} requires project")
    memory_context = decision.project.memory_context
    if not memory_context.query.strip() or (
        not memory_context.summary.strip() and not memory_context.memories
    ):
        raise ValueError("non-skip task decision requires project.memory_context")
    if decision.action == "update_project" and decision.project.id is None:
        raise RepairableTaskDecisionValidationError(
            "update_project requires project.id"
        )


def _validate_task_agent_validation_repair(
    rejected: TaskAgentDecision,
    replacement: TaskAgentDecision,
) -> None:
    rejected_unresolved_update = (
        rejected.action == "update_project"
        and rejected.project is not None
        and rejected.project.id is None
    )
    if rejected_unresolved_update and replacement.action == "create_project":
        raise ValueError(
            "validation repair cannot convert unresolved update_project "
            "to create_project"
        )


def _require_evidence_fields(
    evidence: object,
    *,
    label: str,
    fields: tuple[str, ...],
) -> None:
    if not isinstance(evidence, dict):
        raise ValueError(f"{label} is required")
    for field in fields:
        if not str(evidence.get(field) or "").strip():
            raise ValueError(f"{label}.{field} is required")


def _validate_future_follow_up_time(value: str, *, now: str) -> None:
    try:
        scheduled = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("follow_up_change.next_due_at must be an ISO datetime") from exc
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
        raise ValueError("follow_up_change.next_due_at must be within local work hours")
    if now.strip():
        try:
            current = datetime.fromisoformat(now.strip().replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError("now must be an ISO datetime") from exc
    else:
        current = datetime.now(timezone.utc)
    current_aware = (
        current.replace(tzinfo=timezone.utc)
        if current.tzinfo is None
        else current.astimezone(timezone.utc)
    )
    if local_scheduled.astimezone(timezone.utc) <= current_aware:
        raise ValueError("follow_up_change.next_due_at must be in the future")


def _normalize_follow_up_change_times(decision: TaskAgentDecision) -> TaskAgentDecision:
    """Move reversible follow-up scheduling requests into the next work window."""
    changes = []
    changed = False
    for change in decision.follow_up_changes:
        if change.action in {"reschedule", "keep_open"} and change.next_due_at:
            normalized = _normalize_follow_up_time(change.next_due_at)
            if normalized != change.next_due_at:
                change = change.model_copy(update={"next_due_at": normalized})
                changed = True
        changes.append(change)
    if not changed:
        return decision
    return decision.model_copy(update={"follow_up_changes": changes})


def _json_object(value: str) -> dict[str, object]:
    try:
        parsed = json.loads(value or "{}")
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


class OwnerResolutionRequired(ValueError):
    pass


def _require_supported_owner(
    *,
    assigned: dict[str, object],
    evidence: object,
    label: str,
) -> None:
    if not any(str(value or "").strip() for value in assigned.values()):
        return
    stable_id = next(
        (
            str(assigned.get(field) or "").strip()
            for field in ("owner_user_id", "user_id", "open_dingtalk_id")
            if str(assigned.get(field) or "").strip()
        ),
        "",
    )
    if not stable_id:
        raise OwnerResolutionRequired(f"{label} requires a stable owner ID")
    _require_evidence_fields(
        evidence,
        label=label,
        fields=("source", "reason", "description"),
    )
    if not owner_identity_is_supported(assigned, evidence):
        raise OwnerResolutionRequired(
            f"{label} does not support assigned owner identity"
        )


def _validate_owner_changes(store: AutoReplyStore, decision: TaskAgentDecision) -> None:
    project = decision.project
    if project is not None:
        current_project = (
            store.get_work_project(project.id)
            if decision.action == "update_project" and project.id is not None
            else None
        )
        final_project_owner = {
            "owner_user_id": (
                project.owner_user_id
                if "owner_user_id" in project.model_fields_set
                else current_project.owner_user_id if current_project is not None else ""
            ),
            "owner_name": (
                project.owner_name
                if "owner_name" in project.model_fields_set
                else current_project.owner_name if current_project is not None else ""
            ),
        }
        project_owner_changed = current_project is None or final_project_owner != {
            "owner_user_id": current_project.owner_user_id,
            "owner_name": current_project.owner_name,
        }
        if project_owner_changed:
            _require_supported_owner(
                assigned=final_project_owner,
                evidence=project.owner_evidence,
                label="project.owner_evidence",
            )

    for change in decision.todo_changes:
        current_todo = (
            store.get_work_todo(change.todo_id)
            if change.todo_id is not None and change.action != "create"
            else None
        )
        if change.action == "create" or {
            "owner_user_id",
            "owner_name",
            "owner_evidence",
        } & change.model_fields_set:
            final_todo_owner = {
                "owner_user_id": (
                    change.owner_user_id
                    if "owner_user_id" in change.model_fields_set
                    else current_todo.owner_user_id if current_todo is not None else ""
                ),
                "owner_name": (
                    change.owner_name
                    if "owner_name" in change.model_fields_set
                    else current_todo.owner_name if current_todo is not None else ""
                ),
            }
            todo_owner_changed = current_todo is None or final_todo_owner != {
                "owner_user_id": current_todo.owner_user_id,
                "owner_name": current_todo.owner_name,
            }
            if todo_owner_changed:
                _require_supported_owner(
                    assigned=final_todo_owner,
                    evidence=change.owner_evidence,
                    label="todo_change.owner_evidence",
                )

    for draft in decision.follow_up_drafts:
        evidence = draft.risk_check.get("owner_evidence")
        _require_supported_owner(
            assigned={
                "owner_user_id": draft.owner_user_id,
                "owner_name": draft.owner_name,
            },
            evidence=evidence,
            label="follow_up_draft.risk_check.owner_evidence",
        )
        for index, owner in enumerate(draft.owners):
            _require_supported_owner(
                assigned=owner,
                evidence=evidence,
                label=f"follow_up_draft.owners[{index}].owner_evidence",
            )

    for change in decision.follow_up_changes:
        if change.action != "reassign":
            continue
        current = store.get_follow_up_draft(change.follow_up_id)
        if current is None:
            continue
        final_owner = {
            "owner_user_id": (
                change.owner_user_id
                if change.owner_user_id is not None
                else current.owner_user_id
            ),
            "owner_name": (
                change.owner_name if change.owner_name is not None else current.owner_name
            ),
        }
        changed = final_owner != {
            "owner_user_id": current.owner_user_id,
            "owner_name": current.owner_name,
        }
        evidence = change.owner_evidence
        if not evidence and not changed:
            evidence = _json_object(current.risk_check_json).get("owner_evidence")
        _require_supported_owner(
            assigned=final_owner,
            evidence=evidence,
            label="follow_up_change.owner_evidence",
        )


def _validate_memory_recall_tool_event(
    decision: TaskAgentDecision,
    audit_tool_events: object,
    *,
    memory_issue: str = "",
    memory_runtime_unavailable: bool = False,
) -> None:
    if decision.action == "skip" or audit_tool_events is None:
        return
    if not isinstance(audit_tool_events, list):
        return
    if _audit_events_include_memory_recall(audit_tool_events):
        return
    if memory_runtime_unavailable:
        return
    if memory_issue.strip():
        return
    raise ValueError("non-skip task decision requires memory_recall tool event")


def _audit_events_include_memory_recall(audit_tool_events: object) -> bool:
    if not isinstance(audit_tool_events, list):
        return False
    for event in audit_tool_events:
        if not isinstance(event, dict):
            continue
        tool = str(event.get("tool") or "")
        if "memory_recall" in tool:
            return True
    return False


def _audit_events_include_memory_tool_discovery(audit_tool_events: object) -> bool:
    if not isinstance(audit_tool_events, list):
        return False
    discovery_tools = {
        "tool_search_call",
        "list_mcp_resources",
        "list_mcp_resource_templates",
    }
    for event in audit_tool_events:
        if not isinstance(event, dict):
            continue
        tool = str(event.get("tool") or "")
        if tool in discovery_tools:
            return True
    return False


def _decision_reports_memory_runtime_unavailable(
    decision: TaskAgentDecision,
) -> bool:
    if decision.project is None:
        return False
    return any(
        item.source
        in {
            "memory_connector_runtime_unavailable",
            "memory_recall_runtime_failure",
        }
        for item in decision.project.memory_context.memories
    )


def _apply_project(
    store: AutoReplyStore,
    decision: TaskAgentDecision,
    *,
    _db: sqlite3.Connection | None = None,
) -> int:
    project = decision.project
    if project is None:
        raise ValueError(f"{decision.action} requires project")
    if decision.action == "create_project":
        return store.create_work_project(_db=_db, **_project_values(project))
    if project.id is None:
        raise ValueError("update_project requires project.id")
    current_project = store.get_work_project(project.id, _db=_db)
    fields = project.model_fields_set - {"id"}
    if current_project is not None:
        final_owner = {
            "owner_user_id": (
                project.owner_user_id
                if "owner_user_id" in fields
                else current_project.owner_user_id
            ),
            "owner_name": (
                project.owner_name
                if "owner_name" in fields
                else current_project.owner_name
            ),
        }
        if final_owner == {
            "owner_user_id": current_project.owner_user_id,
            "owner_name": current_project.owner_name,
        }:
            fields -= {"owner_user_id", "owner_name", "owner_evidence"}
    values = _project_values(project, only_fields=fields)
    store.update_work_project(project.id, _db=_db, **values)
    return project.id


def _project_values(project, only_fields: set[str] | None = None) -> dict[str, object]:
    fields = {
        "title": "title",
        "category": "category",
        "tags": "tags_json",
        "status": "status",
        "priority": "priority",
        "risk_level": "risk_level",
        "needs_derek_attention": "needs_derek_attention",
        "owner_user_id": "owner_user_id",
        "owner_name": "owner_name",
        "owner_evidence": "owner_evidence_json",
        "related_people": "related_people_json",
        "goal": "goal",
        "background": "background",
        "memory_context": "memory_context_json",
        "facts": "facts_json",
        "current_state": "current_state",
        "blocker": "blocker",
        "next_step": "next_step",
        "next_follow_up_at": "next_follow_up_at",
        "follow_up_mode": "follow_up_mode",
        "source_conversations": "source_conversations_json",
    }
    values: dict[str, object] = {}
    for model_field, store_field in fields.items():
        if only_fields is not None and model_field not in only_fields:
            continue
        value = getattr(project, model_field)
        if model_field in {
            "tags",
            "related_people",
            "memory_context",
            "owner_evidence",
            "facts",
            "source_conversations",
        }:
            values[store_field] = _json_dumps(_jsonable(value))
        elif model_field == "needs_derek_attention":
            values[store_field] = int(bool(value))
        else:
            values[store_field] = _enum_value(value)
    return values


def _apply_todo_change(
    store: AutoReplyStore,
    *,
    project_id: int,
    update_id: int,
    change: TodoChange,
    _db: sqlite3.Connection | None = None,
) -> int:
    if change.action == "create":
        values = _todo_values(change)
        return store.create_work_todo(
            _db=_db,
            project_id=project_id,
            created_from_update_id=update_id,
            **values,
        )
    if change.todo_id is None:
        raise ValueError(f"{change.action} requires todo_id")
    current_todo = store.get_work_todo(change.todo_id, _db=_db)
    fields = change.model_fields_set - {"action", "todo_id"}
    if current_todo is not None:
        final_owner = {
            "owner_user_id": (
                change.owner_user_id
                if "owner_user_id" in fields
                else current_todo.owner_user_id
            ),
            "owner_name": (
                change.owner_name
                if "owner_name" in fields
                else current_todo.owner_name
            ),
        }
        if final_owner == {
            "owner_user_id": current_todo.owner_user_id,
            "owner_name": current_todo.owner_name,
        }:
            fields -= {"owner_user_id", "owner_name", "owner_evidence"}
    values = _todo_values(
        change,
        only_fields=fields,
    )
    if change.action == "close":
        values["status"] = "done"
    elif change.action == "cancel":
        values["status"] = "cancelled"
    store.update_work_todo(change.todo_id, _db=_db, **values)
    if change.action == "close" and change.completion_evidence:
        complete_follow_ups_for_todo(
            store,
            todo_id=change.todo_id,
            evidence=change.completion_evidence,
            now=str(change.completion_evidence.get("completed_at") or ""),
            _db=_db,
        )
    return change.todo_id


def _todo_values(
    change: TodoChange,
    only_fields: set[str] | None = None,
) -> dict[str, object]:
    values: dict[str, object] = {}
    fields = [
        "title",
        "description",
        "owner_user_id",
        "owner_name",
        "owner_evidence",
        "status",
        "priority",
        "deadline_at",
        "next_follow_up_at",
        "follow_up_question",
        "blocker",
    ]
    for field in fields:
        if only_fields is not None and field not in only_fields:
            continue
        value = getattr(change, field)
        if value not in ("", None):
            if field == "next_follow_up_at":
                value = _normalize_follow_up_time(str(value))
            if field == "owner_evidence":
                values["owner_evidence_json"] = _json_dumps(value)
            else:
                values[field] = _enum_value(value)
    if (
        only_fields is None or "completion_evidence" in only_fields
    ) and change.completion_evidence is not None:
        values["completion_evidence_json"] = _json_dumps(change.completion_evidence)
    return values


def _todo_change_audit_payload(change: TodoChange) -> dict[str, object]:
    payload: dict[str, object] = {"action": change.action}
    if change.todo_id is not None:
        payload["todo_id"] = change.todo_id
    if change.todo_ref:
        payload["todo_ref"] = change.todo_ref
    if change.owner_evidence:
        payload["owner_evidence"] = _jsonable(change.owner_evidence)
    if change.action == "create":
        payload.update(_todo_values(change))
        return payload

    for field, value in _todo_values(
        change,
        only_fields=change.model_fields_set - {"action", "todo_id"},
    ).items():
        payload[field] = value
    if change.action == "close":
        payload["status"] = "done"
    elif change.action == "cancel":
        payload["status"] = "cancelled"
    return payload


def _create_follow_up_draft(
    store: AutoReplyStore,
    *,
    project_id: int,
    draft: FollowUpDraftDecision,
    todo_refs: dict[str, int],
    _db: sqlite3.Connection | None = None,
) -> int:
    todo_id = _resolve_follow_up_todo_id(
        store,
        project_id=project_id,
        draft=draft,
        todo_refs=todo_refs,
        _db=_db,
    )
    todo = store.get_work_todo(todo_id, _db=_db)
    if todo is not None and (
        todo.status in {TodoStatus.DONE, TodoStatus.CANCELLED}
        or _has_json_content(todo.completion_evidence_json)
    ):
        return 0
    return store.create_follow_up_draft(
        _db=_db,
        project_id=project_id,
        todo_id=todo_id,
        title=draft.title,
        description=draft.description,
        owner_user_id=draft.owner_user_id,
        owner_name=draft.owner_name,
        owners_json=_json_dumps(draft.owners),
        target_conversation_id=draft.target_conversation_id,
        target_kind=draft.target_kind,
        question_text=draft.question_text,
        priority=_enum_value(draft.priority),
        tags_json=_json_dumps(draft.tags),
        participants_json=_json_dumps(draft.participants),
        files_json=_json_dumps(draft.files),
        risk_check_json=_json_dumps(draft.risk_check),
        status=_enum_value(draft.status),
        scheduled_at=_normalize_follow_up_time(draft.scheduled_at),
    )


def _apply_follow_up_change(
    store: AutoReplyStore,
    change: FollowUpDraftChange,
    *,
    _db: sqlite3.Connection | None = None,
) -> None:
    current = store.get_follow_up_draft(change.follow_up_id, _db=_db)
    if current is None:
        raise ValueError(
            f"follow_up_change.follow_up_id not found: {change.follow_up_id}"
        )
    evidence = {
        "source": "task_agent",
        "action": change.action,
        "reason": change.reason,
        "evidence": change.evidence_check,
    }
    values: dict[str, object] = {
        "evidence_check_json": _json_dumps(evidence),
    }
    if change.todo_id is not None:
        values["todo_id"] = change.todo_id

    if change.action == "suppress":
        values["status"] = "skipped"
        values["suppressed_reason"] = change.reason or "task_agent_suppressed"
    elif change.action == "close":
        if _enum_value(current.status) in {"draft", "approved"}:
            values["status"] = "skipped"
            values["suppressed_reason"] = change.reason or "task_agent_closed"
        values["reaction_status"] = "completed"
        values["reaction_summary"] = change.reason
    elif change.action == "reschedule":
        values["status"] = "draft"
        values["suppressed_reason"] = ""
        if change.next_due_at and change.next_due_at.strip():
            values["scheduled_at"] = change.next_due_at.strip()
    elif change.action == "reassign":
        values["suppressed_reason"] = ""
        if change.owner_user_id is not None:
            values["owner_user_id"] = change.owner_user_id.strip()
        if change.owner_name is not None:
            values["owner_name"] = change.owner_name.strip()
        if change.owner_evidence:
            risk_check = _json_object(current.risk_check_json)
            risk_check["owner_evidence"] = change.owner_evidence
            values["risk_check_json"] = _json_dumps(risk_check)
        values["reaction_status"] = "redirect_owner"
        values["reaction_summary"] = change.reason
    elif change.action == "keep_open":
        values["status"] = "draft"
        values["suppressed_reason"] = ""
        values["scheduled_at"] = change.next_due_at or ""
        values["reaction_summary"] = change.reason
        if change.todo_id is not None:
            todo = store.get_work_todo(change.todo_id, _db=_db)
            if todo is not None and todo.follow_up_question.strip():
                values["question_text"] = todo.follow_up_question.strip()

    store.update_follow_up_draft(change.follow_up_id, _db=_db, **values)


def _validate_follow_up_change_targets(
    store: AutoReplyStore,
    changes: list[FollowUpDraftChange],
) -> None:
    for change in changes:
        if store.get_follow_up_draft(change.follow_up_id) is None:
            raise ValueError(
                f"follow_up_change.follow_up_id not found: {change.follow_up_id}"
            )


def _resolve_follow_up_todo_id(
    store: AutoReplyStore,
    *,
    project_id: int,
    draft: FollowUpDraftDecision,
    todo_refs: dict[str, int],
    _db: sqlite3.Connection | None = None,
) -> int:
    todo_id = draft.todo_id
    if todo_id is None and draft.todo_ref.strip():
        todo_id = todo_refs.get(draft.todo_ref.strip())
        if todo_id is None:
            raise ValueError(f"unknown follow_up_draft.todo_ref: {draft.todo_ref}")
    if todo_id is None or todo_id <= 0:
        raise ValueError("follow_up_draft requires todo_id or todo_ref")
    todo = store.get_work_todo(todo_id, _db=_db)
    if todo is None:
        raise ValueError(f"follow_up_draft.todo_id not found: {todo_id}")
    if todo.project_id != project_id:
        raise ValueError(
            f"follow_up_draft.todo_id {todo_id} does not belong to project {project_id}"
        )
    return todo_id


def _has_json_content(value: str) -> bool:
    text = (value or "").strip()
    if not text:
        return False
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return True
    return bool(parsed)


def _normalize_follow_up_time(value: str) -> str:
    text = (value or "").strip()
    if not text:
        return ""
    try:
        scheduled = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return value

    adjusted = scheduled
    if adjusted.weekday() >= 5:
        days_until_monday = 7 - adjusted.weekday()
        adjusted = adjusted + timedelta(days=days_until_monday)
        adjusted = adjusted.replace(
            hour=FOLLOW_UP_WORK_START_HOUR,
            minute=0,
            second=0,
            microsecond=0,
        )
    elif adjusted.hour < FOLLOW_UP_WORK_START_HOUR:
        adjusted = adjusted.replace(
            hour=FOLLOW_UP_WORK_START_HOUR,
            minute=0,
            second=0,
            microsecond=0,
        )
    elif adjusted.hour >= FOLLOW_UP_WORK_END_HOUR:
        adjusted = adjusted + timedelta(days=1)
        while adjusted.weekday() >= 5:
            adjusted = adjusted + timedelta(days=1)
        adjusted = adjusted.replace(
            hour=FOLLOW_UP_WORK_START_HOUR,
            minute=0,
            second=0,
            microsecond=0,
        )

    if adjusted == scheduled:
        return value
    return adjusted.isoformat(timespec="seconds")


def _json_dumps(value: object) -> str:
    return json.dumps(_jsonable(value), ensure_ascii=False, separators=(",", ":"))


def _jsonable(value: object) -> object:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if isinstance(value, list):
        return [_jsonable(item) for item in value]
    if isinstance(value, dict):
        return {key: _jsonable(item) for key, item in value.items()}
    return _enum_value(value)


def _enum_value(value: object) -> object:
    return getattr(value, "value", value)


def _task_decision_text_candidates(payload: object) -> list[str]:
    candidates: list[str] = []

    def visit(value: object) -> None:
        if isinstance(value, str):
            candidates.append(value)
            return
        if isinstance(value, list):
            for item in value:
                visit(item)
            return
        if not isinstance(value, dict):
            return
        for key in ("message", "last_agent_message", "content", "text"):
            if key in value:
                visit(value[key])
        for key in ("item", "payload"):
            if key in value:
                visit(value[key])

    visit(payload)
    return candidates


def _parse_task_agent_decision(raw: str) -> TaskAgentDecision:
    def validate_candidate(candidate: str) -> TaskAgentDecision | None:
        try:
            return TaskAgentDecision.model_validate_json(candidate)
        except (ValueError, ValidationError):
            # Some Codex JSONL adapters concatenate a complete object with a
            # repeated continuation. Recover only the first complete object;
            # Pydantic still enforces the full decision schema below.
            try:
                payload, _ = json.JSONDecoder().raw_decode(candidate.lstrip())
            except json.JSONDecodeError:
                return None
            try:
                return TaskAgentDecision.model_validate(payload)
            except (ValueError, ValidationError):
                return None

    stripped = raw.strip()
    if decision := validate_candidate(stripped):
        return decision

    payloads: list[object] = []
    for line in stripped.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            payloads.append(json.loads(line))
        except json.JSONDecodeError:
            continue

    for payload in reversed(payloads):
        try:
            return TaskAgentDecision.model_validate(payload)
        except (ValueError, ValidationError):
            pass
        for text in _task_decision_text_candidates(payload):
            if decision := validate_candidate(text):
                return decision
    raise ValueError("No TaskAgentDecision JSON found")
