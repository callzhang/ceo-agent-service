from __future__ import annotations

import json
import sys
from collections.abc import Callable
from hashlib import sha256
from pathlib import Path
from uuid import uuid4

from app.agent_context import _CONSUMER_AGENT_RULES, AgentTaskContext
from app.agent_contracts import (
    AuditAgentResult,
    AuditFeedback,
    ConsumerAgentResult,
    ConsumerOutcome,
    ProposedAction,
)
from app.agent_effects import LEASE_SECONDS, McpToolEffectRegistry
from app.agent_result import ResultParseError
from app.agent_runtime_config import AgentRuntimeConfig
from app.agent_runtime_contracts import RuntimeKind
from app.agent_runtime_router import AgentRuntimeRouter
from app.agent_turn_runner import AgentTurnProcess, AgentTurnRunResult, ProcessExecutor
from app.agent_wire_contracts import (
    AuditAgentWireResult,
    ConsumerAgentWireResult,
    parse_consumer_agent_wire_result,
)
from app.audit_rules import render_audit_rules, validate_audit_rules_text
from app.business_skills import (
    installed_business_skill_catalog,
    render_business_skill_protocol,
)
from app.claude_runtime_adapter import ClaudeRuntimeAdapter
from app.codex_history import find_codex_session_path
from app.codex_runtime_adapter import CodexRuntimeAdapter
from app.config import feedback_spike_vercel_base_url
from app.feedback_spike import prepare_outgoing_reply_text
from app.native_cli_metadata import (
    NativeCliMetadataClassifier,
    dingtalk_message_text,
    dingtalk_outgoing_message_command_path,
    describe_native_command,
    native_command_argv,
    replace_dingtalk_message_text,
    service_read_command_contract,
)
from app.store import AgentRole, AutoReplyStore, ReplyTask
from app.wechat.codex_safety import ControlledCliConfig, make_consumer_agent_command

SERVICE_ROOT = Path(__file__).resolve().parent.parent
SCHEMA_PATH = SERVICE_ROOT / "app" / "schemas" / "consumer_agent_result.schema.json"
DYNAMIC_SKILL_MARKER = "[dynamic-skill]"
CONSUMER_DYNAMIC_SKILL_SENTENCE = (
    "Consumer Agent A independently gathers the context required for the task "
    "before forming the candidate."
)
AUDIT_DYNAMIC_SKILL_SENTENCE = (
    "Audit Agent B independently checks the candidate against the supplied task "
    "context and returns feedback_provided when the Consumer must regenerate it."
)
CONSUMER_DYNAMIC_SKILL_BODY = (
    f"{DYNAMIC_SKILL_MARKER} {CONSUMER_DYNAMIC_SKILL_SENTENCE}"
)
AUDIT_DYNAMIC_SKILL_BODY = (
    f"{DYNAMIC_SKILL_MARKER} {AUDIT_DYNAMIC_SKILL_SENTENCE}"
)
CORE_DYNAMIC_SKILL_BODY = (
    f"{CONSUMER_DYNAMIC_SKILL_BODY} {AUDIT_DYNAMIC_SKILL_SENTENCE}"
)
SHARED_RULES_PATH = Path.home() / ".agents" / "AGENT.md"
REVIEWED_DWS_READ_INSTRUCTIONS = """
Use the capabilities available to the calling agent to gather the evidence
needed for the task. Return a single structured result. The application does
not prescribe provider command names, MCP tools, shell syntax, or readback
procedures; those belong to the runtime and the selected agent capability.
""".strip()


def consumer_wire_contract_hash() -> str:
    """Fingerprint stable Consumer output, instructions, and read-tool policy."""
    contract = {
        "consumer_rules": _CONSUMER_AGENT_RULES,
        "role_boundary": CONSUMER_ROLE_BOUNDARY,
        "reviewed_dws_read_instructions": REVIEWED_DWS_READ_INSTRUCTIONS,
        "business_skill_protocol": render_business_skill_protocol(
            installed_business_skill_catalog()
        ),
        "service_read_commands": service_read_command_contract(),
        "wire_schema": ConsumerAgentWireResult.model_json_schema(),
    }
    encoded = json.dumps(contract, sort_keys=True, separators=(",", ":"))
    return sha256(encoded.encode("utf-8")).hexdigest()

