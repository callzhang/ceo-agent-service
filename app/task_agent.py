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
    task_agent_output_schema,
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
from app.task_source_documents import (
    source_contains_quote,
    source_document_key,
    source_is_observed,
)
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
    }
)
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
    ) -> TaskAgentDecision:
        return self.codex.decide(
            prompt=build_task_agent_prompt(
                work_item,
                candidate_prompt,
                memory_issue=memory_issue,
            ),
            workload_key=(
                str(run_id) if repair_round == 0
                else f"{run_id}:decision_repair.{repair_round}"
            ),
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
                    output_schema_path=TASK_AGENT_DECISION_SCHEMA_PATH,
                    use_output_schema=True,
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
        "the same Agent turn and return exactly one valid TaskAgentDecision "
        "JSON object.\n\n"
        f"Problems in the previous output:\n{detail}\n\n"
        "Rules that must hold:\n"
        "- project_decisions, task_decisions and project_assessments are all required (0..N). Return exactly one assessment for every relevant business Project or Project clue in the current source and current Tasks' confirmed Project links, including every separate Project row in a multi-Project report, whether or not this output emitted a selector for it. Return one outcome, concrete reason, and original evidence for each. Semantic coverage is not limited to structured selectors. Every attention_proposal belongs to its needs_attention assessment, with optional actual Task membership; zero Tasks is valid. Project selectors index project_decisions, never task_decisions. Use [] assessments only when there is no relevant Project or clue, with a nonblank update_summary.\n"
        "- Every current-source citation must be an exact contiguous quote from the immutable Work Item (a visible raw range or one decoded JSON string leaf), with the exact current source_ref and null signal_id. Project registration.source_excerpt must itself quote the current passage that defines that Project; never paraphrase it or quote a different source. Assess every Project separately when a source contains multiple Projects.\n"
        "- Historical Project/context/Attention evidence must use the exact quote and source_ref from the delivered original observed Signal. Do not reconstruct or paraphrase an earlier statement from a later summary. If the original quote is unavailable, omit that historical claim, use only verified current evidence, and state what cannot be confirmed.\n"
        "- For project_assessments, use exactly one selector: if this output has a matching Project decision, use its project_decision_index; otherwise use anchor_id for a known registered Project; use neither only for an unresolved Project clue with insufficient_evidence. Never provide both anchor_id and project_decision_index.\n"
        "- Include one matching assessment for every project_decisions index: project_decisions[i] must have exactly one project_assessment whose project_decision_index is i (or whose anchor_id matches that existing Project). Do not omit a report row because another Project is also assessed.\n"
        "- Never include a skip decision in decision_indexes; supporting indexes may identify only a real candidate, create, or update Task decision that belongs to this Project and directly supports this specific Project assessment. When a Task decision updates the same concrete Project work described by the assessment, include that decision index as support, including for a not_needed progress assessment. An existing Task belongs in task_ids only when it directly supports the assessment. A Task being linked to the Project is not enough: completed or unrelated Project Tasks are not members.\n"
        "- When retaining an existing_attention_id, cite an exact stored evidence item from current_project_attention.assessment_json.evidence with the same signal_id, source_ref and source_excerpt; new current evidence must not replace the card's stored original proof. Copy supporting membership only from the actual current_project_attention card. If its delivered task_ids are empty, keep assessment.task_ids and decision_indexes empty; do not add any Task to that existing card, even a same-risk candidate suggested on an earlier turn. This keeps the stored Attention membership unchanged; the Task may still remain a separate Project-linked candidate.\n"
        "- project_link_evidence requires a Project selector. Include evidence only when task.project selects the registered Project; for a standalone Task omit both fields.\n"
        "- status and business_relevance may only change through update_fields: set transition=update_fields when either field changes. They remain top-level fields, not a nested update_fields object; never set them under another transition.\n"
        "- New, record_candidate and skip decisions must leave status and business_relevance unset. For an existing Task, set transition=update_fields to change either field; do not try to set a status or relevance during creation.\n"
        "- When promoting a suggestion, omit the suggestion field; the saved Task keeps its existing suggestion history. A suggestion field is valid only when recording or updating a candidate.\n"
        "- project_link_evidence requires a Project selector. Include evidence only when task.project selects the registered Project; for a standalone Task omit both fields.\n"
        "- Return the TaskAgentDecision envelope with task_decisions (0..N); "
        "every non-skip item needs a source_excerpt (a sentence of the source), source_ref and a locator (source_link when there is one, otherwise source_description) "
        "(evidence_origin says whether it is the current Work Item, an earlier session turn, or memory provenance).\n"
        "- A formal assignment requires an explicit owner and authorized "
        "assignment source. Owner evidence alone does not prove authority.\n"
        "- For an existing Task update, keep commitment_status unchanged unless the named owner explicitly accepts, disputes, completes or cancels it in the source. Work progress, continued handling, receiving materials or updating the Project does not prove acceptance. Set acceptance polarity only with apply_acceptance and verified owner/reply evidence; otherwise omit acceptance fields.\n"
        "- For a display-only suggestion, keep owner_name/owner_user_id empty, owner_evidence empty, and owner_kind/owner_relation unset (null or omitted); also leave formal_basis, acceptance fields, dates, status and business_relevance unset. Put a proposed person only in suggestion.suggested_owner_name/user_id, backed by the cited Project responsibility.\n"
        "- Return every list-valued field as an array; use [] when empty, never null (including decision_indexes, task_ids, todo_changes, follow_up_changes and search_trace).\n"
        "- A concrete action and expected result stated by the current source is normally a source-origin Task, even if its metadata does not authorize a formal assignment. Exception: routine milestones and next steps in the normal Project sequence remain Project facts, not Task candidates, even when phrased as actions. A named person responsible to verify a specific, independently tracked matter and report back is an action; a bare duty-area description such as 'responsible for reconciliation' is not. Preserve a clearly named responsible person as source-reported owner evidence without inferring acceptance; do not relabel that human-stated action as an Agent suggestion. Use suggestion only when the action itself is inferred and absent from the source.\n"
        "- When a needs_attention Project has a material unresolved impact and a directly relevant saved Project responsibility, create one display-only candidate next-step Task for that role even when the current message states no assignment or explicit action. Infer a concrete action from the risk; do not say there is no action merely because the source does not spell out the next step. An existing Task suppresses that suggestion only if it addresses the same unresolved risk; a completed or unrelated Project deliverable does not.\n"
        "- A named person's assignment remaining unaccepted is not by itself a material Project risk when current evidence shows the work is progressing and no meaningful impact or dispute; preserve assigned_unaccepted without generating Attention solely for that status.\n"
        "- A bare responsibility clause (for example, a person being responsible for an area) is ProjectContext only, not a source Task or candidate. Create a Task only for an explicitly stated, independently completable deliverable/action, or a separate actionable suggestion required by an evidenced material Project risk.\n"
        "- The clause X负责Y by itself remains a Project responsibility when Y is only a duty area (for example, 王五负责商务对账). When Y names a specific independently completable action and result (for example, 李四负责核实客户付款排期并反馈), preserve it as a source-origin Task; lack of an authorized meeting action record makes it a candidate, not a formal assignment.\n"
        "- When overall-owner evidence conflicts, keep overall_owner=null and record the competing claims and challenge as sourced facts. State plainly in a Project fact that the owner remains in conflict; do not merely imply the conflict. Do not move candidate overall owners into responsibilities; that list contains only independently evidenced, distinct work responsibilities. Preserve unchanged responsibilities such as a separate deliverable owner.\n"
        "- State explicitly in a Project fact: 总体负责人存在冲突，仍待确认. Evidence that only says a transfer is not confirmed or must be verified is not enough; preserve the competing source claims as separate evidence.\n"
        "- A person explicitly identified as the Project's overall accountable owner belongs in overall_owner, not responsibilities. In Chinese, an explicit description such as 张三总负责交付验收 denotes that role; keep 总 out of the person's name.\n"
        "- Before suggesting another next-step Task for a Project risk, check current linked Tasks. If an existing actionable Task already addresses that risk, use its existing Task ID as the supporting next step and do not add a duplicate monitoring/evaluation suggestion.\n"
        "- For project_link_evidence, when the Project title and Task action occur in different parts of the current source, cite one exact contiguous span from the title occurrence through the action and include the intervening source text verbatim; never splice nonadjacent excerpts into one quotation.\n"
        "- Any non-empty owner_name or owner_user_id requires owner_evidence. "
        "Normally it has source_ref and excerpt containing every named person. "
        "When authoritative memory or session context is explicitly bound to "
        "the current source, it may instead include linked_source_ref equal to "
        "the current source_ref, a stable episode_id, and memory_excerpt with "
        "the explicit owner-action relation. Topical similarity is insufficient.\n"
        "- apply_acceptance requires accepted polarity, an explicitly cited "
        "assignment signal, and a verified reply-to source reference; never "
        "infer or manufacture that link.\n"
        "- Memory is background only unless its provenance is explicitly bound "
        "to this source. When memory_recall is available, follow the exact "
        "episode or session provenance; for owner identity, use a live directory "
        "read and keep only an ID that maps to the identified owner.\n"
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
    scheduled_prompt = (
        f"## Scheduled Consumer Prompt\n{scheduled_consumer.prompt}\n"
        if scheduled_consumer is not None
        else ""
    )
    work_item_payload = work_item.model_dump(mode="json")
    work_item_payload.pop("summary")
    scheduled_payload = work_item_payload.get("scheduled_consumer")
    if isinstance(scheduled_payload, dict):
        scheduled_payload.pop("skill_protocol", None)
    work_item_json = json.dumps(work_item_payload, ensure_ascii=False, indent=2)
    effective_current_time = (
        current_time.strip() or datetime.now(timezone.utc).isoformat()
    )
    decision_schema = json.dumps(
        task_agent_output_schema(), ensure_ascii=False, indent=2
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

Tool-use boundary (prompt guidance): use connected CLI/API/MCP tools only for
read-only discovery. Do not create, update, delete, send, or complete external records.
For Memory MCP specifically, you may retrieve existing context, but must never call
memory_connector.memory_write.
Return structured local changes; the service applies supported operations.
This prompt does not technically disable write-capable tools and is not an enforced
permission boundary.

Current execution time: {effective_current_time}
Memory connector status: {_memory_connector_prompt_status(memory_issue)}
The shared logical session preserves context; runtime routes retain separate native
sessions and the native CLI manages compaction. Current source authority takes precedence.

Current Work Item JSON:
{work_item_json}
Source body: current_work_item.document_id in the semantic context below;
read its visible_ranges. Offsets are character ranges in that exact version,
not a claim that an omitted middle or another version was read.
Current semantic Project/Task context (rank is context, never authority):
{candidate_prompt}

Apply the Skill before returning:
- Read original Project definitions, current roles/facts and Tasks together.
  Preserve genuinely standalone Tasks; do not turn a department/topic/customer or
  small unrelated action into an official Project.
- project_decisions registers a confirmed report registry Project or explicit
  meeting decision, or updates an existing active anchor. Preserve exact source
  title, reference and reporting period. Chats/emails can supplement known Project
  facts, not create official identity by topical similarity.
  Project title contains only the entity name, not its status or action. For
  example, in “甲客户一期交付项目正式启动”, keep “甲客户一期交付项目” as the
  title and store “正式启动” as a fact when relevant.
  For reports containing multiple Projects, assess each Project separately;
  do not let one Project's decision or assessment cover another report row.
  Adopt the exact current authoritative Project definition with registration to register or reuse.
  A different stored name cannot replace that definition merely because the action uses its shorter name.
  A Project may have an optional CRM customer. Only set crm_customer_label with
  crm_customer_evidence when the source explicitly names the customer; a clear
  complete customer prefix plus distinct Project work may be cited as the label.
  Do not put a customer ID or display name into the Project title, and do not
  copy a customer onto Tasks. The service performs a read-only CRM name
  resolution. Every result is an unconfirmed candidate, including a single
  returned result; a person must explicitly confirm the Project association.
- context is a complete current snapshot: one overall owner and responsible result,
  other people each with a distinct responsibility. Unknown owner is null.
  A bare responsibility clause (a person responsible for a business area) is
  ProjectContext only, not a Task or candidate. Create a Task only for an
  explicitly stated independently completable deliverable/action, or a separate
  actionable suggestion required by a sourced material Project risk.
  The clause X负责Y remains a Project responsibility when Y is only a duty area
  (for example, “王五负责商务对账”). When Y names a specific independently
  completable action and result (for example, “李四负责核实客户付款排期并反馈”),
  preserve it as a source-origin Task; without an authorized meeting action record,
  it is a candidate, not a formal assignment.
  When overall-owner evidence conflicts, keep overall_owner null and preserve
  the competing claims and challenge as sourced facts. Do not reclassify the
  competing overall-owner candidates as responsibilities; responsibilities are
  only independently evidenced, distinct work duties. Preserve other unchanged
  responsibilities such as a separately owned deliverable.
  State explicitly in a Project fact: 总体负责人存在冲突，仍待确认. Do not
  replace this with only “尚未形成一致确认” or “仍需核实”; preserve the
  competing source claims as separate evidence.
  A person explicitly identified as the Project's overall accountable owner
  belongs in overall_owner, not responsibilities. In Chinese, a description such
  as “张三总负责交付验收” denotes that role; keep “总” out of the person's name.
  Keep unchanged roles' original citations; never replace them with the new message.
  In Chinese source wording, separate a person's name from a trailing rank/honorific
  such as "总"; store only the person's name and keep the role in responsibility.
  If overall-owner evidence is explicitly disputed, state the unresolved conflict
  directly in a Project fact, preserve each competing claim's original evidence,
  and keep `overall_owner` null.
  Read the saved ProjectContext together with this source and return the complete
  current ProjectContext whenever new facts or roles are learned. Retain unchanged
  facts/roles with their original citations and add or update only what this source
  establishes; do not omit prior context or cite an unrelated current message.
- Task.project uses anchor_id or project_decision_index (index into project_decisions).
  project_link_evidence proves a new association and requires that Project selector;
  for a standalone Task omit both fields, and for an existing confirmed association
  reuse the actual link without new proof.
- For project_assessments, use exactly one selector: anchor_id for a known registered
  Project when this output has no matching Project decision, project_decision_index
  for the matching Project decision in this output, or neither only for an unresolved
  Project clue with insufficient_evidence. Never provide both anchor_id and
  project_decision_index.
  Include one matching assessment for every project_decisions index:
  project_decisions[i] must have exactly one assessment with project_decision_index=i
  (or an anchor_id matching that existing Project). Do not omit a report row
  because another Project is also assessed.
- Actual human work requires its assignment/commitment proof. For inferred next steps,
  use record_candidate or existing-ID update_fields with suggestion:
  suggested_owner_name/user_id plus responsibility_evidence and basis_evidence.
  A proposed person may be absent from this message when sourced Project/org roles
  establish the relevant duty. The saved single overall_owner may be the proposed
  owner for a project-wide coordination action. Actual owner fields, assignment metadata, formal basis,
  typed dates and status remain unset for a pure suggestion. Keep `owner_kind` and
  `owner_relation` unset, with empty `owner_evidence`; put the proposed person only
  in `suggestion.suggested_owner_name/user_id`. It is display-only.
  A Project risk may need Attention without a Task. Create a display-only Task
  suggestion only when a concrete next action is warranted and a saved, sourced
  Project responsibility supports the suggested person and relevant duty, or the
  single overall_owner is suitable for a project-wide coordination action. If no
  Project role supports an actionable owner, keep the Project risk in Attention
  without inventing a task, owner, monitoring item or deadline. Do not create a
  suggestion for routine progress, a settled/resolved fact, or an ambiguous clue
  that does not establish a material impact.
  When a needs_attention Project has a material unresolved impact and a directly
  relevant saved Project responsibility, create one display-only candidate next-step
  Task for that role even when the current message states no assignment or explicit
  action. Infer a concrete action from the risk. An existing Task suppresses this
  suggestion only if it addresses the same unresolved risk; a completed or unrelated
  Project deliverable does not.
  Return every list-valued field as a JSON array; use `[]` when empty and never
  `null` (including `decision_indexes`, `task_ids`, `todo_changes`,
  `follow_up_changes`, and `search_trace`).
  When the current source itself states a concrete action and expected result,
  normally record it as a source-origin Task; do not relabel that human-stated action
  as an Agent suggestion just because its metadata does not support a formal assignment.
  Exception: routine milestones and next steps in the normal Project sequence are
  Project facts, not Task candidates, even when phrased as actions.
  For project_link_evidence, when the Project title and Task action occur in
  different parts of the current source, cite one exact contiguous span from the
  title occurrence through the action and include the intervening source text
  verbatim; never splice nonadjacent excerpts into one quotation.
  A named person responsible to verify a specific matter and report back is an
  action; a bare duty-area description such as “responsible for reconciliation” is not.
  Preserve a clearly named responsible person as source-reported owner evidence
  without inferring acceptance. Use `suggestion` only when the action itself is
  inferred by the Agent and is absent as an action from the source.
  A Project role or responsibility is not itself a Task, and ordinary milestones
  or routine next steps are Project facts, not candidates. Do not create a Task
  just to fill a missing/contested Project owner or other ProjectContext field;
  record the unresolved fact and assess its Project impact. Missing ownership alone
  is not a material risk; use 需关注 only when source evidence shows a material
  delivery/business impact or required Gate. A suggestion must be an independently
  actionable step beyond clarifying the Project record itself.
  Before suggesting another next-step Task for a Project risk, check current linked
  Tasks. If an existing actionable Task already addresses that risk, use its existing
  Task ID as the supporting next step and do not add a duplicate monitoring/evaluation
  suggestion.
- On later evidence reuse the existing Task ID, including an existing suggestion.
  Do not rely on source-link duplication or wording similarity as Task identity.
- New Tasks need a nonblank title. Only update_fields changes a supplied title;
  promotion/acceptance/merge preserve the stored title. Explicit assignment is
  assigned_unaccepted, not accepted. apply_acceptance needs identified owner proof,
  cited assignment Signal and verified reply_to_source_ref; “收到” or TODO existence
  is not acceptance. Unaccepted status alone is not a material Project risk when
  current evidence shows progress with no meaningful impact or unresolved dispute.
  For an existing Task update, leave commitment_status unchanged unless the named
  owner explicitly accepts, disputes, completes or cancels it in the source; work
  progress or continued handling is not acceptance. Set acceptance polarity only
  through apply_acceptance with verified owner and reply evidence.
  Similar deliverables are linked/clustered, not identity-merged.
  status and business_relevance may only change through update_fields: set
  transition=update_fields when either field changes. They remain top-level fields,
  not a nested update_fields object; never set them under another transition.
  New, record_candidate and skip decisions must leave status and business_relevance
  unset. For an existing Task, set transition=update_fields to change either field;
  do not try to set a status or relevance during creation.
  When promoting a suggestion, omit the suggestion field; the saved Task keeps its
  existing suggestion history. A suggestion field is valid only when recording or
  updating a candidate.
  For any Agent-generated suggestion, leave `owner_name`/`owner_user_id` empty,
  `owner_evidence` empty, and `owner_kind`/`owner_relation` unset; also leave
  formal basis, acceptance, dates, status and relevance unset. A proposed person
  belongs only in suggestion.suggested_owner_name/user_id. Return all list-valued
  fields as JSON arrays, using `[]` when empty and never `null`.
  For AI Minutes read complete meeting_summary, transcript_excerpts and action items.
  The assigned person is not automatically the speaker; owner proof quotes the
  actual sentence with speaker label included when present. A generic speaker
  placeholder such as 发言人 N is not an owner, nor is a team or department.
  A meeting action item is an actual assignment only when source_conversation_kind
  is minutes and the current AI Minutes source reference carries the action-item
  record marker #todos-sha256=. Otherwise keep it as evidence/candidate and do not
  claim formal meeting assignment. A non-meeting explicit assignment is formal
  only when the current source metadata explicitly says assignment_authorized=true;
  otherwise record it as a display-only candidate, never as an actual assignment.
  Actual owner_evidence uses {{"source_ref": "the source reference", "excerpt": "the literal owner-action sentence"}}.
- In project_assessments provide one factual reason and original proof for every
  relevant Project/clue in this source and current Tasks' confirmed Project links,
  even when no Project selector or Task update is emitted. No Task is not insufficient
  evidence. Supported routine progress is not_needed; genuine missing identity/facts
  is insufficient_evidence, without inventing risk or a card.
- Attention belongs once to its assessment. Task decision_indexes and existing task_ids
  are optional real members, not carriers. Never include a skip decision in
  decision_indexes; supporting indexes may identify only a real candidate, create,
  or update Task decision that belongs to this Project and directly supports this
  specific Project assessment. When a Task decision updates the same concrete
  Project work described by the assessment, include that decision index as support,
  including for a not_needed progress assessment. An existing Task belongs in
  task_ids only when it directly supports the assessment. A Task being linked to
  the Project is not enough:
  completed or unrelated Project Tasks are not members. Only a real material impact merits watch,
  decision or push; “需关注” does not mean “需介入”. A retained existing_attention_id
  requires an exact citation from this card's stored
  `current_project_attention.assessment_json.evidence` (same `signal_id`, `source_ref`
  and `source_excerpt`); newer evidence may supplement but cannot replace that proof.
  It also requires actual membership, not Project peers.
  For an existing card, task_ids must be copied only from that card's actual stored
  member IDs delivered in current_project_attention; never add a peer Task solely
  because it belongs to the same Project. If the delivered member list is empty,
  keep task_ids and decision_indexes empty, including when a linked Task is completed
  or updated in this turn, or when a same-risk candidate was suggested on an earlier
  turn. This preserves the stored Attention membership; that candidate remains a
  separate Project-linked Task and is not added to the existing card. An explicit
  unresolved dispute over who
  holds the Project's overall accountable role (such as a claimed transfer that the
  prior owner says was not confirmed) is needs_attention even before operational
  impact is separately quantified; record the conflict in ProjectContext and do not
  create a Task just to resolve that Project field. Missing overall ownership
  without a stated impact or dispute is not a material risk and should be not_needed
  when the other Project evidence is normal and complete.
  A current update goes in that assessment's attention_proposal. not_needed or an
  empty Task list never closes a Project risk; completing one Task is not Project completion.
  First assessment of a source-observed unresolved material business risk does not
  require a prior card or a fresh delta against a nonexistent assessment. Candidate
  Tasks may support Attention without a formal owner or accepted commitment.
  Labels, relevance, routine progress, and date proximity alone do not explain material impact.
  An existing card already reflecting the same facts does not need a new proposal.
  Never invent a Task, owner, assignment, commitment, or date to fill a card.
- All source_excerpt values are exact contiguous quotes, preserving punctuation,
  spaces and line breaks (a decoded JSON string leaf is allowed). Keep facts and
  inference separate. Current evidence has null signal_id/current source_ref;
  historical proof uses real positive observed Signal IDs, matching references/quotes,
  not session or Memory provenance. historical_comparison needs current and original
  historical proof; if originals are unavailable, state uncertainty, not invented history.
  Do not reconstruct an earlier quotation from a later summary. If the exact historical
  quote is not available in the original Signal, omit that claim and say what cannot be confirmed.
- Project registration.source_excerpt must itself be an exact contiguous quote
  from the immutable current Work Item that defines the Project; do not substitute
  a responsibility sentence, paraphrase, or quote from another source. When a
  report contains multiple Projects, cite each Project's defining row and assess
  each Project separately.
- Keep created/assigned/requested/external/committed/estimated/check dates distinct.
  Date evidence must be literal current-source wording and a trusted actor.
  AI Minutes has no trusted speaker-to-identity date mapping: do not emit source-derived
  typed deadlines there. next_check_at is not an accepted due date or invented cadence.
  Never transfer a Project deadline onto a Task.
- Schema todo_changes/follow_up_changes/search_trace and old completion-check source
  enums are historical, not current operations. Human DingTalk completion still updates
  only its explicitly linked Task through the deterministic service path.
- Memory is background/discovery, not original observed evidence or human acceptance.
  Keep runtime paths, credentials and diagnostics out of business fields.

TaskAgentDecision Pydantic JSON schema:
{decision_schema}
Return only the envelope after checking every item.
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


def _task_source_signal(work_item: WorkItem, item: TaskDecision) -> SourceSignal:
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
    return SourceSignal(
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
            """select link.anchor_id from business_task_anchor_links link
               join business_anchors anchor on anchor.id=link.anchor_id
               join business_projects project on project.canonical_anchor_id=link.anchor_id
               where link.task_id=? and link.status='confirmed' and link.active=1
                 and anchor.anchor_type='project' and anchor.active=1""",
            (task_id,),
        ).fetchall()
    }


