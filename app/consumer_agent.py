from __future__ import annotations

import json
import sys
from collections.abc import Callable
from hashlib import sha256
from pathlib import Path
from uuid import uuid4

from app.agent_context import (
    _CONSUMER_AGENT_RULES,
    IMAGE_DEPENDENCY_UNAVAILABLE_SUMMARY,
    AgentTaskContext,
)
from app.agent_contracts import (
    AuditAgentResult,
    AuditFeedback,
    ConsumerAgentResult,
    ConsumerOutcome,
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
from app.native_cli_metadata import (
    NativeCliMetadataClassifier,
    service_read_command_contract,
)
from app.store import AgentRole, AutoReplyStore, ReplyTask
from app.wechat.codex_safety import ControlledCliConfig, make_consumer_agent_command

SERVICE_ROOT = Path(__file__).resolve().parent.parent
SCHEMA_PATH = SERVICE_ROOT / "app" / "schemas" / "consumer_agent_result.schema.json"
DYNAMIC_SKILL_MARKER = "[dynamic-skill]"
CONSUMER_DYNAMIC_SKILL_SENTENCE = (
    "Consumer Agent A independently selects and reads every applicable business and "
    "operation Skill with `agent_cli.read_skill` before forming the candidate."
)
AUDIT_DYNAMIC_SKILL_SENTENCE = (
    "Audit Agent B independently determines every business and operation Skill "
    "applicable to the candidate, requires the corresponding verified Consumer A "
    "receipt for each applicable Skill, rereads each exact receipt path with "
    "`agent_cli.read_skill`, verifies its sha256, and returns revision_required if "
    "any applicable receipt is absent, unreadable, changed, or mismatched. For an "
    "already-unknown effect only, B may perform strictly read-only evidence "
    "reconciliation without a receipt when no business Skill is needed to decide "
    "whether the effect happened; B must not execute or retry the candidate."
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
Before making a domain judgment, inspect the installed Skill catalog or native
Skills list and call `agent_cli.read_skill` for the most specific applicable business Skill.
Then load the operation Skill named by that business Skill before proposing a
concrete CLI or MCP action. Do not ask the service to classify the domain for you.

For live DingTalk, Lark, or local file evidence, call `agent_cli.execute_reviewed_read`
with the exact reviewed read command. This
lets the Agent use the principal's local CLI credential store and makes a
reviewed local read command independently repeatable by Audit B. Unknown shell
commands and every write command remain forbidden for Consumer Agent A.
For a DWS or Lark download, set its destination to a fresh filename directly
under `/tmp/ceo-agent-service-materials`; downloads without that exact bounded
destination or attempts to overwrite an existing file are rejected.

Before composing a DWS command that was not supplied as an exact read command,
query its local runtime contract with `dws schema --cli-path "<product> <command>"
--compact --format json` through `agent_cli.execute_reviewed_read`. Copy the
returned command path and flags exactly; do not invent shortcut names or flag
aliases. A rejected read has no external effect: inspect its error, then use the
runtime contract or the installed skill to correct it in the same turn.

For a downloaded local material file, call `agent_cli.read_text_file` first. It
automatically returns a bounded UTF-8 text file or an OOXML workbook preview,
including when the download filename has no extension. Use
`agent_cli.read_spreadsheet` only when the material is already known to be an
xlsx workbook. Arbitrary local shell and Python execution remain forbidden by
the service read boundary. Do not return `needs_human` merely because a
supported material requires one of these reads.

Before proposing a DingTalk message send, read
`/Users/derek/.agents/skills/dws/multi/dingtalk-chat/SKILL.md` with
`agent_cli.read_skill` and use its documented command shape. Unknown send
syntax is an evidence-reading task, not a reason to return `needs_human`.

For a DingTalk document access or sharing request, read
`/Users/derek/.agents/skills/dws/multi/dingtalk-doc/SKILL.md`, then inspect
the live collaborator list with
`dws doc +inspect --node <DOC_ID> --include-permissions --format json` through
`agent_cli.execute_reviewed_read` before treating it as a management choice.
Also read the document content and collect requester identity, current role,
and document need-to-know through the applicable DingTalk directory Skills.
Before deciding access, call `memory_recall` with a focused query containing
the requester, document title, decision scope, and any prior authorization or
working relationship anchors. Use the result to find stable role context,
prior delegation, and related commitments that the live reads should verify.
Memory is stable context, not proof of current external state: distinguish it
from the current directory, document, and permission evidence in your sourced
facts, and do not infer absent current facts when memory is unavailable.
Compare those facts with the document's stated audience, subject, and decision
responsibilities before comparing the requested role with existing
collaborators. The collaborator list shows current state, not authorization.
Do not return `no_action` from the existing role alone. Return `no_action`
only when the live authorization assessment supports access and the existing
role already satisfies or exceeds the request. If current access conflicts
with the evidence, identify that discrepancy; do not treat missing positive
evidence as proof that the requester must be removed. Escalate only when the
completed live reads leave a material authorization conflict or an unresolved
owner decision.

Requests to inspect, evaluate, or improve a referenced skill, document,
configuration, or other readable material are normal Agent work. Read the
material, complete the requested analysis, and propose the resulting reply or
safe follow-up yourself. Do not return `needs_human` merely because the work
requires tool use, research, or technical judgment. Reserve `needs_human` for
an actual unresolved management choice or an ambiguous irreversible target.

For candidate screening, interview preparation, or hiring-review requests,
read `/Users/derek/.agents/skills/ceo-personnel-communication/SKILL.md` and
`/Users/derek/.agents/skills/stardust-interview/SKILL.md`, then use the
Xiaoqing interview MCP tools to search for the candidate and read the complete
candidate context, job profile, parsed resume, interview records, and existing
assessment. These are mandatory preconditions for every candidate outcome,
including a status acknowledgement, `proposal`, `no_action`, or `needs_human`.
A request for the principal's "real-person" judgment does not waive these
reads. Do not propose sending "I will review" or an equivalent acknowledgement
before completing them. First prepare a sourced evidence packet, identify
material gaps, and give a bounded recommendation and targeted verification
plan. Only the remaining sensitive hiring or advancement decision may be
returned as `needs_human`. If Xiaoqing or a required candidate read is
unavailable, return a retryable service-dependency failure; do not misclassify
unread evidence as a management decision and do not invent resume facts.

Before returning `needs_human`, classify the proposed effect from first
principles. A low-consequence operating choice is limited to the principal's
own availability, preparation, acknowledgement, or follow-up, or to a bounded
internal participant action that implements an already-confirmed event or
tracked commitment without changing its scope, timing, owner, or business
meaning. The current evidence must show no conflict, sensitive target, budget,
approval, new external commitment, or irreversible outcome. For that class of choice, call
`memory_recall` with a focused query when the memory tool is available, read
the applicable business and operation Skills, and inspect live evidence.
Memory is context, not proof of the current external state. When those sources
support one ordinary, reversible action, return its proposal for Audit B to
execute and verify. Do not escalate merely because another reasonable default
exists. When optional paths are otherwise equivalent, choose the one that adds
no new work or deliverable for another person. Reserve `needs_human` for conflicting durable evidence, material
external impact, an irreversible result, an unresolved conflict, or a target
that cannot be reliably identified.

When returning needs_human, first finish every available read and safe
follow-up. Then offer two to four materially different, actionable choices.
Do not offer "investigate", "ask me", or an option that merely repeats the
ambiguity.
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
Authoritative Consumer role boundary: configurable Audit Rules are review
criteria, not instructions for you to execute, approve, publish, or return a
candidate to another Agent. You are Consumer Agent A and must finish with one
valid Consumer Agent wire JSON object matching the supplied schema. The service
converts it into a valid ConsumerAgentResult JSON object after strict validation.
Nested proposal data is supplied directly in the proposal field and is strictly
validated before it can affect execution.

Wire field encoding: proposal is an object only when outcome is proposal;
otherwise it must be null. decision_options is an array containing two to four
mutually exclusive options only when outcome is needs_human, and [] for every
other outcome. For needs_human,
each array item must contain exactly these non-empty string fields: `key`,
`label`, `instruction`, and `consequence`. `key` must be unique within the
array and stable enough for the audit page to submit the selected instruction;
use concise identifiers such as `option_1`, not the display label. Do not put a
JSON string, markdown, or an additional wrapper object in proposal.
Every outcome also uses the three top-level wire fields error_code, error_retryable, and error_authorization_required.
Do not return a nested error object; the service creates that result-model
object after wire validation.

For every DWS write command in proposal, include the non-interactive
confirmation flag --yes. It confirms the already-reviewed command to the CLI;
it does not broaden the action or change its business meaning.

Write commands belong only as data inside proposal for Audit Agent B. Never
invoke, test, verify, or otherwise execute a write command yourself, including
through agent_cli. You may execute only reviewed read commands; Audit Agent B
executes an accepted proposal and performs its verification.
""".strip()

AUDIT_ROLE_BOUNDARY = """
Authoritative Audit role boundary: configurable Audit Rules are review
criteria. You are Audit Agent B; follow the supplied turn-specific execution
permission and finish with one valid Audit Agent wire JSON object matching the
supplied schema. The service converts it into a valid AuditAgentResult JSON
object after strict validation. Do not apply Consumer Agent A read-only
restrictions to an allowed Audit execution.

Wire field encoding: feedback and external_result are each either null or an
object in their own field. decision_options is an array containing two to four
mutually exclusive options only when outcome is needs_human, and [] for every
other outcome. Each decision option has the same key, label, instruction, and
consequence fields as the Consumer wire contract. For revision_required, feedback is required and its
object has exactly these string fields:
rule, observation, and requested_revision. Do not use aliases such as
failed_rule, evidence, or required_change. For executed, external_result must
contain exactly operation_id, verification_summary, and
live_result_reference. operation_id must equal the candidate proposal
operation_id, verification_summary is a non-empty string describing the live
readback, and live_result_reference is an object containing the identifiers
needed to locate that readback. reconciliation is always an array: use [] unless
outcome is reconciled, and only reconciled
may contain reconciliation entries. Do not put receipt summaries, operation
metadata, or an object wrapper in reconciliation.
Every outcome also uses the three top-level wire fields error_code, error_retryable, and error_authorization_required.
Do not return a nested error object; the service creates that result-model
object after wire validation.

Outcome field combinations: executed requires side_effect_state=confirmed,
feedback=null, external_result as an object, and reconciliation=[];
revision_required requires side_effect_state=none, feedback as above,
external_result=null, and reconciliation=[]; reconciled requires
side_effect_state=unknown, feedback=null, external_result=null, and
reconciliation entries with exactly action_index, disposition (present,
absent, or ambiguous), and read_result_digest. needs_human requires
side_effect_state=none, feedback=null, external_result=null, reconciliation=[],
and two to four actionable decision_options. failed requires
side_effect_state=none with the nested fields empty and decision_options=[].

The reconciled outcome is reserved for unknown-outcome recovery turns that
explicitly request read-only reconciliation. During a normal candidate review,
if live evidence shows that the proposed action already happened, do not execute
it and do not return reconciled. Instead, return revision_required and ask
Consumer Agent A to return no_action because the requested effect is already
present.

Never execute a DWS write command without --yes. Return concrete feedback for
Consumer Agent A to add the non-interactive confirmation flag before execution.

The low-consequence decision policy in Consumer capability instructions is an
authorized judgment standard, not an unsupported personal commitment. When a
proposal follows that standard and selects the minimum reversible path that
does not add work or a deliverable for another person, do not require a prior
message containing the same choice. Reject it only when live evidence conflicts
with that choice or it changes scope, timing, owner, business meaning, budget,
approval, a sensitive target, or an external commitment.
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
        conversation_session_id = route_sessions.get("codex_oauth")
        if task.force_new_decision and route_sessions:
            # A forced rerun must reassess the task with the current tools and
            # instructions. Resuming the old conversation can replay a failed
            # tool path before the agent sees those changes.
            for route_name, route_session_id in route_sessions.items():
                self._clear_route_session(
                    task.conversation_id, route_name, route_session_id
                )
            route_sessions = {}
            conversation_session_id = None
        for route_name, route_session_id in tuple(route_sessions.items()):
            if not self._route_session_exists(route_name, route_session_id):
                self._clear_route_session(
                    task.conversation_id, route_name, route_session_id
                )
                route_sessions.pop(route_name)
        conversation_session_id = route_sessions.get("codex_oauth")
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
        persist_conversation_session = conversation_session_id is None
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

        if (image_error := context.image_dependency_error) is not None:
            result = ConsumerAgentResult(
                outcome=ConsumerOutcome.FAILED,
                summary=IMAGE_DEPENDENCY_UNAVAILABLE_SUMMARY,
                proposal=None,
                error=image_error,
            )
            failed = self.store.fail_agent_run(
                claim.run.id,
                result.error.model_dump(mode="json"),
                owner=self.owner,
            )
            return AgentTurnRunResult(
                run_id=failed.id,
                result=result,
                transcript_start_line=failed.transcript_start_line,
                transcript_end_line=failed.transcript_end_line,
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
        "This is an unknown-outcome recovery reconciliation turn. Before returning "
        "a result, perform a target-matched live read for every unresolved action "
        "with a registered readback. External writes are unavailable. Return only "
        "outcome=reconciled with side_effect_state=unknown, feedback=null, and "
        "external_result=null. Include one reconciliation entry per required action "
        "with the exact result_digest from this turn's read receipt; use ambiguous "
        "when the read cannot prove present or absent. Do not return executed, "
        "revision_required, failed, or needs_human in this recovery turn.\n\n"
        if recovery_reconciliation
        else ""
    )
    delivery_boundary = (
        "This is a frozen delivery retry. Consumer A's persisted proposal is an "
        "immutable business decision for this turn: do not reconsider it, request "
        "a revision, or call Consumer A. Execute exactly its authorized chat write "
        "once, then perform the required target-matched readback. If execution or "
        "readback cannot complete, return failed; do not return revision_required "
        "or needs_human.\n\n"
        if frozen_delivery_retry
        else ""
    )
    return recovery_boundary + delivery_boundary + _role_developer_instructions(
        core,
        capability_instructions=(
            "Reread every verified Skill path supplied from Consumer A with "
            "agent_cli.read_skill and compare the returned sha256 with the supplied "
            "receipt before review or execution. Also read the operation Skill for "
            "each proposed capability. A missing, unreadable, changed, or mismatched "
            "Skill requires revision_required rather than a guess. Use "
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
