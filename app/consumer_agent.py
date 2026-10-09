from __future__ import annotations

import json
import sys
from collections.abc import Callable, Mapping
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
    frozen_task_skill_names,
    installed_runtime_skills,
    render_business_skill_protocol,
)
from app.managed_skills import RuntimeSkillSnapshot
from app.claude_runtime_adapter import ClaudeRuntimeAdapter
from app.codex_history import find_codex_session_path
from app.codex_runtime_adapter import CodexRuntimeAdapter
from app.friday_runtime_adapter import FridayRuntimeAdapter
from app.config import principal_display_name
from app.prompt import runtime_context_instruction
from app.prompt_composition import (
    PromptConfiguration,
    PromptSection,
    compose_consumer_task_assembly,
    load_prompt_configuration,
    prompt_section_facts,
    render_prompt_sections,
)
from app.runtime_prompt_context import explicit_participant_timezones
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
Use only stable provider identities established by the current source for the
exact operation; a similarly named business object is not identity evidence.
Preserve concrete provider error codes and source context when a read fails.
When a required DWS, MCP, or other external-source read actually fails because
the dependency is unavailable, return failed with error_code
`dependency_read_unavailable` and preserve the exact underlying error in the
summary. Do not use this code when a successful read returns no match, evidence
is incomplete or ambiguous, or a governing rule is missing; those are evidence
or business-rule outcomes, not infrastructure outages.
Technical/provider authentication or schema errors are failed, not fabricated
business questions. An unresolved provider-risk refusal for the current
intended action remains failed with its original error code and no retry;
not retrying a refused action does not make the task completed or no_action.
Audit must return a candidate that incorrectly reports such a refusal as
no_action. Historical refusal evidence never authorizes a new effect, a new
action identity, or another channel to evade the refusal.
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

CONSUMER_ROLE_BOUNDARY = """
You are Consumer Agent A. Complete the business preparation using the task's
applicable Skills and permitted tools, including report and document work.
Controlled structured actions are proposals: do not dispatch them yourself.
Audit reads and reviews the complete candidate; system code executes its exact
persisted approved plan. Return one valid ConsumerAgentResult.
Every action has a stable action_identity for the same intended external
outcome. Change it only when the intended outcome, target or purpose changes.
Supply canonical typed capability/operation, exact target and payload; no shell
argv. Preserve all accepted service-prepared message text in later stages.
Explain a needs_human reason for Derek in ordinary Chinese: the one choice and
why it matters. Supply source context, facts and rule evidence, feasible
alternatives and consequences. Every executable option includes a complete
current-instance plan; a stop choice has terminal_outcome skipped and reason.
Use requested_input without options when an open-ended fact is needed.
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
        context_skill_protocol_source = (
            context.skill_protocol_source
            if context.skill_protocol_override is not None
            else self.skill_protocol_source
        )
        skill_protocol_is_custom = (
            context_skill_protocol_source == "explicit_custom"
        )
        configuration = load_prompt_configuration()
        contract_hash = consumer_wire_contract_hash(
            self.runtime_skill_snapshot,
            skill_protocol_override=(
                context_skill_protocol if skill_protocol_is_custom else None
            ),
            prompt_configuration=configuration,
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
                command, task_workspace=str(task_workspace),
                controlled_cli=ControlledCliConfig(
                    command=sys.executable,
                    args=("-m", "app.agent_cli", "--role", "consumer", "--task-id",
                          str(task.id), "--db", str(self.store.path),
                          "--execution-generation", task.execution_generation),
                    cwd=str(SERVICE_ROOT),
                ),
            )

        developer_sections = consumer_developer_sections(
            runtime_context="",
            prompt_configuration=configuration,
        )
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
        task_assembly = compose_consumer_task_assembly(
            configuration,
            task_context=context.render(
                proposal_revision=proposal_revision,
                feedback=feedback,
            ),
            scheduled_prompt=context.consumer_prompt,
            continuation=continuation_prompt,
            skill_names=context.skill_names,
            frozen_skill_names=frozen_skill_names_for_task,
            skill_protocol=context_skill_protocol or "",
            skill_protocol_is_custom=skill_protocol_is_custom,
            skill_catalog=default_task_skill_catalog(context.skill_names),
        )
        result = process.execute(
                run=claim.run,
                skill_names=context.skill_names,
                invocation_facts={
                    "stage_index": context.stage_index,
                    "skill_names": list(context.skill_names),
                    "skill_protocol": skill_protocol_fact,
                    "skill_protocol_source": skill_protocol_source,
                    "participant_timezones": explicit_participant_timezones(
                        context.trigger_raw_payload
                    ),
                    "prompt_configuration": configuration.fingerprints(),
                    "prompt_sections": prompt_section_facts(
                        developer_sections, task_assembly.sections
                    ),
                },
                prompt=task_assembly.text,
                session_id=session_id,
                developer_instructions=render_prompt_sections(
                    developer_sections,
                    placement="developer",
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
) -> tuple[PromptSection, ...]:
    # Retain the legacy argument for caller compatibility, but never expose
    # Audit's independent review policy to the Consumer.
    del audit_rules, skill_protocol
    configuration = prompt_configuration or load_prompt_configuration()
    core = _developer_instruction_sections(
        audit_rules=None,
        skill_instruction=CONSUMER_DYNAMIC_SKILL_BODY,
        common_principles=configuration.developer_instructions,
        wire_model=ConsumerAgentWireResult,
    )
    sections = _role_developer_sections(
        core,
        capability_instructions=AGENT_CAPABILITY_INSTRUCTIONS,
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
    *, runtime_context: str | None = None, work_profile: str | None = None,
    prompt_configuration: PromptConfiguration | None = None,
) -> str:
    """Render the Audit contract; provider policy belongs to the runtime."""
    return render_prompt_sections(
        audit_developer_sections(
            audit_rules,
            runtime_context=runtime_context,
            work_profile=work_profile,
            prompt_configuration=prompt_configuration,
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
            PromptSection(
                "输出契约",
                "Pydantic 业务模型",
                "developer",
                f"## Pydantic Wire Contract\n{_schema_json(wire_model)}",
            ),
        )
    )
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


def _compact_prompt_schema(value):
    """Omit schema annotations, preserving property/definition names and assertions."""
    if isinstance(value, list):
        return [_compact_prompt_schema(item) for item in value]
    if not isinstance(value, dict):
        return value
    result = {}
    for key, item in value.items():
        if key in {"title", "default"}:
            continue
        if key in {"properties", "$defs", "definitions", "patternProperties", "dependentSchemas"}:
            result[key] = {name: _compact_prompt_schema(schema) for name, schema in item.items()}
        elif key in {
            "allOf", "anyOf", "oneOf", "prefixItems", "items", "additionalProperties",
            "additionalItems", "contains", "not", "if", "then", "else", "propertyNames",
            "unevaluatedProperties", "unevaluatedItems", "contentSchema",
        }:
            result[key] = _compact_prompt_schema(item)
        else:
            result[key] = item
    return result


def _role_developer_sections(
    role_sections: tuple[PromptSection, ...],
    *,
    capability_instructions: str,
    role_boundary: str,
) -> tuple[PromptSection, ...]:
    return role_sections + (
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
        PromptSection(
            "角色边界",
            "服务角色合同",
            "developer",
            "## Role Boundary\n" + role_boundary,
        ),
    )
