from __future__ import annotations

import json
import sys
from collections.abc import Callable, Mapping
from datetime import datetime
from hashlib import sha256
from pathlib import Path
from uuid import uuid4

from app.agent_context import _AUDIT_AGENT_RULES, _CONSUMER_AGENT_RULES, AgentTaskContext
from app.action_contract_catalog import action_contract_catalog, system_action_contract_document
from app.agent_contracts import (
    DINGTALK_MESSAGE_CHANNELS,
    AuditAgentResult,
    AuditFeedback,
    ConsumerAgentResult,
    ConsumerOutcome,
    ProposedAction,
    dingtalk_chat_delivery,
)
from app.prompt_schema import _compact_prompt_schema
from app.consumer_purpose import purpose_for_context
from app.agent_result import ResultParseError
from app.agent_effects import LEASE_SECONDS
from app.agent_runtime_config import AgentRuntimeConfig
from app.agent_runtime_contracts import RuntimeKind
from app.decision_quality import require_needs_human_context_eligible
from app.agent_runtime_router import AgentRuntimeRouter
from app.agent_turn_runner import (
    AgentTurnProcess,
    AgentTurnRunResult,
    ProcessExecutor,
    repeated_result_failure_requires_fresh_session,
    result_correction_prompt,
)
from app.agent_wire_contracts import (
    AuditAgentWireResult,
    ConsumerAgentWireResult,
    consumer_wire_model,
    parse_consumer_agent_wire_result,
)
from app.audit_rules import validate_audit_rules_text
from app.business_skills import (
    BUNDLED_BUSINESS_SKILL_NAMES,
    default_skill_catalog,
    frozen_task_skill_names,
    frozen_task_skill_binding,
    installed_runtime_skills,
    render_business_skill_protocol,
)
from app.managed_skills import RuntimeSkillSnapshot
from app.claude_runtime_adapter import ClaudeRuntimeAdapter
from app.codex_history import find_codex_session_path
from app.native_prompt_input import codex_session_wire_schema as read_codex_session_wire_schema
from app.native_agent_output import native_output_schema_path
from app.codex_runner import _codex_home
from app.codex_runtime_adapter import CodexRuntimeAdapter
from app.friday_runtime_adapter import FridayRuntimeAdapter
from app.config import principal_display_name
from app.prompt import runtime_context_instruction
from app.prompt_composition import (
    CONSUMER_OUTBOUND_INSTRUCTION,
    PromptConfiguration,
    PromptSection,
    compose_consumer_task_assembly,
    load_prompt_configuration,
    prompt_section_facts,
    render_prompt_sections,
)
from app.runtime_prompt_context import explicit_participant_timezones
from app.native_trajectory import codex_session_has_final_user_marker_without_compaction
from app.codex_history import count_codex_session_lines
from app.service_message_sender import ServiceMessageSender, agent_message_delivery_key
from app.store import AgentRole, AutoReplyStore, ReplyTask
from app.wechat.codex_safety import ControlledCliConfig, make_consumer_agent_command

SERVICE_ROOT = Path(__file__).resolve().parent.parent
# The Consumer's output schema is generated from ConsumerAgentResult at prompt
# time (_schema_json), with output-only constraints such as the bounded human
# reason. New output is parsed through the strict wire contract, while
# parse_persisted_consumer_result remains permissive for historical records.
# The reviewable snapshot is kept equal to the generated model schema by tests.
# It is deliberately not read here: a file is live the moment it is saved and
# code is live only after a restart, so comparing them at runtime could turn an
# editor's intermediate state into an outage.
DYNAMIC_SKILL_MARKER = "[dynamic-skill]"
CONSUMER_UNREVIEWED_EFFECT = "consumer_unreviewed_provider_effect"
_CONSUMER_FINGERPRINT_TIME = "2000-01-01T00:00:00+00:00"


