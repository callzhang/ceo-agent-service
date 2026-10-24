from app.business_definitions import BUSINESS_PROJECTIONS
from app.business_candidate_definitions import candidate_projection
from app.action_business_definitions import business_action_projection
import hashlib
import json
import logging
import re
import sqlite3
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
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
from app.leak_check import contains_credential
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
    TaskAttentionProjectionOutcome,
    TaskAttentionProjectionReceipt,
    TaskAttentionAssessmentResult,
    TaskAttentionVerifiedCitation,
    TaskProjectAssessment,
    TaskProjectDecisionResult,
    TaskDecisionResult,
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
    AttentionCategory,
    SourceCitation,
    ProjectContext,
    ProjectCrmCustomerCandidate,
    TaskSuggestion,
)
from app.task_semantic_service import (
    AcceptancePolarity,
    ApplyAcceptance,
    MergeBusinessTasks,
    PromoteCandidate,
    RecordCandidate,
    RecordFormalTask,
    RecordTaskSuggestion,
    SourceSignal,
    TaskDateInput,
    TaskSemanticService,
    UpdateBusinessTask,
)
from app.task_semantic_rules import FormalityEvidence, IdentityEvidence
from app.task_business_resolution import BusinessResolutionService
from app.task_attention_projection import AttentionProposal, BusinessAttentionProjection
from app.service_codex_config import service_mcp_tool_approval_modes_for_agent_role
from app.task_source_documents import (
    source_contains_quote,
    source_document_key,
    source_is_observed,
)
from app.business_source_reader import PreparedSources, RequestSourceReader
from app.project_context_service import ProjectContextService
from app.fxiaoke_customer_lookup import CrmCustomerLookup, lookup_account_customers

TASK_AGENT_DECISION_SCHEMA_PATH = (
    Path(__file__).resolve().parent / "schemas" / "task_agent_decision.schema.json"
)

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
        # The Agent reads historical source text itself with dws, so the route
        # must run local processes.
        "local_process_execution",
    }
)
# The Agent reads historical sources itself, so it needs these dws Skills in the
# harness's own Skill catalog.
TASK_SOURCE_READING_SKILLS = ("dingtalk-minutes", "dingtalk-doc")
TASK_RESULT_CODEC = RoutedResultCodec.text(
    schema_id="task_agent.decision.v2",
    allow_evidence_source_refs=True,
)


@dataclass(frozen=True)
class AppliedTaskAttention:
    assessment_index: int
    assessment: TaskProjectAssessment
    task_ids: tuple[int, ...]
    signal_id: int
    anchor_id: int


@dataclass(frozen=True)
class AppliedTaskDecision:
    decision_index: int
    task_id: int
    signal_id: int
    anchor_id: int | None


@dataclass(frozen=True)
class AppliedProjectDecision:
    project_decision_index: int
    project_id: int
    anchor_id: int
    revision_id: int | None
    signal_ids: tuple[int, ...]


@dataclass(frozen=True)
class TaskAgentApplyResult:
    task_ids: tuple[int, ...]
    attention_proposals: tuple[AppliedTaskAttention, ...]
    affected_task_ids: tuple[int, ...] = ()
    skipped_reasons: tuple[str, ...] = ()
    projection_receipt: TaskAttentionProjectionReceipt | None = None
    project_links: tuple[tuple[int, int], ...] = ()
    applied_decisions: tuple[AppliedTaskDecision, ...] = ()
    applied_projects: tuple[AppliedProjectDecision, ...] = ()
    current_signal_id: int | None = None

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


def _canonicalize_current_source_provenance(
    decision: TaskAgentDecision, *, work_item: WorkItem
) -> TaskAgentDecision:
    """Bind current-source evidence to the immutable Work Item identity.

    The model may describe the current source, but it does not own its identity.
    Session and memory evidence keeps its original reference and is therefore
    deliberately excluded from this normalization.
    """
    source_ref = work_item.source.ref
    ai_minutes_owner_relations = frozenset(
        {"explicit_assignment", "self_commitment", "meeting_summary_action_item"}
    )
    decisions: list[TaskDecision] = []
    for item in decision.task_decisions:
        if item.evidence_origin != "current" or item.action == "skip":
            decisions.append(item)
            continue
        owner_evidence = dict(item.owner_evidence)
        if owner_evidence:
            owner_evidence["source_ref"] = source_ref
        date_evidence = [
            fact.model_copy(update={"source_ref": source_ref})
            for fact in item.date_evidence
        ]
        formal_basis = item.formal_basis
        if (
            formal_basis is FormalTaskBasis.EXTERNAL_TODO
            and work_item.source.type is WorkItemSourceType.AI_MINUTES
            and "#todos-sha256=" in source_ref
        ):
            formal_basis = FormalTaskBasis.MEETING_ACTION_ITEM
        if (
            formal_basis
            in {
                FormalTaskBasis.EXPLICIT_ASSIGNMENT,
                FormalTaskBasis.EXPLICIT_COMMITMENT,
            }
            and work_item.source.type is WorkItemSourceType.AI_MINUTES
            and "#todos-sha256=" in source_ref
            and item.owner_kind == "individual"
            and item.owner_relation in ai_minutes_owner_relations
        ):
            # AI Minutes is a meeting action source, not an owner-authored
            # reply channel. Preserve the named owner as assigned_unaccepted.
            formal_basis = FormalTaskBasis.MEETING_ACTION_ITEM
        action = item.action
        if (
            action == "record_candidate"
            and work_item.source.type is WorkItemSourceType.AI_MINUTES
            and work_item.context.source_conversation_kind is WorkItemSourceKind.MINUTES
            and "#todos-sha256=" in source_ref
            and item.owner_kind == "individual"
            and item.owner_relation in ai_minutes_owner_relations
            and item.owner_name.strip()
            and str(owner_evidence.get("excerpt") or "").strip()
        ):
            action = "create_task"
            formal_basis = FormalTaskBasis.MEETING_ACTION_ITEM
        decisions.append(
            item.model_copy(
                update={
                    "action": action,
                    "source_ref": source_ref,
                    "owner_evidence": owner_evidence,
                    "date_evidence": date_evidence,
                    "formal_basis": formal_basis,
                }
            )
        )
    return decision.model_copy(update={"task_decisions": decisions})


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
        continuation_prompt: str | None = None,
        continuation_session_id: str | None = None,
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
        repair_round: int = 0,
        repair_context: str | None = None,
    ) -> TaskAgentDecision:
        continuation = {}
        prior_session = getattr(self.codex, "last_session_id", None)
        if repair_context and prior_session:
            continuation = {
                "continuation_prompt": build_task_agent_repair_prompt(repair_context),
                "continuation_session_id": prior_session,
            }
        effective_time = datetime.now(timezone.utc).isoformat()
        context = candidate_prompt + ("\n\n" + repair_context if repair_context else "")
        return self.codex.decide(
            prompt=build_task_agent_prompt(
                work_item, context, memory_issue=memory_issue,
                current_time=effective_time,
            ),
            workload_key=(
                str(run_id) if repair_round == 0
                else f"{run_id}:decision_repair.{repair_round}"
            ),
            session_scope_id=session_scope_id,
            **continuation,
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
        self.last_runtime_attempt_id: int | None = None
        self.last_audit_tool_events: list[dict[str, str]] = []
        self.last_transcript_start_line = 0
        self.last_transcript_end_line = 0

    def decide(
        self,
        *,
        prompt: str,
        workload_key: str,
        session_scope_id: str | None = None,
        continuation_prompt: str | None = None,
        continuation_session_id: str | None = None,
    ) -> TaskAgentDecision:
        self.last_session_id = None
        self.last_runtime_attempt_id = None
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
                    skill_names=(
                        WORK_TRACKING_SKILL_PATH.parent.name,
                        *TASK_SOURCE_READING_SKILLS,
                    ),
                    inlined_skill_names=(WORK_TRACKING_SKILL_PATH.parent.name,),
                    tool_approval_modes_by_server=(
                        service_mcp_tool_approval_modes_for_agent_role("task")
                    ),
                    output_schema_path=TASK_AGENT_DECISION_SCHEMA_PATH,
                    use_output_schema=True,
                ),
                parser=_encode_task_agent_result,
                result_codec=TASK_RESULT_CODEC,
                conversation_id=session_scope_id,
                continuation_prompt=continuation_prompt,
                continuation_session_id=continuation_session_id,
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
        self.last_runtime_attempt_id = result.attempt_id
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
    result = {
        "decision": decision.model_dump(mode="json", exclude_unset=True),
        "audit_tool_events": audit_references_from_full_events(
            extract_codex_audit_events(raw, limit=TASK_AGENT_AUDIT_EVENT_LIMIT),
            limit=TASK_AGENT_AUDIT_EVENT_LIMIT,
        ),
    }
    encoded = json.dumps(
        result,
        ensure_ascii=False,
        separators=(",", ":"),
    )
    try:
        TASK_RESULT_CODEC.encode(encoded)
    except ValueError as exc:
        credential_field = _first_credential_field_path(result)
        raise RoutedResultValidationError(
            (
                f"task result contains credential-like text in {credential_field}"
                if credential_field
                else "task result contains a runtime path outside an evidence source field"
            ),
            raw_output=raw,
        ) from exc
    return encoded


def _first_credential_field_path(value: object, path: tuple[str, ...] = ()) -> str:
    """Name a sensitive leaf for correction without copying its value."""
    if isinstance(value, dict):
        for key, item in value.items():
            key_text = str(key)
            safe_key = (
                key_text
                if re.fullmatch(r"[A-Za-z_][A-Za-z_0-9]{0,63}", key_text)
                and not contains_credential(key_text)
                else "<field>"
            )
            if contains_credential(key_text):
                return ".".join((*path, safe_key))
            found = _first_credential_field_path(item, (*path, safe_key))
            if found:
                return found
    elif isinstance(value, list):
        for index, item in enumerate(value):
            found = _first_credential_field_path(item, (*path, str(index)))
            if found:
                return found
    elif isinstance(value, str) and contains_credential(value):
        return ".".join(path)
    return ""


def _task_result_validation_repair_prompt(raw_output: str) -> str:
    """Return only the failed result's bounded validation errors for correction."""
    decision, problems = _validate_task_decision_candidates(raw_output)
    if decision is not None:
        # The schema held; give the codec's relevant category and field name,
        # never the rejected value.
        credential_field = _first_credential_field_path(
            decision.model_dump(mode="json", exclude_unset=True)
        )
        if credential_field:
            detail = f"- credential-like text in {credential_field}"
            path_rule = "\nRemove the credential-like text from that field."
        else:
            detail = (
                "- the decision satisfied the schema but a business field "
                "contained a runtime path"
            )
            path_rule = (
                "\nDo not include local filesystem paths, session paths, lock paths, "
                "credentials, or runtime diagnostics in business fields. A source "
                "path may appear only in an evidence field named source or source_ref."
            )
    elif problems:
        detail = "\n".join(f"- {problem}" for problem in problems)
        path_rule = ""
    else:
        detail = (
            "- the reply did not contain a TaskAgentDecision JSON object; "
            "return only the JSON object without prose or code fences"
        )
        path_rule = ""
    return (
        "The previous output was not accepted as a TaskAgentDecision. Continue "
        "in this same Agent session and return exactly one complete "
        "TaskAgentDecision JSON object. Repair the problems below using the "
        "existing Work Item, Skill, and output schema. Preserve all valid "
        "decisions, fields, Project assessments, owner assignments, and exact "
        "source citations. Do not invent facts or remove a valid assessment "
        "merely to satisfy a relationship check. Change only fields needed "
        "to resolve the listed errors; leave other fields and empty optional "
        "lists unchanged.\n\n"
        f"Problems in the previous output:\n{detail}{path_rule}"
    )


