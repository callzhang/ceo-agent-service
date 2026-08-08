from __future__ import annotations

import sys
from collections.abc import Callable
from pathlib import Path
from uuid import uuid4

from app.agent_context import AgentTaskContext
from app.agent_contracts import AuditFeedback, ConsumerAgentResult
from app.agent_result import ResultParseError, parse_typed_agent_result
from app.native_cli_metadata import NativeCliMetadataClassifier
from app.audit_rules import render_audit_rules
from app.agent_effects import LEASE_SECONDS, McpToolEffectRegistry
from app.agent_turn_runner import AgentTurnProcess, AgentTurnRunResult, ProcessExecutor
from app.codex_history import find_codex_session_path
from app.store import AgentRole, AutoReplyStore, ReplyTask
from app.wechat.codex_safety import ControlledCliConfig, make_consumer_agent_command


SCHEMA_PATH = Path(__file__).resolve().parent / "schemas" / "consumer_agent_result.schema.json"
SERVICE_ROOT = Path(__file__).resolve().parent.parent
SHARED_RULES_PATH = Path.home() / ".agents" / "AGENT.md"
REVIEWED_DWS_READ_INSTRUCTIONS = """
When DingTalk evidence is required, call `agent_cli.execute_reviewed_read` with
the exact read-only `dws` command. This lets the Agent use the principal's
local DWS credential store without exposing it inside the read-only sandbox.
Unknown shell commands and every write command remain forbidden for Consumer
Agent A.
""".strip()

CONSUMER_ROLE_BOUNDARY = """
Authoritative Consumer role boundary: configurable Audit Rules are review
criteria, not instructions for you to execute, approve, publish, or return a
candidate to another Agent. You are Consumer Agent A and must finish with one
valid ConsumerAgentResult JSON object matching the supplied schema.
""".strip()

AUDIT_ROLE_BOUNDARY = """
Authoritative Audit role boundary: configurable Audit Rules are review
criteria. You are Audit Agent B; follow the supplied turn-specific execution
permission and finish with one valid AuditAgentResult JSON object matching the
supplied schema. Do not apply Consumer Agent A read-only restrictions to an
allowed Audit execution.
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
        conversation_session_id = (
            self.store.get_codex_session_id(task.conversation_id) or None
        )
        if (
            conversation_session_id
            and not self.codex_session_exists(conversation_session_id)
        ):
            self.store.clear_codex_session(task.conversation_id)
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
        session_id = claim.run.codex_session_id or conversation_session_id
        if session_id and not self.codex_session_exists(session_id):
            raise RuntimeError("agent_run_session_unavailable")
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
            return process.execute(
                run=claim.run,
                prompt=context.render(
                    proposal_revision=proposal_revision,
                    feedback=feedback,
                ),
                session_id=session_id,
                schema_path=SCHEMA_PATH,
                expected_schema=ConsumerAgentResult.model_json_schema(),
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
                parse_result=lambda raw: parse_typed_agent_result(
                    raw,
                    ConsumerAgentResult,
                ),
                persist_conversation_session=True,
                on_progress=renew_session_lock,
            )
        except ResultParseError as exc:
            persisted = self.store.get_agent_run(claim.run.id)
            if (
                session_id
                and str(exc) == "no valid typed result JSON found in Codex JSONL"
                and persisted is not None
                and not persisted.tool_events
            ):
                self.store.clear_codex_session(task.conversation_id)
            raise


def consumer_developer_instructions(role_instruction: str) -> str:
    return _role_developer_instructions(
        role_instruction,
        capability_instructions=REVIEWED_DWS_READ_INSTRUCTIONS,
        role_boundary=CONSUMER_ROLE_BOUNDARY,
    )


def audit_developer_instructions(role_instruction: str) -> str:
    return _role_developer_instructions(
        role_instruction,
        capability_instructions=(
            "Use agent_cli.execute_reviewed_read for every live read and "
            "agent_cli.execute_reviewed_write for every allowed external write. "
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