CONSUMER_ROLE_BOUNDARY = """
You are Consumer Agent A. Understand the supplied task, use the capabilities
available to the calling agent, and return one valid Consumer Agent wire JSON
object matching the schema. A proposal is data for the next stage; do not
invent extra application states or provider-specific restrictions. Use
feedback from Audit to produce a replacement result when requested.

A bounded fact-finding inquiry is autonomous when it only gathers facts, states
the concrete risk in the message, and explicitly says it does not make a purchase, budget, or partnership commitment;
it does not authorize a quote, order, agreement, or spend. Do not escalate only because the recipient is external;
preserve the stated boundary and let Audit verify it. If one of the available
decision options already states this bounded path, convert that option into a
proposal instead of returning needs_human.

Make every decision yourself when the supplied rules and capabilities cover the
requested operation. Use the minimum reversible path and state its risks in the
reply. Return needs_human only when the current rules cannot determine how to
handle a repeatable class of cases. Its summary and options must describe the
rule gap and a reusable handling rule, not ask Derek how to finish this one
task. Technical failures and missing runtime evidence are failed results.

For DingTalk OA, read `dingtalk-misc/references/oa.md` and the latest canonical
approval detail. If the process is still running but a document, attachment, or
other fact can be supplied by the applicant, comment on the original approval
with the exact missing material and next step, then notify the actual applicant;
keep the approval pending and do not ask Derek to choose. A timestamp without a
timezone is not a business conflict: preserve the raw value and use the source
ordering. If the process or current task is already handled, return `no_action`.
""".strip()
AUDIT_ROLE_BOUNDARY = """
You are Audit Agent B. Review the supplied candidate against the task context
and return one valid Audit Agent wire JSON object matching the schema. Use the
calling agent's available capabilities. Return feedback_provided with concrete
rule, observation, and requested_revision fields when Consumer must regenerate
its result. Return executed, needs_human, or failed for the other terminal
outcomes. Do not create provider-specific policy, receipt, readback, or recovery
states in the application result. external_result must
contain exactly its typed fields: operation_id, verification_summary, and
live_result_reference. operation_id must equal the candidate proposal
operation_id. Legacy revision_required input is normalized
to the formal feedback_provided output.

Treat a bounded fact-finding inquiry as executable when it only gathers facts,
states the concrete risk in the message, and explicitly says it does not make a purchase, budget, or partnership commitment;
it does not authorize a quote, order, agreement, or spend. External delivery alone is not a management choice;
escalate only when the candidate actually asks Derek to decide or commit.

Judge the candidate under the applicable installed/reviewed Skill. Every
decision that the Skill and available capabilities support belongs to the Agent,
including choosing a minimum reversible path and stating risks in the reply.
Return needs_human only for a reusable policy gap: the existing rules cannot
determine how this class of cases should be handled. Describe the rule key,
the observed recurring pattern, and mutually exclusive policy choices. Do not
turn one task's technical failure, missing read, or execution problem into a
human task decision.
""".strip()


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
        executor: ProcessExecutor | None = None,
        owner: str | None = None,
        refresh_runtime_capabilities: Callable[[], object] | None = None,
        mcp_effect_registry: McpToolEffectRegistry | None = None,
        native_cli_classifier: NativeCliMetadataClassifier | None = None,
        codex_session_exists: Callable[[str], bool] | None = None,
    ) -> None:
        self.store = store
        self.workspace = workspace
        self.codex_bin = codex_bin
        self.runtime_config = runtime_config
        self.runtime_router = runtime_router
        self.codex_adapter = codex_adapter
        self.claude_adapter = claude_adapter
        self.executor = executor
        self.owner = owner or f"consumer-agent-{uuid4().hex}"
        self.refresh_runtime_capabilities = refresh_runtime_capabilities
        self.effects = mcp_effect_registry or McpToolEffectRegistry.default()
        self.native_cli_classifier = native_cli_classifier
        self.codex_session_exists = codex_session_exists or (
            lambda session_id: find_codex_session_path(session_id) is not None
        )

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

    def _consumer_route_sessions(
        self, conversation_id: str, contract_hash: str
    ) -> dict[str, str]:
        sessions: dict[str, str] = {}
        for route_name in self._configured_route_names():
            raw_session_id = self.store.get_conversation_runtime_session(
                conversation_id, route_name
            )
            session_id = self.store.get_conversation_runtime_session(
                conversation_id,
                route_name,
                required_contract_hash=contract_hash,
            )
            if raw_session_id and session_id is None:
                self._clear_route_session(
                    conversation_id, route_name, raw_session_id
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
        required = {
            "task_context",
            f"channel:{context.channel}",
            "mcp:agent_cli:reviewed_read",
            "native_cli:reviewed",
            "mcp:memory_connector:read",
        }
        if context.channel == "dingtalk":
            required.add("native_cli:dws")
        elif context.channel in {"lark", "feishu"}:
            required.add("native_cli:lark")
        if context.image_paths:
            required.add("image_input")
        required.update(
            f"reviewed_skill:{receipt.name}:{receipt.sha256}"
            for receipt in context.required_reviewed_skills
        )
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
        rendered_rules = render_audit_rules(AgentRole.CONSUMER)
        lock_owner = f"consumer-agent:{task.id}:{task.execution_generation}"
        try:
            with self.store.codex_session_lock(task.conversation_id, lock_owner):
                return self._run_locked(
                    task,
                    context,
                    lock_owner=lock_owner,
                    proposal_revision=proposal_revision,
                    parent_agent_run_id=parent_agent_run_id,
                    rendered_rules=rendered_rules,
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
        rendered_rules: str,
        feedback: AuditFeedback | None,
    ) -> AgentTurnRunResult[ConsumerAgentResult]:
        contract_hash = consumer_wire_contract_hash()
        route_sessions = self._consumer_route_sessions(
            task.conversation_id, contract_hash
        )
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
        if json.loads(SCHEMA_PATH.read_text(encoding="utf-8")) != (
            ConsumerAgentResult.model_json_schema()
        ):
            raise ValueError("consumer result schema does not match Pydantic model")
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
            claim.run.codex_session_id
            if conversation_session_id is not None
            else None
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
            mcp_effect_registry=self.effects,
            native_cli_classifier=self.native_cli_classifier,
            refresh_runtime_capabilities=self.refresh_runtime_capabilities,
        )

        def renew_session_lock() -> None:
            if not self.store.renew_codex_session_lock(
                task.conversation_id,
                lock_owner,
            ):
                raise RuntimeError("codex_session_lock_lost")

        try:
            result = process.execute(
                run=claim.run,
                prompt=context.render(
                    proposal_revision=proposal_revision,
                    feedback=feedback,
                ),
                session_id=session_id,
                developer_instructions=consumer_developer_instructions(
                    rendered_rules,
                    skill_protocol=render_business_skill_protocol(
                        installed_business_skill_catalog()
                    ),
                ),
                configure_command=lambda command: make_consumer_agent_command(
                    command,
                    controlled_cli=ControlledCliConfig(
                        command=sys.executable,
                        args=("-m", "app.agent_cli"),
                        cwd=str(SERVICE_ROOT),
                    ),
                ),
                parse_result=parse_consumer_agent_wire_result,
                prepare_result=lambda parsed: _prepare_outgoing_dingtalk_messages(
                    parsed,
                    context=context,
                ),
                persist_conversation_session=persist_conversation_session,
                on_progress=renew_session_lock,
                image_paths=[Path(path) for path in context.image_paths],
                required_capabilities=self._required_capabilities(context),
                conversation_contract_hash=contract_hash,
            )
            if (
                result.result.outcome.value == "failed"
                and result.result.error.retryable
            ):
                persisted = self.store.get_agent_run(claim.run.id)
                if persisted is not None and not persisted.tool_events:
                    attempts = self.store.list_agent_runtime_attempts(claim.run.id)
                    failed_attempt = attempts[-1] if attempts else None
                    failed_session_id = (
                        (failed_attempt.session_id or failed_attempt.source_session_id)
                        if failed_attempt is not None
                        else session_id or persisted.codex_session_id
                    )
                    if failed_session_id and failed_attempt is not None:
                        # A retryable result without any controlled tool event
                        # made no evidence progress. Retry with the current
                        # instructions instead of resuming that dead-end turn.
                        self._clear_route_session(
                            task.conversation_id,
                            failed_attempt.route_name,
                            failed_session_id,
                            failed_attempt.source_session_id,
                            session_id or "",
                            persisted.codex_session_id,
                        )
            return result
        except ResultParseError as exc:
            persisted = self.store.get_agent_run(claim.run.id)
            if (
                str(exc) == "no valid typed result JSON found in Codex JSONL"
                and persisted is not None
                and not persisted.tool_events
            ):
                attempts = self.store.list_agent_runtime_attempts(claim.run.id)
                failed_attempt = attempts[-1] if attempts else None
                failed_session_id = (
                    (failed_attempt.session_id or failed_attempt.source_session_id)
                    if failed_attempt is not None
                    else session_id or persisted.codex_session_id
                )
                if failed_session_id and failed_attempt is not None:
                    self._clear_route_session(
                        task.conversation_id,
                        failed_attempt.route_name,
                        failed_session_id,
                        failed_attempt.source_session_id,
                        session_id or "",
                        persisted.codex_session_id,
                    )
            raise