def build_task_agent_prompt(
    work_item: WorkItem,
    candidate_prompt: str,
    *,
    memory_issue: str = "",
    current_time: str = "",
    inline_skill: bool = False,
) -> str:
    scheduled_consumer = ServiceCommandConsumerContext.from_payload(
        work_item.scheduled_consumer or None
    )
    if inline_skill:
        current_skill_text = load_skill_text([WORK_TRACKING_SKILL_PATH])
    else:
        current_skill_path = WORK_TRACKING_SKILL_PATH.resolve(strict=True)
        current_skill_digest = hashlib.sha256(current_skill_path.read_bytes()).hexdigest()
        current_skill_text = (
            "CEO Work Tracking Skill (complete current protocol): "
            f"{current_skill_path}\nSHA-256: {current_skill_digest}. "
            "Read it before deciding. On a resumed turn, reuse a prior read only if "
            "this exact version and its full text remain in the current context; "
            "otherwise reread the file. Do not use an earlier version."
        )
    scheduled_prompt = (
        f"## Scheduled Consumer Prompt\n{scheduled_consumer.prompt}\n"
        if scheduled_consumer is not None
        else ""
    )
    work_item_payload = work_item.model_dump(mode="json")
    scheduled_payload = work_item_payload.get("scheduled_consumer")
    if isinstance(scheduled_payload, dict):
        scheduled_payload.pop("skill_protocol", None)
        scheduled_payload.pop("skill_materials", None)
    work_item_json = json.dumps(work_item_payload, ensure_ascii=False, separators=(",", ":"))
    transcript_limitation = (
        "The meeting transcript was unavailable for this source. Use only the "
        "explicit Todo and meeting-summary evidence that is present. Do not "
        "infer an individual owner, commitment, or assignment from missing "
        "transcript context; leave unsupported ownership unresolved. If you need "
        "the spoken content to understand the meeting, you may transcribe the "
        "recording yourself with the ai_services transcribe_audio tool (fetch the "
        "recording with dws). Your own transcription is not part of the bound "
        "source, so cite only quotes that appear in the source itself."
        if work_item.context.source_transcript_unavailable
        else ""
    )
    effective_current_time = (
        current_time.strip() or datetime.now(timezone.utc).isoformat()
    )
    return f"""You are the CEO Agent Task/Project reader. Do not reply to the source.
Follow the current CEO Work Tracking Skill and return one TaskAgentDecision envelope:
project_decisions, task_decisions, project_assessments are all required (each 0..N).
Project can have zero Tasks. Project context/evidence and Attention never need a
synthetic Task or an unrelated update_fields carrier.

{scheduled_prompt}
{current_skill_text}

The current independent Project/Task/assessment envelope controls output.
A scheduled prompt supplies specialized business scope only; the freshly loaded
current Skill controls the work protocol. Historical Skill snapshots are not
instructions for this turn. Never upgrade old result fields or manufacture selectors.

Memory-write boundary (prompt guidance): write durable, material Project-level
risks and progress/status updates supported by current evidence using
memory_connector.memory_write. Keep each update concise, identify the Project,
cite the original source, and use the original source time as created_at with
type="text". Include Project entity and source provenance metadata when available.
Distinguish reported facts from evidence-based risk inference. Do not write
individual Task/TODO records, routine activity, temporary discussion, unsupported
guesses, secrets, raw transcripts, or duplicate updates. Without a durable
Project-level change, do not write Memory. This prompt does not impose a general read-only mode.
Return structured local changes; the service applies supported operations.
This prompt does not technically disable tools or enforce permissions.

Current execution time: {effective_current_time}
Memory connector status: {_memory_connector_prompt_status(memory_issue)}
The shared logical session preserves context; runtime routes retain separate native
sessions and the native CLI manages compaction. Current source authority takes precedence.

Current Work Item JSON:
{work_item_json}
{transcript_limitation}
The Work Item's summary above is its source text. Historical sources are in
source_documents, one per Signal in source_signals. A document with
source_in_input false is not copied here: read it yourself with dws from that
Signal's source_type and source_ref (the ref names the minutes id or the
document), using the matching dws Skill. A document with visible_ranges or
decoded_excerpts carries its text here; if it has duplicate_of_document_id, use
that document's ranges, its source identity stays separate. Offsets are character
ranges in that exact version. A quote you cite must be exact text of the version
the Signal is bound to; the service checks it when it applies your decision.
Current semantic Project/Task context (rank is context, never authority):
{candidate_prompt}

Additional Task result constraints:
- For project_assessments, use exactly one selector: anchor_id for a known registered
  Project when this output has no matching Project decision, project_decision_index
  for the matching Project decision in this output, or neither only for an unresolved
  Project clue with insufficient_evidence. Never provide both anchor_id and
  project_decision_index.
- When a needs_attention Project has a material unresolved impact and a directly
  relevant saved Project responsibility, create one display-only candidate next-step
  Task for that role even when the current message states no assignment or explicit
  action. Infer a concrete action from the risk. An existing Task suppresses this
  suggestion only if it addresses the same unresolved risk; a completed or unrelated
  Project deliverable does not. Prefer one suggestion for one material Project risk:
  do not create separate candidates for substeps or for each role involved. When
  one next step is a stated prerequisite or upstream gate for another, treat them
  as sequentially dependent next steps and create one candidate for the nearest
  blocking outcome; include the downstream impact in its description instead of
  creating a second candidate for the dependent confirmation. Create multiple
  suggestions only when current evidence identifies separately completable,
  non-dependent actions with distinct outcomes. For example, acceptance-material
  confirmation and payment-date confirmation in one delivery/collection chain are
  not two candidates just because different Project responsibilities match them;
  choose the nearest blocking outcome and mention the payment impact in its
  description. A Task title must include the decisive unresolved Project outcome
  as well as any upstream gate needed to reach it. For a payment-risk suggestion,
  name the payment date/schedule in the title even when confirming an upstream
  acceptance condition is the first step; do not hide the key outcome behind a
  broad title such as “update the plan”.
- Similar deliverables are linked/clustered, not identity-merged.
  status and business_relevance may only change through update_fields: set
  transition=update_fields when either field changes. They remain top-level fields,
  not a nested update_fields object; never set them under another transition.
  New, record_candidate and skip decisions must leave status and business_relevance
  unset. For an existing Task, set transition=update_fields to change either field;
  do not try to set a status or relevance during creation.
  When promoting a suggestion, omit the suggestion field; the saved Task keeps its
  existing suggestion history. A suggestion field is valid only when recording or
  updating a candidate.
- Actual owner_evidence uses {{"source_ref": "the source reference", "excerpt": "the literal owner-action sentence"}}.
- Attention belongs once to its assessment. Task decision_indexes and existing task_ids
  are optional real members, not carriers. Never include a skip decision in
  decision_indexes; supporting indexes may identify only a real candidate, create,
  or update Task decision that belongs to this Project and directly supports this
  specific Project assessment.
- An existing card already reflecting the same facts does not need a new proposal.

Return only the envelope after checking every item.
"""


def build_task_agent_repair_prompt(repair_context: str) -> str:
    return (
        "Continue the immediately preceding Task Agent decision in this session. "
        "Reuse its source, Work Item and semantic context. Return one complete "
        "TaskAgentDecision JSON object correcting the following validation issues.\n\n"
        + repair_context
    )


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


def select_task_decision_candidate(
    raw: str,
) -> tuple[TaskAgentDecision | None, dict[str, object] | None, list[str]]:
    """Select once, retaining original fields alongside the validated model."""
    problems: list[str] = []
    # The last complete decision wins over an earlier draft.
    for candidate in reversed(_task_decision_candidates(raw)):
        try:
            return TaskAgentDecision.model_validate(candidate), candidate, []
        except ValidationError as exc:
            if not problems:
                # Field path and message only: error["input"] echoes the
                # model's values, which can carry runtime paths or credentials
                # into the correction prompt and the service log.
                problems = [
                    f"{'.'.join(str(part) for part in error['loc'])}: {error['msg']}"
                    for error in exc.errors()[:TASK_DECISION_PROBLEM_LIMIT]
                ]
                from app.task_result_diagnostics import task_envelope_problems

                if any(error["loc"] for error in exc.errors()):
                    problems = (problems + task_envelope_problems(candidate))[:TASK_DECISION_PROBLEM_LIMIT]
    return None, None, problems


def _validate_task_decision_candidates(
    raw: str,
) -> tuple[TaskAgentDecision | None, list[str]]:
    decision, _original, problems = select_task_decision_candidate(raw)
    return decision, problems


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
        "TaskAgentDecision JSON does not satisfy the schema: " + "; ".join(problems),
        raw_output=raw,
    )


def _source_locator(
    work_item: WorkItem, item: TaskDecision | None = None
) -> tuple[str, str, str, str]:
    """Where a reader can find the source (Derek 2026-09-25): its link when it has one; otherwise a
    description of where it is (a DingTalk message is its group and the person who sent it).

    What the decision states wins. For the current Work Item the service fills in what it
    already knows: a URL reference, the meeting page link inside an AI-minutes summary, or
    the conversation and its sender. A Work Item that offers none of these still has its
    `source_ref` on the record; earlier or remembered evidence must state its own locator
    (the decision model requires it).
    """
    link, group, person = (
        (
            item.source_link.strip(),
            item.source_group.strip(),
            item.source_person.strip(),
        )
        if item is not None
        else ("", "", "")
    )
    description = item.source_description.strip() if item is not None else ""
    if (
        (item is not None and item.evidence_origin != "current")
        or link
        or description
        or (group and person)
    ):
        return link, group, person, description
    if work_item.source.ref.startswith(("http://", "https://")):
        return work_item.source.ref, group, person, description
    try:
        meeting = json.loads(work_item.summary).get("meeting")
    except (ValueError, AttributeError):
        meeting = None
    share_url = meeting.get("shareUrl") if isinstance(meeting, dict) else None
    if isinstance(share_url, str) and share_url.strip():
        return share_url.strip(), group, person, description
    return (
        link,
        group or work_item.source.conversation_title,
        person or work_item.context.sender,
        description,
    )


def _report_markdown(work_item: WorkItem) -> str:
    if work_item.source.type not in {
        WorkItemSourceType.MANAGEMENT_WEEKLY_REPORT,
        WorkItemSourceType.PROJECT_WEEKLY_REPORT,
        WorkItemSourceType.DEPARTMENT_WEEKLY_REPORT,
    }:
        return ""
    try:
        payload = json.loads(work_item.summary)
        report = payload.get("report", {})
        markdown = payload.get("markdown", "") or (
            report.get("markdown", "") if isinstance(report, dict) else ""
        )
    except (TypeError, ValueError, AttributeError):
        return ""
    return markdown if isinstance(markdown, str) else ""


def _report_project_registry_title(work_item: WorkItem, source_excerpt: str) -> str:
    """Return a project name only when the cited row is in a report project registry."""
    markdown = _report_markdown(work_item)
    if not markdown or not source_excerpt.strip():
        return ""
    row_start = markdown.find(source_excerpt)
    if row_start < 0:
        return ""
    row_start += len(source_excerpt) - len(source_excerpt.lstrip())
    row_start = markdown.rfind("\n", 0, row_start) + 1
    row_end = markdown.find("\n", row_start)
    if row_end < 0:
        row_end = len(markdown)
    registry_match = re.search(
        r"(?m)^##\s+\**(?:手头项目|项目清单|项目组合)\**\s*$",
        markdown,
    )
    if registry_match is None or row_start < registry_match.end():
        return ""
    next_section = re.search(r"(?m)^##\s+", markdown[registry_match.end() :])
    registry_end = (
        registry_match.end() + next_section.start()
        if next_section is not None
        else len(markdown)
    )
    if row_start >= registry_end:
        return ""
    # The excerpt only locates the original row. Parse its columns from the
    # report so a partial quote cannot shift the authoritative project column.
    first_row = markdown[row_start:row_end]
    if "|" not in first_row or re.fullmatch(r"\|[\s:|\-]+\|", first_row.strip()):
        return ""
    following_line = markdown[row_end + 1 :].split("\n", 1)[0].strip()
    if re.fullmatch(r"\|[\s:|\-]+\|", following_line):
        return ""
    cells = [cell.strip() for cell in first_row.strip().strip("|").split("|")]
    if not cells:
        return ""
    preceding_lines = markdown[registry_match.end() : row_start].splitlines()
    header_cells: list[str] = []
    for index in range(len(preceding_lines) - 2, -1, -1):
        header_line = preceding_lines[index].strip()
        separator_line = preceding_lines[index + 1].strip()
        if not header_line.startswith("|") or not separator_line.startswith("|"):
            continue
        if not re.fullmatch(r"\|[\s:|\-]+\|", separator_line):
            continue
        header_cells = [cell.strip() for cell in header_line.strip("|").split("|")]
        break
    project_column = next(
        (
            index
            for index, header in enumerate(header_cells)
            if (
                re.sub(r"<[^>]+>", "", header).strip()
                in {"项目", "项目名", "项目名称", "Project", "业务项目", "工作流"}
                or "项目/方向" in re.sub(r"<[^>]+>", "", header)
            )
        ),
        None,
    )
    if project_column is None or project_column >= len(cells):
        return ""
    title = cells[project_column].strip()
    if not title or title in {"项目名", "项目", "Project"}:
        return ""
    return title


def _bind_current_source(signal, *, native_provenance=None, provider_locator=None):
    from app.business_source_reader import SourceVersionBinding
    return replace(signal, source_binding=SourceVersionBinding.from_signal(
        signal, native_provenance=native_provenance, provider_locator=provider_locator))


def _work_item_source_coordinates(store, runner, work_item):
    from app import native_trajectory
    attempt_id = getattr(runner.codex, "last_runtime_attempt_id", None)
    if attempt_id is None:
        return None, None
    attempt = store.get_agent_runtime_attempt(attempt_id)
    if attempt is None or attempt.status != "completed":
        raise ValueError("completed source runtime attempt is missing")
    if attempt.runtime_kind in ("codex_cli", "claude_cli"):
        try:
            locator = native_trajectory.locate_native_source_body(
                work_item.summary, runtime_kind=attempt.runtime_kind,
                session_uuid=attempt.session_id, start_line=attempt.transcript_start,
                end_line=attempt.transcript_end)
        except native_trajectory.NativeSourceMissing:
            pass
        else:
            return locator, None
    # Only after the actual native turn has no full original do we retain the
    # existing producer's upstream coordinates. No provider is called here.
    source = _project_source_signal(work_item)
    if source.source_type == "ai_minutes":
        from app.business_minutes_source_reader import minutes_provider_locator
        return None, minutes_provider_locator(source)
    from app.business_weekly_source_reader import WEEKLY_SOURCE_TYPES, weekly_provider_locator
    if source.source_type in WEEKLY_SOURCE_TYPES:
        return None, weekly_provider_locator(source)
    return None, None