def _consumer_source_context_fingerprint(
    *,
    task_id: int,
    execution_generation: str,
    contract_hash: str,
    developer_instructions: str,
    stable_task_prompt: str,
    image_sources: tuple[str, ...],
) -> str:
    encoded = json.dumps(
        {
            "task_id": task_id,
            "execution_generation": execution_generation,
            "contract_hash": contract_hash,
            "developer_instructions": developer_instructions,
            "stable_task_prompt": stable_task_prompt,
            "image_sources": image_sources,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return sha256(encoded.encode("utf-8")).hexdigest()


def _consumer_revision_prompt(
    feedback: AuditFeedback,
    *,
    proposal_revision: int,
    continuation: str,
    context_marker: str,
    current_time: str,
) -> str:
    feedback_json = json.dumps(
        feedback.model_dump(mode="json"),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    sections = [
        "Use the original task, source context, and immediately preceding proposal "
        "already present in this same Consumer session. Apply all Audit feedback "
        f"for proposal revision {proposal_revision}, then return a complete "
        "replacement Consumer candidate. Preserve supported facts, uncertainty, "
        "and valid source references.",
        "## Current turn execution time\n" + current_time.strip(),
        "## Audit Feedback\n" + feedback_json,
    ]
    if continuation.strip():
        sections.append("## Current Runtime Correction\n" + continuation.strip())
    sections.append("<!-- " + context_marker + " -->")
    return "\n\n".join(sections)


def _consumer_revision_context_reusable(
    store: AutoReplyStore,
    task: ReplyTask,
    *,
    proposal_revision: int,
    session_id: str | None,
    fingerprint: str,
    parent_agent_run_id: int | None,
    feedback: AuditFeedback,
) -> bool:
    if proposal_revision <= 0 or not session_id or not fingerprint or not parent_agent_run_id:
        return False
    audit_run = store.get_agent_run(parent_agent_run_id, load_events=False)
    if (
        audit_run is None
        or audit_run.role is not AgentRole.AUDIT
        or audit_run.status != "completed"
        or audit_run.reply_task_id != task.id
        or audit_run.execution_generation != task.execution_generation
        or audit_run.proposal_revision != proposal_revision - 1
        or audit_run.parent_agent_run_id is None
    ):
        return False
    prior_run = store.get_agent_run(audit_run.parent_agent_run_id, load_events=False)
    if (
        prior_run is None
        or prior_run.role is not AgentRole.CONSUMER
        or prior_run.status != "completed"
        or prior_run.reply_task_id != task.id
        or prior_run.execution_generation != task.execution_generation
        or prior_run.proposal_revision != proposal_revision - 1
        or prior_run.codex_session_id != session_id
    ):
        return False
    try:
        adopted_audit = AuditAgentResult.model_validate_json(
            audit_run.adopted_result_json
        )
    except (TypeError, ValueError):
        return False
    if adopted_audit.outcome not in {"return", "reject"} or adopted_audit.feedback != feedback:
        return False

    def completed_codex_attempt(run_id: int):
        return next(
            (
                attempt
                for attempt in reversed(store.list_agent_runtime_attempts(run_id))
                if attempt.status == "completed"
                and attempt.runtime_kind == RuntimeKind.CODEX_CLI.value
                and attempt.session_id == session_id
                and attempt.transcript_end > attempt.transcript_start
            ),
            None,
        )

    prior_attempt = completed_codex_attempt(prior_run.id)
    if prior_attempt is None:
        return False
    codex_home = _codex_home()
    try:
        if count_codex_session_lines(
            session_id, codex_home=codex_home
        ) != prior_attempt.transcript_end:
            return False
    except (OSError, UnicodeError):
        return False

    initial_run = next(
        (
            run
            for run in store.list_agent_runs_for_task_generation(
                task.id, task.execution_generation, load_events=False
            )
            if run.role is AgentRole.CONSUMER
            and run.status == "completed"
            and run.proposal_revision == 0
            and run.codex_session_id == session_id
        ),
        None,
    )
    if initial_run is None:
        return False
    initial_attempt = completed_codex_attempt(initial_run.id)
    if initial_attempt is None:
        return False
    marker = "consumer-source-context-sha256:" + fingerprint
    return codex_session_has_final_user_marker_without_compaction(
        session_id,
        marker,
        codex_home=codex_home,
        context_start_line=initial_attempt.transcript_start,
        attempt_start_line=prior_attempt.transcript_start,
        attempt_end_line=prior_attempt.transcript_end,
        session_end_line=prior_attempt.transcript_end,
    )

CONSUMER_DYNAMIC_SKILL_SENTENCE = (
    "Consumer Agent A independently selects and applies every applicable business and operation Skill before forming the candidate. Provider command names, MCP tools, receipts, and readback procedures belong to the Agent/runtime capability and are not application review conditions."
)
AUDIT_DYNAMIC_SKILL_SENTENCE = (
    "Audit Agent B independently selects and applies every applicable business and operation Skill to the typed candidate. Review the whole candidate with approve, return or reject; execution is owned by system code. Provider command names, MCP tools, receipts, and readback procedures remain runtime-owned."
)
AUDIT_DYNAMIC_SKILL_COMPATIBILITY = f"{DYNAMIC_SKILL_MARKER} {AUDIT_DYNAMIC_SKILL_SENTENCE}"
CONSUMER_DYNAMIC_SKILL_BODY = f"{DYNAMIC_SKILL_MARKER} {CONSUMER_DYNAMIC_SKILL_SENTENCE}"
AUDIT_DYNAMIC_SKILL_BODY = AUDIT_DYNAMIC_SKILL_COMPATIBILITY
CORE_DYNAMIC_SKILL_BODY = f"{CONSUMER_DYNAMIC_SKILL_BODY} {AUDIT_DYNAMIC_SKILL_SENTENCE}"
DECISION_QUALITY_GATE_INSTRUCTIONS = """
## Decision Evidence
Include risk, confidence, rule_coverage and information_completeness as truthful
facts about the current decision. A purpose retry waits on an already verified
access request: keep the business coverage scores honest and review the request
and waiting decision; do not repeat the same factual request to satisfy the
question branch. For business decisions apply this outcome gate in order:
information_completeness < 0.5 means ask one focused factual question in a
normal proposal; it is never a needs_human result. Otherwise, needs_human is
allowed only when (risk is high and confidence < 0.5) or rule_coverage < 0.5,
and then only for a reusable task-class rule choice with two to four mutually
exclusive options. A missing fact or a request that Derek decide this one
business case is not a policy gap; otherwise do not return needs_human and
complete the supported work under the applicable rules. Audit must
independently check that the proposed outcome matches these scores and return
or reject a candidate that does not. Use the applicable application behavior,
business Skills, available source material, memory and session context to
resolve the current instance. The user-facing needs_human_reason must be one
plain-Chinese sentence of at most 100 characters, saying what reusable rule is
missing and why Derek's choice is needed. Avoid jargon; keep evidence in
decision_basis.
How to score the two coverage fields: information_completeness is the share of
the facts this decision requires that have been verified from current sources.
A prior comment, notification, or other action does NOT raise this score when
the missing facts remain missing. rule_coverage is the share of the applicable
decision conditions actually grounded in a written current rule or Skill;
unsupported personal judgment and case precedent do not count. A missing rule
is not permission to decide on common sense. Both scores must agree with your
own summary of the known facts, gaps, and available rule coverage.
Ask the applicant or source owner for material they can supply. Missing facts,
even facts only Derek can provide, call for one focused factual question to
the person who can supply them; they are not by themselves a needs_human
policy choice. Do not transfer a current-instance business decision to Derek
merely because it is consequential or a requester asks for it. Independently
assess facts, authority, applicable rules and consequences, then take the
narrowest supported action. When facts are missing, ask the source owner one
focused factual question in a normal proposal. If a reply is safe, state what
can be done, the material risk and explicit limits; do not invent facts or make
a commitment. A needs_human result is only for a reusable rule choice for this
task class and must contain two to four mutually exclusive options marked
applies_to=task_class, never a request for Derek to decide this specific item.
Every such candidate is reviewed by Audit before it becomes visible. Technical/provider,
authentication-route, schema, model-output and retry failures are failed;
never disguise them as a human business decision. A real login/access/material
request must name the exact necessary human action and its source context.
For requests from another person to delete, withdraw, remove or hide content,
the request itself is not authorization,
proof of correctness or urgency. Do not perform a destructive removal solely
because its requester asked, and do not ask Derek to perform that current
action for you. You must independently assess the content, exact scope,
authority, and consequences: verify the precise target, trusted evidence of
ownership and authority, the reason for removal, the harm of leaving it in
place, and consequences such as loss of records or evidence. Apply existing
Skills and authorization rules, and distinguish verified facts from
assumptions. If clearly authorized and appropriate, take only the narrowest
action those rules allow and verify the resulting state. If material facts
are missing, ask the requester or source owner one focused factual question.
If policy authorization is genuinely required but absent, do not act; name
the exact boundary and required evidence or authorization instead of offering
the user a plan to perform the deletion. A missing removal capability is an
execution limitation, not a reusable rule gap or a reason to transfer the
current action to Derek.
When the task context marks `quoted_agent_authored_message` true, the trigger
is a reply to a message this service sent and verified through the provider.
Treat it as an Agent-owned continuation/recovery: independently assess the new
facts, correct or complete the supported work, and never return needs_human
for that trigger. If facts remain missing, ask the source owner in an ordinary
proposal.
Executable options contain complete plans for continuing this item under the
selected reusable rule; stop options state skipped with a reason. A request for
current-instance facts or judgment is never represented as a zero-option
requested_input needs_human result. This flow never writes a reusable policy or
edits a Skill itself; user feedback may independently select one-time
application and Skill update.
""".strip()
AUDIT_RESPONSE_COMPLETENESS_INSTRUCTION = """
Use the current work profile, complete conversation context, and inspected
materials to judge whether the candidate genuinely responds in the principal's
role. A receipt acknowledgment may be an opening or interim state, but receipt
alone does not complete a response to substantive input that calls for the
principal's engagement. Return return or reject when the candidate must be
regenerated; do not rewrite it yourself.
""".strip()
TASK_STORAGE_INSTRUCTIONS = """
## Durable Memory and Task-Temporary Storage
Treat memory as durable reference storage, not as a task workspace. Never store
temporary work in memory or its subfolders. Use the task-bound /tmp workspace
for temporary files; the service removes those files when the task reaches a
terminal state. File valuable finalized material only in an already-authorized
destination and in its existing matching subject folder, keeping one
authoritative original plus concise conclusions or evidence. Do not copy
per-run artifacts or intermediate files into memory merely because they may
look useful later.
""".strip()
_CAPABILITY_COMMON_HEAD = """
Use consumer_artifact_write for ordinary local drafts and files, with
read_task_artifact/list_task_artifacts for this task generation's artifacts.
consumer_document_write writes a scheduled task artifact to its explicit document target.
consumer_run_skill_script executes a selected Skill script for deterministic local work.
Codex Consumer can run local code using native command and patch tools in its
current task workspace. The native workspace sandbox keeps writes in that
workspace and native temporary directories, and disables command network
access. Claude has declared read and artifact tools only.
Do not try nested CLI inventory, nested Agents,
commands or a different channel to reach an unavailable operation.
Use memory_recall for relevant
stable context; memory never proves current external state or recipient scope.
Before you finish, check whether this turn confirmed anything that should
outlive it: a person's preference, a decision, a reusable rule, a stable
business fact, or the explicit next step of unfinished work. If so, record each
as its own memory_write (one to three plain sentences, people named by name,
created_at the time the information arose). Skip temporary tasks, logs, code,
one-off errors, unconfirmed guesses, sensitive original text, secrets, and
anything already in Memory, including what an earlier revision of this task
already wrote.
Use only stable provider identities established by the current source for the
exact operation; a similarly named business object is not identity evidence.
""".strip()
_DIRECT_SOURCE_READ_INSTRUCTIONS = """
For DingTalk reads in service Consumer turns, prefer the declared
`mcp__agent_cli` read tools. The Consumer may use local `dws` commands through
shell for source reads when appropriate. Never use `dws` through shell to send
DingTalk messages or email, or to perform OA approve, reject, return, or comment
actions; those effects must use the reviewed typed-action path. Audit must use
declared read tools only and must not use shell or local `dws` commands. If the
matching native read tool is not declared, report that exact capability as
unavailable before choosing an appropriate read path.
When a declared read returns source bytes or page references requiring OCR,
call the native `ai_services.ocr_document` MCP directly with those exact source
bytes, MIME type, page numbers and source digest. Do not infer that this tool
is absent from a nested `ALL_TOOLS` catalog, shell inventory or another Agent's
tool list; those do not describe this native Consumer/Audit session. If the
direct call itself fails, preserve its concrete tool/provider error as the
technical read failure.
""".strip()
_OA_OCR_HANDOFF_INSTRUCTIONS = """
In Codex OA turns, use `functions.exec` to call the declared `mcp__agent_cli`
tools. When a read returns `ocr_input_base64`, pass that value to
`mcp__ai_services__ocr_document` as `data_base64` in the same JS cell, with a
filename and its MIME type. For a rendered single-page image, do not pass its
original PDF page number as OCR `page_numbers`; keep that number for the result.
Print only OCR text, page mapping, confidence, warnings and the source SHA-256
(`ocr_source_sha256` or `sha256`), never the image bytes.
Do not print or recopy base64 through a later prompt. For an OA attachment that
the native reader cannot access because preview permission is missing, only the
OA Consumer may use shell for the exact attachment fallback described by the
selected OA Skill: the `dws oa approval attachment authorize-preview` command,
followed by the documented read/download path for that same file. The OA
Consumer may also use `dws` through shell for other source reads. Never use
`dws` to send DingTalk messages or email, or for OA approve, reject, return, or
comment actions; OA effects must use the reviewed typed-action path. Audit must
not use shell or local `dws` commands. If the read or OCR call fails, preserve
its concrete tool/provider error.
""".strip()
_CAPABILITY_COMMON_TAIL = """
If no declared tool or callable selected Skill script can perform an operation,
first decide whether the task can still be completed from the information
already available. If it can, complete it, say which operation was missing and
what it would have added, and do not fail. Return failed with error_code
`operation_unavailable` only when the missing operation is itself the required
effect, or the result cannot be made truthful without it. A declared tool that
fails is a different error; preserve its actual failure code.
When a required DWS, MCP, or other external-source read actually fails because
the dependency is unavailable, return failed with error_code
`dependency_read_unavailable` and preserve the exact underlying error in the
summary. Do not use this code when a successful read returns no match, evidence
is incomplete or ambiguous, or a governing rule is missing; those are evidence
or business-rule outcomes, not infrastructure outages.
An unresolved provider-risk refusal for the current intended action remains
failed with its original error code and no retry; it cannot
become completed or no_action. Historical refusal evidence never authorizes a
new effect, identity or channel to evade the refusal.
A genuine human-only fact or action needs its exact context
and must be formulated by Consumer and reviewed before it is requested.
A low-consequence operating choice is autonomous when the applicable rules and
facts support it. Do not ask Derek merely because an equivalent default exists.
A bounded fact-finding inquiry may be proposed autonomously when it states its
risk boundary and makes no purchase, budget or partnership commitment.
A real provider read outage stays failed and cannot become a substitute
notification. Human choices apply only to the current instance, with complete
bound plans or an explicit stop reason. They never edit Skills or reusable
policies.
Wire results use error_code, error_retryable, error_authorization_required;
do not return a nested error object. Match the supplied wire schema exactly.
""".strip()
AGENT_CAPABILITY_INSTRUCTIONS = "\n".join((
    _CAPABILITY_COMMON_HEAD, _DIRECT_SOURCE_READ_INSTRUCTIONS,
    _CAPABILITY_COMMON_TAIL,
)) + "\n\n" + TASK_STORAGE_INSTRUCTIONS
OA_AGENT_CAPABILITY_INSTRUCTIONS = "\n".join((
    _CAPABILITY_COMMON_HEAD, _OA_OCR_HANDOFF_INSTRUCTIONS,
    _CAPABILITY_COMMON_TAIL,
)) + "\n\n" + TASK_STORAGE_INSTRUCTIONS


def system_action_contracts_text() -> str:
    return action_contract_catalog()


def consumer_wire_contract_hash(
    runtime_skill_snapshot: RuntimeSkillSnapshot | None = None,
    *, skill_protocol_override: str | None = None,
    prompt_configuration: PromptConfiguration | None = None,
) -> str:
    """Fingerprint stable Consumer output, instructions, and read-tool policy."""
    configuration = prompt_configuration or load_prompt_configuration()
    contract = {
        "consumer_rules": _CONSUMER_AGENT_RULES,
        "role_boundary": CONSUMER_ROLE_BOUNDARY,
        "system_action_contracts": system_action_contract_document(),
        "agent_capability_instructions": AGENT_CAPABILITY_INSTRUCTIONS,
        # The runtime Skill tree is the single source the Agent reads; the
        # snapshot below records which revisions were in force, it is not the
        # content the Agent is served.
        "business_skill_protocol": render_business_skill_protocol(
            installed_runtime_skills(names=BUNDLED_BUSINESS_SKILL_NAMES)
            + default_skill_catalog(), compact=True,
        ),
        "work_profile_instruction": configuration.work_profile,
        "prompt_configuration": configuration.fingerprints(),
        "wire_schema": ConsumerAgentWireResult.model_json_schema(),
        "codex_multi_agent": False,
        "codex_apps": False,
        "runtime_skill_snapshot": (
            runtime_skill_snapshot.protocol() if runtime_skill_snapshot is not None else ""
        ),
        "skill_protocol_override": skill_protocol_override or "",
    }
    encoded = json.dumps(contract, sort_keys=True, separators=(",", ":"))
    return sha256(encoded.encode("utf-8")).hexdigest()


def consumer_selected_skill_source_fingerprints(
    selected_names: tuple[str, ...], frozen_materials: tuple[object, ...]
) -> tuple[dict[str, str], ...]:
    """Fingerprint the exact selected Skill bodies visible to this Consumer turn."""
    frozen_by_name: dict[str, list[str]] = {}
    for material in frozen_materials:
        frozen_by_name.setdefault(str(material.name), []).append(
            sha256(str(material.content).encode("utf-8")).hexdigest()
        )
    fingerprints: list[dict[str, str]] = []
    for name in sorted(set(selected_names)):
        frozen_digests = sorted(frozen_by_name.get(name, ()))
        if frozen_digests:
            fingerprints.extend(
                {"name": name, "source": "frozen_task_input", "sha256": digest}
                for digest in frozen_digests
            )
            continue
        sources = installed_runtime_skills(names=(name,))
        if not sources:
            fingerprints.append(
                {"name": name, "source": "selected_source_unavailable", "sha256": ""}
            )
            continue
        for source in sorted(sources, key=lambda entry: str(entry.skill_path)):
            fingerprints.append({
                "name": name,
                "source": str(source.skill_path.resolve()),
                "sha256": sha256(source.skill_path.read_bytes()).hexdigest(),
            })
    return tuple(fingerprints)


def consumer_selected_skill_session_contract_hash(
    fingerprints: tuple[dict[str, str], ...],
) -> str:
    """Build the narrow session key for selected Skill bodies, independent of other contract edits."""
    if not fingerprints:
        return ""
    encoded = json.dumps(fingerprints, sort_keys=True, separators=(",", ":"))
    return "selected-skills:" + sha256(encoded.encode("utf-8")).hexdigest()


def consumer_task_session_contract_hash(selected_skills_hash: str, *, purpose_name: str) -> str:
    if purpose_name != "oa":
        return selected_skills_hash
    payload = selected_skills_hash + "\n" + OA_AGENT_CAPABILITY_INSTRUCTIONS
    return "selected-skills:oa-ocr-handoff:" + sha256(payload.encode("utf-8")).hexdigest()

CONSUMER_ROLE_BOUNDARY = f"""
You are Consumer Agent A. Complete the business preparation using the task's
applicable Skills and permitted tools, including report and document work.
Controlled structured actions are proposals: do not dispatch them yourself.
{CONSUMER_OUTBOUND_INSTRUCTION}
Audit reads and reviews the complete candidate; system code executes its exact
persisted approved plan. Return one valid purpose output using its supplied action/reason/target/payload/evidence_refs schema and assessment metadata. Multi-operation work uses action=plan with the complete proposal in payload.
Every action has a stable action_identity for the same intended external
outcome. Change it only when the intended outcome, target or purpose changes.
Supply canonical typed capability/operation, exact target and payload; no shell
argv. Preserve all accepted service-prepared message text in later stages.
Supply source context, facts and rule evidence, and two to four mutually
exclusive policy choices, each marked applies_to=task_class. Every executable option includes a complete plan for
continuing this item under that selected rule; a stop choice has
terminal_outcome skipped and reason. Ask for missing current-instance facts in
a normal proposal, never with requested_input as a zero-option needs_human.
Feedback may separately select one-time application and Skill update.
For direct business actions, plan, retry, no_action, and failed, decision_options must be empty and
requested_input, needs_human_reason, and decision_basis must be null. Use
those fields only for a needs_human result.
An action plan and a human question are separate candidates. When source work
must run before a later decision, submit the first complete plan. Set
continue_after_execution true only when the next stage can run immediately
from the first action's verified result, such as notifying an applicant after
approval. A request for missing applicant material must use false: its send
receipt proves only that the request arrived, not that the material arrived.
Finish that request stage and review a fresh candidate when a source update
supplies the material. Do not repeat the same request or approve with material
still missing. Prior verified receipts remain evidence; do not mix immediate
actions with unselected option branches.
Use only provider identifiers explicitly established for that operation. A
different business object's ID does not establish the current provider target.
Read the native source to establish a missing target; if it cannot be
established, report the concrete missing dependency instead of inventing one.
The candidate's stated objective must match the complete proposed effects; a
notification or acknowledgment does not perform a different business action.
Audit return may retain actions while adding facts or explanation. Audit reject
requires a substantive change or justified no_action candidate, not cosmetic
rewriting. Runtime failures are failed and do not consume content revisions.
""".strip()
AUDIT_ROLE_BOUNDARY = _AUDIT_AGENT_RULES


# Runtime capabilities every Consumer turn requires before per-turn additions
# (a turn with images also needs image input).  The scheduled-task catalog
# reads this to describe route availability with the Consumer's real baseline.
CONSUMER_BASE_RUNTIME_CAPABILITIES: frozenset[str] = frozenset({"role_bound_agent_tools"})


class ConsumerAgentRunner:
    def __init__(
        self,
        *,
        store: AutoReplyStore,
        workspace: Path,
        codex_bin: str = "codex",
        runtime_config: AgentRuntimeConfig | None = None,
        runtime_router: AgentRuntimeRouter | None = None,
        codex_adapter: CodexRuntimeAdapter | None = None,
        claude_adapter: ClaudeRuntimeAdapter | None = None,
        friday_adapter: FridayRuntimeAdapter | None = None,
        executor: ProcessExecutor | None = None,
        owner: str | None = None,
        refresh_runtime_capabilities: Callable[[], object] | None = None,
        codex_session_exists: Callable[[str], bool] | None = None,
        codex_session_wire_schema: Callable[[str], dict[str, object] | None] | None = None,
        runtime_skill_snapshot: RuntimeSkillSnapshot | None = None,
        forced_runtime_route=None,
        reasoning_effort: str = "",
        skill_protocol_override: str | None = None,
        skill_protocol_source: str = "explicit_custom",
        execution_environment: Mapping[str, str] | None = None,
        source_client=None,
    ) -> None:
        self.store = store
        self.source_client = source_client
        self.workspace = workspace
        self.codex_bin = codex_bin
        self.runtime_config = runtime_config
        self.runtime_router = runtime_router
        self.codex_adapter = codex_adapter
        self.claude_adapter = claude_adapter
        self.friday_adapter = friday_adapter
        self.executor = executor
        self.owner = owner or f"consumer-agent-{uuid4().hex}"
        self.refresh_runtime_capabilities = refresh_runtime_capabilities
        self.codex_session_exists = codex_session_exists or (
            lambda session_id: find_codex_session_path(session_id) is not None
        )
        self.codex_session_wire_schema = (
            codex_session_wire_schema
            if codex_session_wire_schema is not None
            else read_codex_session_wire_schema if codex_session_exists is None else None
        )
        self.runtime_skill_snapshot = runtime_skill_snapshot
        self.forced_runtime_route = forced_runtime_route
        self.reasoning_effort = reasoning_effort
        self.skill_protocol_override = skill_protocol_override
        self.skill_protocol_source = skill_protocol_source
        self.execution_environment = dict(execution_environment or {})

    def _configured_route_names(self) -> tuple[str, ...]:
        config = self.runtime_config or (
            self.codex_adapter.config if self.codex_adapter is not None else None
        )
        return tuple(route.name for route in config.routes) if config else ("codex_oauth",)

    def _route_kind(self, route_name: str) -> RuntimeKind | None:
        config = self.runtime_config or (
            self.codex_adapter.config if self.codex_adapter is not None else None
        )
        if config is None:
            return RuntimeKind.CODEX_CLI if route_name == "codex_oauth" else None
        return next(
            (route.runtime_kind for route in config.routes if route.name == route_name),
            None,
        )

    def _route_session_exists(
        self, route_name: str, session_id: str,
        *, expected_wire_schema: dict[str, object] | None = None,
        expected_legacy_wire_schema: dict[str, object] | None = None,
    ) -> bool:
        route_kind = self._route_kind(route_name)
        if route_kind is None:
            return False
        if route_kind is RuntimeKind.CLAUDE_CLI:
            # Claude owns its remote conversation ledger. The adapter validates
            # the persisted ID before --resume and handles incompatibility safely.
            return True
        if not self.codex_session_exists(session_id):
            return False
        if (
            route_kind is RuntimeKind.CODEX_CLI
            and expected_wire_schema is not None
            and self.codex_session_wire_schema is not None
        ):
            saved_schema = self.codex_session_wire_schema(session_id)
            return saved_schema == expected_wire_schema or (
                expected_legacy_wire_schema is not None
                and saved_schema == expected_legacy_wire_schema
            )
        return True

    def _consumer_route_sessions(
        self, conversation_id: str, session_contract_hash: str = ""
    ) -> dict[str, str]:
        """Return live route sessions for one continuous business conversation.

        Selected Skill turns reuse only the exact Skill-compatible session. Other
        prompt edits retain continuity unless the saved Codex wire schema differs.
        """
        sessions: dict[str, str] = {}
        for route_name in self._configured_route_names():
            session_id = self.store.get_conversation_runtime_session(
                conversation_id,
                route_name,
                required_contract_hash=session_contract_hash or None,
            )
            if session_id:
                sessions[route_name] = session_id
        return sessions

    def _clear_route_session(
        self,
        conversation_id: str,
        route_name: str,
        session_id: str,
        *additional_session_ids: str,
    ) -> None:
        self.store.clear_conversation_runtime_session_if_matches(
            conversation_id,
            route_name,
            session_id,
            additional_expected_session_ids=tuple(
                value for value in additional_session_ids if value
            ),
        )

    @staticmethod
    def _required_capabilities(context: AgentTaskContext) -> frozenset[str]:
        required = set(CONSUMER_BASE_RUNTIME_CAPABILITIES)
        if context.image_paths:
            required.add("image_input")
        # Skill loading is part of the Agent execution environment.  The
        # application consumes the typed result and does not make a receipt
        # for the loaded Skill a route or business-result prerequisite.
        return frozenset(required)

    def run(
        self,
        task: ReplyTask,
        context: AgentTaskContext,
        *,
        proposal_revision: int,
        parent_agent_run_id: int | None,
        feedback: AuditFeedback | None = None,
    ) -> AgentTurnRunResult[ConsumerAgentResult]:
        if context.task_id != task.id:
            raise ValueError("agent context task does not match reply task")
        lock_owner = f"consumer-agent:{task.id}:{task.execution_generation}"
        try:
            with self.store.codex_session_lock(task.conversation_id, lock_owner):
                return self._run_locked(
                    task,
                    context,
                    lock_owner=lock_owner,
                    proposal_revision=proposal_revision,
                    parent_agent_run_id=parent_agent_run_id,
                    feedback=feedback,
                )
        except RuntimeError as exc:
            if str(exc).startswith("codex session locked:"):
                raise RuntimeError("codex_session_locked") from exc
            raise

    def _run_locked(
        self,
        task: ReplyTask,
        context: AgentTaskContext,
        *,
        lock_owner: str,
        proposal_revision: int,
        parent_agent_run_id: int | None,
        feedback: AuditFeedback | None,
    ) -> AgentTurnRunResult[ConsumerAgentResult]:
        context_skill_protocol = (
            context.skill_protocol_override
            if context.skill_protocol_override is not None
            else self.skill_protocol_override
        )
        context_skill_protocol_source = (
            context.skill_protocol_source
            if context.skill_protocol_override is not None
            else self.skill_protocol_source
        )
        skill_protocol_is_custom = (
            context_skill_protocol_source == "explicit_custom"
        )
        configuration = load_prompt_configuration()
        frozen_skill_materials = frozen_task_skill_binding(
            task.trigger_message_json
        ).materials
        selected_skill_source_fingerprints = (
            consumer_selected_skill_source_fingerprints(
                context.skill_names, frozen_skill_materials
            )
        )
        contract_hash = consumer_wire_contract_hash(
            self.runtime_skill_snapshot,
            skill_protocol_override=(
                context_skill_protocol if skill_protocol_is_custom else None
            ),
            prompt_configuration=configuration,
        )
        purpose_name = purpose_for_context(context, task)
        skill_session_contract_hash = consumer_task_session_contract_hash(
            consumer_selected_skill_session_contract_hash(
                selected_skill_source_fingerprints
            ),
            purpose_name=purpose_name,
        )
        expected_wire_schema = json.loads(_schema_json(consumer_wire_model(purpose_name)))
        expected_native_schema = {
            "$native_output_schema_sha256": sha256(
                native_output_schema_path("consumer", purpose=purpose_name).read_bytes()
            ).hexdigest(),
        }
        route_sessions = self._consumer_route_sessions(
            task.conversation_id, skill_session_contract_hash
        )
        # A forced rerun changes the execution generation and prompt, but it
        # must keep the compatible conversation session so the agent sees the
        # original context plus the new feedback.  Route-specific session
        # selection is performed by AgentTurnProcess; passing one persisted
        # id here only supplies a fast-path hint and never clears other routes.
        conversation_session_id = next(iter(route_sessions.values()), None)
        for route_name, route_session_id in tuple(route_sessions.items()):
            expected_route_schema = (
                expected_native_schema
                if self._route_kind(route_name) is RuntimeKind.CODEX_CLI
                else expected_wire_schema
            )
            if not self._route_session_exists(
                route_name,
                route_session_id,
                expected_wire_schema=expected_route_schema,
                expected_legacy_wire_schema=(
                    expected_wire_schema
                    if self._route_kind(route_name) is RuntimeKind.CODEX_CLI
                    else None
                ),
            ):
                self._clear_route_session(
                    task.conversation_id, route_name, route_session_id
                )
                route_sessions.pop(route_name)
        conversation_session_id = next(iter(route_sessions.values()), None)
        force_new_session = repeated_result_failure_requires_fresh_session(
            self.store, task, role=AgentRole.CONSUMER,
            proposal_revision=proposal_revision,
        )
        claim = self.store.claim_agent_run(
            task.id,
            task.execution_generation,
            role=AgentRole.CONSUMER,
            proposal_revision=proposal_revision,
            turn_attempt=self.store.next_agent_run_turn_attempt(
                task.id,
                task.execution_generation,
                role=AgentRole.CONSUMER,
                proposal_revision=proposal_revision,
            ),
            parent_agent_run_id=parent_agent_run_id,
            operation_id="",
            owner=self.owner,
            fresh_session=force_new_session,
            lease_seconds=LEASE_SECONDS,
        )
        if not claim.claimed:
            raise RuntimeError("agent_run_unavailable")
        session_id = None if force_new_session else (
            claim.run.codex_session_id if conversation_session_id is not None else None
        ) or conversation_session_id
        persist_conversation_session = not bool(route_sessions)
        process = AgentTurnProcess[ConsumerAgentResult](
            store=self.store,
            task=task,
            workspace=self.workspace,
            owner=self.owner,
            executor=self.executor,
            codex_bin=self.codex_bin,
            runtime_config=self.runtime_config,
            runtime_router=self.runtime_router,
            codex_adapter=self.codex_adapter,
            claude_adapter=self.claude_adapter,
            friday_adapter=self.friday_adapter,
            refresh_runtime_capabilities=self.refresh_runtime_capabilities,
            forced_runtime_route=self.forced_runtime_route,
            reasoning_effort=self.reasoning_effort,
            execution_mode_environment=self.execution_environment,
        )

        def renew_session_lock() -> None:
            if not self.store.renew_codex_session_lock(
                task.conversation_id,
                lock_owner,
            ):
                raise RuntimeError("codex_session_lock_lost")

        continuation_prompt = ""
        if task.error in {
            "service_restart_interrupted",
            "service_restart_immediate_retry",
        }:
            continuation_prompt = (
                "\n\n## Service Restart Recovery\n"
                "继续。请在当前原有会话中接着完成上一轮尚未完成的工作，"
                "不要重新开始一个新的业务判断。"
            )
        continuation_prompt += result_correction_prompt(
            self.store,
            task,
            role=AgentRole.CONSUMER,
            proposal_revision=proposal_revision,
        )

        def configure_consumer_command(command):
            from app.agent_cli import _task_file_root

            task_workspace = _task_file_root(
                self.store.path, task.id, task.execution_generation, create=True,
            )
            make_consumer_agent_command(
                command, task_workspace=str(task_workspace), purpose_name=purpose_name,
                controlled_cli=ControlledCliConfig(
                    command=sys.executable,
                    args=("-m", "app.agent_cli", "--role", "consumer", "--task-id",
                          str(task.id), "--db", str(self.store.path),
                          "--execution-generation", task.execution_generation),
                    cwd=str(SERVICE_ROOT),
                ),
            )

        developer_sections_by_runtime = consumer_developer_sections_by_runtime(
            runtime_context="",
            prompt_configuration=configuration,
            purpose_name=purpose_name,
        )
        developer_sections = consumer_developer_sections(
            runtime_context="",
            prompt_configuration=configuration,
            purpose_name=purpose_name,
        )
        developer_instructions_by_runtime = {
            runtime_kind: render_prompt_sections(sections, placement="developer")
            for runtime_kind, sections in developer_sections_by_runtime.items()
        }
        frozen_skill_names_for_task = frozen_task_skill_names(
            task.trigger_message_json, context.skill_names,
        )
        unfrozen_skill_names_for_task = tuple(
            name
            for name in context.skill_names
            if name not in frozen_skill_names_for_task
        )
        if context.skill_names:
            skill_protocol_fact = (
                context_skill_protocol or ""
                if skill_protocol_is_custom
                else ""
            )
            skill_protocol_source = (
                "explicit_custom"
                if skill_protocol_is_custom and context_skill_protocol
                else "selected_installed_skills"
                if unfrozen_skill_names_for_task
                else "frozen_task_skills"
            )
        else:
            skill_protocol_fact = (
                context_skill_protocol or "" if skill_protocol_is_custom else ""
            )
            skill_protocol_source = (
                "task_override"
                if skill_protocol_is_custom and context_skill_protocol is not None
                else "runtime_catalog"
            )
        current_context_time = datetime.now().astimezone().strftime(
            "%Y-%m-%d %H:%M:%S %z"
        )
        developer_instructions = render_prompt_sections(
            developer_sections,
            placement="developer",
        )
        assembly_options = {
            "scheduled_prompt": context.consumer_prompt,
            "skill_names": context.skill_names,
            "frozen_skill_names": frozen_skill_names_for_task,
            "frozen_skill_materials": frozen_skill_materials,
            "skill_protocol": context_skill_protocol or "",
            "skill_protocol_is_custom": skill_protocol_is_custom,
            "skill_catalog": default_task_skill_catalog(context.skill_names),
        }
        task_assembly = compose_consumer_task_assembly(
            configuration,
            task_context=context.render(
                proposal_revision=proposal_revision,
                feedback=feedback,
                current_time=current_context_time,
            ),
            continuation=continuation_prompt,
            **assembly_options,
        )
        stable_task_assembly = compose_consumer_task_assembly(
            configuration,
            task_context=context.render(
                proposal_revision=0,
                feedback=None,
                current_time=_CONSUMER_FINGERPRINT_TIME,
            ),
            continuation=continuation_prompt,
            **assembly_options,
        )
        image_sources = tuple(
            f"{index}:{path}:{context.image_sha256s[index] if index < len(context.image_sha256s) else ''}"
            for index, path in enumerate(context.image_paths)
        )
        source_context_fingerprint = _consumer_source_context_fingerprint(
            task_id=task.id,
            execution_generation=task.execution_generation,
            contract_hash=contract_hash,
            developer_instructions=developer_instructions,
            stable_task_prompt=stable_task_assembly.text,
            image_sources=image_sources,
        )
        context_marker = (
            "consumer-source-context-sha256:" + source_context_fingerprint
        )
        context_marker_section = PromptSection(
            "Consumer source context fingerprint",
            "service-generated binding to the complete original task input",
            "task",
            "<!-- " + context_marker + " -->",
        )
        full_task_prompt = task_assembly.text + "\n\n" + context_marker_section.text
        full_task_sections = task_assembly.sections + (context_marker_section,)
        full_invocation_facts = {
            "consumer_purpose": purpose_name,
            "stage_index": context.stage_index,
            "skill_names": list(context.skill_names),
            "skill_protocol": skill_protocol_fact,
            "skill_protocol_source": skill_protocol_source,
            "participant_timezones": explicit_participant_timezones(
                context.trigger_raw_payload
            ),
            "prompt_configuration": configuration.fingerprints(),
            "prompt_sections": prompt_section_facts(
                developer_sections, full_task_sections
            ),
        }
        prompt_sections_by_runtime = {
            runtime_kind: prompt_section_facts(sections, full_task_sections)
            for runtime_kind, sections in developer_sections_by_runtime.items()
        }
        session_scoped_prompt = None
        matching_routes = [
            name for name, persisted_id in route_sessions.items()
            if persisted_id == session_id
        ]
        runtime_config = self.runtime_config or (
            self.codex_adapter.config if self.codex_adapter is not None else None
        )
        matching_codex_routes = [
            route.name
            for route in (runtime_config.routes if runtime_config else ())
            if route.name in matching_routes
            and route.runtime_kind is RuntimeKind.CODEX_CLI
        ]
        if (
            feedback is not None
            and not force_new_session
            and len(matching_routes) == 1
            and len(matching_codex_routes) == 1
            and _consumer_revision_context_reusable(
                self.store,
                task,
                proposal_revision=proposal_revision,
                session_id=session_id,
                fingerprint=source_context_fingerprint,
                parent_agent_run_id=parent_agent_run_id,
                feedback=feedback,
            )
        ):
            compact_prompt = _consumer_revision_prompt(
                feedback,
                proposal_revision=proposal_revision,
                continuation=continuation_prompt,
                context_marker=context_marker,
                current_time=current_context_time,
            )
            compact_sections = (
                PromptSection(
                    "Consumer Audit-feedback revision",
                    "complete feedback with unchanged source context bound to this exact session",
                    "task",
                    compact_prompt,
                ),
            )
            compact_invocation_facts = {
                **full_invocation_facts,
                "prompt_sections": prompt_section_facts(
                    developer_sections, compact_sections
                ),
            }
            compact_prompt_sections_by_runtime = {
                runtime_kind: prompt_section_facts(sections, compact_sections)
                for runtime_kind, sections in developer_sections_by_runtime.items()
            }
            session_scoped_prompt = {
                "route_name": matching_codex_routes[0],
                "session_id": session_id,
                "prompt": compact_prompt,
                "invocation_facts": compact_invocation_facts,
                "prompt_sections_by_runtime": compact_prompt_sections_by_runtime,
            }
        result = process.execute(
                run=claim.run,
                invocation_facts=full_invocation_facts,
                prompt=full_task_prompt,
                session_id=session_id,
                developer_instructions=developer_instructions,
                developer_instructions_by_runtime=developer_instructions_by_runtime,
                prompt_sections_by_runtime=prompt_sections_by_runtime,
                configure_command=configure_consumer_command,
                parse_result=lambda raw: _parse_consumer_result(
                    raw,
                    purpose=purpose_name,
                    stage_index=context.stage_index,
                    predecessor_review_id=context.predecessor_review_id,
                ),
                prepare_result=lambda parsed: _prepare_reviewable_candidate(
                    parsed,
                    store=self.store,
                    task=task,
                    context=context,
                    proposal_revision=proposal_revision,
                    source_client=self.source_client,
                ),
                persist_conversation_session=persist_conversation_session,
                on_progress=renew_session_lock,
                image_paths=[Path(path) for path in context.image_paths],
                required_capabilities=self._required_capabilities(context),
                conversation_contract_hash=skill_session_contract_hash or contract_hash,
                force_new_session=force_new_session,
                session_scoped_prompt=session_scoped_prompt,
        )
        return result


def _prepare_reviewable_candidate(parsed, *, store, task, context, proposal_revision, source_client):
    from app.reviewed_sources import capture_candidate_sources
    if (parsed.purpose_output or {}).get("action") == "request_access":
        from app.consumer_purpose_retry import verified_access_request_for_file
        file_id = (parsed.purpose_output or {}).get("target", {}).get("file_id")
        try:
            prior_request = verified_access_request_for_file(store, task, file_id)
        except ValueError as exc:
            raise ResultParseError(str(exc)) from exc
        if prior_request:
            raise ResultParseError("This file already has a verified access request. Reuse its receipt for retry.")
    if parsed.outcome is ConsumerOutcome.RETRY:
        from app.consumer_purpose_retry import verified_access_request
        try:
            verified_access_request(store, task, parsed.purpose_output["evidence_refs"])
        except ValueError as exc:
            raise ResultParseError(str(exc)) from exc
    try:
        require_needs_human_context_eligible(
            outcome=parsed.outcome.value,
            trigger_quotes_agent_message=context.trigger_quotes_agent_message,
        )
    except ValueError as exc:
        raise ResultParseError(str(exc)) from exc
    prepared = _prepare_outgoing_dingtalk_messages(parsed, store=store, task=task,
        context=context, proposal_revision=proposal_revision)
    prepared = _prepare_outgoing_wechat_messages(prepared, store=store, task=task,
        context=context, proposal_revision=proposal_revision)
    if prepared.outcome is ConsumerOutcome.FAILED:
        return prepared
    return capture_candidate_sources(prepared, context, source_client)


def _prepare_outgoing_dingtalk_messages(
    result: ConsumerAgentResult,
    *,
    store: AutoReplyStore,
    task: ReplyTask,
    context: AgentTaskContext,
    proposal_revision: int,
) -> ConsumerAgentResult:
    """Apply the service-owned reply postfix before Audit reviews the candidate."""
    if context.channel not in DINGTALK_MESSAGE_CHANNELS:
        return result
    from app.agent_orchestrator import _enrich_oa_applicant_target
    sender = ServiceMessageSender(store=store)
    def prepare_plan(proposal, option_key=""):
        if proposal is None:
            return None
        proposal = _enrich_oa_applicant_target(proposal, context)
        actions = tuple(
            _prepare_outgoing_dingtalk_action(
                action, sender=sender,
                delivery_key=agent_message_delivery_key(
                    business_object_key=task.business_object_key,
                    action_identity=action.action_identity + (":" + option_key if option_key else ""),
                    execution_generation=task.execution_generation,
                    proposal_revision=proposal_revision,
                ), context=context,
            ) for action in proposal.actions
        )
        return proposal.model_copy(update={"actions": actions})
    return result.model_copy(update={
        "proposal": prepare_plan(result.proposal),
        "decision_options": tuple(option.model_copy(update={"plan": prepare_plan(option.plan, option.key)}) for option in result.decision_options),
    })


def _prepare_outgoing_wechat_messages(
    result: ConsumerAgentResult,
    *,
    store: AutoReplyStore,
    task: ReplyTask,
    context: AgentTaskContext,
    proposal_revision: int,
) -> ConsumerAgentResult:
    """Bind a WeChat reply to its task and apply the service-owned suffix.

    A WeChat task may send exactly one message, to the conversation that
    triggered it: the target the service put in the task-bound contract is the
    only one accepted, so a model-typed account or conversation can never
    reach Audit. The body is prepared here so Audit reviews the final text.
    """
    if context.channel != "wechat":
        return result
    expected = context.required_proposal_action.get("target")
    sender = ServiceMessageSender(store=store)

    def prepare_plan(proposal, option_key=""):
        if proposal is None:
            return None
        if len(proposal.actions) != 1:
            raise ResultParseError(
                "A WeChat task proposes exactly one action: a single "
                "wechat / send_message to the triggering conversation."
            )
        action = proposal.actions[0]
        if (action.capability, action.operation) != ("wechat", "send_message"):
            raise ResultParseError(
                "A WeChat task may only propose wechat / send_message; "
                f"{action.capability} / {action.operation} is not available here."
            )
        if dict(action.target) != expected:
            raise ResultParseError(
                "The wechat / send_message target must be exactly the task-bound "
                f"target {expected}; do not change or add target fields."
            )
        text = action.payload.get("text")
        try:
            prepared = sender.prepare(
                channel="wechat",
                delivery_key=agent_message_delivery_key(
                    business_object_key=task.business_object_key,
                    action_identity=action.action_identity + (":" + option_key if option_key else ""),
                    execution_generation=task.execution_generation,
                    proposal_revision=proposal_revision,
                ),
                body=text,
                original_text=context.trigger_text,
            )
        except ValueError as exc:
            if str(exc) != "feedback_callback_pair_invalid":
                raise
            raise ResultParseError(
                "Do not put feedback links or a service signature in the message "
                "body: the service appends them. Propose the message text alone."
            ) from exc
        return proposal.model_copy(update={"actions": (
            action.model_copy(update={"payload": {"text": prepared.final_body}}),
        )})

    return result.model_copy(update={
        "proposal": prepare_plan(result.proposal),
        "decision_options": tuple(
            option.model_copy(update={"plan": prepare_plan(option.plan, option.key)})
            for option in result.decision_options
        ),
    })


def _prepare_outgoing_dingtalk_action(
    action: ProposedAction,
    *,
    sender: ServiceMessageSender,
    delivery_key: str,
    context: AgentTaskContext,
) -> ProposedAction:
    payload = action.payload
    text_key = structured_dingtalk_outgoing_text_key(action)
    if text_key is None:
        return action
    reply_text = payload[text_key]
    if not isinstance(reply_text, str) or not reply_text.strip():
        return action
    try:
        prepared = sender.prepare(
            channel="dingtalk",
            delivery_key=delivery_key,
            body=reply_text,
            original_text=context.trigger_text,
        )
    except ValueError as exc:
        if str(exc) != "feedback_callback_pair_invalid":
            raise
        # The proposal carried feedback links the model typed itself. The
        # service appends them, and a hand-copied pair is invalid -- run 20064
        # failed the whole turn on it, which only sends the same body back.
        raise ResultParseError(
            "Do not put feedback links or a service signature in the message "
            "body: the service appends them to every outbound message, and a "
            "hand-typed pair is rejected. Propose the message text alone."
        ) from exc
    prepared_payload = dict(payload)
    prepared_payload[text_key] = prepared.final_body
    return action.model_copy(update={"payload": prepared_payload})


def dingtalk_outgoing_text_key(payload: Mapping[str, object]) -> str | None:
    """Return the field carrying the message body a chat action would send.

    This answers "does this action carry outgoing message text", which is a
    narrower question than where the message could be delivered.  Preparing a
    body needs a resolvable target as well; deciding whether a turn claimed to
    send one does not.
    """
    return next(
        (
            key
            for key in ("content", "text", "reply_text")
            if isinstance(payload.get(key), str)
        ),
        None,
    )


def structured_dingtalk_outgoing_text_key(action: ProposedAction) -> str | None:
    """Return the content field for a typed action Audit can execute as chat.

    A body is prepared only when the action also names somewhere to send it,
    so this stays narrower than `dingtalk_outgoing_text_key`.
    """
    if action.capability != "dingtalk-chat":
        return None
    payload = action.payload
    text_key = dingtalk_outgoing_text_key(payload)
    if text_key is None:
        return None
    target = action.target
    conversation_id = str(target.get("conversation_id") or "").strip()
    message_id = str(
        target.get("message_id") or target.get("source_message_id") or ""
    ).strip()
    recipient = str(
        target.get("open_dingtalk_id")
        or target.get("recipient_open_dingtalk_id")
        or target.get("sender_open_dingtalk_id")
        or target.get("user_id")
        or ""
    ).strip()
    delivery = dingtalk_chat_delivery(action.operation)
    if delivery == "reply":
        return text_key if conversation_id and message_id else None
    if delivery == "group":
        return text_key if conversation_id else None
    if recipient:
        return text_key
    # Generic send operations are dispatched to a conversation when one is
    # supplied. Explicit direct sends deliberately clear that target in the
    # handler, so they still require a stable recipient ID.
    if action.operation != "send_direct_message" and conversation_id:
        return text_key
    return None


def consumer_developer_instructions(
    audit_rules: str | None = None,
    *,
    skill_protocol: str = "",
    runtime_context: str | None = None,
    work_profile: str | None = None,
    prompt_configuration: PromptConfiguration | None = None,
) -> str:
    return render_prompt_sections(
        consumer_developer_sections(
            audit_rules,
            skill_protocol=skill_protocol,
            runtime_context=runtime_context,
            work_profile=work_profile,
            prompt_configuration=prompt_configuration,
        ),
        placement="developer",
    )


def consumer_developer_sections(
    audit_rules: str | None = None,
    *,
    skill_protocol: str = "",
    runtime_context: str | None = None,
    work_profile: str | None = None,
    prompt_configuration: PromptConfiguration | None = None,
    purpose_name: str = "general",
    include_wire_contract: bool = True,
) -> tuple[PromptSection, ...]:
    # Retain the legacy argument for caller compatibility, but never expose
    # Audit's independent review policy to the Consumer.
    del audit_rules, skill_protocol
    configuration = prompt_configuration or load_prompt_configuration()
    core = _developer_instruction_sections(
        audit_rules=None,
        skill_instruction=CONSUMER_DYNAMIC_SKILL_BODY,
        common_principles=configuration.developer_instructions,
        wire_model=consumer_wire_model(purpose_name),
        include_wire_contract=include_wire_contract,
    )
    sections = _role_developer_sections(
        core,
        capability_instructions=(OA_AGENT_CAPABILITY_INSTRUCTIONS
                                 if purpose_name == "oa" else AGENT_CAPABILITY_INSTRUCTIONS),
        role_boundary=CONSUMER_ROLE_BOUNDARY.replace("Derek", principal_display_name()),
    )
    return sections + (
        PromptSection("决策证据", "服务决策质量合同", "developer", DECISION_QUALITY_GATE_INSTRUCTIONS),
        PromptSection("应用结果合同", "服务应用合同", "developer", _CONSUMER_AGENT_RULES),
        PromptSection(
            "运行环境",
            "当前运行环境",
            "developer",
            runtime_context_instruction() if runtime_context is None else runtime_context,
        ),
        PromptSection(
            "工作人格",
            "已保存 Work Profile",
            "developer",
            configuration.work_profile if work_profile is None else work_profile,
        ),
    )


def consumer_developer_sections_by_runtime(
    audit_rules: str | None = None,
    *,
    skill_protocol: str = "",
    runtime_context: str | None = None,
    work_profile: str | None = None,
    prompt_configuration: PromptConfiguration | None = None,
    purpose_name: str = "general",
) -> dict[str, tuple[PromptSection, ...]]:
    """Return the matching Developer sections for each Consumer runtime."""
    native_schema_runtimes = {RuntimeKind.CODEX_CLI, RuntimeKind.CLAUDE_CLI}
    sections_by_runtime = {}
    for runtime in RuntimeKind:
        sections = consumer_developer_sections(
            audit_rules,
            skill_protocol=skill_protocol,
            runtime_context=runtime_context,
            work_profile=work_profile,
            prompt_configuration=prompt_configuration,
            purpose_name=purpose_name,
            include_wire_contract=runtime not in native_schema_runtimes,
        )
        if runtime is RuntimeKind.CODEX_CLI:
            schema_path = native_output_schema_path("consumer", purpose=purpose_name)
            schema_fingerprint = sha256(schema_path.read_bytes()).hexdigest()
            sections += (
                PromptSection(
                    "原生输出契约指纹",
                    "服务结构化输出 Schema",
                    "developer",
                    f"## Native Output Schema SHA-256\n{schema_fingerprint}\n\n"
                    "## Native Output Format\n"
                    "Return only the native JSON object, with action, target and payload "
                    "inside decision. Read current field definitions on demand with "
                    f'agent_cli.read_consumer_output_schema(purpose="{purpose_name}"). '
                    "The service decodes this transport before business validation.",
                ),
            )
        sections_by_runtime[runtime.value] = sections
    return sections_by_runtime


def default_consumer_skill_protocol() -> str:
    return render_business_skill_protocol(
        installed_runtime_skills(names=BUNDLED_BUSINESS_SKILL_NAMES) + default_skill_catalog(), compact=True,
    )


def default_task_skill_catalog(
    selected_names: tuple[str, ...] = (), *, target_root: Path | None = None
):
    if selected_names:
        catalog = installed_runtime_skills(target_root, names=selected_names)
    else:
        catalog = ()
    return tuple(dict((entry.name, entry) for entry in catalog).values())


def _parse_consumer_result(
    raw: str, *, purpose="general", stage_index=None, predecessor_review_id=None
):
    """Reject a Consumer failure that reports a page it never opened.

    The Consumer turn is read-only and has no browser: it cannot have
    observed an unsubscribe page state. One live task failed eight Consumer
    turns in a row with `email_unsubscribe_page_state_unknown` while its
    durable record held no claim, no step and no receipt -- nothing had ever
    run. The model was repeating a technical failure it found in its own task
    context as though it were this turn's finding, and the task could never
    retry its way out because it never reached the turn that would open the
    page.

    A borrowed verdict is a result-contract violation, so it gets the ordinary
    correction turn rather than terminating the task.
    """

    from app.email_unsubscribe import BROWSER_EXECUTION_ERROR_CODES

    # Stage lineage is bound to the JSON candidate before wire validation, so a
    # model mistake in these service-owned fields cannot turn an ordinary result
    # into a schema failure or a candidate predecessor mismatch.
    result = parse_consumer_agent_wire_result(
        raw,
        purpose=purpose,
        stage_index=stage_index,
        predecessor_review_id=predecessor_review_id,
    )
    if result.outcome is not ConsumerOutcome.FAILED:
        return result
    code = str(getattr(result.error, "code", "") or "")
    if code in BROWSER_EXECUTION_ERROR_CODES:
        raise ResultParseError(
            f"error_code: {code} reports operating a page, and this turn has "
            "no browser. Propose the authorized action, or fail with a code "
            "naming what this turn itself could not do."
        )
    if _is_parent_mcp_inventory_failure(code):
        raise ResultParseError(
            f"error_code: {code} reports parent Agent MCP injection state. "
            "Do not infer current-session MCP availability from nested shell "
            "commands such as `codex mcp list`; call the required MCP tool "
            "directly, or fail with the concrete provider or tool error from "
            "that direct attempt."
        )
    return result


def _is_parent_mcp_inventory_failure(code: str) -> bool:
    return code.endswith("_mcp_not_injected")


def audit_developer_instructions(
    audit_rules: str,
    *, runtime_context: str | None = None, work_profile: str | None = None,
    prompt_configuration: PromptConfiguration | None = None,
    purpose_name: str = "general",
) -> str:
    """Render the Audit contract; provider policy belongs to the runtime."""
    return render_prompt_sections(
        audit_developer_sections(
            audit_rules,
            runtime_context=runtime_context,
            work_profile=work_profile,
            prompt_configuration=prompt_configuration,
            purpose_name=purpose_name,
        ),
        placement="developer",
        omit_empty=False,
    )


def audit_developer_sections(
    audit_rules: str,
    *,
    runtime_context: str | None = None,
    work_profile: str | None = None,
    prompt_configuration: PromptConfiguration | None = None,
    purpose_name: str = "general",
) -> tuple[PromptSection, ...]:
    configuration = prompt_configuration or load_prompt_configuration(role="audit")
    core = _developer_instruction_sections(
        audit_rules=audit_rules, skill_instruction=AUDIT_DYNAMIC_SKILL_BODY,
        common_principles=configuration.developer_instructions,
        wire_model=AuditAgentWireResult,
    )
    sections = _role_developer_sections(
        core,
        capability_instructions=(
            "Use the capabilities available to the calling Agent.\n"
            + (_OA_OCR_HANDOFF_INSTRUCTIONS if purpose_name == "oa"
               else _DIRECT_SOURCE_READ_INSTRUCTIONS)
            + "\nApply the applicable business and operation Skills to the typed candidate. "
            "The application does "
            "not inspect command names, MCP tools, receipt formats, or readback procedures; "
            "those are runtime capabilities. When acceptance depends on dynamic external state "
            "and the candidate supplies a stable target identifier, use the available read "
            "capability to verify that state itself before returning feedback for missing live "
            "evidence. A proposal need not embed raw tool output, and Audit must not ask Consumer "
            "to reproduce runtime tool output. If the read fails, return failed for the dependency "
            "instead of requesting another content revision. Return return or reject when the "
            "candidate must change, and ordinary failed when a dependency does not "
            "complete.\n\n" + TASK_STORAGE_INSTRUCTIONS
        ), role_boundary=None,
    )
    return sections + (
        PromptSection("决策证据", "服务决策质量合同", "developer", DECISION_QUALITY_GATE_INSTRUCTIONS),
        PromptSection("应用结果合同", "服务应用合同", "developer", _AUDIT_AGENT_RULES),
        PromptSection(
            "响应完整性",
            "审核角色合同",
            "developer",
            AUDIT_RESPONSE_COMPLETENESS_INSTRUCTION,
        ),
        PromptSection(
            "运行环境",
            "当前运行环境",
            "developer",
            runtime_context_instruction() if runtime_context is None else runtime_context,
        ),
        PromptSection(
            "工作人格",
            "已保存 Work Profile",
            "developer",
            configuration.work_profile if work_profile is None else work_profile,
        ),
    )


def _developer_instruction_sections(
    *,
    audit_rules: str | None,
    skill_instruction: str,
    common_principles: str,
    wire_model: type[ConsumerAgentWireResult] | type[AuditAgentWireResult],
    include_wire_contract: bool = True,
) -> tuple[PromptSection, ...]:
    sections: list[PromptSection] = []
    if audit_rules is not None:
        validate_audit_rules_text(audit_rules)
        sections.append(
            PromptSection("审核规则", "已保存 Audit Rules", "developer", f"## Audit Rules\n{audit_rules}")
        )
    sections.extend(
        (
            PromptSection(
                "角色合同",
                "服务角色合同",
                "developer",
                "## Runtime Invariants\n"
                "1. [role_boundary] Consumer Agent A gathers facts and proposes a typed candidate; Audit Agent B reads and reviews the whole candidate; system code executes the exact persisted approved action plan.\n"
                "2. [output_contracts] Output Contracts: return the typed wire contract.\n"
                "3. [supported_facts] Supported Facts: use only supported facts.\n"
                "4. [meaning_preservation] Meaning Preservation: preserve candidate meaning.\n"
                "5. [duplicate_effects] Duplicate Effects: retry through the normal result contract and use current business state.\n"
                "6. [execution_facts] Execution Facts: preserve stable provider identifiers supplied by the runtime.\n"
                "7. [external_secrecy] External Secrecy: do not expose secrets.\n"
                "8. [dependency_auth] Dependency Authentication: verify dependency evidence.",
            ),
            PromptSection("Skill 使用约定", "服务角色合同", "developer", f"## Dynamic Skill\n{skill_instruction}"),
            PromptSection("共同工作原则", "已保存 Developer 模板", "developer", common_principles),
            PromptSection(
                "系统动作目录",
                "系统动作合同",
                "developer",
                "## System Action Contracts\n" + system_action_contracts_text(),
            ),
        )
    )
    if include_wire_contract:
        sections.append(PromptSection(
            "输出契约",
            "Pydantic 业务模型",
            "developer",
            f"## Pydantic Wire Contract\n{_schema_json(wire_model)}",
        ))
    return tuple(sections)


def _schema_json(
    model: type[
        ConsumerAgentWireResult
        | AuditAgentWireResult
        | ConsumerAgentResult
        | AuditAgentResult
    ],
) -> str:
    return json.dumps(
        _compact_prompt_schema(model.model_json_schema()),
        ensure_ascii=False,
        separators=(",", ":"),
    )


def _role_developer_sections(
    role_sections: tuple[PromptSection, ...],
    *,
    capability_instructions: str,
    role_boundary: str | None,
) -> tuple[PromptSection, ...]:
    sections = role_sections + (
        PromptSection(
            "能力边界",
            "服务能力合同",
            "developer",
            "## Capability Instructions\n" + capability_instructions,
        ),
        PromptSection(
            "后台记忆说明",
            "服务运行合同",
            "developer",
            "This is a background service turn, not an interactive Codex session. "
        "The current work profile is already injected below, so interactive session "
        "bootstrap requirements do not apply: do not call `memory_connector.user_get` "
        "as a session-start prerequisite. Use Memory tools only when the current "
            "business task specifically needs durable memory evidence.",
        ),
    )
    if role_boundary is not None:
        sections += (PromptSection(
            "角色边界",
            "服务角色合同",
            "developer",
            "## Role Boundary\n" + role_boundary,
        ),)
    return sections