def _prepare_outgoing_dingtalk_messages(
    result: ConsumerAgentResult,
    *,
    context: AgentTaskContext,
) -> ConsumerAgentResult:
    """Apply the service-owned reply postfix before Audit reviews the candidate."""
    proposal = result.proposal
    if proposal is None or context.channel != "dingtalk":
        return result
    actions = tuple(
        _prepare_outgoing_dingtalk_action(action, context=context)
        for action in proposal.actions
    )
    if actions == proposal.actions:
        return result
    return result.model_copy(
        update={"proposal": proposal.model_copy(update={"actions": actions})}
    )


def _prepare_outgoing_dingtalk_action(
    action: ProposedAction,
    *,
    context: AgentTaskContext,
) -> ProposedAction:
    payload = action.payload
    argv = native_command_argv({"type": "command_execution", **payload})
    descriptor = describe_native_command({"type": "command_execution", **payload})
    command_path = dingtalk_outgoing_message_command_path(argv)
    if (
        argv is None
        or descriptor is None
        or descriptor.cli != "dws"
        or command_path is None
    ):
        return action
    reply_text = dingtalk_message_text(argv)
    if not reply_text.strip():
        return action
    prepared = prepare_outgoing_reply_text(
        reply_text=reply_text,
        original_text=context.trigger_text,
        feedback_base_url=feedback_spike_vercel_base_url(),
    )
    prepared_argv = list(replace_dingtalk_message_text(argv, prepared.text))
    prepared_payload = dict(payload)
    prepared_payload.pop("command", None)
    prepared_payload["argv"] = prepared_argv
    return action.model_copy(update={"payload": prepared_payload})