def _task_source_signal(work_item: WorkItem, item: TaskDecision, *,
                        native_provenance=None, provider_locator=None) -> SourceSignal:
    import hashlib

    if item.evidence_origin == "current":
        if item.source_ref != work_item.source.ref:
            raise ValueError("task decision source_ref must match the Work Item source")
        if not item.source_excerpt.strip():
            raise ValueError("task decision needs a source_excerpt")
    link, group, person, description = _source_locator(work_item, item)

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
    if (
        item.action in {"create_task", "record_candidate"}
        or item.transition == "promote_candidate"
    ):
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
        current_task_id = (
            item.identity_proposal.target_task_id
            if item.transition == "merge_identity" and item.identity_proposal
            else item.task_id
        )
        relation_effects = sorted(
            (*r.endpoints(current_task_id), r.relation_type)
            for r in item.relation_proposals
        )
        anchor_effects = sorted(
            (proposal.anchor_id,) for proposal in item.anchor_match_proposals
        )
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
                if item.identity_proposal
                else None
            ),
            "relations": relation_effects,
            "cluster": (
                {
                    "cluster_id": item.cluster_proposal.cluster_id,
                    "title": normalized(item.cluster_proposal.title),
                    "task_ids": sorted(item.cluster_proposal.task_ids),
                }
                if item.cluster_proposal
                else None
            ),
            "anchors": anchor_effects,
            "project_candidate": (
                {
                    "cluster_id": item.project_candidate_proposal.cluster_id,
                    "title": normalized(item.project_candidate_proposal.title),
                }
                if item.project_candidate_proposal
                else None
            ),
            "date_effects": date_effects,
        }
    stable_item = hashlib.sha256(
        json.dumps(
            semantic_identity, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
    ).hexdigest()
    if item.evidence_origin != "current":
        # Earlier evidence (this session's history, or provenance Memory pointed to) is
        # kept as its own source signal, under the ORIGINAL reference and text, with the
        # Work Item that led to it in the context. The service cannot re-read the original,
        # so the origin is recorded and a reader can see this was cited, not observed now.
        source_type = f"{item.evidence_origin}_provenance"
        return SourceSignal(
            source_type=source_type,
            source_ref=item.source_ref,
            evidence_text=item.source_excerpt,
            dedupe_key=f"{source_type}:{item.source_ref}:task-item:{stable_item}",
            conversation_title=group,
            author_name=person,
            context_json=json.dumps(
                {
                    "evidence_origin": item.evidence_origin,
                    "cited_while_processing": work_item.source.ref,
                    **({"source_link": link} if link else {}),
                    **({"source_description": description} if description else {}),
                },
                ensure_ascii=False,
                sort_keys=True,
            ),
        )
    signal = SourceSignal(
        source_type=work_item.source.type.value,
        source_ref=work_item.source.ref,
        evidence_text=work_item.summary,
        dedupe_key=f"{work_item.source.type.value}:{work_item.source.ref}:task-item:{stable_item}",
        source_time=work_item.source.created_at,
        conversation_id=work_item.source.conversation_id,
        conversation_title=group,
        author_user_id=work_item.context.sender_user_id,
        author_name=person or work_item.context.sender,
        author_kind=(
            BusinessActorKind.HUMAN
            if work_item.context.sender_user_id
            else BusinessActorKind.UNKNOWN
        ),
        context_json=json.dumps(
            {
                "work_item_title": work_item.source.title,
                "assignment_authorized": work_item.context.assignment_authorized,
                **({"source_link": link} if link else {}),
                **({"source_description": description} if description else {}),
                **(
                    {"reply_to_source_ref": work_item.context.reply_to_source_ref}
                    if work_item.context.reply_to_source_ref
                    else {}
                ),
                **(
                    {"external_task_id": work_item.context.external_task_id}
                    if work_item.context.external_task_id
                    else {}
                ),
                **(
                    {"owner_identity": work_item.context.owner_identity}
                    if work_item.context.owner_identity
                    else {}
                ),
            },
            ensure_ascii=False,
            sort_keys=True,
        ),
    )
    return _bind_current_source(signal, native_provenance=native_provenance,
                                provider_locator=provider_locator)


def _task_date_inputs(
    item: TaskDecision, work_item: WorkItem, *, is_acceptance: bool
) -> tuple[TaskDateInput, ...]:
    values: list[TaskDateInput] = []
    for evidence in item.date_evidence:
        if evidence.source_ref != item.source_ref:
            raise ValueError("date evidence source_ref must match its task decision")
        if evidence.source_excerpt not in work_item.summary:
            raise ValueError(
                "date evidence source_excerpt must be an exact source substring"
            )
        if evidence.kind == "assigned_at":
            raise ValueError(
                "assigned_at is derived only from trusted source timestamp metadata"
            )
        if (
            work_item.source.type is WorkItemSourceType.AI_MINUTES
            and evidence.kind != "next_check_at"
        ):
            raise ValueError(
                "AI Minutes date actor cannot be attributed without trusted speaker identity metadata"
            )
        actor_kind = (
            BusinessActorKind.HUMAN
            if work_item.context.sender_user_id
            else BusinessActorKind.UNKNOWN
        )
        actor_user_id = work_item.context.sender_user_id
        actor_name = work_item.context.sender
        agent_date = evidence.kind == "next_check_at"
        expected_actor = (
            ("task-agent", "CEO Agent")
            if agent_date
            else (work_item.context.sender_user_id, work_item.context.sender)
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
                raise ValueError(
                    "source date actor is not attributable to an identified source actor"
                )
        values.append(
            TaskDateInput(
                date_type=BusinessTaskDateType(evidence.kind),
                value_at=evidence.value,
                raw_phrase=evidence.source_excerpt,
                actor_kind=actor_kind,
                actor_user_id=actor_user_id,
                actor_name=actor_name,
            )
        )
    if (
        (item.action == "create_task" or item.transition == "promote_candidate")
        and item.formal_basis is not None
        and item.formal_basis is FormalTaskBasis.EXPLICIT_ASSIGNMENT
        and work_item.source.created_at
        and work_item.context.sender_user_id
    ):
        values.append(
            TaskDateInput(
                date_type=BusinessTaskDateType.ASSIGNED_AT,
                value_at=work_item.source.created_at,
                raw_phrase=work_item.source.created_at,
                actor_kind=BusinessActorKind.HUMAN,
                actor_user_id=work_item.context.sender_user_id,
                actor_name=work_item.context.sender,
            )
        )
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
            raise ValueError(
                "explicit commitment must be authored by its identified owner"
            )
        return
    if basis is FormalTaskBasis.MEETING_ACTION_ITEM:
        if not (
            work_item.source.type is WorkItemSourceType.AI_MINUTES
            and work_item.context.source_conversation_kind is WorkItemSourceKind.MINUTES
            and "#todos-sha256=" in work_item.source.ref
        ):
            raise ValueError(
                "meeting action item requires a sourced meeting action-item record"
            )
        if item.owner_kind != "individual" or item.owner_relation not in {
            "explicit_assignment",
            "self_commitment",
            "meeting_summary_action_item",
        }:
            raise ValueError(
                "meeting action item requires an explicit individual owner relation"
            )
        return
    if basis is FormalTaskBasis.EXTERNAL_TODO:
        if not (
            work_item.source.type
            in {
                WorkItemSourceType.TODO_COMPLETION_CHECK,
                WorkItemSourceType.TODO_COMPLETION_EVIDENCE_CANDIDATE,
            }
            and work_item.context.external_task_id.strip()
        ):
            raise ValueError(
                "external TODO basis requires trusted external TODO source metadata"
            )


def _validate_task_agent_decision(
    decision: TaskAgentDecision, *, work_item: WorkItem, now: str = ""
) -> None:
    citation_errors: list[str] = []
    for citation in _project_citations(decision):
        if citation.signal_id is None:
            try:
                _validate_current_project_citation(citation, work_item=work_item)
            except ValueError as exc:
                citation_errors.append(
                    f"{exc}; source_ref={citation.source_ref!r}; "
                    f"source_excerpt={citation.source_excerpt!r}"
                )
    if citation_errors:
        raise RepairableTaskDecisionValidationError(
            "\n".join(dict.fromkeys(citation_errors))
        )
    for item in decision.task_decisions:
        if item.action == "skip":
            continue
        if item.action == "create_task" and not (
            item.owner_user_id.strip() or item.owner_name.strip()
        ):
            raise RepairableTaskDecisionValidationError(
                "formal task creation requires explicit source-backed owner; retain as candidate"
            )
        if item.action == "create_task" or item.transition == "promote_candidate":
            _validate_formal_basis_source(item, work_item)
            evidence = dict(item.owner_evidence)
            evidence.setdefault("source_ref", item.source_ref)
            if not str(evidence.get("excerpt") or "").strip():
                evidence["excerpt"] = item.source_excerpt
            try:
                TaskSemanticService._require_source_backed_owner(
                    signal=_task_source_signal(work_item, item),
                    owner_user_id=item.owner_user_id,
                    owner_name=item.owner_name,
                    owner_evidence_json=_json_dumps(evidence),
                )
            except ValueError as exc:
                raise RepairableTaskDecisionValidationError(
                    f"Task {item.title}: {exc}. Cite the named owner's assignment "
                    "or undertaking from the supplied source, or linked memory evidence "
                    "with its episode_id. If ownership is not established, retain a candidate."
                ) from exc
        if (
            item.transition == "apply_acceptance"
            and item.acceptance_polarity != "accepted"
        ):
            raise RepairableTaskDecisionValidationError(
                "only explicit accepted owner evidence may use apply_acceptance"
            )
        if item.transition == "apply_acceptance" and item.action != "update_task":
            raise RepairableTaskDecisionValidationError(
                "apply_acceptance requires update_task"
            )
        if (
            item.transition == "merge_identity"
            and item.identity_proposal is not None
            and (
                item.identity_proposal.identity_evidence.basis
                == "same_deliverable_owner_context_time"
            )
        ):
            raise ValueError(
                "same deliverable, owner, context, and time can link or cluster Tasks but cannot merge identity"
            )
        try:
            _task_date_inputs(item, work_item, is_acceptance=item.transition == "apply_acceptance")
        except ValueError as exc:
            raise RepairableTaskDecisionValidationError(str(exc)) from exc


def _confirmed_official_project_anchors(
    store: AutoReplyStore, *, task_id: int, db: sqlite3.Connection
) -> set[int]:
    if store.get_business_task_in_transaction(task_id=task_id, _db=db) is None:
        return set()
    return {
        int(row["anchor_id"])
        for row in db.execute(
            f'select link.anchor_id from ({business_action_projection("business_task_anchor_links")}) link\n               join ({BUSINESS_PROJECTIONS["anchor"]}) anchor on anchor.id=link.anchor_id\n               join ({BUSINESS_PROJECTIONS["project"]}) project on project.canonical_anchor_id=link.anchor_id\n               where link.task_id=? and link.status=\'confirmed\' and link.active=1\n                 and anchor.anchor_type=\'project\' and anchor.active=1',
            (task_id,),
        ).fetchall()
    }


def _project_source_signal(work_item: WorkItem, *, native_provenance=None, provider_locator=None) -> SourceSignal:
    """One observed source version can support Project facts without a Task."""
    link, group, person, description = _source_locator(work_item)
    fields = dict(
        source_type=work_item.source.type.value,
        source_ref=work_item.source.ref,
        evidence_text=work_item.summary,
        source_time=work_item.source.created_at,
        conversation_id=work_item.source.conversation_id,
        author_user_id=work_item.context.sender_user_id,
        author_name=person or work_item.context.sender,
        author_kind=(
            BusinessActorKind.HUMAN
            if work_item.context.sender_user_id
            else BusinessActorKind.UNKNOWN
        ),
    )
    signal = SourceSignal(
        **fields,
        dedupe_key=f"project-source:{source_document_key(**fields)}",
        conversation_title=group,
        context_json=json.dumps(
            {
                "work_item_title": work_item.source.title,
                **({"source_link": link} if link else {}),
                **({"source_description": description} if description else {}),
            },
            ensure_ascii=False,
            sort_keys=True,
        ),
    )
    return _bind_current_source(signal, native_provenance=native_provenance,
                                provider_locator=provider_locator)


def _project_citations(decision: TaskAgentDecision):
    for project in decision.project_decisions:
        yield from project.evidence
        if project.crm_customer_evidence is not None:
            yield project.crm_customer_evidence
        if project.context is not None:
            yield from ProjectContextService._citations(project.context)
    for task in decision.task_decisions:
        yield from task.project_link_evidence
        if task.suggestion is not None:
            yield from task.suggestion.responsibility_evidence
            yield from task.suggestion.basis_evidence
    for assessment in decision.project_assessments:
        yield from assessment.evidence
        if assessment.attention_proposal is not None:
            yield from assessment.attention_proposal.evidence


def _prepare_decision_sources(
    store: AutoReplyStore, decision: TaskAgentDecision, *, source_reader: RequestSourceReader | None = None
) -> PreparedSources:
    """Collect finite historical IDs before any Task domain writer starts."""
    ids = {proof.signal_id for proof in _project_citations(decision)
           if proof.signal_id is not None}
    task_proofs: dict[int, set[int]] = {}
    for item in decision.task_decisions:
        if item.acceptance_target_signal_id is not None:
            ids.add(item.acceptance_target_signal_id)
            if item.task_id is not None:
                task_proofs.setdefault(item.task_id, set()).add(
                    item.acceptance_target_signal_id
                )
        proposal = item.identity_proposal
        if proposal is None:
            continue
        proof = proposal.identity_evidence
        ids.update((proof.source_signal_id, proof.target_signal_id))
        task_proofs.setdefault(proposal.source_task_id, set()).add(proof.source_signal_id)
        task_proofs.setdefault(proposal.target_task_id, set()).add(proof.target_signal_id)
        if proof.basis != "same_source_item_same_owner":
            continue
        for task_id, signal_id in (
            (proposal.source_task_id, proof.source_signal_id),
            (proposal.target_task_id, proof.target_signal_id),
        ):
            task = store.get_business_task(task_id)
            signal = store.get_business_task_signal(signal_id)
            if task is None or signal is None:
                continue
            owner_ref = str(_task_owner_identity_evidence(task).get("source_ref") or "")
            owner_signal = store.get_business_task_signal_for_task_source_ref(
                task_id=task_id, source_ref=owner_ref, source_type=signal.source_type
            )
            if owner_signal is not None:
                ids.add(owner_signal.id)
                task_proofs.setdefault(task_id, set()).add(owner_signal.id)
    card_snapshots: dict[int, tuple[object, tuple[int, ...]] | None] = {}
    project_link_snapshots: dict[int, set[int]] = {}
    for assessment in decision.project_assessments:
        if assessment.existing_attention_id is None:
            continue
        with store._connect() as db:
            card = store.get_business_attention_item_in_transaction(
                item_id=assessment.existing_attention_id, _db=db
            )
        if card is None:
            card_snapshots[assessment.existing_attention_id] = None
            continue
        with store._connect() as db:
            members = tuple(sorted(
                link.task_id for link in store.list_business_attention_tasks_in_transaction(
                    attention_item_id=card.id, _db=db
                )
            ))
            project_row = db.execute(
                f'select id from ({BUSINESS_PROJECTIONS["project"]}) '
                'where canonical_anchor_id=?', (card.anchor_id,)
            ).fetchone()
            if project_row is not None:
                project_id = int(project_row["id"])
                proof_ids = {
                    proof.get("signal_id")
                    for proof in json.loads(card.assessment_json).get("evidence", [])
                    if isinstance(proof, dict) and isinstance(proof.get("signal_id"), int)
                }
                rows = db.execute(
                    f"select signal_id from ({business_action_projection('business_project_evidence')}) "
                    "where project_id=?", (project_id,)
                ).fetchall()
                project_link_snapshots.setdefault(project_id, set()).update(
                    int(row["signal_id"]) for row in rows if int(row["signal_id"]) in proof_ids
                )
        card_snapshots[card.id] = (card, members)
        for proof in json.loads(card.assessment_json).get("evidence", []):
            signal_id = proof.get("signal_id") if isinstance(proof, dict) else None
            if isinstance(signal_id, int) and signal_id > 0:
                ids.add(signal_id)
    prepared = PreparedSources.load(store, ids, reader=source_reader)
    for task_id, signal_ids in task_proofs.items():
        links = tuple(sorted(
            (row.signal_id, row.evidence_role.value)
            for row in store.list_business_task_evidence(task_id)
            if row.signal_id in signal_ids
        ))
        prepared.task_evidence_links[task_id] = (frozenset(signal_ids), links)
    prepared.attention_cards = card_snapshots
    prepared.project_evidence_links = {
        project_id: frozenset(signal_ids)
        for project_id, signal_ids in project_link_snapshots.items()
    }
    return prepared


def _assert_prepared_relationships(
    store: AutoReplyStore, prepared: PreparedSources, db: sqlite3.Connection
) -> None:
    """Reject a changed historical relationship before any decision write."""
    for task_id, (signal_ids, expected) in prepared.task_evidence_links.items():
        actual = tuple(sorted(
            (row.signal_id, row.evidence_role.value)
            for row in store.list_business_task_evidence_in_transaction(
                task_id=task_id, _db=db
            ) if row.signal_id in signal_ids
        ))
        if actual != expected:
            raise ValueError("historical Task evidence link changed before apply")
    _assert_prepared_attention_relationships(store, prepared, db)


def _assert_prepared_attention_relationships(
    store: AutoReplyStore, prepared: PreparedSources, db: sqlite3.Connection,
    *, card_ids: frozenset[int] | None = None,
    project_ids: frozenset[int] | None = None,
) -> None:
    """Recheck retained card proofs without replaying this decision's new Task links."""
    for card_id, expected in prepared.attention_cards.items():
        if card_ids is not None and card_id not in card_ids:
            continue
        card = store.get_business_attention_item_in_transaction(item_id=card_id, _db=db)
        if card is None:
            actual = None
        else:
            members = tuple(sorted(
                link.task_id for link in store.list_business_attention_tasks_in_transaction(
                    attention_item_id=card.id, _db=db
                )
            ))
            actual = (card, members)
        if actual != expected:
            raise ValueError("existing Attention evidence or membership changed before apply")
    for project_id, signal_ids in prepared.project_evidence_links.items():
        if project_ids is not None and project_id not in project_ids:
            continue
        if not signal_ids:
            continue
        rows = db.execute(
            f"select signal_id from ({business_action_projection('business_project_evidence')}) "
            "where project_id=?", (project_id,)
        ).fetchall()
        actual = {int(row["signal_id"]) for row in rows}
        if not signal_ids.issubset(actual):
            raise ValueError("existing Project evidence link changed before apply")


def _validate_current_project_citation(citation, *, work_item: WorkItem) -> None:
    if citation.source_ref != work_item.source.ref:
        raise ValueError(
            "current Project evidence must cite the immutable Work Item source_ref; "
            f"expected={work_item.source.ref!r}, received={citation.source_ref!r}"
        )
    if not source_contains_quote(work_item.summary, citation.source_excerpt):
        raise ValueError(
            "current Project quote is absent from the immutable Work Item"
        )


def _resolve_project_citation(
    store: AutoReplyStore,
    citation,
    *,
    work_item: WorkItem,
    db: sqlite3.Connection,
    prepared_sources: PreparedSources,
    current_signal_id: int | None = None,
) -> SourceCitation:
    if citation.signal_id is None:
        _validate_current_project_citation(citation, work_item=work_item)
        signal_id = current_signal_id
    else:
        signal, body = prepared_sources.require_body(
            store, citation.signal_id, db=db
        )
        if signal is None:
            raise ValueError("historical Project evidence signal does not exist")
        if signal.source_ref != citation.source_ref:
            raise ValueError(
                "historical Project evidence signal/source_ref does not match"
            )
        if not source_is_observed(signal.source_type):
            raise ValueError(
                "Project evidence must be observed original source, not cited provenance"
            )
        if not source_contains_quote(body, citation.source_excerpt):
            raise ValueError("historical Project quote is absent from original source")
        signal_id = signal.id
    return SourceCitation(
        signal_id=signal_id,
        source_ref=citation.source_ref,
        source_excerpt=citation.source_excerpt,
    )


def _resolved_context(store, context, *, work_item, db, current_signal_id, prepared_sources):
    if context is None:
        return None

    def role(value):
        return value.model_copy(
            update={
                "evidence": [
                    _resolve_project_citation(
                        store,
                        proof,
                        work_item=work_item,
                        db=db,
                        current_signal_id=current_signal_id,
                        prepared_sources=prepared_sources,
                    )
                    for proof in value.evidence
                ]
            }
        )

    return ProjectContext(
        goal=context.goal,
        scope=context.scope,
        overall_owner=role(context.overall_owner) if context.overall_owner else None,
        responsibilities=[role(value) for value in context.responsibilities],
        facts=[
            value.model_copy(
                update={
                    "evidence": [
                        _resolve_project_citation(
                            store,
                            proof,
                            work_item=work_item,
                            db=db,
                            current_signal_id=current_signal_id,
                            prepared_sources=prepared_sources,
                        )
                        for proof in value.evidence
                    ]
                }
            )
            for value in context.facts
        ],
    )


def _official_project(store, anchor_id, *, db):
    row = db.execute(
        f'select p.id from ({BUSINESS_PROJECTIONS["project"]}) p join ({BUSINESS_PROJECTIONS["anchor"]}) a on a.id=p.canonical_anchor_id '
        "where a.id=? and a.anchor_type='project' and a.active=1",
        (anchor_id,),
    ).fetchone()
    if row is None:
        raise ValueError(
            "Project selector requires a registered active official Project"
        )
    return store.get_business_project_in_transaction(project_id=int(row["id"]), _db=db)


def _validate_stored_project_assessments(
    store: AutoReplyStore,
    decision: TaskAgentDecision,
    *,
    work_item: WorkItem,
    db: sqlite3.Connection,
    prepared_sources: PreparedSources,
) -> None:
    """Verify original citations and existing identities before domain writes."""
    for citation in _project_citations(decision):
        _resolve_project_citation(store, citation, work_item=work_item, db=db,
                                  prepared_sources=prepared_sources)

    project_anchors: dict[int, int | None] = {}
    for index, project in enumerate(decision.project_decisions):
        if project.anchor_id is not None:
            project_anchors[index] = _official_project(
                store, project.anchor_id, db=db
            ).canonical_anchor_id
            continue
        proposal = project.registration
        assert proposal is not None
        if proposal.authority == "meeting_decision":
            valid = (
                work_item.source.type is WorkItemSourceType.AI_MINUTES
                or work_item.context.source_conversation_kind
                is WorkItemSourceKind.MINUTES
            ) and source_contains_quote(work_item.summary, proposal.source_excerpt)
            if not valid:
                raise ValueError(
                    "meeting Project registration must cite the current official Project source"
                )
        else:
            valid = (
                work_item.source.type.value == proposal.authority
                and _report_project_registry_title(work_item, proposal.source_excerpt)
                == proposal.title
            )
            if not valid:
                raise ValueError(
                    "report Project registration must match the cited report registry row and source authority"
                )
        matches = db.execute(
            f'select p.canonical_anchor_id from ({BUSINESS_PROJECTIONS["project"]}) p '
            f'join ({BUSINESS_PROJECTIONS["anchor"]}) a on a.id=p.canonical_anchor_id '
            "where p.title=? and a.active=1 and a.anchor_type='project' limit 2",
            (proposal.title,),
        ).fetchall()
        if len(matches) > 1:
            raise ValueError(
                "Project identity conflict: multiple active official Projects have this exact title"
            )
        project_anchors[index] = (
            int(matches[0]["canonical_anchor_id"]) if matches else None
        )

    def selected_anchor(anchor_id, project_index):
        if anchor_id is not None:
            return _official_project(store, anchor_id, db=db).canonical_anchor_id
        return project_anchors[project_index] if project_index is not None else None

    referenced_task_ids: set[int] = set()
    for task in decision.task_decisions:
        for task_id in (task.task_id, task.target_task_id):
            if task_id is not None:
                if (
                    store.get_business_task_in_transaction(task_id=task_id, _db=db)
                    is None
                ):
                    raise ValueError(f"supporting Task {task_id} does not exist")
                referenced_task_ids.add(task_id)
        if task.project is not None:
            selected_anchor(task.project.anchor_id, task.project.project_decision_index)

    assessed_anchors: set[int] = set()
    for assessment in decision.project_assessments:
        anchor_id = selected_anchor(
            assessment.anchor_id, assessment.project_decision_index
        )
        if anchor_id is not None:
            project = _official_project(store, anchor_id, db=db)
            if project.title != assessment.project_title:
                raise ValueError(
                    "project assessment project_title must match the canonical stored Project title"
                )
            if anchor_id in assessed_anchors:
                raise ValueError(
                    "canonical stored Project must have exactly one assessment"
                )
            assessed_anchors.add(anchor_id)
        supporting = set(assessment.task_ids)
        for index in assessment.decision_indexes:
            task = decision.task_decisions[index]
            if task.task_id is not None:
                supporting.add(
                    task.target_task_id
                    if task.transition == "merge_identity"
                    else task.task_id
                )
        for task_id in supporting:
            if store.get_business_task_in_transaction(task_id=task_id, _db=db) is None:
                raise ValueError(f"supporting Task {task_id} does not exist")
            referenced_task_ids.add(task_id)
            confirmed = _confirmed_official_project_anchors(
                store, task_id=task_id, db=db
            )
            selected_for_this_project = any(
                task.task_id == task_id
                and task.project is not None
                and (
                    (
                        anchor_id is not None
                        and selected_anchor(
                            task.project.anchor_id, task.project.project_decision_index
                        )
                        == anchor_id
                    )
                    or (
                        anchor_id is None
                        and task.project.project_decision_index
                        == assessment.project_decision_index
                    )
                )
                for index in assessment.decision_indexes
                for task in [decision.task_decisions[index]]
            )
            if anchor_id not in confirmed and not selected_for_this_project:
                raise ValueError(
                    "supporting Task is not confirmed to the assessed Project"
                )
        if assessment.existing_attention_id is None:
            continue
        card = store.get_business_attention_item_in_transaction(
            item_id=assessment.existing_attention_id, _db=db
        )
        if card is None:
            raise ValueError("existing Attention card does not exist")
        if card.anchor_id != anchor_id or card.stable_key != f"project:{anchor_id}":
            raise ValueError("existing Attention card belongs to a different Project")
        if card.status.value != "active":
            raise ValueError("existing Attention card is not active")
        members = {
            link.task_id
            for link in store.list_business_attention_tasks_in_transaction(
                attention_item_id=card.id, _db=db
            )
        }
        if not supporting.issubset(members):
            raise ValueError(
                "existing Attention card does not contain the assessment's supporting Tasks"
            )
        proofs = json.loads(card.assessment_json).get("evidence", [])
        if not proofs:
            raise ValueError(
                "existing Attention card lacks stored original assessment evidence"
            )
        verified = set()
        for proof in proofs:
            original = SourceCitation.model_validate(
                {
                    key: proof[key]
                    for key in ("signal_id", "source_ref", "source_excerpt")
                    if key in proof
                }
            )
            if original.signal_id is None:
                raise ValueError(
                    "existing Attention card lacks stored original assessment evidence"
                )
            _resolve_project_citation(store, original, work_item=work_item, db=db,
                                      prepared_sources=prepared_sources)
            verified.add(
                (original.signal_id, original.source_ref, original.source_excerpt)
            )
        if card.evidence_signal_id not in {proof[0] for proof in verified}:
            raise ValueError(
                "existing Attention primary evidence is absent from stored original evidence"
            )
        if not any(
            (proof.signal_id, proof.source_ref, proof.source_excerpt) in verified
            for proof in assessment.evidence
        ):
            raise ValueError(
                "existing Attention assessment must cite this card's stored original evidence"
            )

    for task_id in referenced_task_ids:
        for anchor_id in _confirmed_official_project_anchors(
            store, task_id=task_id, db=db
        ):
            if anchor_id not in assessed_anchors:
                raise ValueError(
                    f"current Task {task_id} confirmed Project {anchor_id} requires exactly one assessment"
                )


def _task_project_anchor_is_retired(store: AutoReplyStore, title: str, *, db) -> bool:
    import hashlib

    key = hashlib.sha256(" ".join(title.split()).casefold().encode("utf-8")).hexdigest()
    anchor = store.get_business_anchor_by_identity_in_transaction(
        anchor_type="project", anchor_ref=f"task-agent-project:{key}", _db=db
    )
    return anchor is not None and not anchor.active


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
    crm_customer_lookups: dict[int, CrmCustomerLookup] | None = None,
    _db: sqlite3.Connection | None = None,
    prepared_sources: PreparedSources | None = None,
    source_native_provenance: dict[str, object] | None = None,
    source_provider_locator: dict[str, object] | None = None,
) -> TaskAgentApplyResult:
    """Persist all source-grounded task decisions, atomically when _db is supplied."""
    _validate_task_agent_decision(decision, work_item=work_item, now=now)
    if prepared_sources is None:
        if _db is not None:
            raise ValueError("historical source map was not prepared before writer")
        prepared_sources = _prepare_decision_sources(
            store, decision, source_reader=store.business_source_reader(provider_reader=dws))
    service = TaskSemanticService(store)
    resolution = BusinessResolutionService(store)
    task_ids: list[int] = []
    affected_task_ids: list[int] = []
    attention: list[AppliedTaskAttention] = []
    applied_decisions: list[AppliedTaskDecision] = []
    skipped_reasons: list[str] = []
    project_links: set[tuple[int, int]] = set()
    applied_projects: list[AppliedProjectDecision] = []
    current_signal_id: int | None = None

    def apply(db: sqlite3.Connection) -> None:
        nonlocal current_signal_id
        _assert_prepared_relationships(store, prepared_sources, db)
        _validate_stored_project_assessments(
            store, decision, work_item=work_item, db=db,
            prepared_sources=prepared_sources,
        )
        if any(proof.signal_id is None for proof in _project_citations(decision)):
            project_signal = _project_source_signal(work_item, native_provenance=source_native_provenance, provider_locator=source_provider_locator)
            current_signal_id = service._signal_id_or_create(
                signal=project_signal, db=db, now=service._now()
            )
            current_fact = store.get_business_task_signal_in_transaction(
                signal_id=current_signal_id, _db=db
            )
            assert current_fact is not None
            prepared_sources.add_current(current_fact, project_signal.evidence_text)
        projects_by_index = {}
        for index, project_decision in enumerate(decision.project_decisions):
            if project_decision.anchor_id is not None:
                project = _official_project(store, project_decision.anchor_id, db=db)
            else:
                proposal = project_decision.registration
                assert proposal is not None
                if _task_project_anchor_is_retired(store, proposal.title, db=db):
                    skipped_reasons.append(
                        f"Project {proposal.title} is retired; not reactivated."
                    )
                    continue
                project = resolution.register_source_project(
                    title=proposal.title,
                    registry_source=f"{proposal.authority}:{work_item.source.ref}",
                    _db=db,
                )
            projects_by_index[index] = project
            if project_decision.crm_customer_label and project_decision.crm_customer_evidence:
                lookup = (crm_customer_lookups or {}).get(index)
                if lookup is None:
                    lookup = CrmCustomerLookup(status="unavailable", error_code="not_run")
                customer_evidence = _resolve_project_citation(
                    store,
                    project_decision.crm_customer_evidence,
                    work_item=work_item,
                    db=db,
                    current_signal_id=current_signal_id,
                    prepared_sources=prepared_sources,
                )
                customer_candidates = [
                    ProjectCrmCustomerCandidate(
                        customer_id=candidate.customer_id,
                        name=candidate.name,
                        alias=candidate.alias,
                        registered_name=candidate.registered_name,
                        matched_fields=list(candidate.matched_fields),
                    )
                    for candidate in lookup.candidates
                ]
                lookup_status = (
                    "needs_confirmation" if lookup.status == "matched" else lookup.status
                )
                store.update_business_project_crm_customer_lookup_in_transaction(
                    project_id=project.id,
                    label=project_decision.crm_customer_label,
                    evidence=customer_evidence,
                    lookup_status=lookup_status,
                    candidates=customer_candidates,
                    _db=db,
                )
            context = _resolved_context(
                store,
                project_decision.context,
                work_item=work_item,
                db=db,
                current_signal_id=current_signal_id,
                prepared_sources=prepared_sources,
            )
            citations = [
                _resolve_project_citation(
                    store,
                    proof,
                    work_item=work_item,
                    db=db,
                    current_signal_id=current_signal_id,
                    prepared_sources=prepared_sources,
                )
                for proof in project_decision.evidence
            ]
            if context is not None:
                citations.extend(ProjectContextService._citations(context))
            ids = tuple(sorted({proof.signal_id for proof in citations}))
            revision_id = ProjectContextService(store).apply(
                project_id=project.id, context=context, signal_ids=ids, db=db,
                prepared_sources=prepared_sources,
            )
            applied_projects.append(
                AppliedProjectDecision(
                    project_decision_index=index,
                    project_id=project.id,
                    anchor_id=project.canonical_anchor_id,
                    revision_id=revision_id,
                    signal_ids=ids,
                )
            )

        def selected_project(anchor_id, project_index):
            return (
                _official_project(store, anchor_id, db=db)
                if anchor_id is not None
                else projects_by_index.get(project_index)
            )

        def apply_project_link(item, task_id):
            if item.project is None:
                return None, None
            project = selected_project(
                item.project.anchor_id, item.project.project_decision_index
            )
            if project is None:
                return None, None
            anchor_id = project.canonical_anchor_id
            proofs = [
                _resolve_project_citation(
                    store,
                    proof,
                    work_item=work_item,
                    db=db,
                    current_signal_id=current_signal_id,
                    prepared_sources=prepared_sources,
                )
                for proof in item.project_link_evidence
            ]
            if anchor_id not in _confirmed_official_project_anchors(
                store, task_id=task_id, db=db
            ):
                if not proofs:
                    raise ValueError(
                        "new confirmed Project link requires project_link_evidence"
                    )
                resolution.confirm_anchor_match(
                    task_id=task_id,
                    anchor_id=anchor_id,
                    evidence_signal_id=proofs[0].signal_id,
                    reason="Original evidence establishes this Project association.",
                    relevance=BusinessRelevance.RELEVANT,
                    _db=db,
                )
                affected_task_ids.append(task_id)
            for proof in proofs:
                store.link_business_task_evidence_in_transaction(
                    task_id=task_id,
                    signal_id=proof.signal_id,
                    evidence_role=BusinessEvidenceRole.RELEVANCE,
                    _db=db,
                )
            if proofs:
                ProjectContextService(store).apply(
                    project_id=project.id,
                    context=None,
                    signal_ids=tuple(sorted({proof.signal_id for proof in proofs})),
                    db=db,
                )
            project_links.add((task_id, anchor_id))
            return anchor_id, proofs[0].signal_id if proofs else None

        for decision_index, item in enumerate(decision.task_decisions):
            applied_project_anchor_id = None
            if item.action == "skip":
                continue
            if item.transition == "apply_acceptance":
                if not work_item.context.reply_to_source_ref:
                    skipped_reasons.append(
                        f"Acceptance for task {item.task_id} was not applied: source has no verified reply-to reference."
                    )
                    continue
                task = (
                    store.get_business_task(item.task_id)
                    if db is None
                    else store.get_business_task_in_transaction(
                        task_id=item.task_id, _db=db
                    )
                )
                evidence = (
                    store.list_business_task_evidence(item.task_id)
                    if db is None
                    else store.list_business_task_evidence_in_transaction(
                        task_id=item.task_id, _db=db
                    )
                )
                cited = next(
                    (
                        row
                        for row in evidence
                        if row.signal_id == item.acceptance_target_signal_id
                    ),
                    None,
                )
                target_signal = (
                    prepared_sources.require_fact(
                        store, item.acceptance_target_signal_id, db=db
                    ) if item.acceptance_target_signal_id is not None else None
                )
                linked_assignment = cited is not None and cited.evidence_role in {
                    BusinessEvidenceRole.ASSIGNMENT.value,
                    BusinessEvidenceRole.COMMITMENT.value,
                }
                same_reply_thread = (
                    target_signal is not None
                    and bool(work_item.source.conversation_id)
                    and target_signal.conversation_id
                    == work_item.source.conversation_id
                )
                explicit_reply_link = (
                    target_signal is not None
                    and target_signal.source_ref
                    == work_item.context.reply_to_source_ref
                )
                owner_is_reply_author = (
                    task is not None
                    and bool(work_item.context.sender_user_id)
                    and task.owner_user_id == work_item.context.sender_user_id
                    and (
                        not task.owner_name
                        or task.owner_name == work_item.context.sender
                    )
                )
                if not (
                    task is not None
                    and linked_assignment
                    and same_reply_thread
                    and explicit_reply_link
                    and owner_is_reply_author
                ):
                    skipped_reasons.append(
                        f"Acceptance for task {item.task_id} was not applied: cited assignment, exact reply link, conversation, or owner identity did not match."
                    )
                    continue
            signal = _task_source_signal(work_item, item, native_provenance=source_native_provenance, provider_locator=source_provider_locator)
            date_facts = _task_date_inputs(
                item, work_item, is_acceptance=item.transition == "apply_acceptance"
            )
            owner_evidence = dict(item.owner_evidence)
            owner_identity = work_item.context.owner_identity
            source_owner_id = (
                owner_identity.get("user_id", "")
                if owner_identity.get("name", "") == item.owner_name
                else work_item.context.sender_user_id
                if work_item.context.sender == item.owner_name
                else ""
            )
            if item.evidence_origin != "current":
                # Sender and owner identity metadata describe the current Work Item only.
                source_owner_id = ""
            if item.owner_user_id and item.owner_user_id != source_owner_id:
                raise ValueError(
                    "owner_user_id is not established by source identity metadata"
                )
            owner_user_id = source_owner_id
            if owner_evidence:
                # The owner's citation is a sentence from the source, not necessarily word for
                # word; default to the decision's own excerpt.
                owner_evidence.setdefault("source_ref", item.source_ref)
                if not str(owner_evidence.get("excerpt") or "").strip():
                    owner_evidence["excerpt"] = item.source_excerpt
                owner_evidence.setdefault("user_id", owner_user_id)
                owner_evidence.setdefault("name", item.owner_name)
            task_before = (
                store.get_business_task_in_transaction(task_id=item.task_id, _db=db)
                if item.task_id is not None
                else None
            )
            formality = FormalityEvidence(
                basis=item.formal_basis,
                assigner_is_authorized=(
                    item.formal_basis is not FormalTaskBasis.EXPLICIT_ASSIGNMENT
                    or work_item.context.assignment_authorized
                ),
                deliverable_is_explicit=(
                    bool(task_before is not None and task_before.title.strip())
                    if item.action == "update_task"
                    else bool(item.title.strip())
                ),
                owner_is_explicit=bool(
                    item.owner_user_id.strip() or item.owner_name.strip()
                ),
            )
            if (
                item.action == "update_task"
                and item.transition == "update_fields"
                and item.owner_name
            ):
                # One item whose owner the source does not establish must not fail the
                # meeting's other items: leave that Task as it was and say why.
                try:
                    TaskSemanticService._require_source_backed_owner(
                        signal=signal,
                        owner_user_id=owner_user_id,
                        owner_name=item.owner_name,
                        owner_evidence_json=json.dumps(
                            owner_evidence, ensure_ascii=False
                        ),
                    )
                except ValueError as exc:
                    skipped_reasons.append(
                        f"Task {item.task_id} owner was not applied: {exc}."
                    )
                    continue
            if (
                item.suggestion is None
                and item.action == "update_task"
                and item.transition == "update_fields"
                and task_before is not None
                and not date_facts
                and _update_fields_restates_task(task_before, item, owner_user_id)
            ):
                anchor_id, proof_id = apply_project_link(item, item.task_id)
                if proof_id is not None:
                    # An association has its own evidence; it is not a Task details update.
                    task_ids.append(item.task_id)
                    applied_decisions.append(
                        AppliedTaskDecision(
                            decision_index=decision_index,
                            task_id=item.task_id,
                            signal_id=proof_id,
                            anchor_id=anchor_id,
                        )
                    )
                    continue
                skipped_reasons.append(
                    f"Task {item.task_id} already matches this source; nothing to update."
                )
                continue
            if item.suggestion is not None:
                project = selected_project(
                    item.project.anchor_id, item.project.project_decision_index
                )
                if project is None:
                    raise ValueError(
                        "suggestion requires an applied active official Project"
                    )
                suggestion = TaskSuggestion(
                    reason=item.suggestion.reason,
                    suggested_owner_user_id=item.suggestion.suggested_owner_user_id,
                    suggested_owner_name=item.suggestion.suggested_owner_name,
                    responsibility_evidence=[
                        _resolve_project_citation(
                            store,
                            proof,
                            work_item=work_item,
                            db=db,
                            current_signal_id=current_signal_id,
                            prepared_sources=prepared_sources,
                        )
                        for proof in item.suggestion.responsibility_evidence
                    ],
                    basis_evidence=[
                        _resolve_project_citation(
                            store,
                            proof,
                            work_item=work_item,
                            db=db,
                            current_signal_id=current_signal_id,
                            prepared_sources=prepared_sources,
                        )
                        for proof in item.suggestion.basis_evidence
                    ],
                )
                result = service.record_suggestion(
                    RecordTaskSuggestion(
                        title=item.title or task_before.title,
                        signal=signal,
                        suggestion=suggestion,
                        project_anchor_id=project.canonical_anchor_id,
                        description=item.description
                        or (task_before.description if task_before is not None else ""),
                        task_id=item.task_id,
                    ),
                    _db=db,
                    prepared_sources=prepared_sources,
                )
            elif item.action == "record_candidate":
                result = service.record_candidate(
                    RecordCandidate(
                        title=item.title,
                        signal=signal,
                        description=item.description,
                        owner_user_id=owner_user_id,
                        owner_name=item.owner_name,
                        missing_evidence_json=json.dumps(
                            item.missing_evidence, ensure_ascii=False
                        ),
                        date_facts=date_facts,
                    ),
                    _db=db,
                )
            elif item.action == "create_task":
                result = service.record_formal_task(
                    RecordFormalTask(
                        title=item.title,
                        signal=signal,
                        formality=formality,
                        description=item.description,
                        owner_user_id=owner_user_id,
                        owner_name=item.owner_name,
                        owner_evidence_json=json.dumps(
                            owner_evidence, ensure_ascii=False
                        ),
                        date_facts=date_facts,
                    ),
                    _db=db,
                )
            else:
                assert item.task_id is not None
                if item.transition == "promote_candidate":
                    result = service.promote_candidate(
                        PromoteCandidate(
                            task_id=item.task_id,
                            signal=signal,
                            formality=formality,
                            owner_user_id=owner_user_id or None,
                            owner_name=item.owner_name or None,
                            owner_evidence_json=json.dumps(
                                owner_evidence, ensure_ascii=False
                            )
                            if owner_evidence
                            else None,
                            date_facts=date_facts,
                        ),
                        _db=db,
                    )
                elif item.transition == "apply_acceptance":
                    result = service.apply_acceptance(
                        ApplyAcceptance(
                            task_id=item.task_id,
                            signal=signal,
                            acceptance_is_explicit=True,
                            acceptance_polarity=AcceptancePolarity(
                                item.acceptance_polarity
                            ),
                            acceptance_excerpt=item.source_excerpt,
                            referenced_signal_id=item.acceptance_target_signal_id,
                            date_facts=date_facts,
                        ),
                        _db=db,
                        prepared_sources=prepared_sources,
                    )
                elif item.transition == "merge_identity":
                    proposal = item.identity_proposal
                    assert proposal is not None
                    _validate_identity_proposal(store, proposal, db=db,
                                                prepared_sources=prepared_sources)
                    identity = IdentityEvidence(
                        same_external_task_id=proposal.identity_evidence.basis
                        == "same_external_task_id",
                        explicit_source_reference=proposal.identity_evidence.basis
                        == "explicit_source_reference",
                        same_source_item_same_owner=proposal.identity_evidence.basis
                        == "same_source_item_same_owner",
                        same_deliverable=proposal.identity_evidence.basis
                        == "same_deliverable_owner_context_time",
                        same_owner=proposal.identity_evidence.basis
                        == "same_deliverable_owner_context_time",
                        same_context=proposal.identity_evidence.basis
                        == "same_deliverable_owner_context_time",
                        compatible_time_window=proposal.identity_evidence.basis
                        == "same_deliverable_owner_context_time",
                    )
                    result = service.merge_same_deliverable(
                        MergeBusinessTasks(
                            source_task_id=proposal.source_task_id,
                            target_task_id=proposal.target_task_id,
                            signal=signal,
                            identity_evidence=identity,
                            reason=proposal.reason,
                        ),
                        _db=db,
                    )
                else:
                    fields = {}
                    if item.title:
                        fields["title"] = item.title
                    if item.description:
                        fields["description"] = item.description
                    if owner_user_id or item.owner_name:
                        fields.update(
                            owner_user_id=owner_user_id,
                            owner_name=item.owner_name,
                            owner_evidence_json=json.dumps(
                                owner_evidence, ensure_ascii=False
                            ),
                        )
                    if item.status:
                        fields["status"] = BusinessTaskStatus(item.status)
                    if item.business_relevance:
                        fields["business_relevance"] = BusinessRelevance(
                            item.business_relevance
                        )
                    result = service.update_task(
                        UpdateBusinessTask(
                            task_id=item.task_id,
                            signal=signal,
                            date_facts=date_facts,
                            reason=item.update_summary
                            or "根据来源证据更新了任务字段。",
                            **fields,
                        ),
                        _db=db,
                    )
            if (
                not result.created
                and date_facts
                and (
                    item.action in {"record_candidate", "create_task"}
                    or item.transition == "promote_candidate"
                )
            ):
                existing_dates = db.execute(
                    f"""select date_type, value_at, raw_phrase, source_signal_id,
                              actor_kind, actor_user_id, actor_name
                       from ({business_action_projection('business_task_date_evidence')}) where task_id=?""",
                    (result.task_id,),
                ).fetchall()
                existing_date_effects = {
                    (
                        row["date_type"],
                        row["value_at"],
                        row["raw_phrase"],
                        row["source_signal_id"],
                        row["actor_kind"],
                        row["actor_user_id"],
                        row["actor_name"],
                    )
                    for row in existing_dates
                }
                proposed_date_effects = {
                    (
                        fact.date_type.value,
                        fact.value_at,
                        fact.raw_phrase,
                        result.signal_id,
                        fact.actor_kind.value,
                        fact.actor_user_id,
                        fact.actor_name,
                    )
                    for fact in date_facts
                }
                if not proposed_date_effects.issubset(existing_date_effects):
                    raise ValueError(
                        "replayed create/promotion has new date evidence; update the existing Task explicitly"
                    )
            task_id = result.task_id
            current_fact = store.get_business_task_signal_in_transaction(
                signal_id=result.signal_id, _db=db
            )
            assert current_fact is not None
            prepared_sources.add_current(current_fact, signal.evidence_text)
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
                store.complete_business_task_follow_ups(
                    business_task_id=task_id,
                    evidence_check_json=json.dumps(completion_evidence, ensure_ascii=False),
                    suppressed_reason=completion_evidence["reason"],
                    _db=db,
                )
                linked_todos = store.list_business_task_dingtalk_links(
                    business_task_id=task_id,
                    statuses=("creating", "active"),
                    limit=1,
                    _db=db,
                )
                if linked_todos:
                    store.enqueue_business_task_todo_sync_outbox(
                        operation_key=f"task-agent:{summary_input_id}:business-task:{task_id}:complete",
                        business_task_id=task_id,
                        operation="complete",
                        evidence_json=json.dumps(
                            completion_evidence, ensure_ascii=False
                        ),
                        _db=db,
                    )
            next_checks = (
                fact
                for fact in date_facts
                if fact.date_type is BusinessTaskDateType.NEXT_CHECK_AT
            )
            if (
                task_after is not None
                and task_after.stage.value == "formal"
                and task_after.status.value in {"open", "waiting"}
                and task_after.owner_user_id.strip()
                and work_item.source.conversation_id.strip()
                and work_item.context.source_conversation_kind.value
                in {"group", "direct"}
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
                    f"select 1 from ({business_action_projection('business_task_date_evidence')}) where task_id=? "
                    "and date_type='committed_deadline_at' and trim(value_at)<>'' limit 1",
                    (task_id,),
                ).fetchone()
                is not None
            ):
                store.enqueue_business_task_todo_sync_outbox(
                    operation_key=f"task-agent:{summary_input_id}:business-task:{task_id}:create",
                    business_task_id=task_id,
                    operation="create",
                    _db=db,
                )
            if (
                item.transition == "merge_identity"
                and item.identity_proposal is not None
            ):
                affected_task_ids.extend(
                    (
                        item.identity_proposal.source_task_id,
                        item.identity_proposal.target_task_id,
                    )
                )
            for relation in item.relation_proposals:
                from_task_id, to_task_id = relation.endpoints(task_id)
                resolution.add_relation(
                    from_task_id=from_task_id,
                    to_task_id=to_task_id,
                    relation_type=BusinessRelationType(relation.relation_type),
                    evidence_signal_id=result.signal_id,
                    status="proposed",
                    reason=relation.reason,
                    _db=db,
                )
            if item.cluster_proposal is not None:
                cluster = item.cluster_proposal
                if cluster.cluster_id is None:
                    cluster_id = resolution.create_cluster(
                        title=cluster.title or item.title,
                        task_ids=list(dict.fromkeys([*cluster.task_ids, task_id])),
                        _db=db,
                    )
                else:
                    cluster_id = cluster.cluster_id
                    if (
                        db.execute(
                            f"select 1 from ({business_action_projection('business_work_cluster_tasks')}) where cluster_id=? and task_id=?",
                            (cluster_id, task_id),
                        ).fetchone()
                        is None
                    ):
                        store.add_business_work_cluster_task_in_transaction(
                            cluster_id=cluster_id, task_id=task_id, _db=db
                        )
            else:
                cluster_id = None
            for anchor_match in item.anchor_match_proposals:
                resolution.propose_anchor_match(
                    task_id=task_id,
                    anchor_id=anchor_match.anchor_id,
                    evidence_signal_id=result.signal_id,
                    reason=anchor_match.reason,
                    _db=db,
                )
            applied_project_anchor_id, _ = apply_project_link(item, task_id)
            if cluster_id is not None and item.project is not None and item.project.project_decision_index is not None:
                registration = decision.project_decisions[item.project.project_decision_index].registration
                project = projects_by_index.get(item.project.project_decision_index)
                if registration is not None and registration.authority != "meeting_decision" and project is not None:
                    existing_candidate = db.execute(
                        f"select id from ({candidate_projection('project_candidate')}) "
                        "where cluster_id=? and title=? and status='proposed' limit 1",
                        (cluster_id, project.title),
                    ).fetchone()
                    if existing_candidate is not None:
                        changed_members = [int(row["task_id"]) for row in db.execute(
                            f"select member.task_id from ({business_action_projection('business_work_cluster_tasks')}) member "
                            f"left join ({business_action_projection('business_task_anchor_links')}) link on link.task_id=member.task_id "
                            "and link.anchor_id=? where member.cluster_id=? "
                            "and (link.id is null or link.status<>'confirmed' or link.active=0)",
                            (project.canonical_anchor_id, cluster_id),
                        )]
                        registration_signal_id = service._signal_id_or_create(
                            signal=_project_source_signal(work_item, native_provenance=source_native_provenance, provider_locator=source_provider_locator), db=db, now=service._now()
                        )
                        resolution.confirm_project_candidate(
                            candidate_id=int(existing_candidate["id"]), project_id=project.id,
                            evidence_signal_id=registration_signal_id, _db=db,
                        )
                        affected_task_ids.extend(changed_members)
                        project_links.update(
                            (int(link["task_id"]), project.canonical_anchor_id)
                            for link in db.execute(
                                f"select link.task_id from ({business_action_projection('business_task_anchor_links')}) link "
                                f"join ({business_action_projection('business_work_cluster_tasks')}) member on member.task_id=link.task_id "
                                "where member.cluster_id=? and link.anchor_id=? "
                                "and link.status='confirmed' and link.active=1",
                                (cluster_id, project.canonical_anchor_id),
                            )
                        )
            candidate = item.project_candidate_proposal
            if candidate is not None:
                existing = db.execute(
                    f"select id from ({candidate_projection('project_candidate')}) "
                    "where cluster_id=? and title=? and status='proposed' limit 1",
                    (candidate.cluster_id, candidate.title),
                ).fetchone()
                if existing is None:
                    resolution.propose_project(
                        cluster_id=candidate.cluster_id,
                        title=candidate.title,
                        reason=candidate.reason,
                        _db=db,
                    )
            confirmed = _confirmed_official_project_anchors(
                store, task_id=task_id, db=db
            )
            actual_anchor = (
                applied_project_anchor_id
                if applied_project_anchor_id is not None
                else next(iter(confirmed))
                if len(confirmed) == 1
                else None
            )
            applied_decisions.append(
                AppliedTaskDecision(
                    decision_index=decision_index,
                    task_id=task_id,
                    signal_id=result.signal_id,
                    anchor_id=actual_anchor,
                )
            )

        applied_by_index = {value.decision_index: value for value in applied_decisions}
        for assessment_index, assessment in enumerate(decision.project_assessments):
            project = selected_project(
                assessment.anchor_id, assessment.project_decision_index
            )
            if project is None:
                continue
            ids = set(assessment.task_ids)
            for index in assessment.decision_indexes:
                applied = applied_by_index.get(index)
                if applied is not None:
                    ids.add(applied.task_id)
                else:
                    task = decision.task_decisions[index]
                    if task.task_id is not None:
                        ids.add(
                            task.target_task_id
                            if task.transition == "merge_identity"
                            else task.task_id
                        )
            for task_id in ids:
                if (
                    project.canonical_anchor_id
                    not in _confirmed_official_project_anchors(
                        store, task_id=task_id, db=db
                    )
                ):
                    raise ValueError(
                        "supporting Task is not confirmed to the assessed Project"
                    )
            proofs = [
                *assessment.evidence,
                *(
                    assessment.attention_proposal.evidence
                    if assessment.attention_proposal
                    else []
                ),
            ]
            resolved = [
                _resolve_project_citation(
                    store,
                    proof,
                    work_item=work_item,
                    db=db,
                    current_signal_id=current_signal_id,
                    prepared_sources=prepared_sources,
                )
                for proof in proofs
            ]
            signal_ids = tuple(sorted({proof.signal_id for proof in resolved}))
            ProjectContextService(store).apply(
                project_id=project.id, context=None, signal_ids=signal_ids, db=db
            )
            if assessment.attention_proposal is not None:
                primary_signal_id = (
                    current_signal_id
                    if any(
                        proof.signal_id is None
                        for proof in assessment.attention_proposal.evidence
                    )
                    else resolved[0].signal_id
                )
                attention.append(
                    AppliedTaskAttention(
                        assessment_index=assessment_index,
                        assessment=assessment,
                        task_ids=tuple(sorted(ids)),
                        signal_id=primary_signal_id,
                        anchor_id=project.canonical_anchor_id,
                    )
                )

    recorded_run_id = None
    if _db is not None:
        apply(_db)
    else:
        with store.task_agent_domain_apply_transaction() as db:
            apply(db)
        if record_run:
            recorded_run_id = store.create_logical_run(
                'task_agent_runs',
                task_public_id=summary_input_id,
                codex_session_id=codex_session_id,
            )
    result = TaskAgentApplyResult(
        task_ids=tuple(task_ids),
        attention_proposals=tuple(attention),
        affected_task_ids=tuple(dict.fromkeys(affected_task_ids)),
        skipped_reasons=tuple(skipped_reasons),
        project_links=tuple(sorted(project_links)),
        applied_decisions=tuple(applied_decisions),
        applied_projects=tuple(applied_projects),
        current_signal_id=current_signal_id,
    )
    receipt = _projection_receipt(work_item, decision, result)
    result = replace(result, projection_receipt=receipt)
    if _db is None:
        if recorded_run_id is not None:
            _save_projection_receipt(store, recorded_run_id, receipt)
        receipt = _project_task_attention(
            store, result.attention_proposals, result.affected_task_ids, receipt=receipt,
            prepared_sources=prepared_sources,
        )
        receipt = _finalize_assessment_results(
            store,
            work_item=work_item,
            decision=decision,
            result=result,
            receipt=receipt,
        )
        result = replace(result, projection_receipt=receipt)
        if recorded_run_id is not None:
            _save_projection_receipt(store, recorded_run_id, receipt)
    return result


