"""Consumer-direct decision boundary for automatic email unsubscribe."""

from __future__ import annotations

from collections.abc import Callable, Mapping
import json
import sys
from hashlib import sha256
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.agent_context import AgentTaskContext
from app.agent_effects import McpToolEffectRegistry
from app.agent_result import AgentError, ResultParseError, parse_typed_agent_result
from app.agent_runtime_config import AgentRuntimeConfig
from app.agent_runtime_router import AgentRuntimeRouter
from app.agent_turn_runner import AgentTurnProcess, AgentTurnRunResult, ProcessExecutor
from app.agent_contracts import ConsumerAgentResult, ConsumerOutcome
from app.claude_runtime_adapter import ClaudeRuntimeAdapter
from app.codex_runtime_adapter import CodexRuntimeAdapter
from app.friday_runtime_adapter import FridayRuntimeAdapter
from app.native_cli_metadata import NativeCliMetadataClassifier
from app.store import AgentRole, AutoReplyStore
from app.wechat.codex_safety import ControlledCliConfig, make_consumer_agent_command


SERVICE_ROOT = Path(__file__).resolve().parent.parent
_EXECUTED_MARKER = "email-unsubscribe-direct-executed:"


class EmailUnsubscribeConsumerResult(BaseModel):
    """Strict result exchanged by the unsubscribe Consumer and its runner."""

    model_config = ConfigDict(extra="forbid", strict=True)

    outcome: Literal["no_action", "execute", "executed", "failed"]
    summary: str = Field(min_length=1)
    receipt_id: str = ""
    error: AgentError


