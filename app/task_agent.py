import json
import logging
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Protocol
from zoneinfo import ZoneInfo

from pydantic import ValidationError

from app.agent_cron.commands import ServiceCommandConsumerContext
from app.agent_runtime_router import (
    CodexCommandFactory,
    BACKGROUND_AGENT_RUNTIME_BOUNDARY,
    RoutedCodexExecution,
    RoutedCodexExecutionError,
    RoutedResultCodec,
    RoutedResultValidationError,
    RoutedResultValidationRetry,
)
from app.codex_runner import memory_connector_config_issue
from app.external_retry import ExternalDependencyError
from app.agent_result import agent_message_json_objects
from app.routed_result_privacy import audit_references_from_full_events
from app.store import AutoReplyStore
from app.business_skills import bundled_business_skills_root
from app.structured_agent import load_skill_text
from app.task_models import (
    TaskDecision,
    TaskAgentDecision,
    WorkItem,
    WorkItemSourceKind,
    WorkItemSourceType,
    WorkSummaryInput,
)
from app.task_agent_session import TASK_AGENT_SESSION_SCOPE_ID
from app.task_retrieval import (
    render_task_semantic_context,
    retrieve_task_semantic_context,
)
from app.task_semantic_models import (
    BusinessActorKind,
    BusinessEvidenceRole,
    BusinessRelationType,
    BusinessTask,
    BusinessTaskDateType,
    BusinessTaskStatus,
    BusinessRelevance,
    FormalTaskBasis,
)
from app.task_semantic_service import (
    AcceptancePolarity,
    ApplyAcceptance,
    MergeBusinessTasks,
    PromoteCandidate,
    RecordCandidate,
    RecordFormalTask,
    SourceSignal,
    TaskDateInput,
    TaskSemanticService,
    UpdateBusinessTask,
)
from app.task_semantic_rules import FormalityEvidence, IdentityEvidence
from app.task_business_resolution import BusinessResolutionService
from app.task_attention_projection import AttentionProposal, BusinessAttentionProjection

TASK_AGENT_AUDIT_EVENT_LIMIT = 200
# Field errors quoted back to the model in one correction turn.
TASK_DECISION_PROBLEM_LIMIT = 12
TASK_AGENT_MAX_TIMEOUT_SECONDS = 900
LOGGER = logging.getLogger(__name__)
# A required live DWS read can legitimately take several minutes without
# producing Codex JSONL output. Keep a finite bound while matching launchd's
# task-agent timeout policy.
TASK_AGENT_MAX_IDLE_TIMEOUT_SECONDS = 300
RECENT_FOLLOW_UP_CONTEXT_WINDOW = timedelta(days=7)
FOLLOW_UP_WORK_START_HOUR = 9
FOLLOW_UP_WORK_END_HOUR = 18
FOLLOW_UP_WORK_TZ = ZoneInfo("Asia/Shanghai")
WORK_TRACKING_SKILL_PATH = (
    bundled_business_skills_root() / "ceo-work-tracking" / "SKILL.md"
)
TASK_RUNTIME_CAPABILITIES = frozenset(
    {
        "structured_output",
        "local_schema_validation",
    }
)
TASK_RESULT_CODEC = RoutedResultCodec.text(
    schema_id="task_agent.decision.v1",
    allow_evidence_source_refs=True,
)


@dataclass(frozen=True)
class TaskAgentApplyResult:
    task_ids: tuple[int, ...]
    attention_proposals: tuple[tuple[TaskDecision, int, int], ...]
    affected_task_ids: tuple[int, ...] = ()
    skipped_reasons: tuple[str, ...] = ()

    def __iter__(self):
        return iter(self.task_ids)

    def __len__(self) -> int:
        return len(self.task_ids)

    def __getitem__(self, index):
        return self.task_ids[index]


# Repair turns a work item may spend per pass on repairable rules.
TASK_DECISION_REPAIR_ROUNDS = 2

# These fields describe the durable identity and management meaning of a project.
# The structured response schema supplies defaults for them, so a model that emits
# a full update object can otherwise erase good stored data accidentally.
PROTECTED_PROJECT_FIELDS = frozenset(
    {
        "title",
        "category",
        "tags",
        "status",
        "priority",
        "risk_level",
        "owner_user_id",
        "owner_name",
        "owner_evidence",
        "related_people",
        "goal",
        "background",
        "facts",
        "source_conversations",
    }
)

PROJECT_STORAGE_FIELDS = {
    "tags": "tags_json",
    "owner_evidence": "owner_evidence_json",
    "related_people": "related_people_json",
    "facts": "facts_json",
    "source_conversations": "source_conversations_json",
}


class RepairableTaskDecisionValidationError(ValueError):
    """A typed Agent decision can be corrected in one bounded follow-up turn."""