def _update_fields_restates_task(
    task: BusinessTask,
    item: TaskDecision,
    owner_user_id: str,
    *,
    attention_only: bool = False,
) -> bool:
    """True when every field the decision sets already has that value on the Task."""
    return (
        (not item.title or item.title == task.title)
        and (not item.description or item.description == task.description)
        and (not item.status or BusinessTaskStatus(item.status) is task.status)
        # "unknown" is the absence of a judgement, so restating it asserts nothing;
        # restating "relevant" normally confirms Task evidence; a supported
        # Attention-only proposal must leave the Task event history unchanged.
        and (
            not item.business_relevance
            or (
                BusinessRelevance(item.business_relevance) is BusinessRelevance.UNKNOWN
                and task.business_relevance is BusinessRelevance.UNKNOWN
            )
            or (
                attention_only
                and BusinessRelevance(item.business_relevance)
                is BusinessRelevance.RELEVANT
                and task.business_relevance is BusinessRelevance.RELEVANT
            )
        )
        and (
            not (owner_user_id or item.owner_name)
            or (
                owner_user_id == task.owner_user_id
                and item.owner_name == task.owner_name
            )
        )
    )


def _project_task_attention(
    store: AutoReplyStore,
    proposals: tuple[AppliedTaskAttention, ...],
    affected_task_ids: tuple[int, ...],
    *,
    receipt: TaskAttentionProjectionReceipt,
    prepared_sources: PreparedSources,
) -> TaskAttentionProjectionReceipt:
    applied_ids: set[int] = set()
    projection = BusinessAttentionProjection(store)
    for applied in proposals:
        proposal = applied.assessment.attention_proposal
        assert proposal is not None
        try:
            with store.business_task_transaction() as db:
                project = _official_project(store, applied.anchor_id, db=db)
                existing_card_id = getattr(applied.assessment, "existing_attention_id", None)
                _assert_prepared_attention_relationships(
                    store, prepared_sources, db,
                    card_ids=frozenset({existing_card_id}) if existing_card_id is not None else frozenset(),
                    project_ids=frozenset({project.id}),
                )
                task_ids = set(applied.task_ids)
                eligible = set(
                    projection._current_eligible_task_ids(
                        task_ids=applied.task_ids, anchor_id=applied.anchor_id, db=db
                    )
                )
                if eligible != task_ids:
                    raise ValueError(
                        "every supporting Task must be relevant, open/waiting and confirmed to this active Project"
                    )
                existing = (
                    store.get_business_attention_item_by_stable_key_in_transaction(
                        stable_key=f"project:{applied.anchor_id}", _db=db
                    )
                )
                if existing is not None:
                    old_ids = tuple(
                        link.task_id
                        for link in store.list_business_attention_tasks_in_transaction(
                            attention_item_id=existing.id, _db=db
                        )
                    )
                    task_ids.update(
                        projection._current_eligible_task_ids(
                            task_ids=old_ids, anchor_id=applied.anchor_id, db=db
                        )
                    )
                linked_ids = {
                    value.signal_id
                    for value in store.list_business_project_evidence_in_transaction(
                        project_id=project.id,
                        pinned_signal_ids=tuple(
                            proof.signal_id
                            if proof.signal_id is not None
                            else applied.signal_id
                            for proof in proposal.evidence
                        ),
                        _db=db,
                    )
                }
                verified = {}
                for proof in proposal.evidence:
                    signal_id = (
                        proof.signal_id
                        if proof.signal_id is not None
                        else applied.signal_id
                    )
                    signal, body = prepared_sources.require_body(
                        store, signal_id, db=db
                    )
                    if signal.source_ref != proof.source_ref:
                        raise ValueError(
                            "attention evidence signal/source_ref does not match"
                        )
                    if not source_is_observed(signal.source_type):
                        raise ValueError(
                            "attention evidence must be observed original source, not cited provenance"
                        )
                    if not source_contains_quote(
                        body, proof.source_excerpt
                    ):
                        raise ValueError(
                            "attention quote is absent from original source"
                        )
                    if signal.id not in linked_ids:
                        raise ValueError(
                            "attention evidence must be linked to the assessed Project"
                        )
                    entry = dict(
                        signal_id=signal.id,
                        source_ref=signal.source_ref,
                        source_excerpt=proof.source_excerpt,
                        source_time=signal.source_time,
                        source_link=json.loads(signal.context_json).get(
                            "source_link", ""
                        ),
                    )
                    verified[json.dumps(entry, ensure_ascii=False, sort_keys=True)] = (
                        entry
                    )
                effective = AttentionProposal(
                    stable_key=f"project:{applied.anchor_id}",
                    category=AttentionCategory(proposal.category),
                    title=proposal.title,
                    business_area="",
                    why_attention=proposal.why_attention,
                    current_state=proposal.current_state,
                    ceo_action=proposal.ceo_action,
                    anchor_id=applied.anchor_id,
                    task_ids=tuple(sorted(task_ids)),
                    evidence_signal_id=applied.signal_id,
                    assessment_json=json.dumps(
                        {
                            "assessment_basis": proposal.assessment_basis,
                            "material_trigger": proposal.material_trigger,
                            "inference": proposal.why_attention,
                            "evidence": [
                                verified[key]
                                for key in sorted(
                                    verified,
                                    key=lambda key: (
                                        verified[key]["signal_id"] != applied.signal_id,
                                        key,
                                    ),
                                )
                            ],
                        },
                        ensure_ascii=False,
                        sort_keys=True,
                    ),
                )
                attention_id = projection.upsert(effective, _db=db)
        except ValueError as exc:
            receipt.outcomes.append(
                TaskAttentionProjectionOutcome(
                    task_id=None,
                    assessment_index=applied.assessment_index,
                    anchor_id=applied.anchor_id,
                    status="rejected",
                    reason=str(exc),
                )
            )
        except Exception as exc:
            receipt.outcomes.append(
                TaskAttentionProjectionOutcome(
                    task_id=None,
                    assessment_index=applied.assessment_index,
                    anchor_id=applied.anchor_id,
                    status="error",
                    reason=str(exc),
                )
            )
        else:
            applied_ids.add(attention_id)
            receipt.outcomes.append(
                TaskAttentionProjectionOutcome(
                    task_id=None,
                    assessment_index=applied.assessment_index,
                    anchor_id=applied.anchor_id,
                    attention_id=attention_id,
                    status="applied",
                    reason="Attention proposal applied.",
                )
            )
    try:
        projection.recompute_for_tasks(affected_task_ids)
    except Exception as exc:
        receipt.recompute_error = str(exc)
    receipt.applied_count = len(applied_ids)
    failed = bool(
        receipt.recompute_error
        or any(value.status != "applied" for value in receipt.outcomes)
    )
    receipt.status = (
        "partial"
        if failed and applied_ids
        else "failed"
        if failed
        else "completed"
        if receipt.proposal_count
        else "no_proposal"
    )
    return receipt