class _OperationResult(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    status: Literal["done", "failed"]
    receipt_id: str = ""
    summary: str = Field(min_length=1)
    error: AgentError = AgentError()


class EmailUnsubscribeConsumerRunner:
    """Run one typed Consumer decision and at most one task-bound operation."""

    def __init__(
        self,
        *,
        decide: Callable[[object, AgentTaskContext], EmailUnsubscribeConsumerResult],
        execute: Callable[..., Mapping[str, object]],
    ) -> None:
        self.decide = decide
        self.execute = execute

    def run(
        self,
        task: object,
        context: AgentTaskContext,
    ) -> EmailUnsubscribeConsumerResult:
        before = _identity_snapshot(task, context)
        if before is None:
            return _failed("unsubscribe_identity_invalid")
        try:
            decision = self.decide(task, context)
            if not isinstance(decision, EmailUnsubscribeConsumerResult):
                raise TypeError("Consumer result has an invalid type")
        except Exception as exc:  # noqa: BLE001 - strict terminal projection
            return _failed(f"unsubscribe_consumer_failed:{type(exc).__name__}")
        if _identity_snapshot(task, context) != before:
            return _failed("unsubscribe_identity_changed")
        if decision.outcome == "no_action":
            return decision
        if decision.outcome == "failed":
            return decision
        if decision.outcome != "execute":
            return _failed("unsubscribe_consumer_result_invalid")
        try:
            operation = _OperationResult.model_validate(
                self.execute(
                    task_id=before[0],
                    execution_generation=before[1],
                )
            )
        except Exception as exc:  # noqa: BLE001 - strict terminal projection
            return _failed(f"unsubscribe_operation_failed:{type(exc).__name__}")
        if operation.status == "failed":
            return EmailUnsubscribeConsumerResult(
                outcome="failed",
                summary=operation.summary,
                error=operation.error,
            )
        if not operation.receipt_id.strip():
            return _failed("unsubscribe_operation_receipt_missing")
        return EmailUnsubscribeConsumerResult(
            outcome="executed",
            summary=operation.summary,
            receipt_id=operation.receipt_id,
            error=AgentError(),
        )


class EmailUnsubscribeConsumerAgentRunner:
    """Run the real Consumer turn for a Consumer-direct unsubscribe task.

    This runner deliberately has a separate wire contract from the normal
    proposal Consumer. The only effect exposed to the turn is the task-bound
    ``execute_email_unsubscribe`` tool; the service verifies its durable
    receipt before projecting a successful result.
    """

    def __init__(
        self,
        *,
        store: AutoReplyStore,
        workspace: Path,
        receipt_loader: Callable[[str], Mapping[str, object] | None],
        codex_bin: str = "codex",
        runtime_config: AgentRuntimeConfig | None = None,
        runtime_router: AgentRuntimeRouter | None = None,
        codex_adapter: CodexRuntimeAdapter | None = None,
        claude_adapter: ClaudeRuntimeAdapter | None = None,
        friday_adapter: FridayRuntimeAdapter | None = None,
        executor: ProcessExecutor | None = None,
        owner: str | None = None,
        refresh_runtime_capabilities: Callable[[], object] | None = None,
        mcp_effect_registry: McpToolEffectRegistry | None = None,
        native_cli_classifier: NativeCliMetadataClassifier | None = None,
    ) -> None:
        self.store = store
        self.workspace = workspace
        self.receipt_loader = receipt_loader
        self.codex_bin = codex_bin
        self.runtime_config = runtime_config
        self.runtime_router = runtime_router
        self.codex_adapter = codex_adapter
        self.claude_adapter = claude_adapter
        self.friday_adapter = friday_adapter
        self.executor = executor
        self.owner = owner or "email-unsubscribe-consumer"
        self.refresh_runtime_capabilities = refresh_runtime_capabilities
        self.effects = mcp_effect_registry or McpToolEffectRegistry.default()
        self.native_cli_classifier = native_cli_classifier

    def run(
        self,
        task: object,
        context: AgentTaskContext,
    ) -> EmailUnsubscribeConsumerResult:
        before = _identity_snapshot(task, context)
        if before is None:
            return _failed("unsubscribe_identity_invalid")
        action_identity = str(before[3])
        lock_owner = f"email-unsubscribe-consumer:{task.id}:{task.execution_generation}"
        try:
            with self.store.codex_session_lock(task.conversation_id, lock_owner):
                result = self._run_locked(task, context, before=before)
        except RuntimeError as exc:
            if str(exc).startswith("codex session locked:"):
                return _failed("codex_session_locked", retryable=True)
            return _failed(f"unsubscribe_consumer_failed:{type(exc).__name__}")
        outcome = getattr(result.result.outcome, "value", result.result.outcome)
        summary = str(getattr(result.result, "summary", ""))
        executed = outcome == "executed" or summary.startswith(_EXECUTED_MARKER)
        if outcome == "no_action" and not executed:
            return EmailUnsubscribeConsumerResult(
                outcome="no_action",
                summary=summary,
                error=AgentError(),
            )
        if outcome == "failed":
            return (
                result.result
                if isinstance(result.result, EmailUnsubscribeConsumerResult)
                else EmailUnsubscribeConsumerResult(
                    outcome="failed",
                    summary=summary,
                    error=result.result.error,
                )
            )
        if not executed:
            return _failed("unsubscribe_operation_not_invoked")
        receipt = self.receipt_loader(action_identity)
        if not isinstance(receipt, Mapping):
            return _failed("unsubscribe_receipt_missing")
        receipt_id = str(receipt.get("receipt_id") or "")
        reported_receipt_id = str(getattr(result.result, "receipt_id", "") or "")
        if not reported_receipt_id:
            reported_receipt_id = _marked_receipt_id(summary)
        if not receipt_id or receipt_id != reported_receipt_id:
            return _failed("unsubscribe_receipt_mismatch")
        result_text = str(receipt.get("result_text") or "").strip()
        return EmailUnsubscribeConsumerResult(
            outcome="executed",
            summary=result_text or "Unsubscribe completed.",
            receipt_id=receipt_id,
            error=AgentError(),
        )

    def _run_locked(
        self,
        task: object,
        context: AgentTaskContext,
        *,
        before: tuple[object, ...],
    ) -> AgentTurnRunResult[EmailUnsubscribeConsumerResult]:
        claim = self.store.claim_agent_run(
            task.id,
            task.execution_generation,
            role=AgentRole.CONSUMER,
            proposal_revision=0,
            turn_attempt=self.store.next_agent_run_turn_attempt(
                task.id,
                task.execution_generation,
                role=AgentRole.CONSUMER,
                proposal_revision=0,
            ),
            parent_agent_run_id=None,
            operation_id="",
            owner=self.owner,
        )
        if not claim.claimed:
            raise RuntimeError("agent_run_unavailable")
        process = AgentTurnProcess[EmailUnsubscribeConsumerResult](
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
            native_cli_classifier=self.native_cli_classifier,
            refresh_runtime_capabilities=self.refresh_runtime_capabilities,
        )
        contract_hash = sha256(
            json.dumps(
                {
                    "schema": EmailUnsubscribeConsumerResult.model_json_schema(),
                    "prompt": _DIRECT_CONSUMER_PROMPT,
                },
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        command_config = ControlledCliConfig(
            command=sys.executable,
            args=("-m", "app.agent_cli"),
            cwd=str(SERVICE_ROOT),
            env=(("CEO_WORKER_DB", str(self.store.path)),),
        )
        process_result = process.execute(
            run=claim.run,
            prompt=(
                _DIRECT_CONSUMER_PROMPT
                + "\n\n## Complete Email Context\n"
                + context.render()
                + "\n\n## Direct Task Identity\n"
                + json.dumps(
                    {
                        "task_id": before[0],
                        "execution_generation": before[1],
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                )
            ),
            session_id=None,
            developer_instructions=_DIRECT_CONSUMER_INSTRUCTIONS,
            configure_command=lambda command: make_consumer_agent_command(
                command,
                controlled_cli=command_config,
                additional_agent_cli_tools=("execute_email_unsubscribe",),
            ),
            parse_result=parse_email_unsubscribe_agent_turn_result,
            persist_conversation_session=False,
            required_capabilities=frozenset({"task_context", "channel:email"}),
            conversation_contract_hash=contract_hash,
            force_new_session=True,
        )
        if _identity_snapshot(task, context) != before:
            return AgentTurnRunResult(
                run_id=process_result.run_id,
                result=_failed("unsubscribe_identity_changed"),
                transcript_start_line=process_result.transcript_start_line,
                transcript_end_line=process_result.transcript_end_line,
            )
        return process_result


_DIRECT_CONSUMER_PROMPT = """
You are the dedicated Consumer for one already-authorized email unsubscribe
task. Inspect the complete email text and related thread in the supplied
context. Attachment bodies are unavailable and must not be requested; use only
their metadata. Return `no_action` unless the message is clearly an unwanted
bulk subscription and not work, security, billing, order, account, or personal
mail. If it is appropriate to unsubscribe, call exactly one
`execute_email_unsubscribe` operation using the supplied task_id and
execution_generation, then return `executed` with the receipt_id returned by
that operation. Never invent a URL, call another write, or return `execute`
without invoking the operation. If the operation fails, return `failed` with
the structured error. Return only one JSON object matching the supplied
EmailUnsubscribeConsumerResult schema.
""".strip()

_DIRECT_CONSUMER_INSTRUCTIONS = """
## Role Boundary
This is an automatic email unsubscribe Consumer turn. The classifier and
ActionPlan are already fixed. You may decide only whether this exact task's
unsubscribe should run. You cannot add actions, change the account, change the
message, or use the normal audited external-write tool.

## Skill Boundary
Read the installed `ceo-mail-review` Skill before deciding. It defines the
complete mail-review semantics. Do not read attachment contents. The only
effectful capability available to you is the task-bound unsubscribe operation.

## Output Contract
Return exactly one JSON object with fields `outcome`, `summary`, `receipt_id`,
and `error`. `outcome` is one of `no_action`, `executed`, or `failed`.
""".strip()


def parse_email_unsubscribe_consumer_result(
    raw: str,
) -> EmailUnsubscribeConsumerResult:
    try:
        return parse_typed_agent_result(
            raw,
            EmailUnsubscribeConsumerResult,
        )
    except ResultParseError:
        raise
    except Exception as exc:  # noqa: BLE001 - normalize parser boundary
        raise ResultParseError(
            "email unsubscribe Consumer result does not match the strict schema"
        ) from exc


def parse_email_unsubscribe_agent_turn_result(raw: str) -> ConsumerAgentResult:
    """Adapt the dedicated wire result to the shared typed turn runtime.

    The shared runtime intentionally accepts only ``ConsumerAgentResult``.
    Successful direct execution is carried by a bounded internal summary
    marker, while the durable unsubscribe receipt remains authoritative.
    """

    direct = parse_email_unsubscribe_consumer_result(raw)
    if direct.outcome == "failed":
        outcome = ConsumerOutcome.FAILED
        summary = direct.summary
    elif direct.outcome in {"execute", "executed"}:
        outcome = ConsumerOutcome.NO_ACTION
        summary = _EXECUTED_MARKER + json.dumps(
            {
                "receipt_id": direct.receipt_id,
                "summary": direct.summary,
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )
    else:
        outcome = ConsumerOutcome.NO_ACTION
        summary = direct.summary
    return ConsumerAgentResult(
        outcome=outcome,
        summary=summary,
        proposal=None,
        error=direct.error,
    )


def _marked_receipt_id(summary: str) -> str:
    if not summary.startswith(_EXECUTED_MARKER):
        return ""
    try:
        marker = json.loads(summary[len(_EXECUTED_MARKER) :])
    except json.JSONDecodeError:
        return ""
    if not isinstance(marker, Mapping):
        return ""
    receipt_id = marker.get("receipt_id")
    return receipt_id.strip() if isinstance(receipt_id, str) else ""


def _identity_snapshot(
    task: object,
    context: AgentTaskContext,
) -> tuple[object, ...] | None:
    try:
        payload = json.loads(str(getattr(task, "trigger_message_json")))
    except (TypeError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    if (
        payload.get("schema") != "email_agent_action.v1"
        or payload.get("lifecycle_version")
        != "email_unsubscribe_consumer_direct_v1"
        or payload.get("action_type") != "unsubscribe"
    ):
        return None
    raw_context = getattr(context, "trigger_raw_payload", None)
    if not isinstance(raw_context, Mapping) or dict(raw_context) != payload:
        return None
    if any(
        (
            getattr(task, "channel", "") != "email",
            getattr(context, "channel", "") != "email",
            getattr(context, "task_id", None) != getattr(task, "id", None),
            getattr(context, "conversation_id", "")
            != getattr(task, "conversation_id", ""),
            getattr(context, "trigger_message_id", "")
            != getattr(task, "trigger_message_id", ""),
            payload.get("action_identity") != getattr(task, "trigger_message_id", ""),
        )
    ):
        return None
    return (
        getattr(task, "id", None),
        getattr(task, "execution_generation", ""),
        getattr(task, "trigger_message_id", ""),
        payload.get("action_identity"),
        payload.get("action_plan_id"),
        payload.get("action_plan_version"),
        payload.get("classification_id"),
        payload.get("account_id"),
        payload.get("stable_message_identity"),
        payload.get("thread_identity"),
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
    )


def _failed(
    code: str,
    *,
    retryable: bool = False,
) -> EmailUnsubscribeConsumerResult:
    return EmailUnsubscribeConsumerResult(
        outcome="failed",
        summary=code,
        error=AgentError(code=code, retryable=retryable),
    )
