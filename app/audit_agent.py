from __future__ import annotations

from collections.abc import Callable
import hashlib
import json
from uuid import NAMESPACE_URL, uuid5
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
from app.native_cli_metadata import (
    describe_native_command,
    dingtalk_message_text,
    native_command_argv,
)
from app.service_message_sender import agent_message_delivery_key
from app.business_identity import external_action_key
from app.store import (
    AgentRole,
    AgentRun,
    AutoReplyStore,
    ReplyTask,
)
from app.wechat.codex_safety import ControlledCliConfig, make_audit_agent_command

RECOVERY_WRITE_ALLOWLIST_ENV = "CEO_AGENT_RECOVERY_WRITE_ALLOWLIST"
EFFECT_INTENT_CONTEXT_ENV = "CEO_AGENT_EFFECT_INTENT_CONTEXT"
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
            expected = _bind_stable_external_action(
                _expected_effect_action(action, action_index=index),
                business_object_key=task.business_object_key,
            )
            expected["delivery_key"] = agent_message_delivery_key(
                business_object_key=task.business_object_key,
                action_identity=action.action_identity,
            )
            expected_effect_actions_list.append(expected)
        expected_effect_actions = tuple(expected_effect_actions_list)
        write_authorizations = (
            _initial_write_authorizations(
                run,
                expected_effect_actions,
                business_object_key=task.business_object_key,
            )
            if not self.dry_run
            else ()
        )
        if write_authorizations:
            self.store.prepare_agent_effect_intents(
                run.id, write_authorizations, owner=self.owner
            )
            prompt += _write_authorization_prompt(write_authorizations)

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
                    env=(
                        (
                            RECOVERY_WRITE_ALLOWLIST_ENV,
                            json.dumps(
                                write_authorizations,
                                sort_keys=True,
                                separators=(",", ":"),
                            ),
                        ),
                        (
                            EFFECT_INTENT_CONTEXT_ENV,
                            json.dumps(
                                {"db_path": str(self.store.path), "run_id": run.id},
                                sort_keys=True,
                                separators=(",", ":"),
                            ),
                        ),
                    )
                    if write_authorizations
                    else (),
                ),
                allow_write=not self.dry_run,
                additional_agent_cli_tools=email_unsubscribe_tools,
            ),
            parse_result=parse_audit_agent_wire_result,
            persist_conversation_session=False,
            expected_effect_actions=expected_effect_actions,
            allow_effectful_tools=not self.dry_run,
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
            or payload.get("lifecycle_version")
            != "email_unsubscribe_audited_v2"
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
        if not completed_consumers or max(
            completed_consumers,
            key=lambda candidate: (candidate.turn_attempt, candidate.id),
        ).id != parent.id:
            return ()
        return ("execute_audited_email_unsubscribe",)


def _audit_recovery_error_code(exc: Exception) -> str:
    """Map a process exception to the ordinary failed-result code."""
    code = getattr(exc, "code", "")
    return str(code) if code else "codex_process_failed"


def _json_digest(value: object) -> str:
    import hashlib
    import json
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _expected_effect_action(action, *, action_index: int = 0) -> dict[str, object]:
    """Bind supported typed chat and OA-comment proposals to exact commands."""
    target = getattr(action, "target", {})
    payload = getattr(action, "payload", {})
    capability = getattr(action, "capability", "")
    operation = getattr(action, "operation", "")
    legacy_argv = native_command_argv({"type": "command_execution", **payload})
    content = payload.get("content") or payload.get("text") or payload.get("reply_text")
    recipient = (
        target.get("open_dingtalk_id")
        or target.get("recipient_open_dingtalk_id")
        or target.get("sender_open_dingtalk_id")
        or target.get("verified_participant_open_dingtalk_id")
    )
    argv: list[str] | None = None
    if (
        capability == "agent_cli.dws"
        and legacy_argv is not None
        and dingtalk_message_text(legacy_argv).strip()
    ):
        argv = list(legacy_argv)
        descriptor = describe_native_command(
            {"type": "command_execution", "argv": argv}
        )
        if descriptor is None or descriptor.cli != "dws":
            argv = None
    elif capability == "dingtalk-chat" and isinstance(content, str) and content:
        conversation_id = str(target.get("conversation_id") or "").strip()
        message_id = str(target.get("message_id") or target.get("source_message_id") or "").strip()
        if operation in {"send_to_group", "messages-send-to-group"} and conversation_id:
            argv = ["dws", "chat", "+send-to-group", "--group", conversation_id,
                    "--content", content, "--yes", "--format", "json"]
        elif operation in {"messages-reply", "message.reply"} and conversation_id and message_id:
            argv = ["dws", "chat", "+messages-reply", "--conversation-id", conversation_id,
                    "--message-id", message_id, "--content", content, "--yes", "--format", "json"]
        elif isinstance(recipient, str) and recipient:
            argv = ["dws", "chat", "+messages-send", "--open-dingtalk-id", recipient,
                    "--text", content, "--yes", "--format", "json"]
    elif capability in {"dingtalk_oa", "dingtalk-oa"} and operation in {
        "oa-comments", "approval.comment"
    }:
        process_id = str(target.get("process_instance_id") or "").strip()
        comment = payload.get("comment_text") or payload.get("content")
        if process_id and isinstance(comment, str) and comment:
            argv = ["dws", "oa", "approval", "oa-comments", "--instance-id", process_id,
                    "--content", comment, "--format", "json", "--yes"]
    elif capability in {"dingtalk-misc", "dingtalk_oa", "dingtalk-oa"} and operation in {
        "dws oa approval approve", "oa approval approve", "approval.approve"
    }:
        process_id = str(target.get("process_instance_id") or "").strip()
        task_id = str(target.get("task_id") or "").strip()
        remark = payload.get("remark")
        if process_id and task_id and isinstance(remark, str):
            argv = [
                "dws", "oa", "approval", "approve",
                "--instance-id", process_id,
                "--task-id", task_id,
                "--remark", remark,
                "--format", "json", "--yes",
            ]
    if argv is None:
        return {"action_index": action_index}
    descriptor = describe_native_command(
        {"type": "command_execution", "argv": argv}
    )
    if descriptor is None:
        return {"action_index": action_index}
    return {
        "action_index": action_index,
        "action_identity": action.action_identity,
        "argv": argv,
        "capability": f"agent_cli.{descriptor.cli}",
        "operation": descriptor.command_path,
        "operation_digest": descriptor.command_digest,
        "arguments_digest": _json_digest({"argv": argv}),
        "target_identifiers": descriptor.target_identifiers,
        "reviewed_server": "agent_cli",
        "reviewed_tool": "execute_reviewed_write",
    }