def _projection_receipt(
    work_item: WorkItem,
    decision: TaskAgentDecision,
    result: TaskAgentApplyResult,
) -> TaskAttentionProjectionReceipt:
    registry_rows = None
    if work_item.source.type in {
        WorkItemSourceType.MANAGEMENT_WEEKLY_REPORT,
        WorkItemSourceType.PROJECT_WEEKLY_REPORT,
        WorkItemSourceType.DEPARTMENT_WEEKLY_REPORT,
    }:
        registry_rows = sum(
            bool(_report_project_registry_title(work_item, line))
            for line in _report_markdown(work_item).splitlines()
            if line.strip()
        )
    applied_indexes = {entry.assessment_index for entry in result.attention_proposals}
    return TaskAttentionProjectionReceipt(
        status="pending",
        source_type=work_item.source.type.value,
        task_decision_count=len(decision.task_decisions),
        project_link_count=len(result.project_links),
        registry_row_count=registry_rows,
        proposal_count=sum(
            assessment.attention_proposal is not None
            for assessment in decision.project_assessments
        ),
        project_decisions=[
            TaskProjectDecisionResult(
                project_decision_index=entry.project_decision_index,
                project_id=entry.project_id,
                anchor_id=entry.anchor_id,
                revision_id=entry.revision_id,
                signal_ids=list(entry.signal_ids),
            )
            for entry in result.applied_projects
        ],
        task_decisions=[
            TaskDecisionResult(
                decision_index=entry.decision_index,
                task_id=entry.task_id,
                signal_id=entry.signal_id,
                anchor_id=entry.anchor_id,
            )
            for entry in result.applied_decisions
        ],
        outcomes=[
            TaskAttentionProjectionOutcome(
                task_id=None,
                assessment_index=index,
                anchor_id=assessment.anchor_id,
                status="rejected",
                reason="proposal has no applied Project identity",
            )
            for index, assessment in enumerate(decision.project_assessments)
            if assessment.attention_proposal is not None
            and index not in applied_indexes
        ],
    )


