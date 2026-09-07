from __future__ import annotations

from collections.abc import Callable
import json
from pathlib import Path
import sys
from uuid import uuid4

from app.agent_context import AuditTurnContext
from app.agent_contracts import AuditAgentResult
from app.agent_effects import LEASE_SECONDS, McpToolEffectRegistry
from app.agent_runtime_config import AgentRuntimeConfig
from app.agent_runtime_router import AgentRuntimeRouter
from app.agent_turn_runner import (
    AgentTurnProcess,
    AgentTurnRunResult,
    ProcessExecutor,
)
from app.agent_wire_contracts import parse_audit_agent_wire_result
from app.audit_rules import render_audit_rules
from app.claude_runtime_adapter import ClaudeRuntimeAdapter
from app.codex_runtime_adapter import CodexRuntimeAdapter
from app.friday_runtime_adapter import FridayRuntimeAdapter
from app.consumer_agent import audit_developer_instructions
from app.service_message_sender import agent_message_delivery_key
from app.external_action_identity import expected_external_action
from app.store import (
    AgentRole,
    AgentRun,
    AutoReplyStore,
    ReplyTask,
)
from app.wechat.codex_safety import ControlledCliConfig, make_audit_agent_command

SERVICE_ROOT = Path(__file__).resolve().parent.parent


