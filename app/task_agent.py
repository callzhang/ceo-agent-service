import json
import logging
import re
import sqlite3
from dataclasses import dataclass, replace
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
    TaskAttentionProjectionOutcome,
    TaskAttentionProjectionReceipt,
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
class AppliedTaskAttention:
    decision: TaskDecision
    task_id: int
    signal_id: int
    anchor_id: int


@dataclass(frozen=True)
class TaskAgentApplyResult:
    task_ids: tuple[int, ...]
    attention_proposals: tuple[AppliedTaskAttention, ...]
    affected_task_ids: tuple[int, ...] = ()
    skipped_reasons: tuple[str, ...] = ()
    projection_receipt: TaskAttentionProjectionReceipt | None = None
    project_links: tuple[tuple[int, int], ...] = ()

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
            formal_basis in {
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
        decisions.append(item.model_copy(update={
            "action": action,
            "source_ref": source_ref,
            "owner_evidence": owner_evidence,
            "date_evidence": date_evidence,
            "formal_basis": formal_basis,
        }))
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
        "every non-skip item needs a source_excerpt (a sentence of the source), source_ref and a locator (source_link when there is one, otherwise source_description) "
        "(evidence_origin says whether it is the current Work Item, an earlier session turn, or memory provenance).\n"
        "- A formal assignment requires an explicit owner and authorized "
        "assignment source. Owner evidence alone does not prove authority.\n"
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
    weekly_report_rules = ""
    if work_item.source.type in {
        WorkItemSourceType.MANAGEMENT_WEEKLY_REPORT,
        WorkItemSourceType.PROJECT_WEEKLY_REPORT,
        WorkItemSourceType.DEPARTMENT_WEEKLY_REPORT,
    }:
        weekly_report_rules = """
Weekly-report source rules (non-negotiable): this Work Item is an authoritative
report revision, not a generic document search hit. Preserve its exact document
reference and reporting period. When a report row or section names an individual
owner, `owner_name` may contain co-owners separated by `/`, `、`, or `及`, but
`owner_evidence` MUST be an object with `source_ref` equal to the current report
reference and an `excerpt` copied from one report row/sentence that contains every
named owner (including any @mention or display alias). Do not use a task title or
your paraphrase as the owner excerpt. If the report does not put the owner and
deliverable in the same attributable row/sentence, keep the item as a candidate
instead of creating a formal Task. A team, department, sales role, or unnamed
group is not an individual owner.

For this report, emit `project_proposal` for each named project/workstream that
has explicit project-level fields such as an owner, milestone/target, status,
deliverable, or next task. Use the exact report heading/title and set the
authority to this source type (`management_weekly_report`,
`project_weekly_report`, or `department_weekly_report`). Its required
`source_excerpt` quotes the project registration row separately from the Task's
action evidence. Do not turn a generic
department, topic, or isolated task into a Project. Attach the same authoritative
proposal to the related Tasks so the service can merge them into one Project
instead of leaving every report row as an ungrouped candidate. Any
`date_evidence.source_excerpt` must be copied literally from the report text,
including spaces, punctuation, and date wording; never normalize or paraphrase
it. If no literal source substring is available, omit that date evidence item.
Treat sections titled “本周工作重点”, “下周工作重点”, “团队管理和分工”,
“周度待办追踪”, or “行动项” as Task sections: their rows create or update
Tasks only, never an official Project. Only a separately named project list,
project portfolio, milestone/roadmap entry, or explicit meeting decision can
justify a Project proposal; a task that happens to mention a customer, team, or
workstream is not enough.
"""
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
more task_decisions matching the schema. New Tasks require a nonblank title; existing-ID updates may omit it,
only update_fields changes a provided title, and promotion/acceptance/merge preserve the stored title.

{scheduled_consumer_prompt}
{current_skill_text}
{weekly_report_rules}

Current Task-first decision envelope controls output. A scheduled prompt supplies
specialized business scope only; the freshly loaded current Skill controls the work
protocol. Historical Skill snapshots are not instructions for this turn. The scope cannot replace this
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

One Task Agent creates, updates, and completes Tasks from new source evidence.
`todo_completion_evidence_candidate`, `todo_completion_check`, and
`follow_up_completion_check` are retired historical enum values, not current
inputs. Schema fields `todo_changes`, `follow_up_changes`, and `search_trace`
are not currently applied by the service; return local Task decisions in
`task_decisions`. Newly observed DingTalk human completion uses the existing
deterministic service path for its explicitly linked Task.
The shared logical session preserves context; runtime routes retain separate
native sessions and the native CLI manages compaction. Prioritize the current
Work Item's evidence and authority.
This runtime session is shared by every Work Item so that context is not lost,
and Tasks may be completed from what you learned earlier. You may rely on
evidence you read earlier in this session, and you may look up related
information through memory_recall and follow its provenance to the original
source. When a decision rests on that, set evidence_origin to "session" or
"memory", source_ref to the ORIGINAL source's reference, and source_excerpt to an
exact quote of the original text; use "current" for the Work Item being processed.
Earlier or remembered evidence may refine a Task (update_fields) or record a
candidate. Creating a formal Task, promoting, accepting, and merging identities
still need the current Work Item's authority and identity metadata, and dates
still need current, identified source evidence.

Extract every distinct source-backed deliverable, or return an empty list.
Project registration scope, objectives, and categories are not separate Tasks
when concrete source actions already cover that work. Use registration text as
Project evidence attached to those real actions, not as an additional umbrella Task.
Keep genuine explicit actions wherever they occur in the source.
One source may yield multiple decisions. Each non-skip item cites its source: the
source_ref, a sentence of the original text as source_excerpt (an extract is fine;
it need not be word for word), and where a reader can find it: source_link
whenever the source has a link (always give it then); when it has none, describe
where it is in source_description (a DingTalk message: its group and the person
who sent it). For the current Work Item the service fills in what it knows, but
state them whenever you can, and always for earlier or remembered evidence.
Never invent a task, owner, assignment, acceptance, date, relevance, or
authority. An owner must be explicit in source text or authoritative source
metadata; an ownerless assignment stays candidate/unmatched evidence.

For AI Minutes, DingTalk leaves each action item's own executor empty, and the
Work Item's transcript_excerpts hold the conversation around it, one line per
sentence as “speaker：text”. Decide the owner from those lines: it is whoever
the conversation gives the work to or who takes it on, not automatically the
speaker (“你写下来” from one person assigns the work to the person addressed).
Set owner_evidence with two keys: "source_ref" (the decision's source_ref) and
"excerpt" (a sentence, speaker label included, that contains the owner's name; an
extract is fine). That sentence is often not the item's own source_excerpt: when
one person hands the work to another (“你写下来”), cite the sentence in which the
person who takes it on speaks, and keep the assigning sentence as the decision's
source_excerpt. A generic label such as “发言人 N” is DingTalk's placeholder for a
speaker it could not name; it is not a person and never an owner. When the lines
do not settle who owns it, or an action item has no excerpt, leave the owner
empty.

The Work Item's meeting_summary, when present, is DingTalk's own structured
summary of the whole meeting: read it too before concluding there is no owner. A
narrow transcript window keyed to one action item's extraction moment can miss the
sentence that actually names the owner, and this summary often states an
assignment explicitly and elsewhere in the meeting (e.g. "行动项：**磊哥**与**周俊杰**
负责代码 Review"), sometimes for several action items in one place, sometimes long
after the moment the item itself was raised. An owner citation may come from
meeting_summary the same way it comes from transcript_excerpts: a sentence
(an extract is fine) that contains the owner's name; the person named must be an
individual DingTalk gave a name to, not a team or department ("研发", "算法团队",
"Product Marketing") and not a placeholder like "发言人 N". When neither the
transcript window nor the summary names an individual, leave the owner empty:
that is the source's limit, not something to fill in.

For every AI Minutes item with an owner, also classify the owner evidence:
`owner_kind` is `individual`, `team`, or `unknown`; `owner_relation` is
`explicit_assignment` when someone assigns the work to that person,
`self_commitment` when the named person takes it on, `meeting_summary_action_item`
when the structured meeting summary names that person for the action item,
`speaker_only` when the person merely speaks, or `unknown`. A named individual
with one of the first three relations is a formal `meeting_action_item` Task
with `assigned_unaccepted` semantics, even when a stable ID, date, or completion
standard still needs enrichment. A team, speaker-only mention, or unresolved
relation remains a candidate.

An assignment creates an assigned_unaccepted Task. Only explicit evidence from
that identified owner may apply_acceptance to exactly one existing formal Task;
“收到” and external TODO existence are not acceptance. Use explicit
promote_candidate, apply_acceptance, update_fields, or merge_identity
transitions with existing IDs. Similarity rank is context only. Generic updates
cannot set commitment status.

Only identical deliverables may be proposed for identity merge.
Relations name the existing `related_task_id` and direction relative to this applied Task:
current_to_related or related_to_current; never guess a new Task's ID or use unrelated endpoints.
Related Tasks remain linked or clustered. Create only independently completable deliverables;
scope/content additions to an existing deliverable update that Task by its real ID.
Identical source quotes alone do not establish Task identity.
Task action excerpts do not originate extra Tasks from Project registration scope already covered by concrete actions.
Reports, meetings, and chats all supply Task and risk evidence;
a weekly report is neither the sole risk source nor a prerequisite for Attention.
Attention.anchor_id selects the Project assessment; it does not confirm a Task's Project link.
For a new or unconfirmed Task explicitly belonging to an existing official Project,
emit `project_link_proposal` with that known positive `anchor_id`, a nonempty exact
current action `source_excerpt` naming the stored Project/anchor title, and a
source-grounded `reason`. A complete same-action compound quote may supply the stored
Project name and contain the shorter Task action quote; not another paragraph or whole report.
The link quote and exact Task action quote must contain one another in the current source.
Use the same positive anchor in attention_proposal. The service confirms that Task's
link and derives relevant business_relevance without promoting its stage.
Do not set business_relevance on a new Task decision. Reuse existing confirmed Task links.
Adopt the exact current authoritative Project definition with project_proposal to register or reuse
its official identity. A different stored name cannot replace that definition merely because the action uses its shorter name.
Use project_link_proposal when the source explicitly supplements that known Project;
do not combine these two Project selections in one decision.
Uncertain matches remain `anchor_match_proposals`; they are proposed, not confirmed.
Do not infer aliases or identity from a title prefix or similarity; judge whether
the source explicitly names this existing Project. Quote/title checks establish
current provenance and a name reference, not independent semantic identity proof.
An official Project requires confirmed report registration or an explicit meeting
registration decision. Resolve that current source definition before selecting a stored Project. Prefer the confirmed official weekly report for Project
definition and registry fields. Chat can update Task/risk evidence but cannot create an
official Project or silently overwrite official fields. Preserve report references
and reporting periods. When newer meeting or chat evidence conflicts with official
fields, preserve both cited sources and their times and mark the conflict pending
verification. Project candidates must cite an existing cluster and authoritative
report/meeting evidence; similarity is not authority.

Project resolution is part of this scan. If the current meeting evidence explicitly
decides to start, approve, 立项, or assign a named project/workstream (not merely
mentioning it), emit `project_proposal` on each related Task with the exact project
title, a reason grounded in the source sentence, a separate `source_excerpt`
quoting the project decision, and `authority="meeting_decision"`.
This is required even when the related Task is an `update_task` or a previously
recorded meeting action: update the Task and attach the project proposal in the
same decision. Read the complete meeting_summary, transcript_excerpts, and action
item text before deciding that a project was only mentioned. When a sentence
explicitly asks for a named product or workstream to be planned or set up, use
that source-named item as the Project title; do not leave the proposal null merely
because the current Task already exists. The service will register that project
and link the Task to it. If the source only
mentions a project or several Tasks appear related without an explicit project
decision, do not emit `project_proposal`; emit a `cluster_proposal` and a
`project_candidate_proposal` instead when the existing cluster and evidence support
that candidate. Never invent a project title from a generic department, topic, or
single unrelated Task.

Quote only the complete parseable date phrase, not a registry row, in date_evidence.
Use trusted WorkItem.context.sender_user_id/sender for source-derived date actors;
next_check_at uses task-agent/CEO Agent. Report/document names are not date actors.
Without a trusted actor or complete parseable date phrase, retain the wording in the original source without typed date_evidence.
Normalized value must match that phrase; do not move Project registry deadlines onto Tasks or add absent time precision.
assigned_at comes only from trusted source timestamp metadata; an estimate is not
the extracting Agent's estimate, and next_check_at is not an owner commitment. AI Minutes
has no trusted speaker-to-identity mapping yet, so do not attribute a quoted
speaker's date to the meeting host or to a model-selected identity (this limit
is for dates; owners come from transcript_excerpts as above). Only owner
acceptance can establish committed_deadline_at. No date is required to retain a Task.
Attention requires an existing confirmed official Project or a valid current-authority
`project_proposal` in this same TaskDecision, resolved to its registered anchor this turn,
plus a material trigger: threatened accepted commitment, material change/dispute,
CEO decision/push, required Gate, or meaningful risk escalation.
First assessment of a source-observed unresolved material business risk may use watch;
it does not require a prior card or a fresh delta against a nonexistent assessment.
Explain the concrete unresolved business impact from the observed source, even when
the report states the risk as a current fact. An existing card already reflecting
the same facts does not need a new proposal; repeated facts alone are insufficient.
Candidate Tasks may support Attention without a formal owner or accepted commitment;
keep their stage and missing ownership evidence truthful. Relevance, acceptance, ordinary progress, or
date proximity alone is not attention. Retain real low-impact work when needed,
but keep it outside attention. “skip” means no plausible retained source task,
not no Project.
Attention proposals cite a nonempty `evidence` list: each entry includes
`source_ref`, an exact `source_excerpt`, and an optional existing `signal_id`.
Keep `why_attention` as your material business-impact inference, separate from
`current_state` facts and source quotes. Quote risk evidence from the full current
source or historical persisted original Signals, separately from Task action
source_excerpt and ProjectProposal.source_excerpt registration evidence.
For current evidence use null signal_id and the current source_ref; historical evidence
requires a real positive persisted signal_id, matching source_ref, and an exact quote.
Set required `assessment_basis`: `current_observation` asserts only current-source facts;
`historical_comparison` uses comparison, continuity, escalation or conflict with stored history
and requires both current null-ID and positive persisted-ID original evidence.
When the current source explicitly compares earlier facts and matching original Signals
are delivered, verify that comparison against the originals and use historical_comparison;
do not reduce it to merely repeating the current source's historical claim.
A current source's reference to an earlier report is a current claim, not a citation of that original report.
Select relevant originals, not all retrieved sources or a required source type. A first assessment
based only on current facts remains allowed. If the original history is unavailable, mark
the comparison uncertain and assert only current facts; never invent historical evidence.
Session/memory cited-only provenance cannot support Attention; use stored observed
original Signals. Labels, relevance, routine progress, and date proximity alone
do not explain material impact. Return at most one unique assessment/card per Project per round.
For multiple newly created Tasks supporting the same Project and risk, repeat the identical `attention_proposal`
on each supporting TaskDecision. The service folds those identical proposals into
one card and combines their Task membership. Keep the assessment fields and evidence
identical across those decisions; anchor resolution and existing related IDs may differ.
Use `related_task_ids` only for real existing Task IDs; never invent IDs for new decisions.
Keep unrelated Project Tasks outside this assessment; conflicting proposal payloads are rejected.
Other project Tasks receive no Attention merely by membership. For watch, ceo_action
may say 当前无需你处理; specify the observable outcome to watch. Attention does not imply 需介入.
Explain unproposed Tasks in update_summary when impact is insufficient, Project is
unconfirmed, or no verifiable evidence exists. Never invent a Task, owner, assignment,
commitment, or date to fill a card.
`anchor_id` is a positive registered ID, or null only when this same TaskDecision
contains the `project_proposal` to resolve this turn. `related_task_ids` contains
only positive existing Task IDs; omit it when there are none. Do not output the
retired `trigger_evidence` field.

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

NON-NEGOTIABLE VALIDATION CHECK (apply this before returning JSON):
- If a decision contains `status` or `business_relevance`, it MUST be an
  existing Task update: set `action` to `update_task`, provide `task_id`, and
  set `transition` to `update_fields`. Do not emit those fields on a new task,
  candidate, or skip decision.
- If a decision contains `acceptance_polarity` or
  `acceptance_target_signal_id`, it MUST set `action=update_task`, provide
  `task_id`, set `transition=apply_acceptance`, and use
  `acceptance_polarity=accepted` with the cited signal id.
- For ordinary source-backed work with no lifecycle change, omit both
  `status` and `business_relevance` rather than guessing an update.
- For an AI Minutes Work Item, do not emit `date_evidence` for dates spoken
  in the meeting (`requested_deadline_at`, `external_deadline_at`,
  `estimated_deadline_at`, or `committed_deadline_at`): speaker identity is
  not trusted for those facts. Only emit `next_check_at` when it is authored
  by the CEO Agent itself; otherwise leave `date_evidence` empty.
- For every other source, every `date_evidence.source_excerpt` must be a
  literal substring copied from the current Work Item text, preserving exact
  spaces and punctuation. If you cannot quote it exactly, omit the date
  evidence rather than paraphrasing it.
Return the envelope only after checking every item against these rules.
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


def _source_locator(work_item: WorkItem, item: TaskDecision) -> tuple[str, str, str, str]:
    """Where a reader can find the source (Derek 2026-09-25): its link when it has one; otherwise a
    description of where it is (a DingTalk message is its group and the person who sent it).

    What the decision states wins. For the current Work Item the service fills in what it
    already knows: a URL reference, the meeting page link inside an AI-minutes summary, or
    the conversation and its sender. A Work Item that offers none of these still has its
    `source_ref` on the record; earlier or remembered evidence must state its own locator
    (the decision model requires it).
    """
    link, group, person = item.source_link.strip(), item.source_group.strip(), item.source_person.strip()
    description = item.source_description.strip()
    if item.evidence_origin != "current" or link or description or (group and person):
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
    return link, group or work_item.source.conversation_title, person or work_item.context.sender, description


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
    next_section = re.search(r"(?m)^##\s+", markdown[registry_match.end():])
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
    following_line = markdown[row_end + 1:].split("\n", 1)[0].strip()
    if re.fullmatch(r"\|[\s:|\-]+\|", following_line):
        return ""
    cells = [cell.strip() for cell in first_row.strip().strip("|").split("|")]
    if not cells:
        return ""
    preceding_lines = markdown[registry_match.end():row_start].splitlines()
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


def source_contains_quote(raw: str, quote: str) -> bool:
    """Check current source text, including decoded strings in structured inputs."""
    if not quote.strip():
        return False
    if quote in raw:
        return True
    try:
        payload = json.loads(raw)
    except ValueError:
        return False

    def contains(value: object) -> bool:
        if isinstance(value, str):
            return quote in value
        if isinstance(value, dict):
            return any(contains(child) for child in value.values())
        if isinstance(value, list):
            return any(contains(child) for child in value)
        return False

    return contains(payload)


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
        current_task_id = (item.identity_proposal.target_task_id
            if item.transition == "merge_identity" and item.identity_proposal else item.task_id)
        relation_effects = sorted(
            (*r.endpoints(current_task_id), r.relation_type) for r in item.relation_proposals
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
            "project": (
                {
                    "title": normalized(item.project_proposal.title),
                    "authority": item.project_proposal.authority,
                }
                if item.project_proposal else None
            ),
            "date_effects": date_effects,
        }
        if item.project_link_proposal is not None:
            semantic_identity["project_link"] = {
                "anchor_id": item.project_link_proposal.anchor_id,
                "source_excerpt": item.project_link_proposal.source_excerpt,
            }
    stable_item = hashlib.sha256(json.dumps(
        semantic_identity, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")).hexdigest()
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
                {"evidence_origin": item.evidence_origin, "cited_while_processing": work_item.source.ref,
                 **({"source_link": link} if link else {}),
                 **({"source_description": description} if description else {})},
                ensure_ascii=False, sort_keys=True,
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
        author_kind=(BusinessActorKind.HUMAN if work_item.context.sender_user_id else BusinessActorKind.UNKNOWN),
        context_json=json.dumps(
            {
                "work_item_title": work_item.source.title,
                "assignment_authorized": work_item.context.assignment_authorized,
                **({"source_link": link} if link else {}),
                **({"source_description": description} if description else {}),
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
        if item.owner_kind != "individual" or item.owner_relation not in {
            "explicit_assignment", "self_commitment", "meeting_summary_action_item",
        }:
            raise ValueError(
                "meeting action item requires an explicit individual owner relation"
            )
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
    attention: list[AppliedTaskAttention] = []
    skipped_reasons: list[str] = []
    project_links: set[tuple[int, int]] = set()

    def apply(db: sqlite3.Connection | None) -> None:
        for item in decision.task_decisions:
            applied_project_anchor_id = None
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
            if item.evidence_origin != "current":
                # Sender and owner identity metadata describe the current Work Item only.
                source_owner_id = ""
            if item.owner_user_id and item.owner_user_id != source_owner_id:
                raise ValueError("owner_user_id is not established by source identity metadata")
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
                if item.task_id is not None else None
            )
            formality = FormalityEvidence(
                basis=item.formal_basis,
                assigner_is_authorized=(
                    item.formal_basis is not FormalTaskBasis.EXPLICIT_ASSIGNMENT
                    or work_item.context.assignment_authorized
                ),
                deliverable_is_explicit=(
                    bool(task_before is not None and task_before.title.strip()) if item.action == "update_task"
                    else bool(item.title.strip())
                ),
                owner_is_explicit=bool(item.owner_user_id.strip() or item.owner_name.strip()),
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
                from_task_id, to_task_id = relation.endpoints(task_id)
                resolution.add_relation(
                    from_task_id=from_task_id,
                    to_task_id=to_task_id,
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
            report_project_title = ""
            if item.project_proposal is not None:
                proposal = item.project_proposal
                if proposal.authority == "meeting_decision":
                    if (
                        item.evidence_origin != "current"
                        or item.source_ref != work_item.source.ref
                        or not (
                            work_item.source.type is WorkItemSourceType.AI_MINUTES
                            or work_item.context.source_conversation_kind is WorkItemSourceKind.MINUTES
                        )
                        or not source_contains_quote(work_item.summary, proposal.source_excerpt)
                    ):
                        raise ValueError("project proposal must cite an exact quote in the current meeting source")
                else:
                    report_project_title = _report_project_registry_title(
                        work_item, proposal.source_excerpt
                    )
                    if (
                        item.evidence_origin != "current"
                        or item.source_ref != work_item.source.ref
                        or work_item.source.type.value != proposal.authority
                        or not report_project_title
                        or proposal.title != report_project_title
                    ):
                        raise ValueError("project proposal title and authority must match the cited report registry row")
            if report_project_title and cluster_id is None:
                existing_cluster = db.execute(
                    """
                    select c.id from business_work_clusters c
                    join business_work_cluster_tasks ct on ct.cluster_id=c.id
                    where c.title=? and ct.task_id=? limit 1
                    """,
                    (report_project_title, task_id),
                ).fetchone()
                if existing_cluster is not None:
                    cluster_id = int(existing_cluster["id"])
                else:
                    cluster_id = resolution.create_cluster(
                        title=report_project_title, task_ids=[task_id], _db=db
                    )
            report_project_id = None
            if report_project_title:
                project = resolution.register_source_project(
                    title=report_project_title,
                    registry_source=f"{work_item.source.type.value}:{work_item.source.ref}",
                    _db=db,
                )
                report_anchor_id = project.canonical_anchor_id
                applied_project_anchor_id = report_anchor_id
                report_project_id = project.id
                resolution.confirm_anchor_match(
                    task_id=task_id,
                    anchor_id=report_anchor_id,
                    evidence_signal_id=result.signal_id,
                    reason=f"{work_item.source.type.value} 的项目登记表明确列出该项目。",
                    relevance=BusinessRelevance.RELEVANT,
                    _db=db,
                )
            for anchor_match in item.anchor_match_proposals:
                resolution.propose_anchor_match(
                    task_id=task_id, anchor_id=anchor_match.anchor_id,
                    evidence_signal_id=result.signal_id, reason=anchor_match.reason, _db=db,
                )
            if item.project_proposal is not None and not report_project_title:
                proposal = item.project_proposal
                project = resolution.register_source_project(
                    title=proposal.title,
                    registry_source=f"{proposal.authority}:{item.source_ref}",
                    _db=db,
                )
                anchor_id = project.canonical_anchor_id
                applied_project_anchor_id = anchor_id
                resolution.confirm_anchor_match(
                    task_id=task_id,
                    anchor_id=anchor_id,
                    evidence_signal_id=result.signal_id,
                    reason=proposal.reason,
                    relevance=BusinessRelevance.RELEVANT,
                    _db=db,
                )
            if item.project_link_proposal is not None:
                link = item.project_link_proposal
                project = db.execute(
                    """select p.title as project_title, a.title as anchor_title
                       from business_projects p join business_anchors a
                         on a.id=p.canonical_anchor_id
                       where p.canonical_anchor_id=? and a.anchor_type='project' and a.active=1""",
                    (link.anchor_id,),
                ).fetchone()
                if project is None:
                    raise ValueError("existing Project link requires an active registered official Project")
                if (
                    item.evidence_origin != "current"
                    or item.source_ref != work_item.source.ref
                    or not source_contains_quote(work_item.summary, link.source_excerpt)
                    or not source_contains_quote(work_item.summary, item.source_excerpt)
                    or not (item.source_excerpt in link.source_excerpt or link.source_excerpt in item.source_excerpt)
                    or not any(title in link.source_excerpt for title in (
                        project["project_title"], project["anchor_title"],
                    ))
                ):
                    raise ValueError("existing Project link must cite the current exact Task action naming its Project")
                resolution.confirm_anchor_match(
                    task_id=task_id, anchor_id=link.anchor_id, evidence_signal_id=result.signal_id,
                    reason=link.reason, relevance=BusinessRelevance.RELEVANT, _db=db,
                )
                applied_project_anchor_id = link.anchor_id
            if applied_project_anchor_id is not None:
                project_links.add((task_id, applied_project_anchor_id))
            project_candidate = item.project_candidate_proposal
            if report_project_id is not None:
                existing_candidate = db.execute(
                    """
                    select id from business_project_candidates
                    where cluster_id=? and title=? and status='proposed' limit 1
                    """,
                    (cluster_id, report_project_title),
                ).fetchone()
                if existing_candidate is not None:
                    resolution.confirm_project_candidate(
                        candidate_id=int(existing_candidate["id"]),
                        project_id=report_project_id,
                        evidence_signal_id=result.signal_id,
                        _db=db,
                    )
                    project_links.update(
                        (int(link["task_id"]), int(link["anchor_id"]))
                        for link in db.execute(
                            """select distinct link.task_id, link.anchor_id
                               from business_task_anchor_links link
                               join business_work_cluster_tasks member on member.task_id=link.task_id
                               where member.cluster_id=? and link.anchor_id=?
                                 and link.status='confirmed' and link.active=1
                                 and link.evidence_signal_id=?""",
                            (cluster_id, applied_project_anchor_id, result.signal_id),
                        )
                    )
            elif project_candidate is not None:
                candidate_cluster_id = project_candidate.cluster_id
                existing_candidate = db.execute(
                    """
                    select id from business_project_candidates
                    where cluster_id=? and title=? and status='proposed' limit 1
                    """,
                    (candidate_cluster_id, project_candidate.title),
                ).fetchone()
                if existing_candidate is None:
                    resolution.propose_project(
                        cluster_id=candidate_cluster_id, title=project_candidate.title,
                        reason=project_candidate.reason, _db=db,
                    )
            if item.attention_proposal is not None:
                attention_anchor_id = item.attention_proposal.anchor_id
                if attention_anchor_id is None:
                    attention_anchor_id = applied_project_anchor_id
                if attention_anchor_id is None:
                    raise ValueError("attention proposal requires this decision's applied Project anchor")
                attention.append(AppliedTaskAttention(
                    decision=item, task_id=task_id, signal_id=result.signal_id,
                    anchor_id=attention_anchor_id,
                ))

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
                audit_summary="; ".join(filter(None, (item.update_summary for item in decision.task_decisions))),
                memory_recall_used=any(item.memory_recall_used for item in decision.task_decisions),
            )
    result = TaskAgentApplyResult(
        task_ids=tuple(task_ids),
        attention_proposals=tuple(attention),
        affected_task_ids=tuple(dict.fromkeys(affected_task_ids)),
        skipped_reasons=tuple(skipped_reasons),
        project_links=tuple(sorted(project_links)),
    )
    receipt = _projection_receipt(work_item, decision, result)
    result = replace(result, projection_receipt=receipt)
    if _db is None:
        if recorded_run_id is not None:
            _save_projection_receipt(store, recorded_run_id, receipt)
        receipt = _project_task_attention(store, result.attention_proposals, result.affected_task_ids, receipt=receipt)
        result = replace(result, projection_receipt=receipt)
        if recorded_run_id is not None:
            _save_projection_receipt(store, recorded_run_id, receipt)
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
    proposals: tuple[AppliedTaskAttention, ...],
    affected_task_ids: tuple[int, ...],
    *,
    receipt: TaskAttentionProjectionReceipt,
) -> TaskAttentionProjectionReceipt:
    applied_ids: set[int] = set()
    groups: dict[int, list[AppliedTaskAttention]] = {}
    for applied in proposals:
        groups.setdefault(applied.anchor_id, []).append(applied)
    for anchor_id, group in groups.items():
        shapes = {
            json.dumps(entry.decision.attention_proposal.model_dump(
                mode="json", exclude={"anchor_id", "related_task_ids"}
            ), sort_keys=True)
            for entry in group
        }
        if len(shapes) != 1:
            receipt.outcomes.extend(
                TaskAttentionProjectionOutcome(
                    task_id=entry.task_id, anchor_id=anchor_id, status="rejected",
                    reason="multiple distinct proposals for the same Project",
                ) for entry in group
            )
            continue
        applied = group[0]
        item, signal_id = applied.decision, applied.signal_id
        proposal = item.attention_proposal
        assert proposal is not None
        try:
            if anchor_id not in {
                project.canonical_anchor_id for project in store.list_business_projects()
            }:
                raise ValueError("attention requires a registered official Project")
            task_ids = {entry.task_id for entry in group}
            task_ids.update(
                value for entry in group
                for value in entry.decision.attention_proposal.related_task_ids
            )
            projection = BusinessAttentionProjection(store)
            with store.business_task_transaction() as db:
                eligible = set(projection._current_eligible_task_ids(
                    task_ids=tuple(sorted(task_ids)), anchor_id=anchor_id, db=db
                ))
                if eligible != task_ids:
                    raise ValueError("every supporting Task must be relevant, open/waiting and confirmed to this active Project")
                existing = store.get_business_attention_item_by_stable_key_in_transaction(
                    stable_key=f"project:{anchor_id}", _db=db
                )
                if existing is not None:
                    old_ids = tuple(
                        link.task_id for link in store.list_business_attention_tasks_in_transaction(
                            attention_item_id=existing.id, _db=db
                        )
                    )
                    task_ids.update(projection._current_eligible_task_ids(
                        task_ids=old_ids, anchor_id=anchor_id, db=db
                    ))
            linked_signal_ids = {
                link.signal_id for member in eligible
                for link in store.list_business_task_evidence(member)
            }
            verified = {}
            for entry in group:
                for evidence in entry.decision.attention_proposal.evidence:
                    source = store.get_business_task_signal(
                        evidence.signal_id if evidence.signal_id is not None else entry.signal_id
                    )
                    if source is None or source.source_ref != evidence.source_ref:
                        raise ValueError("attention evidence signal/source_ref does not match")
                    if source.source_type in {"session_provenance", "memory_provenance"}:
                        raise ValueError("attention evidence must be observed original source, not cited provenance")
                    if not source_contains_quote(source.evidence_text, evidence.source_excerpt):
                        raise ValueError("attention quote is absent from original source")
                    if source.id not in linked_signal_ids:
                        raise ValueError("attention evidence must be linked to a qualifying supporting Task in this Project")
                    verified_entry = {
                        "signal_id": source.id, "source_ref": source.source_ref,
                        "source_excerpt": evidence.source_excerpt,
                        "source_time": source.source_time,
                        "source_link": json.loads(source.context_json).get("source_link", ""),
                    }
                    verified[json.dumps(verified_entry, ensure_ascii=False, sort_keys=True)] = verified_entry
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
                evidence_signal_id=signal_id,
                assessment_json=json.dumps({
                    "assessment_basis": proposal.assessment_basis,
                    "material_trigger": proposal.material_trigger,
                    "inference": proposal.why_attention,
                    "evidence": [verified[key] for key in sorted(
                        verified, key=lambda key: (verified[key]["signal_id"] != signal_id, key)
                    )],
                }, ensure_ascii=False, sort_keys=True),
            )
        except ValueError as exc:
            receipt.outcomes.extend(
                TaskAttentionProjectionOutcome(task_id=entry.task_id, anchor_id=anchor_id,
                                               status="rejected", reason=str(exc))
                for entry in group
            )
            continue
        except Exception as exc:
            receipt.outcomes.extend(
                TaskAttentionProjectionOutcome(task_id=entry.task_id, anchor_id=anchor_id,
                                               status="error", reason=str(exc))
                for entry in group
            )
            continue
        try:
            attention_id = projection.upsert(effective)
            applied_ids.add(attention_id)
            receipt.outcomes.extend(
                TaskAttentionProjectionOutcome(task_id=entry.task_id, anchor_id=anchor_id,
                                               attention_id=attention_id, status="applied")
                for entry in group
            )
        except Exception as exc:
            receipt.outcomes.extend(
                TaskAttentionProjectionOutcome(task_id=entry.task_id, anchor_id=anchor_id,
                                               status="error", reason=str(exc))
                for entry in group
            )
    try:
        BusinessAttentionProjection(store).recompute_for_tasks(affected_task_ids)
    except Exception as exc:
        receipt.recompute_error = str(exc)
    receipt.applied_count = len(applied_ids)
    failed = bool(receipt.recompute_error or any(outcome.status != "applied" for outcome in receipt.outcomes))
    receipt.status = (
        "partial" if failed and applied_ids else "failed" if failed
        else "completed" if receipt.proposal_count else "no_proposal"
    )
    return receipt


def _projection_receipt(
    work_item: WorkItem, decision: TaskAgentDecision, result: TaskAgentApplyResult,
) -> TaskAttentionProjectionReceipt:
    report_source = work_item.source.type in {
        WorkItemSourceType.MANAGEMENT_WEEKLY_REPORT, WorkItemSourceType.PROJECT_WEEKLY_REPORT,
        WorkItemSourceType.DEPARTMENT_WEEKLY_REPORT,
    }
    registry_rows = None
    if report_source:
        markdown = _report_markdown(work_item)
        registry_rows = sum(
            bool(_report_project_registry_title(work_item, line))
            for line in markdown.splitlines() if line.strip()
        )
    unapplied = [
        item for item in decision.task_decisions
        if item.attention_proposal is not None
        and not any(applied.decision is item for applied in result.attention_proposals)
    ]
    return TaskAttentionProjectionReceipt(
        status="pending", source_type=work_item.source.type.value,
        task_decision_count=len(decision.task_decisions), project_link_count=len(result.project_links),
        registry_row_count=registry_rows,
        proposal_count=sum(item.attention_proposal is not None for item in decision.task_decisions),
        outcomes=[TaskAttentionProjectionOutcome(
            task_id=item.task_id, anchor_id=item.attention_proposal.anchor_id,
            status="rejected", reason="proposal has no applied Task decision",
        ) for item in unapplied],
    )


def _save_projection_receipt(
    store: AutoReplyStore, run_id: int, receipt: TaskAttentionProjectionReceipt,
) -> None:
    try:
        store.record_task_agent_projection(run_id, receipt.model_dump_json())
    except Exception:
        LOGGER.exception("Task committed but projection receipt could not be saved run_id=%s", run_id)


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
        decision = _canonicalize_current_source_provenance(
            decision, work_item=work_item
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
            receipt = apply_result.projection_receipt
            assert receipt is not None
            store.record_task_agent_projection(active_run_id, receipt.model_dump_json(), _db=db)
        committed_run_id = active_run_id
        active_run_id = None
    except Exception as exc:
        if active_run_id is not None:
            store.finish_task_agent_run(active_run_id, status="failed", error=str(exc))
        store.mark_work_summary_input_failed(work_input.id, str(exc))
        raise
    try:
        receipt = _project_task_attention(store, apply_result.attention_proposals, apply_result.affected_task_ids, receipt=receipt)
        _save_projection_receipt(store, committed_run_id, receipt)
    except Exception:
        LOGGER.exception("Task committed but projection did not finish run_id=%s", committed_run_id)