class TaskDecisionRepairExhausted(RepairableTaskDecisionValidationError):
    """The bounded repair rounds ended with a rule still unsatisfied.

    The work item is not a terminal failure for this: the caller retries it
    on a later pass with a fresh session.
    """

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
                command_factory=CodexCommandFactory.standard(
                    developer_instructions=(
                        "Return exactly one TaskAgentDecision JSON object.\n\n"
                        + BACKGROUND_AGENT_RUNTIME_BOUNDARY
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
            "decision": decision.model_dump(mode="json", exclude_unset=True),
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


def _task_result_validation_repair_prompt(raw_output: str) -> str:
    """Tell the model which schema rules its last decision broke."""
    decision, problems = _validate_task_decision_candidates(raw_output)
    if decision is not None:
        # The schema held, so the rejection came from the codec leak check in
        # _encode_task_agent_result.
        detail = (
            "- the decision satisfied the schema but a business field "
            "contained a runtime path"
        )
    elif problems:
        detail = "\n".join(f"- {problem}" for problem in problems)
    else:
        detail = (
            "- the reply did not contain a TaskAgentDecision JSON object; "
            "return only the JSON object without prose or code fences"
        )
    return (
        "The previous output was not accepted as a TaskAgentDecision. Resume "
        "the same task decision and return exactly one valid TaskAgentDecision "
        "JSON object.\n\n"
        f"Problems in the previous output:\n{detail}\n\n"
        "Rules that must hold:\n"
        "- Return the TaskAgentDecision envelope with task_decisions (0..N); "
        "every non-skip item needs exact source_excerpt and source_ref.\n"
        "- A formal assignment requires an explicit owner and authorized "
        "assignment source. Owner evidence alone does not prove authority.\n"
        "- apply_acceptance requires accepted polarity, an explicitly cited "
        "assignment signal, and a verified reply-to source reference; never "
        "infer or manufacture that link.\n"
        "- Memory is background only. When memory_recall is available, query "
        "focused prior context; for owner identity, use a live directory read "
        "and keep only an ID that maps to the source-named owner.\n"
        "- When there is nothing to retain, return an empty task_decisions "
        "list or an explicit skip item with a reason.\n\n"
        "Do not include local filesystem paths, session paths, lock paths, "
        "credentials, or runtime diagnostics in any business field. A source "
        "path may appear only in an evidence field whose key is exactly source "
        "or source_ref; summarize read failures without copying the runtime "
        "path."
    )


def build_task_agent_prompt(
    work_item: WorkItem,
    candidate_prompt: str,
    *,
    memory_issue: str = "",
    current_time: str = "",
) -> str:
    scheduled_consumer = ServiceCommandConsumerContext.from_payload(
        work_item.scheduled_consumer or None
    )
    current_skill_text = load_skill_text([WORK_TRACKING_SKILL_PATH])
    scheduled_skill_snapshot = (
        "## Scheduled Consumer Skill Snapshot (Supplemental Context)\n"
        "This snapshot may be older than the current Skill. The current Task-first "
        "decision envelope and rules below control this output if they conflict.\n\n"
        f"{scheduled_consumer.skill_protocol}\n"
        if scheduled_consumer is not None and scheduled_consumer.skill_protocol
        else ""
    )
    scheduled_consumer_prompt = (
        "## Scheduled Consumer Prompt\n"
        f"{scheduled_consumer.prompt}\n"
        if scheduled_consumer is not None
        else ""
    )
    work_item_payload = work_item.model_dump(mode="json")
    scheduled_payload = work_item_payload.get("scheduled_consumer")
    if isinstance(scheduled_payload, dict):
        # Keep scheduled metadata and its specialized prompt, but do not echo
        # stale output-contract text inside the source JSON as if authoritative.
        scheduled_payload.pop("skill_protocol", None)
    work_item_json = json.dumps(
        work_item_payload,
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
    return f"""You are the CEO Agent Task extractor. Do not reply to the source.
Always follow the current CEO Work Tracking Skill and return one TaskAgentDecision envelope with zero or
more task_decisions matching the schema.

{scheduled_consumer_prompt}
{scheduled_skill_snapshot}
{current_skill_text}

Current Task-first decision envelope controls output. A scheduled prompt or
Skill snapshot is supplemental workflow context only; it cannot replace this
envelope or authorize Project/TODO/follow-up writes through this Task Agent.

Tool-use boundary (prompt guidance): use connected tools only for read-only
discovery of source facts, identity, and context. Do not use CLI, API, or MCP
tools to create, update, delete, send, or complete external records or
messages. Return proposed Task and lifecycle changes only in this structured
result; the service validates and applies supported operations. This prompt
does not technically disable write-capable tools, so do not claim that it is
an enforced permission boundary.

Current execution time: {effective_current_time}
Memory connector status: {memory_status}

Prior session turns are background only. Decide this turn from the current
Work Item, current retrieved state, and fresh source evidence. Never cite an
earlier turn as proof of a current assignment, status, deadline, or completion.

Extract every distinct source-backed deliverable, or return an empty list.
One source may yield multiple decisions. Each non-skip item must quote an exact
substring of the supplied source summary and use its exact source reference.
Never invent a task, owner, assignment, acceptance, date, relevance, or
authority. An owner must be explicit in source text or authoritative source
metadata; an ownerless assignment stays candidate/unmatched evidence.

For AI Minutes, DingTalk leaves each action item's own executor empty, and the
Work Item's transcript_excerpts hold the conversation around it, one line per
sentence as “speaker：text”. Decide the owner from those lines: it is whoever
the conversation gives the work to or who takes it on, not automatically the
speaker (“你写下来” from one person assigns the work to the person addressed).
Set owner_evidence with two keys: "source_ref" (the Work Item source reference)
and "excerpt" (one single line copied unchanged, speaker label included,
that contains the owner's name; never join several lines or sentences). That line is often not the item's own source_excerpt: when one person
hands the work to another (“你写下来”), quote the line in which the person who
takes it on speaks, and keep the assigning line as the decision's source_excerpt. A generic label such as “发言人 N” is DingTalk's placeholder for a
speaker it could not name; it is not a person and never an owner. When the lines
do not settle who owns it, or an action item has no excerpt, leave the owner
empty.

An assignment creates an assigned_unaccepted Task. Only explicit evidence from
that identified owner may apply_acceptance to exactly one existing formal Task;
“收到” and external TODO existence are not acceptance. Use explicit
promote_candidate, apply_acceptance, update_fields, or merge_identity
transitions with existing IDs. Similarity rank is context only. Generic updates
cannot set commitment status.

Only identical deliverables may be proposed for identity merge; related tasks
remain linked or clustered. For Project and current Task authority, use the
most recent confirmed official weekly report first, especially a project-
management or management weekly report with explicit project, owner, target,
DDL, status, and next-task fields. A weekly report may aggregate meeting
minutes and project communications, but its exact document reference and
reporting period must be preserved. Confirmed meeting evidence (minutes,
transcript, or action items) is the next authority for newly decided work or
changes not yet reflected in a weekly report. Chat or message evidence only
supplements these sources with context, owners, status, or links; it cannot
create an official Project or override an explicit weekly-report field by
itself. When sources conflict, prefer the latest explicit weekly-report field,
then the latest confirmed meeting decision, and preserve the exact source
reference/excerpt. Project candidates must cite an existing cluster and the
authoritative weekly-report or meeting evidence; anchor and Project matches
remain proposals.

Dates use typed date_evidence with exact source excerpt/reference and actor.
Normalized dates must equal the full exact parseable date phrase; do not add
time precision absent from the source. assigned_at comes only from trusted
source timestamp metadata. An estimate keeps the identified source actor who
made it; the extracting Agent is not its actor. Only next_check_at is
Agent-authored, not an owner commitment. Other date facts need an identified source actor. AI Minutes
has no trusted speaker-to-identity mapping yet, so do not attribute a quoted
speaker's date to the meeting host or to a model-selected identity (this limit
is for dates; owners come from transcript_excerpts as above). Only owner
acceptance can establish committed_deadline_at. No date is required to retain a Task.
Attention requires a registered anchor plus a material trigger: threatened
accepted commitment, material change/dispute, CEO decision/push, required Gate,
or meaningful risk escalation. Relevance, acceptance, ordinary progress, or
date proximity alone is not attention. Retain real low-impact work when needed,
but keep it outside attention. “skip” means no plausible retained source task,
not no Project.

Memory is background only, never source proof. Do not copy runtime paths,
credentials, or diagnostics into business fields.

Current Work Item JSON:
{work_item_json}

Current semantic Task context (rank is context, never authority):
{candidate_prompt}

Evidence protocol:
- Use memory_recall as stable background when available; it is not proof of
  current source facts or assignment authority.
- For source-named owners, perform a focused live directory/contact lookup
  when available. Keep an owner_user_id only when that lookup maps the exact
  source-named person; otherwise preserve the name and leave the ID empty.
- A reply is accepted only when the source/provider context contains a
  verified reply_to_source_ref. Do not infer it from a Task ID or similar text.

TaskAgentDecision Pydantic JSON schema:
{decision_schema}
"""


def _memory_connector_prompt_status(memory_issue: str) -> str:
    issue = memory_issue.strip()
    if issue:
        return f"不可用：{issue}"
    return "可用：memory_recall tool is configured."





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


def _task_decision_candidates(raw: str) -> list[object]:
    """Collect decision-shaped JSON objects from raw text or a Codex JSONL stream."""
    stripped = raw.strip()
    candidates: list[object] = []
    # A Codex JSONL stream carries the decision inside an event's text field.
    for line in stripped.splitlines():
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        candidates.append(payload)
        for text in _task_decision_text_candidates(payload):
            candidates.extend(agent_message_json_objects(text))
    # Models regularly wrap the decision in prose or fences, and some Codex
    # JSONL adapters concatenate a complete object with a repeated
    # continuation; the extractor recovers every complete object either way.
    # The whole message goes last so its final object outranks an earlier
    # draft that also happens to sit alone on one line.
    candidates.extend(agent_message_json_objects(stripped))
    # Codex event objects ({"type": "item.completed", ...}) surround the
    # decision but never are one, so they must not be reported as the
    # failing candidate.
    return [
        candidate
        for candidate in candidates
        if isinstance(candidate, dict) and "task_decisions" in candidate
    ]


def _validate_task_decision_candidates(
    raw: str,
) -> tuple[TaskAgentDecision | None, list[str]]:
    """Return the last schema-valid candidate, else the field errors of the last one."""
    problems: list[str] = []
    # The last complete decision wins over an earlier draft.
    for candidate in reversed(_task_decision_candidates(raw)):
        try:
            return TaskAgentDecision.model_validate(candidate), []
        except ValidationError as exc:
            if not problems:
                # Field path and message only: error["input"] echoes the
                # model's values, which can carry runtime paths or credentials
                # into the correction prompt and the service log.
                problems = [
                    f"{'.'.join(str(part) for part in error['loc'])}: {error['msg']}"
                    for error in exc.errors()[:TASK_DECISION_PROBLEM_LIMIT]
                ]
    return None, problems



def _parse_task_agent_decision(raw: str) -> TaskAgentDecision:
    decision, problems = _validate_task_decision_candidates(raw)
    if decision is not None:
        return decision
    if not problems:
        raise RoutedResultValidationError(
            "No TaskAgentDecision JSON found",
            raw_output=raw,
        )
    raise RoutedResultValidationError(
        "TaskAgentDecision JSON does not satisfy the schema: "
        + "; ".join(problems),
        raw_output=raw,
    )


def _task_source_signal(work_item: WorkItem, item: TaskDecision) -> SourceSignal:
    import hashlib

    if item.source_ref != work_item.source.ref:
        raise ValueError("task decision source_ref must match the Work Item source")
    if not item.source_excerpt or item.source_excerpt not in work_item.summary:
        raise ValueError("task decision source_excerpt must be an exact source substring")
    def normalized(value: str) -> str:
        return " ".join(value.split()).casefold()
    date_effects = sorted(
        (
            evidence.kind,
            evidence.value.strip(),
            evidence.source_ref,
            evidence.source_excerpt,
        )
        for evidence in item.date_evidence
    )
    if item.action in {"create_task", "record_candidate"} or item.transition == "promote_candidate":
        # A source-backed creation is the same semantic item despite wording-only
        # changes to description/reason/attention presentation. Owner and basis
        # remain identity-bearing so same-quote assignments to different people
        # are distinct records.
        semantic_identity = {
            "kind": item.action,
            "transition": item.transition,
            "task_id": item.task_id,
            "source_ref": item.source_ref,
            "source_excerpt": item.source_excerpt,
            "title": normalized(item.title),
            "owner_user_id": item.owner_user_id.strip(),
            "owner_name": normalized(item.owner_name),
            "formal_basis": item.formal_basis.value if item.formal_basis else "",
        }
    else:
        # Updates are idempotent per target and actual business effects. Audit
        # prose, model quality scores, and CEO-attention copy are presentation,
        # not a new Task identity or state transition.
        relation_effects = sorted(
            (r.from_task_id, r.to_task_id, r.relation_type) for r in item.relation_proposals
        )
        anchor_effects = sorted((proposal.anchor_id,) for proposal in item.anchor_match_proposals)
        semantic_identity = {
            "kind": item.action,
            "transition": item.transition,
            "task_id": item.task_id,
            "target_task_id": item.target_task_id or 0,
            "source_ref": item.source_ref,
            "source_excerpt": item.source_excerpt,
            "title": normalized(item.title),
            "description": normalized(item.description),
            "status": item.status or "",
            "business_relevance": item.business_relevance or "",
            "owner_user_id": item.owner_user_id.strip(),
            "owner_name": normalized(item.owner_name),
            "acceptance_polarity": item.acceptance_polarity or "",
            "acceptance_target_signal_id": item.acceptance_target_signal_id or 0,
            "identity": (
                {
                    "source_task_id": item.identity_proposal.source_task_id,
                    "target_task_id": item.identity_proposal.target_task_id,
                    "basis": item.identity_proposal.identity_evidence.basis,
                    "source_signal_id": item.identity_proposal.identity_evidence.source_signal_id,
                    "target_signal_id": item.identity_proposal.identity_evidence.target_signal_id,
                }
                if item.identity_proposal else None
            ),
            "relations": relation_effects,
            "cluster": (
                {
                    "cluster_id": item.cluster_proposal.cluster_id,
                    "title": normalized(item.cluster_proposal.title),
                    "task_ids": sorted(item.cluster_proposal.task_ids),
                }
                if item.cluster_proposal else None
            ),
            "anchors": anchor_effects,
            "project_candidate": (
                {
                    "cluster_id": item.project_candidate_proposal.cluster_id,
                    "title": normalized(item.project_candidate_proposal.title),
                }
                if item.project_candidate_proposal else None
            ),
            "date_effects": date_effects,
        }
    stable_item = hashlib.sha256(json.dumps(
        semantic_identity, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")).hexdigest()
    return SourceSignal(
        source_type=work_item.source.type.value,
        source_ref=work_item.source.ref,
        evidence_text=work_item.summary,
        dedupe_key=f"{work_item.source.type.value}:{work_item.source.ref}:task-item:{stable_item}",
        source_time=work_item.source.created_at,
        conversation_id=work_item.source.conversation_id,
        conversation_title=work_item.source.conversation_title,
        author_user_id=work_item.context.sender_user_id,
        author_name=work_item.context.sender,
        author_kind=(BusinessActorKind.HUMAN if work_item.context.sender_user_id else BusinessActorKind.UNKNOWN),
        context_json=json.dumps(
            {
                "work_item_title": work_item.source.title,
                "assignment_authorized": work_item.context.assignment_authorized,
                **({"reply_to_source_ref": work_item.context.reply_to_source_ref}
                   if work_item.context.reply_to_source_ref else {}),
                **({"external_task_id": work_item.context.external_task_id}
                   if work_item.context.external_task_id else {}),
                **({"owner_identity": work_item.context.owner_identity}
                   if work_item.context.owner_identity else {}),
            }, ensure_ascii=False, sort_keys=True
        ),
    )


def _task_date_inputs(
    item: TaskDecision, work_item: WorkItem, *, is_acceptance: bool
) -> tuple[TaskDateInput, ...]:
    values: list[TaskDateInput] = []
    for evidence in item.date_evidence:
        if evidence.source_ref != item.source_ref:
            raise ValueError("date evidence source_ref must match its task decision")
        if evidence.source_excerpt not in work_item.summary:
            raise ValueError("date evidence source_excerpt must be an exact source substring")
        if evidence.kind == "assigned_at":
            raise ValueError("assigned_at is derived only from trusted source timestamp metadata")
        if work_item.source.type is WorkItemSourceType.AI_MINUTES and evidence.kind != "next_check_at":
            raise ValueError(
                "AI Minutes date actor cannot be attributed without trusted speaker identity metadata"
            )
        actor_kind = BusinessActorKind.HUMAN if work_item.context.sender_user_id else BusinessActorKind.UNKNOWN
        actor_user_id = work_item.context.sender_user_id
        actor_name = work_item.context.sender
        agent_date = evidence.kind == "next_check_at"
        expected_actor = ("task-agent", "CEO Agent") if agent_date else (
            work_item.context.sender_user_id, work_item.context.sender
        )
        if evidence.actor_user_id and evidence.actor_user_id != expected_actor[0]:
            raise ValueError("date actor_user_id must match the trusted date actor")
        if evidence.actor_name and evidence.actor_name != expected_actor[1]:
            raise ValueError("date actor_name must match the trusted date actor")
        parsed_value = _parse_exact_date_value(evidence.value)
        parsed_phrase = _parse_exact_date_value(evidence.source_excerpt)
        if parsed_phrase is None:
            # Keep relative/unparseable wording in the linked source signal;
            # do not normalize it into a guessed typed date.
            continue
        if parsed_value is None or parsed_value != parsed_phrase:
            raise ValueError(
                "date value must match an exact, parseable date phrase in its source excerpt"
            )
        if evidence.kind == "next_check_at":
            actor_kind = BusinessActorKind.AGENT
            actor_user_id = "task-agent"
            actor_name = "CEO Agent"
        else:
            if not work_item.context.sender_user_id:
                raise ValueError("source date actor is not attributable to an identified source actor")
        values.append(TaskDateInput(
            date_type=BusinessTaskDateType(evidence.kind),
            value_at=evidence.value,
            raw_phrase=evidence.source_excerpt,
            actor_kind=actor_kind,
            actor_user_id=actor_user_id,
            actor_name=actor_name,
        ))
    if (
        (item.action == "create_task" or item.transition == "promote_candidate")
        and item.formal_basis is not None
        and item.formal_basis is FormalTaskBasis.EXPLICIT_ASSIGNMENT
        and work_item.source.created_at
        and work_item.context.sender_user_id
    ):
        values.append(TaskDateInput(
            date_type=BusinessTaskDateType.ASSIGNED_AT,
            value_at=work_item.source.created_at,
            raw_phrase=work_item.source.created_at,
            actor_kind=BusinessActorKind.HUMAN,
            actor_user_id=work_item.context.sender_user_id,
            actor_name=work_item.context.sender,
        ))
    return tuple(values)


def _parse_exact_date_value(value: str) -> str | None:
    """Return a precision-tagged date/time only when the whole phrase parses."""
    from datetime import date

    text = value.strip()
    try:
        return f"date:{date.fromisoformat(text).isoformat()}"
    except ValueError:
        try:
            return f"datetime:{datetime.fromisoformat(text.replace('Z', '+00:00')).isoformat()}"
        except ValueError:
            return None


def _validate_formal_basis_source(item: TaskDecision, work_item: WorkItem) -> None:
    basis = item.formal_basis
    if basis is FormalTaskBasis.EXPLICIT_ASSIGNMENT:
        if not work_item.context.assignment_authorized:
            raise ValueError("explicit assignment requires authorized source metadata")
        return
    if basis is FormalTaskBasis.EXPLICIT_COMMITMENT:
        owner_id = (
            work_item.context.owner_identity.get("user_id", "")
            if work_item.context.owner_identity.get("name", "") == item.owner_name
            else work_item.context.sender_user_id
            if work_item.context.sender == item.owner_name
            else ""
        )
        if (
            not owner_id
            or work_item.context.sender_user_id != owner_id
            or (item.owner_name and work_item.context.sender != item.owner_name)
        ):
            raise ValueError("explicit commitment must be authored by its identified owner")
        return
    if basis is FormalTaskBasis.MEETING_ACTION_ITEM:
        if not (
            work_item.source.type is WorkItemSourceType.AI_MINUTES
            and work_item.context.source_conversation_kind is WorkItemSourceKind.MINUTES
            and "#todos-sha256=" in work_item.source.ref
        ):
            raise ValueError("meeting action item requires a sourced meeting action-item record")
        return
    if basis is FormalTaskBasis.EXTERNAL_TODO:
        if not (
            work_item.source.type in {
                WorkItemSourceType.TODO_COMPLETION_CHECK,
                WorkItemSourceType.TODO_COMPLETION_EVIDENCE_CANDIDATE,
            }
            and work_item.context.external_task_id.strip()
        ):
            raise ValueError("external TODO basis requires trusted external TODO source metadata")


def _validate_task_agent_decision(
    decision: TaskAgentDecision, *, work_item: WorkItem, now: str = ""
) -> None:
    for item in decision.task_decisions:
        if item.action == "skip":
            continue
        if not item.title.strip():
            raise RepairableTaskDecisionValidationError("non-skip task decision requires title")
        if item.action == "create_task" and not (
            item.owner_user_id.strip() or item.owner_name.strip()
        ):
            raise RepairableTaskDecisionValidationError(
                "formal task creation requires explicit source-backed owner; retain as candidate"
            )
        if item.action == "create_task" or item.transition == "promote_candidate":
            _validate_formal_basis_source(item, work_item)
        if item.transition == "apply_acceptance" and item.acceptance_polarity != "accepted":
            raise RepairableTaskDecisionValidationError(
                "only explicit accepted owner evidence may use apply_acceptance"
            )
        if item.transition == "apply_acceptance" and item.action != "update_task":
            raise RepairableTaskDecisionValidationError("apply_acceptance requires update_task")
        if item.transition == "merge_identity" and item.identity_proposal is not None and (
            item.identity_proposal.identity_evidence.basis
            == "same_deliverable_owner_context_time"
        ):
            raise ValueError(
                "same deliverable, owner, context, and time can link or cluster Tasks but cannot merge identity"
            )


def apply_task_agent_decision(
    store: AutoReplyStore,
    *,
    summary_input_id: int,
    work_item: WorkItem,
    decision: TaskAgentDecision,
    codex_session_id: str = "",
    record_run: bool = True,
    dws=None,
    now: str = "",
    _db: sqlite3.Connection | None = None,
) -> TaskAgentApplyResult:
    """Persist all source-grounded task decisions, atomically when _db is supplied."""
    _validate_task_agent_decision(decision, work_item=work_item, now=now)
    service = TaskSemanticService(store)
    resolution = BusinessResolutionService(store)
    task_ids: list[int] = []
    affected_task_ids: list[int] = []
    attention: list[tuple[TaskDecision, int, int]] = []
    skipped_reasons: list[str] = []

    def apply(db: sqlite3.Connection | None) -> None:
        for item in decision.task_decisions:
            if item.action == "skip":
                continue
            if item.transition == "apply_acceptance":
                if not work_item.context.reply_to_source_ref:
                    skipped_reasons.append(
                        f"Acceptance for task {item.task_id} was not applied: source has no verified reply-to reference."
                    )
                    continue
                task = store.get_business_task(item.task_id) if db is None else store.get_business_task_in_transaction(task_id=item.task_id, _db=db)
                evidence = store.list_business_task_evidence(item.task_id) if db is None else store.list_business_task_evidence_in_transaction(task_id=item.task_id, _db=db)
                cited = next((row for row in evidence if row.signal_id == item.acceptance_target_signal_id), None)
                target_signal = (
                    store.get_business_task_signal(item.acceptance_target_signal_id)
                    if db is None
                    else store.get_business_task_signal_in_transaction(signal_id=item.acceptance_target_signal_id, _db=db)
                )
                linked_assignment = cited is not None and cited.evidence_role in {
                    BusinessEvidenceRole.ASSIGNMENT.value,
                    BusinessEvidenceRole.COMMITMENT.value,
                }
                same_reply_thread = (
                    target_signal is not None
                    and bool(work_item.source.conversation_id)
                    and target_signal.conversation_id == work_item.source.conversation_id
                )
                explicit_reply_link = (
                    target_signal is not None
                    and target_signal.source_ref == work_item.context.reply_to_source_ref
                )
                owner_is_reply_author = (
                    task is not None
                    and bool(work_item.context.sender_user_id)
                    and task.owner_user_id == work_item.context.sender_user_id
                    and (not task.owner_name or task.owner_name == work_item.context.sender)
                )
                if not (task is not None and linked_assignment and same_reply_thread
                        and explicit_reply_link and owner_is_reply_author):
                    skipped_reasons.append(
                        f"Acceptance for task {item.task_id} was not applied: cited assignment, exact reply link, conversation, or owner identity did not match."
                    )
                    continue
            signal = _task_source_signal(work_item, item)
            date_facts = _task_date_inputs(item, work_item, is_acceptance=item.transition == "apply_acceptance")
            owner_evidence = dict(item.owner_evidence)
            owner_identity = work_item.context.owner_identity
            source_owner_id = (
                owner_identity.get("user_id", "")
                if owner_identity.get("name", "") == item.owner_name
                else work_item.context.sender_user_id
                if work_item.context.sender == item.owner_name
                else ""
            )
            if item.owner_user_id and item.owner_user_id != source_owner_id:
                raise ValueError("owner_user_id is not established by source identity metadata")
            owner_user_id = source_owner_id
            if owner_evidence:
                owner_evidence.setdefault("source_ref", item.source_ref)
                # Never persist an owner excerpt that is not an exact substring of
                # the source: a model may paraphrase or change punctuation, and the
                # decision's own source_excerpt is validated as exact. But an exact
                # owner excerpt is kept: when one person hands the work to another
                # (“你写下来”), the line that names who takes it on is not the
                # decision's source_excerpt.
                model_excerpt = owner_evidence.get("excerpt")
                if not (isinstance(model_excerpt, str) and model_excerpt.strip() and model_excerpt in work_item.summary):
                    owner_evidence["excerpt"] = item.source_excerpt
                owner_evidence.setdefault("user_id", owner_user_id)
                owner_evidence.setdefault("name", item.owner_name)
            formality = FormalityEvidence(
                basis=item.formal_basis,
                assigner_is_authorized=(
                    item.formal_basis is not FormalTaskBasis.EXPLICIT_ASSIGNMENT
                    or work_item.context.assignment_authorized
                ),
                deliverable_is_explicit=bool(item.title.strip()),
                owner_is_explicit=bool(item.owner_user_id.strip() or item.owner_name.strip()),
            )
            task_before = (
                store.get_business_task_in_transaction(task_id=item.task_id, _db=db)
                if item.task_id is not None else None
            )
            if item.action == "update_task" and item.transition == "update_fields" and item.owner_name:
                # One item whose owner the source does not establish must not fail the
                # meeting's other items: leave that Task as it was and say why.
                try:
                    TaskSemanticService._require_source_backed_owner(
                        signal=signal, owner_user_id=owner_user_id, owner_name=item.owner_name,
                        owner_evidence_json=json.dumps(owner_evidence, ensure_ascii=False),
                    )
                except ValueError as exc:
                    skipped_reasons.append(f"Task {item.task_id} owner was not applied: {exc}.")
                    continue
            if (
                item.action == "update_task" and item.transition == "update_fields"
                and task_before is not None and not date_facts
                and _update_fields_restates_task(task_before, item, owner_user_id)
            ):
                # The source says nothing the Task does not already say; there is
                # nothing to apply, and one such item must not fail the whole batch.
                skipped_reasons.append(f"Task {item.task_id} already matches this source; nothing to update.")
                continue
            if item.action == "record_candidate":
                result = service.record_candidate(RecordCandidate(
                    title=item.title,
                    signal=signal,
                    description=item.description,
                    owner_user_id=owner_user_id,
                    owner_name=item.owner_name,
                    missing_evidence_json=json.dumps(item.missing_evidence, ensure_ascii=False),
                    date_facts=date_facts,
                ), _db=db)
            elif item.action == "create_task":
                result = service.record_formal_task(RecordFormalTask(
                    title=item.title,
                    signal=signal,
                    formality=formality,
                    description=item.description,
                    owner_user_id=owner_user_id,
                    owner_name=item.owner_name,
                    owner_evidence_json=json.dumps(owner_evidence, ensure_ascii=False),
                    date_facts=date_facts,
                ), _db=db)
            else:
                assert item.task_id is not None
                if item.transition == "promote_candidate":
                    result = service.promote_candidate(PromoteCandidate(
                        task_id=item.task_id, signal=signal, formality=formality,
                        owner_user_id=owner_user_id or None,
                        owner_name=item.owner_name or None,
                        owner_evidence_json=json.dumps(owner_evidence, ensure_ascii=False) if owner_evidence else None,
                        date_facts=date_facts,
                    ), _db=db)
                elif item.transition == "apply_acceptance":
                    result = service.apply_acceptance(ApplyAcceptance(
                        task_id=item.task_id, signal=signal, acceptance_is_explicit=True,
                        acceptance_polarity=AcceptancePolarity(item.acceptance_polarity),
                        acceptance_excerpt=item.source_excerpt,
                        referenced_signal_id=item.acceptance_target_signal_id,
                        date_facts=date_facts,
                    ), _db=db)
                elif item.transition == "merge_identity":
                    proposal = item.identity_proposal
                    assert proposal is not None
                    _validate_identity_proposal(store, proposal, db=db)
                    identity = IdentityEvidence(
                        same_external_task_id=proposal.identity_evidence.basis == "same_external_task_id",
                        explicit_source_reference=proposal.identity_evidence.basis == "explicit_source_reference",
                        same_deliverable=proposal.identity_evidence.basis == "same_deliverable_owner_context_time",
                        same_owner=proposal.identity_evidence.basis == "same_deliverable_owner_context_time",
                        same_context=proposal.identity_evidence.basis == "same_deliverable_owner_context_time",
                        compatible_time_window=proposal.identity_evidence.basis == "same_deliverable_owner_context_time",
                    )
                    result = service.merge_same_deliverable(MergeBusinessTasks(
                        source_task_id=proposal.source_task_id,
                        target_task_id=proposal.target_task_id,
                        signal=signal, identity_evidence=identity, reason=proposal.reason,
                    ), _db=db)
                else:
                    fields = {}
                    if item.title:
                        fields["title"] = item.title
                    if item.description:
                        fields["description"] = item.description
                    if owner_user_id or item.owner_name:
                        fields.update(owner_user_id=owner_user_id, owner_name=item.owner_name,
                                      owner_evidence_json=json.dumps(owner_evidence, ensure_ascii=False))
                    if item.status:
                        fields["status"] = BusinessTaskStatus(item.status)
                    if item.business_relevance:
                        fields["business_relevance"] = BusinessRelevance(item.business_relevance)
                    result = service.update_task(UpdateBusinessTask(
                        task_id=item.task_id, signal=signal, date_facts=date_facts,
                        reason=item.update_summary or "根据来源证据更新了任务字段。",
                        **fields,
                    ), _db=db)
            if (
                not result.created
                and date_facts
                and (item.action in {"record_candidate", "create_task"}
                     or item.transition == "promote_candidate")
            ):
                existing_dates = db.execute(
                    """select date_type, value_at, raw_phrase, source_signal_id,
                              actor_kind, actor_user_id, actor_name
                       from business_task_date_evidence where task_id=?""",
                    (result.task_id,),
                ).fetchall()
                existing_date_effects = {
                    (row["date_type"], row["value_at"], row["raw_phrase"],
                     row["source_signal_id"], row["actor_kind"], row["actor_user_id"], row["actor_name"])
                    for row in existing_dates
                }
                proposed_date_effects = {
                    (fact.date_type.value, fact.value_at, fact.raw_phrase, result.signal_id,
                     fact.actor_kind.value, fact.actor_user_id, fact.actor_name)
                    for fact in date_facts
                }
                if not proposed_date_effects.issubset(existing_date_effects):
                    raise ValueError(
                        "replayed create/promotion has new date evidence; update the existing Task explicitly"
                    )
            task_id = result.task_id
            task_ids.append(task_id)
            affected_task_ids.append(task_id)
            task_after = store.get_business_task_in_transaction(task_id=task_id, _db=db)
            if task_before is not None:
                store.cancel_pending_business_task_follow_ups(
                    business_task_id=task_id,
                    keep_source_signal_id=result.signal_id,
                    reason=f"Task 已被新信息更新（{item.source_ref}），原催办不再适用",
                    _db=db,
                )
            if (
                task_before is not None
                and task_before.status.value != "done"
                and task_after is not None
                and task_after.status.value == "done"
            ):
                completion_evidence = {
                    "source": item.source_ref,
                    "source_signal_id": result.signal_id,
                    "source_excerpt": item.source_excerpt,
                    "reason": item.update_summary or "Source confirms Task completion.",
                }
                db.execute(
                    "update business_task_follow_ups set status='completed', "
                    "evidence_check_json=?, suppressed_reason=?, updated_at=current_timestamp "
                    "where business_task_id=? and status in ('draft','approved','sent')",
                    (json.dumps(completion_evidence, ensure_ascii=False),
                     completion_evidence["reason"], task_id),
                )
                linked_todo = db.execute(
                    "select 1 from business_task_dingtalk_links where business_task_id=? "
                    "and status in ('creating','active') limit 1", (task_id,),
                ).fetchone()
                if linked_todo is not None:
                    store.enqueue_business_task_todo_sync_outbox(
                        operation_key=f"task-agent:{summary_input_id}:business-task:{task_id}:complete",
                        business_task_id=task_id, operation="complete",
                        evidence_json=json.dumps(completion_evidence, ensure_ascii=False),
                        _db=db,
                    )
            next_checks = (
                fact for fact in date_facts
                if fact.date_type is BusinessTaskDateType.NEXT_CHECK_AT
            )
            if (
                task_after is not None
                and task_after.stage.value == "formal"
                and task_after.status.value in {"open", "waiting"}
                and task_after.owner_user_id.strip()
                and work_item.source.conversation_id.strip()
                and work_item.context.source_conversation_kind.value in {"group", "direct"}
            ):
                for check in next_checks:
                    store.create_business_task_follow_up(
                        business_task_id=task_id,
                        source_signal_id=result.signal_id,
                        target_conversation_id=work_item.source.conversation_id,
                        target_kind=work_item.context.source_conversation_kind.value,
                        question_text=f"请确认「{task_after.title}」的当前进展与下一步。",
                        scheduled_at=check.value_at,
                        owner_user_id=task_after.owner_user_id,
                        owner_name=task_after.owner_name,
                        dedupe_key=f"business-task:{task_id}:signal:{result.signal_id}:next-check:{check.value_at}",
                        _db=db,
                    )
            if (
                task_after is not None
                and task_after.stage.value == "formal"
                and task_after.status.value in {"open", "waiting"}
                and task_after.commitment_status.value == "accepted"
                and task_after.owner_user_id.strip()
                and db.execute(
                    "select 1 from business_task_date_evidence where task_id=? "
                    "and date_type='committed_deadline_at' and trim(value_at)<>'' limit 1",
                    (task_id,),
                ).fetchone() is not None
            ):
                store.enqueue_business_task_todo_sync_outbox(
                    operation_key=f"task-agent:{summary_input_id}:business-task:{task_id}:create",
                    business_task_id=task_id, operation="create", _db=db,
                )
            if item.transition == "merge_identity" and item.identity_proposal is not None:
                affected_task_ids.extend((
                    item.identity_proposal.source_task_id,
                    item.identity_proposal.target_task_id,
                ))
            for relation in item.relation_proposals:
                if task_id not in {relation.from_task_id, relation.to_task_id}:
                    raise ValueError("relation proposal must include the Task evidenced by this decision")
                resolution.add_relation(
                    from_task_id=relation.from_task_id,
                    to_task_id=relation.to_task_id,
                    relation_type=BusinessRelationType(relation.relation_type),
                    evidence_signal_id=result.signal_id,
                    status="proposed", reason=relation.reason, _db=db,
                )
            if item.cluster_proposal is not None:
                cluster = item.cluster_proposal
                if cluster.cluster_id is None:
                    cluster_id = resolution.create_cluster(
                        title=cluster.title or item.title,
                        task_ids=list(dict.fromkeys([*cluster.task_ids, task_id])), _db=db,
                    )
                else:
                    cluster_id = cluster.cluster_id
                    if db.execute(
                        "select 1 from business_work_cluster_tasks where cluster_id=? and task_id=?",
                        (cluster_id, task_id),
                    ).fetchone() is None:
                        store.add_business_work_cluster_task_in_transaction(
                            cluster_id=cluster_id, task_id=task_id, _db=db
                        )
            else:
                cluster_id = None
            for anchor_match in item.anchor_match_proposals:
                resolution.propose_anchor_match(
                    task_id=task_id, anchor_id=anchor_match.anchor_id,
                    evidence_signal_id=result.signal_id, reason=anchor_match.reason, _db=db,
                )
            if item.project_candidate_proposal is not None:
                proposal = item.project_candidate_proposal
                resolution.propose_project(
                    cluster_id=proposal.cluster_id, title=proposal.title,
                    reason=proposal.reason, _db=db,
                )
            if item.attention_proposal is not None:
                attention.append((item, task_id, result.signal_id))

    if _db is not None:
        apply(_db)
    else:
        with store.task_agent_domain_apply_transaction() as db:
            apply(db)
        if record_run:
            store.record_task_agent_run(
                summary_input_id=summary_input_id,
                codex_session_id=codex_session_id,
                decision_json=_json_dumps(decision.model_dump(mode="json")),
                audit_summary="; ".join(filter(None, (item.update_summary for item in decision.task_decisions))),
                memory_recall_used=any(item.memory_recall_used for item in decision.task_decisions),
            )
    result = TaskAgentApplyResult(
        task_ids=tuple(task_ids),
        attention_proposals=tuple(attention),
        affected_task_ids=tuple(dict.fromkeys(affected_task_ids)),
        skipped_reasons=tuple(skipped_reasons),
    )
    if _db is None:
        _project_task_attention(store, result.attention_proposals, result.affected_task_ids)
    return result


def _update_fields_restates_task(task: BusinessTask, item: TaskDecision, owner_user_id: str) -> bool:
    """True when every field the decision sets already has that value on the Task."""
    return (
        (not item.title or item.title == task.title)
        and (not item.description or item.description == task.description)
        and (not item.status or BusinessTaskStatus(item.status) is task.status)
        # "unknown" is the absence of a judgement, so restating it asserts nothing;
        # restating "relevant" is a confirmation the Task's evidence should record.
        and (not item.business_relevance
             or (BusinessRelevance(item.business_relevance) is BusinessRelevance.UNKNOWN
                 and task.business_relevance is BusinessRelevance.UNKNOWN))
        and (not (owner_user_id or item.owner_name)
             or (owner_user_id == task.owner_user_id and item.owner_name == task.owner_name))
    )


def _project_task_attention(
    store: AutoReplyStore,
    proposals: tuple[tuple[TaskDecision, int, int], ...],
    affected_task_ids: tuple[int, ...],
) -> None:
    for item, task_id, signal_id in proposals:
        proposal = item.attention_proposal
        assert proposal is not None
        try:
            exact_trigger_quote = (
                bool(proposal.trigger_evidence.strip())
                and proposal.trigger_evidence in item.source_excerpt
            )
            if not exact_trigger_quote:
                LOGGER.info("Suppressing Task attention without an exact source trigger quote task_id=%s", task_id)
                continue
            BusinessAttentionProjection(store).upsert(AttentionProposal(
                stable_key=f"task:{task_id}:{proposal.material_trigger}",
                category=proposal.category,
                title=proposal.title,
                business_area="",
                why_attention=(
                    f"{proposal.why_attention} Source trigger ({proposal.material_trigger}): "
                    f"{proposal.trigger_evidence}"
                ),
                current_state=proposal.current_state,
                ceo_action=proposal.ceo_action,
                anchor_id=proposal.anchor_id,
                task_ids=(task_id,),
                evidence_signal_id=signal_id,
            ))
        except Exception:
            LOGGER.exception(
                "Task committed but CEO attention projection failed for task_id=%s",
                task_id,
            )
    try:
        BusinessAttentionProjection(store).recompute_for_tasks(affected_task_ids)
    except Exception:
        LOGGER.exception(
            "Task committed but CEO attention membership recomputation failed for task_ids=%s",
            affected_task_ids,
        )


def _validate_identity_proposal(store, proposal, *, db: sqlite3.Connection | None) -> None:
    def get_task(task_id: int):
        if db is None:
            return store.get_business_task(task_id)
        return store.get_business_task_in_transaction(task_id=task_id, _db=db)

    def get_evidence(task_id: int):
        if db is None:
            return store.list_business_task_evidence(task_id)
        return store.list_business_task_evidence_in_transaction(task_id=task_id, _db=db)

    def get_signal(signal_id: int):
        if db is None:
            return store.get_business_task_signal(signal_id)
        return store.get_business_task_signal_in_transaction(signal_id=signal_id, _db=db)

    proof = proposal.identity_evidence
    source_task = get_task(proposal.source_task_id)
    target_task = get_task(proposal.target_task_id)
    if source_task is None or target_task is None:
        raise ValueError("identity proposal Tasks must exist")
    if proof.source_signal_id not in {row.signal_id for row in get_evidence(source_task.id)}:
        raise ValueError("identity source signal is not linked to the source Task")
    if proof.target_signal_id not in {row.signal_id for row in get_evidence(target_task.id)}:
        raise ValueError("identity target signal is not linked to the target Task")
    source_signal = get_signal(proof.source_signal_id)
    target_signal = get_signal(proof.target_signal_id)
    if source_signal is None or target_signal is None:
        raise ValueError("identity proposal cites a missing source signal")
    source_context = json.loads(source_signal.context_json)
    target_context = json.loads(target_signal.context_json)
    if proof.basis == "same_external_task_id":
        source_external = source_context.get("external_task_id")
        target_external = target_context.get("external_task_id")
        if not source_external or source_external != target_external:
            raise ValueError("same_external_task_id identity evidence does not match source records")
        return
    if proof.basis == "explicit_source_reference":
        if not (
            source_signal.source_ref == target_context.get("reply_to_source_ref")
            or target_signal.source_ref == source_context.get("reply_to_source_ref")
        ):
            raise ValueError("explicit_source_reference identity evidence does not match source records")
        return
    same_owner = (
        bool(source_task.owner_name.strip())
        and source_task.owner_name.casefold() == target_task.owner_name.casefold()
    )
    same_context = (
        bool(source_signal.conversation_id)
        and source_signal.conversation_id == target_signal.conversation_id
    )
    same_deliverable = source_task.title.strip().casefold() == target_task.title.strip().casefold()
    if not (same_deliverable and same_owner and same_context):
        raise ValueError("same-deliverable identity evidence does not match canonical Task and source fields")
    try:
        source_time = datetime.fromisoformat(source_signal.source_time.replace("Z", "+00:00"))
        target_time = datetime.fromisoformat(target_signal.source_time.replace("Z", "+00:00"))
    except (TypeError, ValueError) as exc:
        raise ValueError("same-deliverable identity evidence requires parseable source times") from exc
    source_time = source_time.replace(tzinfo=timezone.utc) if source_time.tzinfo is None else source_time
    target_time = target_time.replace(tzinfo=timezone.utc) if target_time.tzinfo is None else target_time
    if abs((source_time - target_time).total_seconds()) > 30 * 24 * 60 * 60:
        raise ValueError("same-deliverable identity source times exceed the matching window")


def process_work_item(
    store: AutoReplyStore,
    runner: TaskAgentRunner,
    work_input: WorkSummaryInput,
    *,
    dws=None,
    now: str = "",
    session_lease=None,
) -> None:
    active_run_id: int | None = None
    try:
        if session_lease is not None:
            session_lease.assert_owned()
        work_item = WorkItem.model_validate_json(work_input.payload_json)
        semantic_context = retrieve_task_semantic_context(store, work_item)
        context_prompt = render_task_semantic_context(semantic_context)
        active_run_id = store.begin_task_agent_run(work_input.id)
        decision = runner.decide(
            work_item, context_prompt,
            memory_issue=memory_connector_config_issue(),
            run_id=active_run_id,
            session_scope_id=TASK_AGENT_SESSION_SCOPE_ID,
        )
        _validate_task_agent_decision(decision, work_item=work_item, now=now)
        session_id = getattr(runner.codex, "last_session_id", None) or ""
        if session_lease is not None:
            session_lease.assert_owned()
        with store.task_agent_domain_apply_transaction() as db:
            apply_result = apply_task_agent_decision(
                store,
                summary_input_id=work_input.id,
                work_item=work_item,
                decision=decision,
                codex_session_id=session_id,
                record_run=False,
                now=now,
                _db=db,
            )
            if apply_result.task_ids:
                store.mark_work_summary_input_done(work_input.id, _db=db)
            else:
                store.mark_work_summary_input_skipped(
                    work_input.id,
                    "; ".join(apply_result.skipped_reasons)
                    or "No source-grounded Task decision.",
                    _db=db,
                )
            store.finish_task_agent_run(
                active_run_id,
                status="completed",
                codex_session_id=session_id,
                decision_json=_json_dumps(decision.model_dump(mode="json")),
                audit_summary="; ".join(filter(None, (
                    *(item.update_summary for item in decision.task_decisions),
                    *apply_result.skipped_reasons,
                ))),
                memory_recall_used=any(item.memory_recall_used for item in decision.task_decisions),
                _db=db,
            )
        active_run_id = None
        _project_task_attention(
            store, apply_result.attention_proposals, apply_result.affected_task_ids
        )
    except Exception as exc:
        if active_run_id is not None:
            store.finish_task_agent_run(active_run_id, status="failed", error=str(exc))
        store.mark_work_summary_input_failed(work_input.id, str(exc))
        raise
