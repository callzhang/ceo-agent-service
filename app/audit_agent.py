from __future__ import annotations

from collections.abc import Callable, Mapping
import json
from pathlib import Path
import sys
from typing import Protocol
from uuid import uuid4

from app.agent_context import AuditTurnContext
from app.agent_contracts import AuditAgentResult, AuditOutcome
from app.decision_rules import decision_violations
from app.agent_effect_claim import (
    EXTERNAL_CLAIM_WITHOUT_TOOLS_REQUIREMENT,
    claims_external_action_without_tools,
    generation_tool_events,
)
from app.agent_effect_claim import channel_is_judged_by_tool_events as _channel_judged
from app.outbound_text_authority import (
    UNPREPARED_SEND_REQUIREMENT,
    provider_send_texts,
    unprepared_send_texts,
)
from app.agent_result import ResultParseError
from app.agent_effects import LEASE_SECONDS
from app.agent_runtime_config import AgentRuntimeConfig
from app.agent_runtime_router import AgentRuntimeRouter
from app.agent_turn_runner import (
    AgentTurnProcess,
    AgentTurnRunResult,
    ProcessExecutor,
    result_correction_prompt,
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


class ExecutionEvidenceDriver(Protocol):
    def audit_run_has_execution_evidence(
        self, task: ReplyTask, *, audit_run_id: int
    ) -> bool: ...

    def execution_evidence_requirement(self) -> str:
        """The correction the model gets when its executed result has no receipt.

        Each domain names its own evidence, so the sentence travels with the
        driver rather than being fixed at the one place that raises it.
        """
        ...


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
        dry_run: bool = False,
        refresh_runtime_capabilities: Callable[[], object] | None = None,
        forced_runtime_route=None,
        reasoning_effort: str = "",
        skill_protocol_override: str = "",
        execution_environment: Mapping[str, str] | None = None,
        domain_continuation: ExecutionEvidenceDriver | None = None,
    ) -> None:
        self.store = store
        self.domain_continuation = domain_continuation
        self.workspace = workspace
        self.codex_bin = codex_bin
        self.runtime_config = runtime_config
        self.runtime_router = runtime_router
        self.codex_adapter = codex_adapter
        self.claude_adapter = claude_adapter
        self.friday_adapter = friday_adapter
        self.executor = executor
        self.owner = owner or f"audit-agent-{uuid4().hex}"
        self.dry_run = dry_run
        self.refresh_runtime_capabilities = refresh_runtime_capabilities
        self.forced_runtime_route = forced_runtime_route
        self.reasoning_effort = reasoning_effort
        self.skill_protocol_override = skill_protocol_override
        self.execution_environment = dict(execution_environment or {})

    @staticmethod
    def _required_capabilities(
        context: AuditTurnContext, *, recovery_phase: str = ""
    ) -> frozenset[str]:
        del recovery_phase
        required = set()
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
        )

    def _execute_claimed(
        self,
        task: ReplyTask,
        context: AuditTurnContext,
        *,
        run: AgentRun,
        rendered_rules: str,
    ) -> AgentTurnRunResult[AuditAgentResult]:
        prompt = context.render() + result_correction_prompt(
            self.store,
            task,
            role=AgentRole.AUDIT,
            proposal_revision=context.proposal_revision,
        )
        expected_actions_list: list[dict[str, object]] = []
        for index, action in enumerate(context.proposal.actions):
            expected = expected_external_action(
                action,
                action_index=index,
                business_object_key=task.business_object_key,
            )
            expected["delivery_key"] = agent_message_delivery_key(
                business_object_key=task.business_object_key,
                action_identity=action.action_identity,
                execution_generation=task.execution_generation,
                proposal_revision=context.proposal_revision,
            )
            expected_actions_list.append(expected)
        expected_actions = tuple(expected_actions_list)
        if expected_actions:
            prompt += _external_action_identity_prompt(expected_actions)

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
            refresh_runtime_capabilities=self.refresh_runtime_capabilities,
            forced_runtime_route=self.forced_runtime_route,
            reasoning_effort=self.reasoning_effort,
            execution_mode_environment=self.execution_environment,
        )
        email_unsubscribe_tools = self._email_unsubscribe_tools(task, run)
        if email_unsubscribe_tools:
            prompt += (
                "\n\n### Email Unsubscribe Capability\n"
                "Call unsubscribe_email once with:\n"
                f"task_id={task.id}\n"
                "One call does the whole unsubscribe: it opens the entry this "
                "task already authorized, operates what the page offers, and "
                "returns status, outcome, evidence and the redacted page text "
                "behind them. There is nothing to propose, accept or continue, "
                "so never retype an operation and never call it twice in one "
                "turn -- a second call after a receipt returns that same "
                "receipt. Return executed only after the tool returned a "
                "receipt; without one return failed, never executed. Report "
                "the returned outcome in the summary as it stands, including a "
                "terminal skip, and do not invent an error code for it: the "
                "service derives the task's terminal state from that receipt. "
                "Never use reply, SMTP, mailto, or attachment content to "
                "unsubscribe."
            )
        prompt += (
            "\n\n### Needs Human Display Contract\n"
            "Every result for every task type and outcome must include risk "
            "(low|medium|high), confidence (0..1), rule_coverage (0..1), and "
            "information_completeness (0..1). If information_completeness < 0.5, "
            "return a normal proposal with one ordinary question, do not return "
            "needs_human, and do not add a persistent outcome; use the existing "
            "proposal/Audit/send chain. Otherwise, needs_human applies only when "
            "(risk == high and confidence < 0.5) or rule_coverage < 0.5; provide "
            "2-4 mutually exclusive, executable rule/Skill options, with one-time "
            "feedback and Skill update selectable together. Otherwise follow the "
            "Skill autonomously. Technical/provider/read/route/schema/Audit/retry "
            "failure is always failed. authorization_required is not generic "
            "needs_human. Feedback reuses the same business object, attempt, and "
            "compatible session while creating a new revision, not a new session."
        )
        if self.dry_run:
            prompt += (
                "\n\n### Dry Run Context\n"
                "Do not publish an external action in this simulation; return failed "
                "with error code dry_run_execution_suppressed when execution is suppressed."
            )
        def parse_result(raw: str) -> AuditAgentResult:
            return _parse_audit_agent_result(
                raw,
                has_typed_actions=bool(expected_actions),
            )
        return process.execute(
            run=run,
            skill_names=context.task.skill_names,
            prompt=prompt,
            session_id=run.codex_session_id or None,
            developer_instructions="\n\n".join(
                part for part in (
                    audit_developer_instructions(rendered_rules),
                    self.skill_protocol_override,
                ) if part
            ),
            configure_command=lambda command: make_audit_agent_command(
                command,
                controlled_cli=ControlledCliConfig(
                    command=sys.executable,
                    args=("-m", "app.agent_cli"),
                    cwd=str(SERVICE_ROOT),
                ) if email_unsubscribe_tools else None,
            ),
            parse_result=self._parse_evidenced_result(
                task,
                run,
                parse_result=parse_result,
                delivery_keys=tuple(
                    str(expected.get("delivery_key") or "")
                    for expected in expected_actions
                ),
            ),
            persist_conversation_session=False,
            expected_actions=expected_actions,
            image_paths=[Path(path) for path in context.task.image_paths],
            required_capabilities=self._required_capabilities(context),
        )

    def _prepared_bodies(self, delivery_keys: tuple[str, ...]) -> list[str]:
        """The exact bodies the service prepared for this proposal's actions."""

        bodies: list[str] = []
        for key in delivery_keys:
            if not key:
                continue
            prepared = self.store.get_outbound_postfix("dingtalk", key)
            if prepared is not None:
                bodies.append(prepared.final_body)
        return bodies

    def _parse_evidenced_result(
        self,
        task: ReplyTask,
        run: AgentRun,
        *,
        parse_result: Callable[[str], AuditAgentResult] = parse_audit_agent_wire_result,
        delivery_keys: tuple[str, ...] = (),
    ) -> Callable[[str], AuditAgentResult]:
        """Accept `executed` only when the audited tool ran for this turn.

        An executed result is a claim about an external write; the durable
        receipt written by the tool is the evidence. Without it the result is
        invalid, which sends the model a correction on its next turn instead
        of ending the task on an unbacked success.
        """

        def parse(raw: str) -> AuditAgentResult:
            result = parse_result(raw)
            # A turn that called no tool cannot report a completed external
            # action, whatever its outcome says. The evidence gate below asks
            # a different question: whether a write backs an `executed`.
            refreshed = self.store.get_agent_run(run.id)
            if refreshed is not None:
                unprepared = unprepared_send_texts(
                    provider_send_texts(refreshed.tool_events),
                    self._prepared_bodies(delivery_keys),
                )
                if unprepared:
                    raise ResultParseError(UNPREPARED_SEND_REQUIREMENT)
            generation_events: list[object] = []
            if refreshed is not None:
                generation_events = list(
                    generation_tool_events(
                        self.store,
                        reply_task_id=refreshed.reply_task_id,
                        execution_generation=refreshed.execution_generation,
                    )
                )
            if (
                refreshed is not None
                and _channel_judged(task.channel)
                and claims_external_action_without_tools(
                    result=result.model_dump(mode="json"),
                    tool_events=generation_events,
                )
            ):
                raise ResultParseError(EXTERNAL_CLAIM_WITHOUT_TOOLS_REQUIREMENT)
            # A decision the rules do not allow is a correction, not a
            # result. The rules are domain-neutral and their registry says
            # which commands decide; this gate only asks them and passes their
            # wording back to the turn.
            decision_rule_violations = decision_violations(
                result=result.model_dump(mode="json"),
                tool_events=generation_events,
            )
            if decision_rule_violations:
                raise ResultParseError(
                    "\n\n".join(violation.detail for violation in decision_rule_violations)
                )
            if (
                result.outcome is not AuditOutcome.EXECUTED
                or self.domain_continuation is None
            ):
                return result
            if not self.domain_continuation.audit_run_has_execution_evidence(
                task, audit_run_id=run.id
            ):
                raise ResultParseError(
                    self.domain_continuation.execution_evidence_requirement()
                )
            if result.external_result is None:
                raise ResultParseError(
                    "external_result: executed requires external_result with "
                    "the tool's live_result_reference"
                )
            # The tool receipt bound to this run is the evidence; the opaque
            # operation id is service-owned, so bind it here instead of
            # failing the turn when the model retyped it.
            if result.external_result.operation_id != run.operation_id:
                result = result.model_copy(
                    update={
                        "external_result": result.external_result.model_copy(
                            update={"operation_id": run.operation_id}
                        )
                    }
                )
            return result

        return parse

    def _email_unsubscribe_tools(
        self,
        task: ReplyTask,
        run: AgentRun,
    ) -> tuple[str, ...]:
        """Grant the unsubscribe tool to the running Audit turn of this task.

        The tool takes one argument the caller cannot forge -- the task id --
        and reads everything else from durable state, so the grant no longer
        has to prove which Consumer proposal an operation came from. What the
        tool will do is already fixed by the ActionPlan.
        """

        try:
            payload = json.loads(task.trigger_message_json)
        except (json.JSONDecodeError, TypeError, RecursionError):
            return ()
        if (
            not isinstance(payload, dict)
            or task.channel != "email"
            or payload.get("schema") != "email_agent_action.v1"
            or payload.get("action_type") != "unsubscribe"
            or run.reply_task_id != task.id
            or run.execution_generation != task.execution_generation
            or run.role is not AgentRole.AUDIT
            or run.status != "running"
        ):
            return ()
        return ("unsubscribe_email",)


def _external_action_identity_prompt(
    actions: tuple[dict[str, object], ...],
) -> str:
    identities = [
        {
            "action_index": entry["action_index"],
            "action_identity": entry["action_identity"],
            "capability": entry["capability"],
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
        "Read the operation Skill named by an action's `capability` before "
        "executing that action, and use the operations it documents. The Skill "
        "is the only place the provider's real command shape is written down; a "
        "turn that skips it invents a command, finds it missing, and reports a "
        "provider failure for a provider that was available the whole time.\n"
        + json.dumps(identities, ensure_ascii=False, separators=(",", ":"))
    )


def _parse_audit_agent_result(
    raw: str,
    *,
    has_typed_actions: bool,
) -> AuditAgentResult:
    """Reject a provider execution flag misreported as a human decision."""
    result = parse_audit_agent_wire_result(raw)
    if (
        has_typed_actions
        and result.outcome is AuditOutcome.FAILED
        and result.error.code == "confirmation_required"
        and result.error.authorization_required
    ):
        raise ResultParseError(
            "error_code: confirmation_required is not a business decision for "
            "an already reviewed typed action. Audit approval is the execution "
            "confirmation; execute with the provider's non-interactive "
            "confirmation flag and verify the result."
        )
    return result
