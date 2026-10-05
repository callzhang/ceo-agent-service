from __future__ import annotations

import json
import sys
from collections.abc import Callable, Mapping
from hashlib import sha256
from pathlib import Path
from uuid import uuid4

from app.agent_context import _AUDIT_AGENT_RULES, _CONSUMER_AGENT_RULES, AgentTaskContext
from app.agent_contracts import (
    DINGTALK_MESSAGE_CHANNELS,
    AuditAgentResult,
    AuditFeedback,
    ConsumerAgentResult,
    ConsumerOutcome,
    ProposedAction,
    dingtalk_chat_delivery,
)
from app.agent_result import ResultParseError
from app.agent_effects import LEASE_SECONDS
from app.agent_runtime_config import AgentRuntimeConfig
from app.agent_runtime_contracts import RuntimeKind
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
    parse_consumer_agent_wire_result,
)
from app.audit_rules import validate_audit_rules_text
from app.business_skills import (
    BUNDLED_BUSINESS_SKILL_NAMES,
    default_skill_catalog,
    installed_runtime_skills,
    render_business_skill_protocol,
)
from app.managed_skills import RuntimeSkillSnapshot
from app.claude_runtime_adapter import ClaudeRuntimeAdapter
from app.codex_history import find_codex_session_path
from app.codex_runtime_adapter import CodexRuntimeAdapter
from app.friday_runtime_adapter import FridayRuntimeAdapter
from app.config import principal_display_name
from app.prompt import runtime_context_instruction, work_profile_instruction
from app.service_message_sender import ServiceMessageSender, agent_message_delivery_key
from app.store import AgentRole, AutoReplyStore, ReplyTask
from app.wechat.codex_safety import ControlledCliConfig, make_consumer_agent_command

SERVICE_ROOT = Path(__file__).resolve().parent.parent
# The Consumer's output schema is generated from ConsumerAgentResult at prompt
# time (_schema_json), so the running model is always what the agent is asked
# for and what the parser accepts.  app/schemas/consumer_agent_result.schema.json
# is the reviewable snapshot of that shape; tests/test_schema_snapshots.py and
# tests/test_agent_contracts.py keep it equal to the model at commit time.  It
# is deliberately not read here: a file is live the moment it is saved and code
# is live only after a restart, so comparing them at run time can only turn an
# editor's intermediate state into an outage.
DYNAMIC_SKILL_MARKER = "[dynamic-skill]"
CONSUMER_UNREVIEWED_EFFECT = "consumer_unreviewed_provider_effect"