def consumer_developer_instructions(
    audit_rules: str,
    *,
    skill_protocol: str = "",
) -> str:
    core = _developer_instructions(
        audit_rules=audit_rules,
        skill_instruction=CONSUMER_DYNAMIC_SKILL_BODY,
        wire_model=ConsumerAgentWireResult,
        result_model=ConsumerAgentResult,
    )
    instructions = _role_developer_instructions(
        core,
        capability_instructions=REVIEWED_DWS_READ_INSTRUCTIONS,
        role_boundary=CONSUMER_ROLE_BOUNDARY,
    )
    return instructions + (f"\n\n{skill_protocol}" if skill_protocol else "")


def audit_developer_instructions(
    audit_rules: str,
    *,
    allow_write: bool = True,
    recovery_reconciliation: bool = False,
    frozen_delivery_retry: bool = False,
) -> str:
    core = _developer_instructions(
        audit_rules=audit_rules,
        skill_instruction=AUDIT_DYNAMIC_SKILL_BODY,
        wire_model=AuditAgentWireResult,
        result_model=AuditAgentResult,
    )
    recovery_boundary = (
        "The previous attempt did not produce a terminal result. Re-run the task "
        "with normal agent capabilities and return executed, feedback_provided, "
        "needs_human, or failed.\n\n"
        if recovery_reconciliation
        else ""
    )
    delivery_boundary = (
        "This is a retry of the same task. Preserve the business intent and return "
        "one terminal structured result.\n\n"
        if frozen_delivery_retry
        else ""
    )
    instructions = _role_developer_instructions(
        core,
        capability_instructions=(
            "Reread every verified Skill path supplied from Consumer A with "
            "agent_cli.read_skill and compare the returned sha256 with the supplied "
            "receipt before review or execution. Also read the operation Skill for "
            "each proposed capability. A missing, unreadable, changed, or mismatched "
            "Skill requires feedback_provided rather than a guess. Use "
            "agent_cli.execute_reviewed_read for live reads. "
            "For an unfamiliar DWS command, inspect its runtime contract with "
            '`dws schema --cli-path "<product> <command>" --compact --format json` '
            "before review; schema discovery is not an unavailable-tool result, "
            "and missing command syntax is a read-only evidence task. Execute the "
            "result only as a reviewed local read command. "
            + (
                "Use agent_cli.execute_reviewed_write only for allowed external "
                "writes. "
                if allow_write
                else "External writes are unavailable in this turn. "
            )
            + "Do not "
            "use native shell execution; the turn-specific permission determines "
            "whether a write is allowed, and unknown outcomes remain read-only."
        ),
        role_boundary=AUDIT_ROLE_BOUNDARY,
    )
    return instructions + "\n\n" + recovery_boundary + delivery_boundary


