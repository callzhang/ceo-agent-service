from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
import json
from typing import Protocol

from app.agent_cron.context import (
    ScheduledAgentContext,
    ScheduledAgentContextBuilder,
    validate_scheduled_execution_availability,
)
from app.agent_cron.scheduler import EXECUTION_UNAVAILABLE
from app.dispatcher.models import ClaimGuard, DispatchEnvelope
from app.store import AutoReplyStore, ReplyTask


class ScheduledOrchestrator(Protocol):
    def process(self, task: ReplyTask, context, *, refresh_context): ...


class ScheduledTaskTriggerConsumer:
    """Convert a Cron trigger into one execution source and finish the trigger."""

    def __init__(self, *, store: AutoReplyStore, option_service, now=None) -> None:
        self._store = store
        self._builder = ScheduledAgentContextBuilder(option_service)
        self._now = now or (lambda: datetime.now(UTC))

    def __call__(self, envelope: DispatchEnvelope, guard: ClaimGuard) -> None:
        now = self._now().astimezone(UTC)
        guard.assert_current(now)
        run = self._store.get_scheduled_task_run(int(envelope.source_id))
        if run is None:
            raise ValueError("scheduled task run does not exist")
        try:
            built = self._builder.build(run, reply_task_id=0)
        except ValueError as exc:
            reason = f"{EXECUTION_UNAVAILABLE}: {exc}"
            self._store.record_error(
                f"scheduled-task:{run.scheduled_task_id}", run.event_id,
                EXECUTION_UNAVAILABLE, reason,
            )
            guard.finish_source(now, status="skipped", reason=reason)
            return
        self._store.dispatch_scheduled_task_reply_execution(
            run.id, owner=guard.token.owner,
            claim_generation=guard.token.generation,
            execution_context_json=built.to_execution_json(), now=now,
        )
        guard.accept_atomic_source_completion()


class ScheduledAgentConsumer:
    """Run a claimed scheduled execution through the ordinary Audit lifecycle."""

    def __init__(
        self, *, store: AutoReplyStore, option_service,
        orchestrator_factory: Callable[[ScheduledAgentContext], ScheduledOrchestrator],
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self._store = store
        self._option_service = option_service
        self._orchestrator_factory = orchestrator_factory
        self._now = now or (lambda: datetime.now(UTC))

    def __call__(self, envelope: DispatchEnvelope, guard: ClaimGuard) -> None:
        now = self._now().astimezone(UTC)
        guard.assert_current(now)
        task = self._store.get_reply_task(int(envelope.source_id))
        if task is None or task.channel != "scheduled" or task.status != "processing":
            raise ValueError("scheduled execution source is not claimed")
        run = self._store.get_scheduled_task_run_for_reply_execution(task.id)
        if run is None or run.dispatch_status != "dispatched":
            raise ValueError("scheduled trigger dispatch fact is missing")
        built = ScheduledAgentContext.from_execution_json(
            task.trigger_message_json, reply_task_id=task.id
        )
        try:
            validate_scheduled_execution_availability(self._option_service, built)
        except ValueError as exc:
            reason = f"{EXECUTION_UNAVAILABLE}: {exc}"
            guard.assert_current(self._now().astimezone(UTC))
            self._store.skip_scheduled_reply_task(
                task.id, reason,
                expected_execution_generation=task.execution_generation,
                dispatcher_owner=guard.token.owner,
                dispatcher_generation=guard.token.generation,
                now=self._now().astimezone(UTC),
            )
            self._store.record_error(
                f"scheduled-task:{run.scheduled_task_id}", run.event_id,
                EXECUTION_UNAVAILABLE, reason,
            )
            guard.accept_atomic_source_completion()
            return
        result = self._orchestrator_factory(built).process(
            task, built.context, refresh_context=lambda: built.context,
        )
        guard.assert_current(self._now().astimezone(UTC))
        if result.status == "failed_retryable":
            self._store.defer_reply_task(
                task.id, result.error.code or "agent_failed",
                expected_execution_generation=task.execution_generation,
            )
            return
        mapping = {
            "executed": ("done", "completed", ""),
            "no_action": ("done", "skipped", ""),
            "needs_human": ("done", "needs_human", result.error.code or "needs_human"),
            "dry_run": ("done", "dry_run", result.error.code),
            "failed_terminal": ("failed", "failed", result.error.code or "agent_failed"),
        }
        if result.status not in mapping:
            raise ValueError("invalid scheduled orchestration status")
        task_status, send_status, error = mapping[result.status]
        final_run = self._store.get_agent_run(result.final_run_id)
        if final_run is None:
            raise RuntimeError("scheduled orchestration final run was not persisted")
        decision_options = result.audit_result.decision_options if result.audit_result else ()
        self._store.finalize_orchestrated_reply_task(
            task_id=task.id, expected_execution_generation=task.execution_generation,
            run_id=final_run.id, task_status=task_status, task_error=error,
            available_at="", conversation_id=task.conversation_id,
            conversation_title=task.conversation_title,
            trigger_message_id=task.trigger_message_id,
            trigger_sender=task.trigger_sender, trigger_text=task.trigger_text,
            codex_reason=result.summary, codex_session_id=final_run.codex_session_id,
            codex_transcript_start_line=final_run.transcript_start_line,
            codex_transcript_end_line=final_run.transcript_end_line,
            audit_tool_events_json=json.dumps(final_run.tool_events),
            audit_summary=result.summary,
            human_decision_options_json=json.dumps(
                [item.model_dump(mode="json") for item in decision_options], ensure_ascii=False,
            ),
            send_status=send_status, send_error=error, channel="scheduled",
        )


def build_scheduled_orchestrator(
    *, store: AutoReplyStore, built: ScheduledAgentContext, runtime_config,
    codex_bin: str = "codex", dry_run: bool = False,
    refresh_runtime_capabilities=None,
):
    """Build both roles in the saved workdir on the one saved route."""
    from app.agent_orchestrator import AgentOrchestrator
    from app.audit_agent import AuditAgentRunner
    from app.consumer_agent import ConsumerAgentRunner

    scoped_config = runtime_config.model_copy(update={"routes": (built.route,)})
    execution_environment = scheduled_execution_environment(dry_run)
    common = {
        "store": store, "workspace": built.workspace, "codex_bin": codex_bin,
        "runtime_config": scoped_config, "forced_runtime_route": built.route,
        "reasoning_effort": built.reasoning_effort,
        "skill_protocol_override": built.skill_protocol,
        "refresh_runtime_capabilities": refresh_runtime_capabilities,
        "execution_environment": execution_environment,
    }
    return AgentOrchestrator(
        store=store, consumer=ConsumerAgentRunner(**common),
        audit=AuditAgentRunner(**common, dry_run=dry_run),
    )


def scheduled_execution_environment(dry_run: bool) -> dict[str, str]:
    """Pin the service execution mode for every local scheduled Agent child."""
    value = "1" if dry_run else "0"
    return {
        "CEO_DRY_RUN": value,
        "CEO_NOT_SEND_MESSAGE": value,
    }
