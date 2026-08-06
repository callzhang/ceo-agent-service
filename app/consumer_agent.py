from __future__ import annotations

import sys
from collections.abc import Callable
from pathlib import Path
from uuid import uuid4

from app.agent_context import AgentTaskContext
from app.agent_contracts import ConsumerAgentResult
from app.agent_result import parse_typed_agent_result
from app.audit_rules import render_audit_rules
from app.agent_runner import LEASE_SECONDS, McpToolEffectRegistry
from app.agent_turn_runner import AgentTurnProcess, AgentTurnRunResult, ProcessExecutor
from app.codex_history import find_codex_session_path
from app.store import AgentRole, AutoReplyStore, ReplyTask
from app.wechat.codex_safety import ControlledCliConfig, make_consumer_agent_command


SCHEMA_PATH = Path(__file__).resolve().parent / "schemas" / "consumer_agent_result.schema.json"
SERVICE_ROOT = Path(__file__).resolve().parent.parent
SHARED_RULES_PATH = Path.home() / ".agents" / "AGENT.md"


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
        codex_session_exists: Callable[[str], bool] | None = None,
    ) -> None:
        self.store = store
        self.workspace = workspace
        self.codex_bin = codex_bin
        self.executor = executor
        self.owner = owner or f"consumer-agent-{uuid4().hex}"
        self.effects = mcp_effect_registry or McpToolEffectRegistry.default()
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
                    proposal_revision=proposal_revision,
                    parent_agent_run_id=parent_agent_run_id,
                    rendered_rules=rendered_rules,
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
        proposal_revision: int,
        parent_agent_run_id: int | None,
        rendered_rules: str,
    ) -> AgentTurnRunResult[ConsumerAgentResult]:
        session_id = self.store.get_codex_session_id(task.conversation_id) or None
        if session_id and not self.codex_session_exists(session_id):
            self.store.clear_codex_session(task.conversation_id)
            session_id = None
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
        process = AgentTurnProcess[ConsumerAgentResult](
            store=self.store,
            task=task,
            workspace=self.workspace,
            owner=self.owner,
            executor=self.executor,
            codex_bin=self.codex_bin,
            mcp_effect_registry=self.effects,
        )
        return process.execute(
            run=claim.run,
            prompt=context.render(),
            session_id=session_id,
            schema_path=SCHEMA_PATH,
            expected_schema=ConsumerAgentResult.model_json_schema(),
            developer_instructions=_developer_instructions(
                "Consumer Agent A is read-only.\n\n" + rendered_rules
            ),
            configure_command=lambda command: make_consumer_agent_command(
                command,
                reviewed_mcp_tools=self.effects.reviewed_read_tools(),
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
        )


def _developer_instructions(role_instruction: str) -> str:
    shared = (
        SHARED_RULES_PATH.read_text(encoding="utf-8").strip()
        if SHARED_RULES_PATH.is_file()
        else ""
    )
    return role_instruction if not shared else role_instruction + "\n\n" + shared