def _developer_instructions(
    *,
    audit_rules: str,
    skill_instruction: str,
    wire_model: type[ConsumerAgentWireResult] | type[AuditAgentWireResult],
    result_model: type[ConsumerAgentResult] | type[AuditAgentResult],
) -> str:
    validate_audit_rules_text(audit_rules)
    return "\n\n".join(
        (
            f"## Audit Rules\n{audit_rules}",
            "## Runtime Invariants\n"
            "1. [role_boundary] Role Boundary: Consumer Agent A is the user's read-only representative; Audit Agent B is the only role allowed to execute an accepted candidate.\n"
            "2. [output_contracts] Output Contracts: return the typed wire contract.\n"
            "3. [supported_facts] Supported Facts: use only supported facts.\n"
            "4. [meaning_preservation] Meaning Preservation: preserve candidate meaning.\n"
            "5. [duplicate_effects] Duplicate Effects: prevent duplicate external effects.\n"
            "6. [unknown_effects] Unknown Effects: keep unknown effects read-only.\n"
            "7. [external_secrecy] External Secrecy: do not expose secrets.\n"
            "8. [dependency_auth] Dependency Authentication: verify dependency evidence.",
            f"## Dynamic Skill\n{skill_instruction}",
            f"## Pydantic Wire Contract\n{_schema_json(wire_model)}",
            f"## Pydantic Result Contract\n{_schema_json(result_model)}",
        )
    )


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
        "command tool. Any additional permitted local file read must use the "
        "matching controlled `agent_cli` read tool."
    )
    return instructions + "\n\n## Role Boundary\n" + role_boundary