def _finalize_assessment_results(
    store: AutoReplyStore,
    *,
    work_item: WorkItem,
    decision: TaskAgentDecision,
    result: TaskAgentApplyResult,
    receipt: TaskAttentionProjectionReceipt,
) -> TaskAttentionProjectionReceipt:
    """Read back only identities actually applied or verified before application."""
    projects = {
        entry.project_decision_index: entry for entry in result.applied_projects
    }
    tasks = {entry.decision_index: entry for entry in result.applied_decisions}
    outcomes = {entry.assessment_index: entry for entry in receipt.outcomes}
    values = []
    for index, assessment in enumerate(decision.project_assessments):
        applied_project = projects.get(assessment.project_decision_index)
        anchor_id = (
            applied_project.anchor_id
            if applied_project is not None
            else assessment.anchor_id
        )
        task_ids = set(assessment.task_ids if anchor_id is not None else ())
        if anchor_id is not None:
            for task_index in assessment.decision_indexes:
                applied_task = tasks.get(task_index)
                if applied_task is not None:
                    task_ids.add(applied_task.task_id)
                else:
                    task = decision.task_decisions[task_index]
                    task_id = (
                        task.target_task_id
                        if task.transition == "merge_identity"
                        else task.task_id
                    )
                    if task_id is not None:
                        with store._connect() as db:
                            if anchor_id in _confirmed_official_project_anchors(
                                store, task_id=task_id, db=db
                            ):
                                task_ids.add(task_id)
        card = (
            store.get_business_attention_item(assessment.existing_attention_id)
            if assessment.existing_attention_id is not None
            else None
        )
        attention_id = card.id if card is not None else None
        if card is not None:
            task_ids.update(
                link.task_id for link in store.list_business_attention_tasks(card.id)
            )
        status, reason = (
            "recorded",
            "Assessment recorded; no Attention application requested.",
        )
        if anchor_id is None:
            reason = (
                "Assessment recorded without an applied Project or Attention identity."
            )
        if assessment.outcome == "needs_attention":
            if assessment.attention_proposal is not None:
                outcome = outcomes.get(index)
                if outcome is not None:
                    status, reason = outcome.status, outcome.reason
                    if outcome.attention_id is not None:
                        attention_id = outcome.attention_id
                else:
                    status, reason = (
                        "error",
                        "Attention application result is unavailable.",
                    )
            elif card is not None:
                status, reason = "existing", "Existing Attention card verified."
            else:
                status, reason = "error", "Attention application result is unavailable."
        if receipt.recompute_error and task_ids.intersection(result.affected_task_ids):
            status, reason = (
                "error",
                f"{reason}; recompute_error: {receipt.recompute_error}",
            )
        citations = []
        for proof in assessment.evidence:
            signal_id = (
                proof.signal_id
                if proof.signal_id is not None
                else result.current_signal_id
            )
            signal = (
                store.get_business_task_signal(signal_id)
                if signal_id is not None
                else None
            )
            citations.append(
                TaskAttentionVerifiedCitation(
                    signal_id=signal.id if signal is not None else None,
                    source_ref=proof.source_ref,
                    source_excerpt=proof.source_excerpt,
                    source_time=signal.source_time
                    if signal is not None
                    else work_item.source.created_at or "",
                    source_link=json.loads(signal.context_json).get("source_link", "")
                    if signal is not None
                    else "",
                )
            )
        values.append(
            TaskAttentionAssessmentResult(
                assessment_index=index,
                anchor_id=anchor_id,
                task_ids=sorted(task_ids),
                attention_id=attention_id,
                status=status,
                reason=reason,
                evidence=citations,
            )
        )
    receipt.project_assessments = values
    return receipt