CONSUMER_DYNAMIC_SKILL_SENTENCE = (
    "Consumer Agent A independently selects and reads every applicable business and operation Skill before forming the candidate. Provider command names, MCP tools, receipts, and readback procedures belong to the Agent/runtime capability and are not application review conditions."
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
facts about the current decision. Scores do not automatically create a human
question. Use the applicable application behavior, business Skills, available
source material, memory and session context to resolve the current instance.
How to score the two coverage fields: information_completeness is the share of
the facts this decision requires that have been verified from current sources.
A prior comment, notification, or other action does NOT raise this score when
the missing facts remain missing. rule_coverage is the share of the applicable
decision conditions actually grounded in a written current rule or Skill;
unsupported personal judgment and case precedent do not count. A missing rule
is not permission to decide on common sense. Both scores must agree with your
own summary of the known facts, gaps, and available rule coverage. These are
evidence-quality measures, not numerical outcome gates.
Ask the applicant or source owner for material they can supply. A genuine
current-instance business choice or a fact only Derek can provide may be a
needs_human candidate with a concrete reason and source context. Every such
candidate is reviewed by Audit before it becomes visible. Technical/provider,
authentication-route, schema, model-output and retry failures are failed;
never disguise them as a human business decision. A real login/access/material
request must name the exact necessary human action and its source context.
Executable options contain complete current-instance plans; stop options state
skipped with a reason. Open-ended input uses requested_input with zero options.
This flow never creates a reusable policy or edits a Skill.
""".strip()
AUDIT_RESPONSE_COMPLETENESS_INSTRUCTION = """
Use the current work profile, complete conversation context, and inspected
materials to judge whether the candidate genuinely responds in the principal's
role. A receipt acknowledgment may be an opening or interim state, but receipt
alone does not complete a response to substantive input that calls for the
principal's engagement. Return return or reject when the candidate must be
regenerated; do not rewrite it yourself.
""".strip()
SHARED_RULES_PATH = Path.home() / ".agents" / "AGENT.md"
AGENT_CAPABILITY_INSTRUCTIONS = """
Use the role's actual declared tools to read the sources needed for this task.
Consumer may use its ordinary work tools for documents, artifacts, research,
analysis and computation. Ordinary work does not require a controlled-action
proposal merely because a tool writes. Use actual tool results and readback as
evidence of that work. A summary is not a receipt of a controlled action.
Use consumer_artifact_write for ordinary local drafts and files, with
read_task_artifact/list_task_artifacts for this task generation's artifacts.
consumer_document_write remains the bound scheduled-report document workflow.
Codex Consumer can run local code using native command and patch tools in its
current task workspace. The native workspace sandbox keeps writes in that
workspace and native temporary directories, and disables command network
access. Claude has declared read and artifact tools only. Use the actual current runtime catalog; do not invent a
successful execution result.
Only the registered reviewed system actions in the supplied action contracts
are dispatched by system code after whole-candidate approval. Do not invoke
those actions directly or claim them completed without the System receipt.
Audit has read tools only. Do not try nested CLI inventory, nested Agents,
commands or a different channel to reach an unavailable operation.
Call direct read MCP tools where available. Use memory_recall for relevant
stable context; memory never proves current external state or recipient scope.
Preserve concrete provider error codes and source context when a read fails.
Technical/provider authentication or schema errors are failed, not fabricated
business questions.
A genuine human-only fact or action needs its exact context
and must be formulated by Consumer and reviewed before it is requested.
A low-consequence operating choice is autonomous when the applicable rules and
facts support it. Do not ask Derek merely because an equivalent default exists.
A bounded fact-finding inquiry may be proposed autonomously when it states its
risk boundary and makes no purchase, budget or partnership commitment.
Read dingtang-okr-review for OKR business decisions and its real live source;
do not substitute a screenshot, repository URL or another provider for the
actual owner, target or completion facts. Read permissions and current identity
for document-sharing decisions. For candidate/interview work, use the declared
Xiaoqing read tools to build a sourced evidence packet before judgment. A real
provider read outage stays failed and cannot become a substitute notification.
Use originatorUserid/originatorOpenDingTalkId from the live OA or task context
for applicant notifications; never replace a stable identity with display-name
search. Human choices apply only to the current instance, with complete bound
plans or an explicit stop reason. They never edit Skills or reusable policies.
Wire results use error_code, error_retryable, error_authorization_required;
do not return a nested error object. Match the supplied wire schema exactly.
""".strip()


def system_action_contracts_text() -> str:
    return (SERVICE_ROOT / "docs" / "system-action-contracts.md").read_text(encoding="utf-8").strip()


def consumer_wire_contract_hash(
    runtime_skill_snapshot: RuntimeSkillSnapshot | None = None,
    *, skill_protocol_override: str | None = None,
) -> str:
    """Fingerprint stable Consumer output, instructions, and read-tool policy."""
    contract = {
        "consumer_rules": _CONSUMER_AGENT_RULES,
        "role_boundary": CONSUMER_ROLE_BOUNDARY,
        "system_action_contracts": system_action_contracts_text(),
        "agent_capability_instructions": AGENT_CAPABILITY_INSTRUCTIONS,
        # The runtime Skill tree is the single source the Agent reads; the
        # snapshot below records which revisions were in force, it is not the
        # content the Agent is served.
        "business_skill_protocol": render_business_skill_protocol(
            installed_runtime_skills(names=BUNDLED_BUSINESS_SKILL_NAMES)
            + default_skill_catalog()
        ),
        "work_profile_instruction": work_profile_instruction(),
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

CONSUMER_ROLE_BOUNDARY = """
You are Consumer Agent A. Complete the business preparation using the task's
applicable Skills and permitted tools, including report and document work.
Controlled structured actions are proposals: do not dispatch them yourself.
For a `dingtalk-doc` `create_document` action, put the complete, exact document
body in `payload.content`. Keep `description` to a short action summary of at
most 2048 characters; never put the document body there. The next stage reads
`payload.content` to create and verify the document.

For every `dingtalk-chat` ProposedAction, use the service wire target names,
not provider response names: group sends use `conversation_id`; replies use
both `conversation_id` and `message_id`; direct sends use a stable recipient
identifier such as `open_dingtalk_id`. Never put `open_conversation_id` or
`reply_to_message_id` in a proposal target. Keep the same `action_identity`
when feedback or retry still requests the same external result.

Before asking for authorization to answer a group message about internal
direction or responsibilities, read prior messages in that conversation and
verify the same conversation ID, recipients, and scope of disclosure. Use a
focused `memory_recall` and earlier communications to resolve established
facts, then compare the proposed reply with what those recipients have already
seen. Memory alone does not prove recipient scope. Do not ask Derek to
reconfirm already disclosed direction when the reply stays within the same
audience and adds no private detail or new commitment. If the proposed reply
would broaden the audience or disclose something new, narrow it to supported
content or ask only for the remaining boundary.

A bounded fact-finding inquiry is autonomous when it only gathers facts, states
the concrete risk in the message, and explicitly says it does not make a purchase, budget, or partnership commitment;
it does not authorize a quote, order, agreement, or spend. Do not escalate only because the recipient is external;
preserve the stated boundary and let Audit verify it. If one of the available
decision options already states this bounded path, convert that option into a
proposal instead of returning needs_human.

Do not treat an ordinary conversation request to improve a policy, Skill, or service behavior
as a feedback-processing queue item. Require a feedback_key or batch_id only when the supplied context explicitly identifies
a feedback-processing item. Otherwise form the supported proposal from the message and current system sources; the
absence of a feedback-processing record is not a task failure.

OKR approval/review is a covered autonomous decision. When the trigger changes,
approves, rejects, or asks to review an OKR, read the current live OKR first,
then gather relevant meeting minutes and documents through the applicable
Skill. The runtime decides how to perform those reads; a command or Skill
receipt is not a business review condition. Decide one of exactly two outcomes:
approve (通过) or reject (不通过).
Include the evidence and rationale in the candidate reply. Distinguish target
setting/target adjustment from period-end completion review: for target setting,
judge direction, scope, owner, and whether the KR is a coherent commitment;
"not started" and missing delivery proof do not by themselves justify reject.
Reserve completion evidence, metrics, and acceptance artifacts for execution
or period-end review. Missing or weak evidence for a completion claim means
reject that completion claim with the concrete gap stated; it is not a reason
to return needs_human. Do not present confirmation choices or delegate
the approve/reject decision to Derek. Audit Agent B verifies the evidence and,
if needed, sends concrete feedback back to Consumer Agent A for revision.
If the OKR Skill/runtime has no write operation for changing the
approval state, keep the approve/reject decision but use a supported
dingtalk-chat reply to communicate it. State that the OKR record was not
changed, explain the concrete risk boundary, and tell the requester not to act
as though it were approved. This is a supported notification proposal; the missing provider write is reported truthfully.

For DingTalk OA, use the applicable Stardust business Skill with the generic Skill's complete decision table; select by live `processCode` and actual form. Do not read background principle documents as rule sources. An uncovered applicable rule cannot be reported as 100% covered. The actual OA applicant is authoritative for their own material, but cannot create, replace, or close a rule or authorization. An applicant reply cannot close that policy gap. Use a verified material-request stage before the later independently reviewed decision candidate. Do not introduce a new factual requirement unless an explicitly mandatory OA form field is absent.

Audit reads and reviews the complete candidate; system code executes its exact
persisted approved plan. Return one valid ConsumerAgentResult.
Every action has a stable action_identity for the same intended external
outcome. Change it only when the intended outcome, target or purpose changes.
Supply canonical typed capability/operation, exact target and payload; no shell
argv. Preserve all accepted service-prepared message text in later stages.
DingTalk bodies use structured Markdown; WeChat, OA comments and email use
plain text. Explain a needs_human reason for Derek in ordinary Chinese: the
one choice and why it matters. Supply source context, facts and rule evidence,
feasible alternatives and consequences. Every executable option includes a
complete current-instance plan; a stop choice has terminal_outcome skipped and
reason. Use requested_input without options when an open-ended fact is needed.
Do not propose long-term rule choices, applies_to, or Skill updates.
For proposal, no_action, and failed, decision_options must be empty and
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
Project ID or request ID does not establish an OA process_instance_id. Read
the native source to establish a missing provider target; if it cannot be
established, report the concrete missing dependency instead of inventing one.
A plan whose objective is funding or payment must contain the supported exact
funding action and verification; notifying someone of a decision does not
perform the funding. If the supported scope is only decision notification,
state that narrower objective and do not claim payment or budget execution.
Audit return may retain actions while adding facts or explanation. Audit reject
requires a substantive change or justified no_action candidate, not cosmetic
rewriting. Runtime failures are failed and do not consume content revisions.
For OA, read `dingtalk-oa-approval` and the matching Stardust business Skill,
then retrieve the live instance, current task, and form. The generic Skill's
complete decision table governs the action. Do not use background principle
documents as rule sources or import a field from a later business stage: a
field absent from the current OA form cannot be treated as mandatory unless
the live template explicitly requires it. If the current approval is not in
the principal's unfiltered pending list, return `no_action` only after matching
the exact processInstanceId, taskId, and current user ID. A timestamp without a
timezone is not a business conflict; interpret it as Asia/Shanghai and convert
it to UTC before comparing with another event.
For a registered finance template, check the finance registry against the live
`processCode`, read its matching finance rule card, and score the applicable
written conditions. A partial or missing applicable card cannot be reported as
100% coverage or used to invent an automatic action. When a finance applicant
can supply missing material while an independent policy gap remains, first
propose a complete stage to comment on the original approval with the exact
missing material and notify the applicant if the Skill requires it. After the requested material actually arrives, form the later current-instance candidate and
independently return `needs_human` for the unresolved policy choice if no
written rule settles it. The applicant's later material cannot close that
policy gap. Re-read the live OA after prior-stage actions.
For OKR review, judge future target commitments using target, owner, scope and
rationale; judge completion using delivery and acceptance evidence. A covered
approve/reject judgment is autonomous. If no OKR write capability exists,
propose the supported applicant notification and truthfully state the record
was not changed. Do not claim a provider result before verified execution.
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
        runtime_skill_snapshot: RuntimeSkillSnapshot | None = None,
        forced_runtime_route=None,
        reasoning_effort: str = "",
        skill_protocol_override: str | None = None,
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
        self.runtime_skill_snapshot = runtime_skill_snapshot
        self.forced_runtime_route = forced_runtime_route
        self.reasoning_effort = reasoning_effort
        self.skill_protocol_override = skill_protocol_override
        self.execution_environment = dict(execution_environment or {})

    def _configured_route_names(self) -> tuple[str, ...]:
        config = self.runtime_config or (
            self.codex_adapter.config if self.codex_adapter is not None else None
        )
        return tuple(route.name for route in config.routes) if config else ("codex_oauth",)

    def _route_session_exists(self, route_name: str, session_id: str) -> bool:
        config = self.runtime_config or (
            self.codex_adapter.config if self.codex_adapter is not None else None
        )
        route_kind = (
            next(
                (
                    route.runtime_kind
                    for route in config.routes
                    if route.name == route_name
                ),
                None,
            )
            if config
            else RuntimeKind.CODEX_CLI
        )
        if route_kind is None:
            return False
        if route_kind is RuntimeKind.CLAUDE_CLI:
            # Claude owns its remote conversation ledger. The adapter validates
            # the persisted ID before --resume and handles incompatibility safely.
            return True
        return self.codex_session_exists(session_id)

    def _consumer_route_sessions(self, conversation_id: str) -> dict[str, str]:
        """Return live route sessions for one continuous business conversation.

        The current Skill/contract revision is recorded on the next completed
        turn, but it is not an identity boundary for the conversation.  A
        revision must receive the facts already verified in this session.
        """
        sessions: dict[str, str] = {}
        for route_name in self._configured_route_names():
            session_id = self.store.get_conversation_runtime_session(
                conversation_id,
                route_name,
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
        contract_hash = consumer_wire_contract_hash(
            self.runtime_skill_snapshot,
            skill_protocol_override=context_skill_protocol,
        )
        route_sessions = self._consumer_route_sessions(task.conversation_id)
        # A forced rerun changes the execution generation and prompt, but it
        # must keep the compatible conversation session so the agent sees the
        # original context plus the new feedback.  Route-specific session
        # selection is performed by AgentTurnProcess; passing one persisted
        # id here only supplies a fast-path hint and never clears other routes.
        conversation_session_id = next(iter(route_sessions.values()), None)
        for route_name, route_session_id in tuple(route_sessions.items()):
            if not self._route_session_exists(route_name, route_session_id):
                self._clear_route_session(
                    task.conversation_id, route_name, route_session_id
                )
                route_sessions.pop(route_name)
        conversation_session_id = next(iter(route_sessions.values()), None)
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
            lease_seconds=LEASE_SECONDS,
        )
        if not claim.claimed:
            raise RuntimeError("agent_run_unavailable")
        session_id = (
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
        force_new_session = repeated_result_failure_requires_fresh_session(
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
                command, task_workspace=str(task_workspace),
                controlled_cli=ControlledCliConfig(
                    command=sys.executable,
                    args=("-m", "app.agent_cli", "--role", "consumer", "--task-id",
                          str(task.id), "--db", str(self.store.path),
                          "--execution-generation", task.execution_generation),
                    cwd=str(SERVICE_ROOT),
                ),
            )

        result = process.execute(
                run=claim.run,
                skill_names=context.skill_names,
                prompt="## Runtime Invariants\nPreserve typed proposal contracts and session boundaries. The proposal must match the supplied JSON Schema exactly.\n\n"
                + (
                    "## Scheduled Consumer Prompt\n"
                    + context.consumer_prompt
                    + "\n\n"
                    if context.consumer_prompt
                    else ""
                )
                + context.render(
                    proposal_revision=proposal_revision,
                    feedback=feedback,
                ) + continuation_prompt,
                session_id=session_id,
                developer_instructions=consumer_developer_instructions(
                    skill_protocol="\n\n".join(
                        part for part in (
                            context_skill_protocol
                            if context_skill_protocol is not None
                            else render_business_skill_protocol(
                                installed_runtime_skills(
                                    names=BUNDLED_BUSINESS_SKILL_NAMES
                                )
                                + default_skill_catalog()
                            ),
                        ) if part
                    ),
                ),
                configure_command=configure_consumer_command,
                parse_result=_parse_consumer_result,
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
                conversation_contract_hash=contract_hash,
                force_new_session=force_new_session,
        )
        return result


def _prepare_reviewable_candidate(parsed, *, store, task, context, proposal_revision, source_client):
    from app.reviewed_sources import capture_candidate_sources
    prepared = _prepare_outgoing_dingtalk_messages(parsed, store=store, task=task,
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
    return text_key if recipient else None


def consumer_developer_instructions(
    audit_rules: str | None = None,
    *,
    skill_protocol: str = "",
) -> str:
    # Retain the legacy argument for caller compatibility, but never expose
    # Audit's independent review policy to the Consumer.
    del audit_rules
    core = _developer_instructions(
        audit_rules=None,
        skill_instruction=CONSUMER_DYNAMIC_SKILL_BODY,
        wire_model=ConsumerAgentWireResult,
    )
    instructions = _role_developer_instructions(
        core,
        capability_instructions=AGENT_CAPABILITY_INSTRUCTIONS,
        role_boundary=CONSUMER_ROLE_BOUNDARY.replace("Derek", principal_display_name()),
    )
    return "\n\n".join(
        part
        for part in (
            instructions,
            DECISION_QUALITY_GATE_INSTRUCTIONS,
            _CONSUMER_AGENT_RULES,
            skill_protocol,
            runtime_context_instruction(),
            work_profile_instruction(),
        )
        if part
    )


def _parse_consumer_result(raw: str):
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

    result = parse_consumer_agent_wire_result(raw)
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
) -> str:
    """Render the Audit contract; provider policy belongs to the runtime."""
    core = _developer_instructions(
        audit_rules=audit_rules, skill_instruction=AUDIT_DYNAMIC_SKILL_BODY,
        wire_model=AuditAgentWireResult,
    )
    instructions = _role_developer_instructions(
        core,
        capability_instructions=(
            "Use the capabilities available to the calling Agent. Apply the applicable "
            "business and operation Skills to the typed candidate. The application does "
            "not inspect command names, MCP tools, receipt formats, or readback procedures; "
            "those are runtime capabilities. When acceptance depends on dynamic external state "
            "and the candidate supplies a stable target identifier, use the available read "
            "capability to verify that state itself before returning feedback for missing live "
            "evidence. A proposal need not embed raw tool output, and Audit must not ask Consumer "
            "to reproduce runtime tool output. If the read fails, return failed for the dependency "
            "instead of requesting another content revision. Return return or reject when the "
            "candidate must change, and ordinary failed when a dependency does not "
            "complete."
        ), role_boundary=AUDIT_ROLE_BOUNDARY,
    )
    return "\n\n".join(
        (
            instructions,
            DECISION_QUALITY_GATE_INSTRUCTIONS,
            _AUDIT_AGENT_RULES,
            AUDIT_RESPONSE_COMPLETENESS_INSTRUCTION,
            runtime_context_instruction(),
            work_profile_instruction(),
        )
    )


def _developer_instructions(
    *,
    audit_rules: str | None,
    skill_instruction: str,
    wire_model: type[ConsumerAgentWireResult] | type[AuditAgentWireResult],
) -> str:
    sections: list[str] = []
    if audit_rules is not None:
        validate_audit_rules_text(audit_rules)
        sections.append(f"## Audit Rules\n{audit_rules}")
    sections.extend(
        (
            "## Runtime Invariants\n"
            "1. [role_boundary] Consumer Agent A gathers facts and proposes a typed candidate; Audit Agent B reads and reviews the whole candidate; system code executes the exact persisted approved action plan.\n"
            "2. [output_contracts] Output Contracts: return the typed wire contract.\n"
            "3. [supported_facts] Supported Facts: use only supported facts.\n"
            "4. [meaning_preservation] Meaning Preservation: preserve candidate meaning.\n"
            "5. [duplicate_effects] Duplicate Effects: retry through the normal result contract and use current business state.\n"
            "6. [execution_facts] Execution Facts: preserve stable provider identifiers supplied by the runtime.\n"
            "7. [external_secrecy] External Secrecy: do not expose secrets.\n"
            "8. [dependency_auth] Dependency Authentication: verify dependency evidence.",
            f"## Dynamic Skill\n{skill_instruction}",
            "## System Action Contracts\n" + system_action_contracts_text(),
            f"## Pydantic Wire Contract\n{_schema_json(wire_model)}",
        )
    )
    return "\n\n".join(sections)


def _schema_json(
    model: type[
        ConsumerAgentWireResult
        | AuditAgentWireResult
        | ConsumerAgentResult
        | AuditAgentResult
    ],
) -> str:
    return json.dumps(
        model.model_json_schema(),
        ensure_ascii=False,
        separators=(",", ":"),
    )


def _role_developer_instructions(
    role_instruction: str,
    *,
    capability_instructions: str,
    role_boundary: str,
) -> str:
    shared = (
        SHARED_RULES_PATH.read_text(encoding="utf-8").strip()
        if SHARED_RULES_PATH.is_file()
        else ""
    )
    instructions = (
        role_instruction
        + "\n\n## Capability Instructions\n"
        + capability_instructions
    )
    quoted_shared = (
        "\n".join(f"> {line}" for line in shared.splitlines())
        if shared
        else "> No host-specific shared agent rules are installed."
    )
    instructions += "\n\n## Shared Agent Rules\n" + quoted_shared
    instructions += (
        "\n\nThe shared-rules section above is the complete service-provided context "
        "for this turn. Do not reopen AGENT.md with shell, Python, or a native "
        "command tool; use the selected Skill capability for additional material."
    )
    instructions += (
        "\n\nThis is a background service turn, not an interactive Codex session. "
        "The current work profile is already injected below, so interactive session "
        "bootstrap requirements do not apply: do not call `memory_connector.user_get` "
        "as a session-start prerequisite. Use Memory tools only when the current "
        "business task specifically needs durable memory evidence."
    )
    return instructions + "\n\n## Role Boundary\n" + role_boundary