def _project_source_signal(work_item: WorkItem) -> SourceSignal:
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
    return SourceSignal(
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


def _resolve_project_citation(
    store: AutoReplyStore,
    citation,
    *,
    work_item: WorkItem,
    db: sqlite3.Connection,
    current_signal_id: int | None = None,
) -> SourceCitation:
    if citation.signal_id is None:
        if citation.source_ref != work_item.source.ref:
            raise ValueError(
                "current Project evidence must cite the immutable Work Item source_ref"
            )
        if not source_contains_quote(work_item.summary, citation.source_excerpt):
            raise ValueError(
                "current Project quote is absent from the immutable Work Item"
            )
        signal_id = current_signal_id
    else:
        signal = store.get_business_task_signal_in_transaction(
            signal_id=citation.signal_id, _db=db
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
        if not source_contains_quote(signal.evidence_text, citation.source_excerpt):
            raise ValueError("historical Project quote is absent from original source")
        signal_id = signal.id
    return SourceCitation(
        signal_id=signal_id,
        source_ref=citation.source_ref,
        source_excerpt=citation.source_excerpt,
    )


def _resolved_context(store, context, *, work_item, db, current_signal_id):
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
        "select p.id from business_projects p join business_anchors a on a.id=p.canonical_anchor_id "
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
) -> None:
    """Verify original citations and existing identities before domain writes."""
    for citation in _project_citations(decision):
        _resolve_project_citation(store, citation, work_item=work_item, db=db)

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
            "select p.canonical_anchor_id from business_projects p "
            "join business_anchors a on a.id=p.canonical_anchor_id "
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
            _resolve_project_citation(store, original, work_item=work_item, db=db)
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
) -> TaskAgentApplyResult:
    """Persist all source-grounded task decisions, atomically when _db is supplied."""
    _validate_task_agent_decision(decision, work_item=work_item, now=now)
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
        _validate_stored_project_assessments(
            store, decision, work_item=work_item, db=db
        )
        if any(proof.signal_id is None for proof in _project_citations(decision)):
            current_signal_id = service._signal_id_or_create(
                signal=_project_source_signal(work_item), db=db, now=service._now()
            )
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
            )
            citations = [
                _resolve_project_citation(
                    store,
                    proof,
                    work_item=work_item,
                    db=db,
                    current_signal_id=current_signal_id,
                )
                for proof in project_decision.evidence
            ]
            if context is not None:
                citations.extend(ProjectContextService._citations(context))
            ids = tuple(sorted({proof.signal_id for proof in citations}))
            revision_id = ProjectContextService(store).apply(
                project_id=project.id, context=context, signal_ids=ids, db=db
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
                    store.get_business_task_signal(item.acceptance_target_signal_id)
                    if db is None
                    else store.get_business_task_signal_in_transaction(
                        signal_id=item.acceptance_target_signal_id, _db=db
                    )
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
            signal = _task_source_signal(work_item, item)
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
                    )
                elif item.transition == "merge_identity":
                    proposal = item.identity_proposal
                    assert proposal is not None
                    _validate_identity_proposal(store, proposal, db=db)
                    identity = IdentityEvidence(
                        same_external_task_id=proposal.identity_evidence.basis
                        == "same_external_task_id",
                        explicit_source_reference=proposal.identity_evidence.basis
                        == "explicit_source_reference",
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
                    """select date_type, value_at, raw_phrase, source_signal_id,
                              actor_kind, actor_user_id, actor_name
                       from business_task_date_evidence where task_id=?""",
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
                    (
                        json.dumps(completion_evidence, ensure_ascii=False),
                        completion_evidence["reason"],
                        task_id,
                    ),
                )
                linked_todo = db.execute(
                    "select 1 from business_task_dingtalk_links where business_task_id=? "
                    "and status in ('creating','active') limit 1",
                    (task_id,),
                ).fetchone()
                if linked_todo is not None:
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
                    "select 1 from business_task_date_evidence where task_id=? "
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
                            "select 1 from business_work_cluster_tasks where cluster_id=? and task_id=?",
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
                        "select id from business_project_candidates "
                        "where cluster_id=? and title=? and status='proposed' limit 1",
                        (cluster_id, project.title),
                    ).fetchone()
                    if existing_candidate is not None:
                        changed_members = [int(row["task_id"]) for row in db.execute(
                            "select member.task_id from business_work_cluster_tasks member "
                            "left join business_task_anchor_links link on link.task_id=member.task_id "
                            "and link.anchor_id=? where member.cluster_id=? "
                            "and (link.id is null or link.status<>'confirmed' or link.active=0)",
                            (project.canonical_anchor_id, cluster_id),
                        )]
                        registration_signal_id = service._signal_id_or_create(
                            signal=_project_source_signal(work_item), db=db, now=service._now()
                        )
                        resolution.confirm_project_candidate(
                            candidate_id=int(existing_candidate["id"]), project_id=project.id,
                            evidence_signal_id=registration_signal_id, _db=db,
                        )
                        affected_task_ids.extend(changed_members)
                        project_links.update(
                            (int(link["task_id"]), project.canonical_anchor_id)
                            for link in db.execute(
                                "select link.task_id from business_task_anchor_links link "
                                "join business_work_cluster_tasks member on member.task_id=link.task_id "
                                "where member.cluster_id=? and link.anchor_id=? "
                                "and link.status='confirmed' and link.active=1",
                                (cluster_id, project.canonical_anchor_id),
                            )
                        )
            candidate = item.project_candidate_proposal
            if candidate is not None:
                existing = db.execute(
                    "select id from business_project_candidates "
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
            recorded_run_id = store.record_task_agent_run(
                summary_input_id=summary_input_id,
                codex_session_id=codex_session_id,
                decision_json=_json_dumps(decision.model_dump(mode="json")),
                audit_summary="; ".join(
                    filter(
                        None, (item.update_summary for item in decision.task_decisions)
                    )
                ),
                memory_recall_used=any(
                    item.memory_recall_used for item in decision.task_decisions
                ),
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
            store, result.attention_proposals, result.affected_task_ids, receipt=receipt
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
) -> TaskAttentionProjectionReceipt:
    applied_ids: set[int] = set()
    projection = BusinessAttentionProjection(store)
    for applied in proposals:
        proposal = applied.assessment.attention_proposal
        assert proposal is not None
        try:
            with store.business_task_transaction() as db:
                project = _official_project(store, applied.anchor_id, db=db)
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
                    signal = store.get_business_task_signal_in_transaction(
                        signal_id=signal_id, _db=db
                    )
                    if signal is None or signal.source_ref != proof.source_ref:
                        raise ValueError(
                            "attention evidence signal/source_ref does not match"
                        )
                    if not source_is_observed(signal.source_type):
                        raise ValueError(
                            "attention evidence must be observed original source, not cited provenance"
                        )
                    if not source_contains_quote(
                        signal.evidence_text, proof.source_excerpt
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
            attention_id = projection.upsert(effective)
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


def _validate_identity_proposal(
    store, proposal, *, db: sqlite3.Connection | None
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
        return store.get_business_task_signal_in_transaction(
            signal_id=signal_id, _db=db
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
        semantic_context = retrieve_task_semantic_context(store, work_item)
        context_prompt = render_task_semantic_context(
            semantic_context, work_item=work_item
        )
        active_run_id = store.begin_task_agent_run(work_input.id)
        memory_issue = memory_connector_config_issue()
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
            )
            decision = _canonicalize_current_source_provenance(
                decision, work_item=work_item
            )
            try:
                _validate_task_agent_decision(decision, work_item=work_item, now=now)
            except RepairableTaskDecisionValidationError as exc:
                if repair_round == TASK_DECISION_REPAIR_ROUNDS:
                    raise TaskDecisionRepairExhausted(str(exc)) from exc
                context_prompt = (
                    render_task_semantic_context(semantic_context, work_item=work_item)
                    + "\n\nDecision validation rejected the previous candidate before any "
                    "domain writes. Correct this evidence error without inventing facts: "
                    + str(exc)
                    + "\nPrevious candidate:\n"
                    + _json_dumps(decision)
                )
                continue
            break
        session_id = getattr(runner.codex, "last_session_id", None) or ""
        if session_lease is not None:
            session_lease.assert_owned()
        crm_customer_lookups = {
            index: lookup_account_customers(project.crm_customer_label)
            for index, project in enumerate(decision.project_decisions)
            if project.crm_customer_label
        }
        with store.task_agent_domain_apply_transaction() as db:
            apply_result = apply_task_agent_decision(
                store,
                summary_input_id=work_input.id,
                work_item=work_item,
                decision=decision,
                codex_session_id=session_id,
                record_run=False,
                now=now,
                crm_customer_lookups=crm_customer_lookups,
                _db=db,
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
            store.finish_task_agent_run(
                active_run_id,
                status="completed",
                codex_session_id=session_id,
                decision_json=_json_dumps(decision.model_dump(mode="json")),
                audit_summary="; ".join(
                    filter(
                        None,
                        (
                            *(item.update_summary for item in decision.task_decisions),
                            *apply_result.skipped_reasons,
                        ),
                    )
                ),
                memory_recall_used=any(
                    item.memory_recall_used for item in decision.task_decisions
                ),
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
            store.finish_task_agent_run(active_run_id, status="failed", error=str(exc))
        store.mark_work_summary_input_failed(work_input.id, str(exc))
        raise
    try:
        receipt = _project_task_attention(
            store,
            apply_result.attention_proposals,
            apply_result.affected_task_ids,
            receipt=receipt,
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