class AuditAgentRunner:
    """Execute one ordinary Audit turn.

    Audit validates the typed proposal/result contract and delegates provider
    capabilities to the selected runtime.  It does not maintain an
    application-level command review, read-only, unknown-outcome, or separate
    recovery state machine.
    """

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
        mcp_effect_registry: McpToolEffectRegistry | None = None,
        dry_run: bool = False,
        refresh_runtime_capabilities: Callable[[], object] | None = None,
    ) -> None:
        self.store = store
        self.workspace = workspace
        self.codex_bin = codex_bin
        self.runtime_config = runtime_config
        self.runtime_router = runtime_router
        self.codex_adapter = codex_adapter
        self.claude_adapter = claude_adapter
        self.friday_adapter = friday_adapter
        self.executor = executor
        self.owner = owner or f"audit-agent-{uuid4().hex}"
        self.effects = mcp_effect_registry or McpToolEffectRegistry.default()
        self.dry_run = dry_run
        self.refresh_runtime_capabilities = refresh_runtime_capabilities

    @staticmethod
    def _required_capabilities(
        context: AuditTurnContext, *, recovery_phase: str = ""
    ) -> frozenset[str]:
        del recovery_phase
        required = {"task_context", f"channel:{context.task.channel}"}
        if context.task.image_paths:
            required.add("image_input")
        return frozenset(required)

    def run(
        self,
        task: ReplyTask,
        context: AuditTurnContext,
        *,
        turn_attempt: int,
        parent_agent_run_id: int,
        frozen_delivery_retry: bool = False,
    ) -> AgentTurnRunResult[AuditAgentResult]:
        if context.task.task_id != task.id:
            raise ValueError("agent context task does not match reply task")
        claim = self.store.claim_agent_run(
            task.id,
            task.execution_generation,
            role=AgentRole.AUDIT,
            proposal_revision=context.proposal_revision,
            turn_attempt=turn_attempt,
            parent_agent_run_id=parent_agent_run_id,
            operation_id=context.operation_id,
            owner=self.owner,
            lease_seconds=LEASE_SECONDS,
        )
        if not claim.claimed:
            raise RuntimeError("agent_run_unavailable")
        return self._execute_claimed(
            task,
            context,
            run=claim.run,
            rendered_rules=render_audit_rules(AgentRole.AUDIT),
            frozen_delivery_retry=frozen_delivery_retry,
        )

    def _execute_claimed(
        self,
        task: ReplyTask,
        context: AuditTurnContext,
        *,
        run: AgentRun,
        rendered_rules: str,
        frozen_delivery_retry: bool = False,
    ) -> AgentTurnRunResult[AuditAgentResult]:
        prompt = context.render()
        expected_effect_actions_list: list[dict[str, object]] = []
        for index, action in enumerate(context.proposal.actions):
            expected = expected_external_action(
                action,
                action_index=index,
                business_object_key=task.business_object_key,
            )
            expected["delivery_key"] = agent_message_delivery_key(
                business_object_key=task.business_object_key,
                action_identity=action.action_identity,
            )
            expected_effect_actions_list.append(expected)
        expected_effect_actions = tuple(expected_effect_actions_list)
        if expected_effect_actions:
            prompt += _external_action_identity_prompt(expected_effect_actions)

        process = AgentTurnProcess[AuditAgentResult](
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
            mcp_effect_registry=self.effects,
            refresh_runtime_capabilities=self.refresh_runtime_capabilities,
        )
        email_unsubscribe_tools = self._email_unsubscribe_tools(task, run)
        if email_unsubscribe_tools:
            prompt += (
                "\n\n### Task-bound Email Unsubscribe Capability\n"
                "Only this running Audit turn may invoke "
                "execute_audited_email_unsubscribe with these exact binding values:\n"
                f"task_id={task.id}\n"
                f"execution_generation={task.execution_generation}\n"
                f"audit_agent_run_id={run.id}\n"
                "Pass the accepted ProposedAction unchanged as accepted_action. "
                "Execute at most one new browser operation and never use reply, "
                "SMTP, mailto, or attachment content."
            )
        if frozen_delivery_retry:
            prompt += (
                "\n\nThis is a delivery retry. Re-evaluate the same typed proposal "
                "against the current task context and return one terminal result."
            )
        prompt += (
            "\n\n### Needs Human Display Contract\n"
            "Return needs_human only for a reusable policy gap: existing rules "
            "cannot determine how this class of cases should be handled. Describe "
            "the rule key, recurring pattern, and mutually exclusive policy choices. "
            "Set top-level risk and confidence for every result. needs_human is "
            "valid only for high risk with confidence strictly below 0.5. "
            "Do not turn a technical failure into needs_human."
        )
        if self.dry_run:
            prompt += (
                "\n\n### Dry Run Context\n"
                "Do not publish an external action in this simulation; return failed "
                "with error code dry_run_execution_suppressed when execution is suppressed."
            )
        return process.execute(
            run=run,
            prompt=prompt,
            session_id=run.codex_session_id or None,
            developer_instructions=audit_developer_instructions(rendered_rules),
            configure_command=lambda command: make_audit_agent_command(
                command,
                controlled_cli=ControlledCliConfig(
                    command=sys.executable,
                    args=("-m", "app.agent_cli"),
                    cwd=str(SERVICE_ROOT),
                ),
                additional_agent_cli_tools=email_unsubscribe_tools,
            ),
            parse_result=parse_audit_agent_wire_result,
            persist_conversation_session=False,
            expected_effect_actions=expected_effect_actions,
            image_paths=[Path(path) for path in context.task.image_paths],
            required_capabilities=self._required_capabilities(context),
        )

    def _email_unsubscribe_tools(
        self,
        task: ReplyTask,
        run: AgentRun,
    ) -> tuple[str, ...]:
        """Grant the write tool only to the current valid audited-v2 turn."""

        try:
            payload = json.loads(task.trigger_message_json)
        except (json.JSONDecodeError, TypeError, RecursionError):
            return ()
        if (
            not isinstance(payload, dict)
            or task.channel != "email"
            or payload.get("schema") != "email_agent_action.v1"
            or payload.get("action_type") != "unsubscribe"
            or payload.get("lifecycle_version") != "email_unsubscribe_audited_v2"
            or run.reply_task_id != task.id
            or run.execution_generation != task.execution_generation
            or run.role is not AgentRole.AUDIT
            or run.status != "running"
            or not run.operation_id.strip()
            or run.parent_agent_run_id is None
        ):
            return ()
        parent = self.store.get_agent_run(run.parent_agent_run_id)
        if (
            parent is None
            or parent.reply_task_id != task.id
            or parent.execution_generation != task.execution_generation
            or parent.role is not AgentRole.CONSUMER
            or parent.status != "completed"
            or parent.proposal_revision != run.proposal_revision
        ):
            return ()
        completed_consumers = [
            candidate
            for candidate in self.store.list_agent_runs_for_task_generation(
                task.id,
                task.execution_generation,
            )
            if candidate.role is AgentRole.CONSUMER
            and candidate.status == "completed"
            and candidate.proposal_revision == run.proposal_revision
        ]
        if (
            not completed_consumers
            or max(
                completed_consumers,
                key=lambda candidate: (candidate.turn_attempt, candidate.id),
            ).id
            != parent.id
        ):
            return ()
        return ("execute_audited_email_unsubscribe",)


def _audit_recovery_error_code(exc: Exception) -> str:
    """Map a process exception to the ordinary failed-result code."""
    code = getattr(exc, "code", "")
    return str(code) if code else "codex_process_failed"


def _external_action_identity_prompt(
    actions: tuple[dict[str, object], ...],
) -> str:
    identities = [
        {
            "action_index": entry["action_index"],
            "action_identity": entry["action_identity"],
            "external_action_key": entry["external_action_key"],
            "delivery_key": entry["delivery_key"],
        }
        for entry in actions
    ]
    return (
        "\n\n### External action identities\n"
        "These stable identities prevent duplicate provider actions across retries. "
        "They are not command authorizations and do not restrict the runtime tool. "
        "Reuse the matching external_action_key as the provider idempotency identity "
        "when the provider supports one.\n"
        + json.dumps(identities, ensure_ascii=False, separators=(",", ":"))
    )


def _recovery_prompt(
    run: AgentRun, context: AuditTurnContext, actions=(), registry=None
) -> str:
    del actions, registry
    return (
        "Run the normal typed Audit turn against the current task context and "
        "the applicable operation Skill.\n\n" + context.render()
    )
