from __future__ import annotations

import json
from hashlib import sha256
import sys
from collections.abc import Callable
from pathlib import Path
from uuid import uuid4

from app.agent_context import AgentTaskContext
from app.agent_contracts import AuditFeedback, ConsumerAgentResult, ConsumerProposal
from app.agent_result import ResultParseError
from app.agent_wire_contracts import (
    ConsumerAgentWireResult,
    parse_consumer_agent_wire_result,
)
from app.native_cli_metadata import NativeCliMetadataClassifier
from app.audit_rules import render_audit_rules
from app.agent_effects import LEASE_SECONDS, McpToolEffectRegistry
from app.agent_turn_runner import AgentTurnProcess, AgentTurnRunResult, ProcessExecutor
from app.codex_history import find_codex_session_path
from app.store import AgentRole, AutoReplyStore, ReplyTask
from app.wechat.codex_safety import ControlledCliConfig, make_consumer_agent_command


SCHEMA_PATH = Path(__file__).resolve().parent / "schemas" / "consumer_agent_wire.schema.json"
SERVICE_ROOT = Path(__file__).resolve().parent.parent
SHARED_RULES_PATH = Path.home() / ".agents" / "AGENT.md"
REVIEWED_DWS_READ_INSTRUCTIONS = """
For live DingTalk, Lark, or local file evidence, call `agent_cli.execute_reviewed_read`
with the exact reviewed read command. This
lets the Agent use the principal's local CLI credential store and makes a
reviewed local read command independently repeatable by Audit B. Unknown shell
commands and every write command remain forbidden for Consumer Agent A.

For a downloaded `.xlsx` or `.xlsm` file in the temporary material directory,
call `agent_cli.read_spreadsheet` with its exact path. This is the approved
Python-backed spreadsheet reader; do not fall back to direct Python or shell
execution to inspect the workbook.

Before proposing a DingTalk message send, read
`/Users/derek/.agents/skills/dws/multi/dingtalk-chat/SKILL.md` with
`agent_cli.read_skill` and use its documented command shape. Unknown send
syntax is an evidence-reading task, not a reason to return `needs_human`.

Requests to inspect, evaluate, or improve a referenced skill, document,
configuration, or other readable material are normal Agent work. Read the
material, complete the requested analysis, and propose the resulting reply or
safe follow-up yourself. Do not return `needs_human` merely because the work
requires tool use, research, or technical judgment. Reserve `needs_human` for
an actual unresolved management choice or an ambiguous irreversible target.
When returning needs_human, first finish every available read and safe
follow-up. Then offer two to four materially different, actionable choices.
Do not offer "investigate", "ask me", or an option that merely repeats the
ambiguity.
""".strip()


def consumer_wire_contract_hash() -> str:
    """Fingerprint the strict wire schema used to decide session compatibility."""
    schema = ConsumerAgentWireResult.model_json_schema()
    encoded = json.dumps(schema, sort_keys=True, separators=(",", ":"))
    return sha256(encoded.encode("utf-8")).hexdigest()

CONSUMER_ROLE_BOUNDARY = """
Authoritative Consumer role boundary: configurable Audit Rules are review
criteria, not instructions for you to execute, approve, publish, or return a
candidate to another Agent. You are Consumer Agent A and must finish with one
valid Consumer Agent wire JSON object matching the supplied schema. The service
converts it into a valid ConsumerAgentResult JSON object after strict validation.
Nested proposal data is encoded as proposal_json and will be strictly validated
before it can affect execution.

Wire field encoding: proposal_json is a JSON-encoded object only when outcome
is proposal; otherwise it must be null. decision_options_json is always a
JSON-encoded array: it must contain two to four mutually exclusive options only
when outcome is needs_human, and [] for every other outcome. Each option has a
short label, an instruction that can be executed after Derek selects it, and a
concrete consequence. Do not put a JSON array, markdown, or an additional
wrapper object in proposal_json.

For every DWS write command in proposal_json, include the non-interactive
confirmation flag --yes. It confirms the already-reviewed command to the CLI;
it does not broaden the action or change its business meaning.

Write commands belong only as data inside proposal_json for Audit Agent B. Never
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

Wire field encoding: feedback_json and external_result_json are each either
null or a JSON-encoded object for their own field. For revision_required,
feedback_json is required and its object has exactly these string fields:
rule, observation, and requested_revision. Do not use aliases such as
failed_rule, evidence, or required_change. For executed, external_result_json
must contain exactly operation_id, verification_summary, and
live_result_reference. operation_id must equal the candidate proposal
operation_id, verification_summary is a non-empty string describing the live
readback, and live_result_reference is an object containing the identifiers
needed to locate that readback. reconciliation_json is always a
JSON-encoded array: use [] unless outcome is reconciled, and only reconciled
may contain reconciliation entries. Do not put receipt summaries, operation
metadata, or an object wrapper in reconciliation_json.

Outcome field combinations: executed requires side_effect_state=confirmed,
feedback_json=null, external_result_json as an object, and reconciliation_json=[];
revision_required requires side_effect_state=none, feedback_json as above,
external_result_json=null, and reconciliation_json=[]; reconciled requires
side_effect_state=unknown, feedback_json=null, external_result_json=null, and
reconciliation_json entries with exactly action_index, disposition (present,
absent, or ambiguous), and read_result_digest. needs_human and failed require
side_effect_state=none with all three nested fields empty (null, null, []).

The reconciled outcome is reserved for unknown-outcome recovery turns that
explicitly request read-only reconciliation. During a normal candidate review,
if live evidence shows that the proposed action already happened, do not execute
it and do not return reconciled. Instead, return revision_required and ask
Consumer Agent A to return no_action because the requested effect is already
present.

Never execute a DWS write command without --yes. Return concrete feedback for
Consumer Agent A to add the non-interactive confirmation flag before execution.
""".strip()