def _save_projection_receipt(
    store: AutoReplyStore,
    run_id: int,
    receipt: TaskAttentionProjectionReceipt,
) -> None:
    try:
        store.record_task_agent_projection(run_id, receipt.model_dump_json())
    except Exception:
        LOGGER.exception(
            "Task committed but projection receipt could not be saved run_id=%s", run_id
        )


def _task_owner_identity_evidence(task) -> dict[str, object]:
    try:
        value = json.loads(task.owner_evidence_json)
    except (TypeError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _versioned_dingtalk_document_ref(source_ref: str) -> tuple[str, str]:
    prefix = "dingtalk-doc:"
    version_marker = "#sha256="
    if not source_ref.startswith(prefix):
        return "", ""
    node, separator, digest = source_ref[len(prefix) :].partition(version_marker)
    if (
        not separator
        or not node.strip()
        or len(digest) != 64
        or any(character not in "0123456789abcdef" for character in digest)
    ):
        return "", ""
    return node, digest


def _canonical_report_item(value: str) -> str:
    lines = [line.strip() for line in value.splitlines() if line.strip()]
    if len(lines) != 1:
        return ""
    line = lines[0]
    if len(line) >= 2 and line[0] in "-*+" and line[1].isspace():
        return line[2:].strip()
    marker, separator, remainder = line.partition(" ")
    if separator and len(marker) > 1 and marker[-1] in ".)":
        if marker[:-1].isdigit():
            return remainder.strip()
    return line


def _report_item_source_body(evidence_text: str) -> str:
    try:
        payload = json.loads(evidence_text)
    except (TypeError, json.JSONDecodeError):
        return evidence_text
    if isinstance(payload, dict) and isinstance(payload.get("markdown"), str):
        return payload["markdown"]
    return ""


def _report_item_occurrences(source_body: str, expected_item: str) -> int:
    return sum(
        _canonical_report_item(line) == expected_item
        for line in source_body.splitlines()
    )


def _validate_identity_proposal(
    store, proposal, *, db: sqlite3.Connection | None,
    prepared_sources: PreparedSources,
) -> None:
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
        return prepared_sources.require_fact(store, signal_id, db=db)

    def get_task_source_signal(task_id: int, source_ref: str, source_type: str):
        if db is None:
            return store.get_business_task_signal_for_task_source_ref(
                task_id=task_id,
                source_ref=source_ref,
                source_type=source_type,
            )
        return store.get_business_task_signal_for_task_source_ref_in_transaction(
            task_id=task_id,
            source_ref=source_ref,
            source_type=source_type,
            _db=db,
        )

    proof = proposal.identity_evidence
    source_task = get_task(proposal.source_task_id)
    target_task = get_task(proposal.target_task_id)
    if source_task is None or target_task is None:
        raise ValueError("identity proposal Tasks must exist")
    if proof.source_signal_id not in {
        row.signal_id for row in get_evidence(source_task.id)
    }:
        raise ValueError("identity source signal is not linked to the source Task")
    if proof.target_signal_id not in {
        row.signal_id for row in get_evidence(target_task.id)
    }:
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
            raise ValueError(
                "same_external_task_id identity evidence does not match source records"
            )
        return
    if proof.basis == "explicit_source_reference":
        if not (
            source_signal.source_ref == target_context.get("reply_to_source_ref")
            or target_signal.source_ref == source_context.get("reply_to_source_ref")
        ):
            raise ValueError(
                "explicit_source_reference identity evidence does not match source records"
            )
        return
    if proof.basis == "same_source_item_same_owner":
        source_owner = _task_owner_identity_evidence(source_task)
        target_owner = _task_owner_identity_evidence(target_task)
        source_node, source_digest = _versioned_dingtalk_document_ref(
            source_signal.source_ref
        )
        target_node, target_digest = _versioned_dingtalk_document_ref(
            target_signal.source_ref
        )
        source_owner_ref = str(source_owner.get("source_ref") or "")
        target_owner_ref = str(target_owner.get("source_ref") or "")
        source_owner_signal = get_task_source_signal(
            source_task.id, source_owner_ref, source_signal.source_type
        )
        target_owner_signal = get_task_source_signal(
            target_task.id, target_owner_ref, target_signal.source_type
        )
        if db is None:
            raise ValueError("identity original check requires domain transaction")
        _, source_original = prepared_sources.require_body(
            store, source_signal.id, db=db
        )
        _, target_original = prepared_sources.require_body(
            store, target_signal.id, db=db
        )
        source_item = _canonical_report_item(str(source_owner.get("excerpt") or ""))
        target_item = _canonical_report_item(str(target_owner.get("excerpt") or ""))
        source_body = _report_item_source_body(source_original).strip()
        target_body = _report_item_source_body(target_original).strip()
        source_owner_body = (
            _report_item_source_body(prepared_sources.require_body(
                store, source_owner_signal.id, db=db
            )[1]).strip()
            if source_owner_signal is not None
            else ""
        )
        target_owner_body = (
            _report_item_source_body(prepared_sources.require_body(
                store, target_owner_signal.id, db=db
            )[1]).strip()
            if target_owner_signal is not None
            else ""
        )
        source_owner_node, source_owner_digest = _versioned_dingtalk_document_ref(
            source_owner_ref
        )
        target_owner_node, target_owner_digest = _versioned_dingtalk_document_ref(
            target_owner_ref
        )
        owner_names = (
            source_task.owner_name.strip().casefold(),
            target_task.owner_name.strip().casefold(),
            str(source_owner.get("name") or "").strip().casefold(),
            str(target_owner.get("name") or "").strip().casefold(),
        )
        owner_ids = {
            value
            for value in (
                source_task.owner_user_id.strip(),
                target_task.owner_user_id.strip(),
                str(source_owner.get("user_id") or "").strip(),
                str(target_owner.get("user_id") or "").strip(),
            )
            if value
        }
        if not (
            source_signal.source_type == target_signal.source_type
            and source_node
            and source_node == target_node
            and source_owner_signal is not None
            and target_owner_signal is not None
            and source_owner_signal.source_ref == source_owner_ref
            and target_owner_signal.source_ref == target_owner_ref
            and source_owner_signal.source_type == source_signal.source_type
            and target_owner_signal.source_type == target_signal.source_type
            and source_owner_node == source_node
            and target_owner_node == target_node
            and source_digest == hashlib.sha256(source_body.encode("utf-8")).hexdigest()
            and target_digest == hashlib.sha256(target_body.encode("utf-8")).hexdigest()
            and source_owner_digest
            == hashlib.sha256(source_owner_body.encode("utf-8")).hexdigest()
            and target_owner_digest
            == hashlib.sha256(target_owner_body.encode("utf-8")).hexdigest()
            and source_item
            and source_item == target_item
            and _report_item_occurrences(source_owner_body, source_item) == 1
            and _report_item_occurrences(target_owner_body, target_item) == 1
            and _report_item_occurrences(
                source_body, source_item
            )
            == 1
            and _report_item_occurrences(
                target_body, target_item
            )
            == 1
            and all(owner_names)
            and len(set(owner_names)) == 1
            and len(owner_ids) <= 1
        ):
            raise ValueError(
                "same_source_item_same_owner identity evidence must prove the same "
                "unique versioned source item and owner"
            )
        return
    same_owner = (
        bool(source_task.owner_name.strip())
        and source_task.owner_name.casefold() == target_task.owner_name.casefold()
    )
    same_context = (
        bool(source_signal.conversation_id)
        and source_signal.conversation_id == target_signal.conversation_id
    )
    same_deliverable = (
        source_task.title.strip().casefold() == target_task.title.strip().casefold()
    )
    if not (same_deliverable and same_owner and same_context):
        raise ValueError(
            "same-deliverable identity evidence does not match canonical Task and source fields"
        )
    try:
        source_time = datetime.fromisoformat(
            source_signal.source_time.replace("Z", "+00:00")
        )
        target_time = datetime.fromisoformat(
            target_signal.source_time.replace("Z", "+00:00")
        )
    except (TypeError, ValueError) as exc:
        raise ValueError(
            "same-deliverable identity evidence requires parseable source times"
        ) from exc
    source_time = (
        source_time.replace(tzinfo=timezone.utc)
        if source_time.tzinfo is None
        else source_time
    )
    target_time = (
        target_time.replace(tzinfo=timezone.utc)
        if target_time.tzinfo is None
        else target_time
    )
    if abs((source_time - target_time).total_seconds()) > 30 * 24 * 60 * 60:
        raise ValueError(
            "same-deliverable identity source times exceed the matching window"
        )


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
        source_reader = store.business_source_reader(provider_reader=dws)
        semantic_context = retrieve_task_semantic_context(store, work_item, source_reader=source_reader)
        context_prompt = render_task_semantic_context(semantic_context)
        active_run_id = store.claim_logical_run(
            'task_agent_runs',
            work_input.id,
        )
        memory_issue = memory_connector_config_issue()
        repair_context = None
        prepared_sources = None
        for repair_round in range(TASK_DECISION_REPAIR_ROUNDS + 1):
            if session_lease is not None:
                session_lease.assert_owned()
            decision = runner.decide(
                work_item,
                context_prompt,
                memory_issue=memory_issue,
                run_id=active_run_id,
                session_scope_id=TASK_AGENT_SESSION_SCOPE_ID,
                repair_round=repair_round,
                repair_context=repair_context,
            )
            decision = _canonicalize_current_source_provenance(
                decision, work_item=work_item
            )
            try:
                _validate_task_agent_decision(decision, work_item=work_item, now=now)
                try:
                    prepared_sources = _prepare_decision_sources(store, decision, source_reader=source_reader)
                except ValueError as exc:
                    raise RepairableTaskDecisionValidationError(str(exc)) from exc
                with store._connect() as db:
                    try:
                        _assert_prepared_relationships(store, prepared_sources, db)
                        _validate_stored_project_assessments(
                            store, decision, work_item=work_item, db=db,
                            prepared_sources=prepared_sources,
                        )
                    except ValueError as exc:
                        raise RepairableTaskDecisionValidationError(str(exc)) from exc
            except RepairableTaskDecisionValidationError as exc:
                if repair_round == TASK_DECISION_REPAIR_ROUNDS:
                    raise TaskDecisionRepairExhausted(str(exc)) from exc
                repair_context = (
                    "Decision validation rejected the previous candidate before any "
                    "domain writes. Correct this evidence error without inventing facts: "
                    + str(exc)
                    + "\nPrevious candidate:\n"
                    + _json_dumps(decision)
                )
                continue
            break
        session_id = getattr(runner.codex, "last_session_id", None) or ""
        source_native, source_provider = _work_item_source_coordinates(store, runner, work_item)
        if session_lease is not None:
            session_lease.assert_owned()
        crm_customer_lookups = {
            index: lookup_account_customers(project.crm_customer_label)
            for index, project in enumerate(decision.project_decisions)
            if project.crm_customer_label
        }
        assert prepared_sources is not None
        with store.task_agent_domain_apply_transaction() as db:
            apply_result = apply_task_agent_decision(
                store,
                summary_input_id=work_input.id,
                work_item=work_item,
                decision=decision,
                codex_session_id=session_id,
                source_native_provenance=source_native, source_provider_locator=source_provider,
                record_run=False,
                now=now,
                crm_customer_lookups=crm_customer_lookups,
                _db=db,
                prepared_sources=prepared_sources,
            )
            if (
                apply_result.task_ids
                or apply_result.applied_projects
                or decision.project_assessments
            ):
                store.mark_work_summary_input_done(work_input.id, _db=db)
            else:
                store.mark_work_summary_input_skipped(
                    work_input.id,
                    "; ".join(apply_result.skipped_reasons)
                    or "No source-grounded Project or Task decision.",
                    _db=db,
                )
            store.finish_logical_run(
                'task_agent_runs',
                active_run_id,
                status="completed",
                codex_session_id=session_id,
                _db=db,
            )
            receipt = apply_result.projection_receipt
            assert receipt is not None
            store.record_task_agent_projection(
                active_run_id, receipt.model_dump_json(), _db=db
            )
        committed_run_id = active_run_id
        active_run_id = None
    except Exception as exc:
        if active_run_id is not None:
            store.finish_logical_run(
                'task_agent_runs',
                active_run_id,
                status="failed",
                error=str(exc),
            )
        store.mark_work_summary_input_failed(work_input.id, str(exc))
        raise
    try:
        receipt = _project_task_attention(
            store,
            apply_result.attention_proposals,
            apply_result.affected_task_ids,
            receipt=receipt,
            prepared_sources=prepared_sources,
        )
        receipt = _finalize_assessment_results(
            store,
            work_item=work_item,
            decision=decision,
            result=apply_result,
            receipt=receipt,
        )
        _save_projection_receipt(store, committed_run_id, receipt)
    except Exception:
        LOGGER.exception(
            "Task committed but projection did not finish run_id=%s", committed_run_id
        )