def _initial_write_authorizations(
    run: AgentRun,
    actions: tuple[dict[str, object], ...],
    *,
    business_object_key: str,
) -> tuple[dict[str, object], ...]:
    entries: list[dict[str, object]] = []
    for original_action in actions:
        action = _bind_stable_external_action(
            original_action, business_object_key=business_object_key
        )
        required = (
            "action_index",
            "action_identity",
            "argv",
            "capability",
            "operation",
            "operation_digest",
            "arguments_digest",
            "target_identifiers",
            "reviewed_server",
            "reviewed_tool",
        )
        if any(key not in action for key in required):
            continue
        identity = {
            "run_id": run.id,
            "operation_id": run.operation_id,
            "proposal_revision": run.proposal_revision,
            "action": action,
        }
        authorization_id = hashlib.sha256(
            json.dumps(identity, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        receipt_operation_id = hashlib.sha256(
            f"{run.operation_id}:{action['action_index']}:{action['operation_digest']}".encode()
        ).hexdigest()
        entries.append(
            {
                "authorization_id": authorization_id,
                "receipt_operation_id": receipt_operation_id,
                "external_action_key": action["external_action_key"],
                "business_object_key": business_object_key,
                **action,
            }
        )
    return tuple(entries)


def _bind_stable_external_action(
    action: dict[str, object],
    *,
    business_object_key: str,
) -> dict[str, object]:
    operation = action.get("operation")
    action_identity = action.get("action_identity")
    target = action.get("target_identifiers")
    if (
        not isinstance(operation, str)
        or not isinstance(action_identity, str)
        or not isinstance(target, dict)
    ):
        return dict(action)
    identity_target = {
        str(key): value
        for key, value in target.items()
        if str(key).replace("_", "-").casefold()
        not in {"uuid", "idempotency-key"}
    }
    action_key = external_action_key(
        business_object_key=business_object_key,
        action_identity=action_identity,
        operation=operation,
        target_identifiers=identity_target,
    )
    bound = dict(action)
    argv = bound.get("argv")
    if (
        operation.startswith("chat ")
        and isinstance(argv, list)
        and "--uuid" not in argv
        and "--idempotency-key" not in argv
    ):
        stable_uuid = str(uuid5(NAMESPACE_URL, f"ceo-agent:{action_key}"))
        bound["argv"] = [*argv, "--uuid", stable_uuid]
        descriptor = describe_native_command(
            {"type": "command_execution", "argv": bound["argv"]}
        )
        if descriptor is None:
            raise ValueError("stable external action command is invalid")
        bound.update(
            {
                "operation": descriptor.command_path,
                "operation_digest": descriptor.command_digest,
                "arguments_digest": _json_digest({"argv": bound["argv"]}),
                "target_identifiers": descriptor.target_identifiers,
            }
        )
    bound["external_action_key"] = action_key
    return bound


def _recovery_authorizations(*args, **kwargs) -> tuple[dict[str, object], ...]:
    return ()


def _write_authorization_prompt(
    authorizations: tuple[dict[str, object], ...],
) -> str:
    allowed = [
        {
            "action_index": entry["action_index"],
            "authorization_id": entry["authorization_id"],
            "argv": entry["argv"],
        }
        for entry in authorizations
    ]
    return (
        "\n\n### Approved execution\n"
        "For each approved action, call agent_cli.execute_reviewed_write exactly "
        "once using the matching authorization_id and argv below.\n"
        + json.dumps(allowed, ensure_ascii=False, separators=(",", ":"))
    )


def _recovery_prompt(run: AgentRun, context: AuditTurnContext, actions=(), registry=None) -> str:
    del actions, registry
    return (
        "Run the normal typed Audit turn against the current task context and "
        "the applicable operation Skill.\n\n" + context.render()
    )