class ConsumerAgentRunner:
    def __init__(
        self,
        *,
        store: AutoReplyStore,
        workspace: Path,
        codex_bin: str = "codex",
        executor: ProcessExecutor | None = None,
        owner: str | None = None,
        mcp_effect_registry: McpToolEffectRegistry | None = None,
        native_cli_classifier: NativeCliMetadataClassifier | None = None,
        codex_session_exists: Callable[[str], bool] | None = None,
    ) -> None:
        self.store = store
        self.workspace = workspace
        self.codex_bin = codex_bin
        self.executor = executor
        self.owner = owner or f"consumer-agent-{uuid4().hex}"
        self.effects = mcp_effect_registry or McpToolEffectRegistry.default()
        self.native_cli_classifier = native_cli_classifier
        self.codex_session_exists = codex_session_exists or (
            lambda session_id: find_codex_session_path(session_id) is not None
        )

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
        conversation_session_id = (
            self.store.get_codex_session_id(task.conversation_id) or None
        )
        if (
            conversation_session_id
            and self.store.get_codex_session_contract_hash(task.conversation_id)
            != contract_hash
        ):
            # A resumed session retains its old output contract. Start a fresh
            # session when the strict wire schema changes instead of accepting
            # an incompatible result or treating it as a permanent task error.
            self.store.clear_codex_session_if_matches(
                task.conversation_id,
                conversation_session_id,
            )
            conversation_session_id = None
        if (
            conversation_session_id
            and not self.codex_session_exists(conversation_session_id)
        ):
            self.store.clear_codex_session_if_matches(
                task.conversation_id,
                conversation_session_id,
            )
            conversation_session_id = None
        claim = self.store.claim_agent_run(
            task.id,
            task.execution_generation,
            role=AgentRole.CONSUMER,
            proposal_revision=proposal_revision,
            turn_attempt=0,
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
        persist_conversation_session = not (
            claim.run.codex_session_id
            and conversation_session_id
            and claim.run.codex_session_id != conversation_session_id
        )
        process = AgentTurnProcess[ConsumerAgentResult](
            store=self.store,
            task=task,
            workspace=self.workspace,
            owner=self.owner,
            executor=self.executor,
            codex_bin=self.codex_bin,
            mcp_effect_registry=self.effects,
            native_cli_classifier=self.native_cli_classifier,
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
                prompt=(
                    context.render(
                        proposal_revision=proposal_revision,
                        feedback=feedback,
                    )
                    + "\n\n"
                    + _consumer_proposal_contract()
                ),
                session_id=session_id,
                schema_path=SCHEMA_PATH,
                expected_schema=ConsumerAgentWireResult.model_json_schema(),
                developer_instructions=consumer_developer_instructions(
                    "Consumer Agent A is read-only.\n\n" + rendered_rules
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
            )
            self.store.set_codex_session_contract_hash(
                task.conversation_id,
                contract_hash,
            )
            return result
        except ResultParseError as exc:
            persisted = self.store.get_agent_run(claim.run.id)
            if (
                str(exc) == "no valid typed result JSON found in Codex JSONL"
                and persisted is not None
                and not persisted.tool_events
            ):
                failed_session_id = session_id or persisted.codex_session_id
                if failed_session_id:
                    self.store.clear_codex_session_if_matches(
                        task.conversation_id,
                        failed_session_id,
                    )
            raise


def consumer_developer_instructions(role_instruction: str) -> str:
    instructions = _role_developer_instructions(
        role_instruction,
        capability_instructions=REVIEWED_DWS_READ_INSTRUCTIONS,
        role_boundary=CONSUMER_ROLE_BOUNDARY,
    )
    return f"{instructions}\n\n{_consumer_proposal_contract()}"


def _consumer_proposal_contract() -> str:
    proposal_schema = json.dumps(
        ConsumerProposal.model_json_schema(),
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return (
        "For proposal outcome, proposal_json must decode to this JSON Schema exactly. "
        "Use this current contract instead of proposal shapes from earlier turns:\n"
        f"{proposal_schema}"
    )


def audit_developer_instructions(role_instruction: str) -> str:
    return _role_developer_instructions(
        role_instruction,
        capability_instructions=(
            "Use agent_cli.execute_reviewed_read for every live read and "
            "agent_cli.execute_reviewed_write for every allowed external write. "
            "When exact candidate content comes from a local file, independently "
            "verify it with the same reviewed local read command before execution. "
            "Before using a DWS command, read the operation-specific installed "
            "skill with agent_cli.read_skill. In a reconciliation turn, missing "
            "command syntax is a read-only evidence task: load the skill, run the "
            "minimal matching readback, and return its digest rather than failing "
            "or escalating. "
            "Do not use exec_command or another native shell tool: Audit B actions "
            "must flow through the reviewed capability so they are checked and "
            "receipted. The turn-specific execution permission determines whether "
            "an external write is allowed."
        ),
        role_boundary=AUDIT_ROLE_BOUNDARY,
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
    instructions = role_instruction + "\n\n" + capability_instructions
    if shared:
        instructions += "\n\n" + shared
    return instructions + "\n\n" + role_boundary
