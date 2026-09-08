from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime, timedelta
import json
from typing import Protocol

from app.agent_cron.context import ScheduledAgentContext, ScheduledAgentContextBuilder
from app.dispatcher.models import ClaimGuard, DispatchEnvelope
from app.store import AutoReplyStore, ReplyTask


class ScheduledOrchestrator(Protocol):
    def process(self, task: ReplyTask, context, *, refresh_context): ...


class ScheduledAgentConsumer:
    """Run one scheduled trigger through the ordinary Consumer/Audit ledger."""

    def __init__(
        self,
        *,
        store: AutoReplyStore,
        option_service,
        orchestrator_factory: Callable[[ScheduledAgentContext], ScheduledOrchestrator],
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self._store = store
        self._builder = ScheduledAgentContextBuilder(option_service)
        self._orchestrator_factory = orchestrator_factory
        self._now = now or (lambda: datetime.now(UTC))

    def __call__(self, envelope: DispatchEnvelope, guard: ClaimGuard) -> None:
        now = self._now().astimezone(UTC)
        guard.assert_current(now)
        run = self._store.get_scheduled_task_run(int(envelope.source_id))
        if run is None:
            raise ValueError("scheduled task run does not exist")
        if run.execution_kind and run.execution_kind != "reply_task":
            raise ValueError("scheduled task run execution kind is unsupported")

        built: ScheduledAgentContext | None = None
        if run.execution_id:
            task = self._store.get_reply_task(int(run.execution_id))
            if task is None:
                raise ValueError("scheduled task execution source is missing")
        else:
            try:
                # Validate mutable availability immediately before creating a
                # business execution fact. The real context is rebuilt below
                # with the persisted source id.
                built = self._builder.build(run, reply_task_id=1)
            except ValueError as exc:
                reason = f"scheduled_task_execution_unavailable: {exc}"
                self._store.record_error(
                    f"scheduled-task:{run.scheduled_task_id}",
                    run.event_id,
                    "scheduled_task_execution_unavailable",
                    reason,
                )
                guard.finish_source(now, status="skipped", reason=reason)
                return
            run, task = self._store.ensure_scheduled_task_reply_execution(
                run.id,
                owner=guard.token.owner,
                claim_generation=guard.token.generation,
                now=self._now().astimezone(UTC),
            )

        if task.status in {"done", "failed"}:
            guard.finish_source(
                self._now().astimezone(UTC),
                status="dispatched" if task.status == "done" else "failed",
                reason="" if task.status == "done" else task.error or "agent_failed",
            )
            return
        if task.status == "pending":
            claimed = self._store.claim_reply_task(
                task.id, now=self._now().astimezone(UTC).strftime("%Y-%m-%d %H:%M:%S")
            )
            if claimed is None:
                guard.release(self._now().astimezone(UTC))
                return
            task = claimed
        if task.status != "processing":
            raise ValueError("scheduled execution source is not runnable")

        try:
            built = (
                replace(built, context=replace(built.context, task_id=task.id))
                if built is not None
                else self._builder.build(run, reply_task_id=task.id)
            )
        except ValueError as exc:
            reason = f"scheduled_task_execution_unavailable: {exc}"
            if task.status == "pending":
                task = self._store.claim_reply_task(
                    task.id,
                    now=self._now().astimezone(UTC).strftime("%Y-%m-%d %H:%M:%S"),
                ) or task
            if task.status == "processing":
                self._store.fail_reply_task(
                    task.id,
                    reason,
                    expected_execution_generation=task.execution_generation,
                )
            self._store.record_error(
                f"scheduled-task:{run.scheduled_task_id}",
                run.event_id,
                "scheduled_task_execution_unavailable",
                reason,
            )
            guard.finish_source(
                self._now().astimezone(UTC), status="skipped", reason=reason
            )
            return
        orchestrator = self._orchestrator_factory(built)
        result = orchestrator.process(
            task,
            built.context,
            refresh_context=lambda: built.context,
        )
        if result.status == "failed_retryable":
            self._store.defer_reply_task(
                task.id,
                result.error.code or "agent_failed",
                expected_execution_generation=task.execution_generation,
                available_at=(
                    self._now().astimezone(UTC) + timedelta(seconds=5)
                ).strftime("%Y-%m-%d %H:%M:%S"),
            )
            guard.release(self._now().astimezone(UTC))
            return

        mapping = {
            "executed": ("done", "completed", ""),
            "no_action": ("done", "skipped", ""),
            "needs_human": ("done", "needs_human", result.error.code or "needs_human"),
            "dry_run": ("done", "dry_run", result.error.code),
            "failed_terminal": (
                "failed",
                "failed",
                result.error.code or "agent_failed",
            ),
        }
        if result.status not in mapping:
            raise ValueError("invalid scheduled orchestration status")
        task_status, send_status, error = mapping[result.status]
        final_run = self._store.get_agent_run(result.final_run_id)
        if final_run is None:
            raise RuntimeError("scheduled orchestration final run was not persisted")
        decision_options = ()
        if result.audit_result is not None:
            decision_options = result.audit_result.decision_options
        self._store.finalize_orchestrated_reply_task(
            task_id=task.id,
            expected_execution_generation=task.execution_generation,
            run_id=final_run.id,
            task_status=task_status,
            task_error=error,
            available_at="",
            conversation_id=task.conversation_id,
            conversation_title=task.conversation_title,
            trigger_message_id=task.trigger_message_id,
            trigger_sender=task.trigger_sender,
            trigger_text=task.trigger_text,
            codex_reason=result.summary,
            codex_session_id=final_run.codex_session_id,
            codex_transcript_start_line=final_run.transcript_start_line,
            codex_transcript_end_line=final_run.transcript_end_line,
            audit_tool_events_json=json.dumps(final_run.tool_events),
            audit_summary=result.summary,
            human_decision_options_json=json.dumps(
                [item.model_dump(mode="json") for item in decision_options],
                ensure_ascii=False,
            ),
            send_status=send_status,
            send_error=error,
            channel="scheduled",
        )
        guard.finish_source(
            self._now().astimezone(UTC),
            status="failed" if task_status == "failed" else "dispatched",
            reason=error if task_status == "failed" else "",
        )


def build_scheduled_orchestrator(
    *,
    store: AutoReplyStore,
    built: ScheduledAgentContext,
    runtime_config,
    codex_bin: str = "codex",
    dry_run: bool = False,
    refresh_runtime_capabilities=None,
):
    """Build both roles in the saved workdir on the one saved route."""
    from app.agent_orchestrator import AgentOrchestrator
    from app.audit_agent import AuditAgentRunner
    from app.consumer_agent import ConsumerAgentRunner

    scoped_config = runtime_config.model_copy(update={"routes": (built.route,)})
    common = {
        "store": store,
        "workspace": built.workspace,
        "codex_bin": codex_bin,
        "runtime_config": scoped_config,
        "forced_runtime_route": built.route,
        "reasoning_effort": built.reasoning_effort,
        "skill_protocol_override": built.skill_protocol,
        "refresh_runtime_capabilities": refresh_runtime_capabilities,
    }
    return AgentOrchestrator(
        store=store,
        consumer=ConsumerAgentRunner(**common),
        audit=AuditAgentRunner(**common, dry_run=dry_run),
    )
