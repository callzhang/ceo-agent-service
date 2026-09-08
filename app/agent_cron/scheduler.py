from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from threading import Event, RLock
from typing import Protocol

from app.agent_cron.models import ScheduledTask, ScheduledTaskRun
from app.agent_cron.schedule import CronSchedule
from app.store import (
    AutoReplyStore,
    ScheduledTaskRunCreateState,
)


PREVIOUS_EXECUTION_ACTIVE = "scheduled_task_previous_execution_active"
RUNTIME_UNAVAILABLE = "scheduled_task_runtime_unavailable"
MANAGED_SKILL_UNAVAILABLE = "scheduled_task_managed_skill_unavailable"
OPERATION_SKILL_UNAVAILABLE = "scheduled_task_operation_skill_unavailable"


class ScheduledTaskOptions(Protocol):
    def resolve_runtime_route(
        self,
        route_name: str,
        *,
        required_capabilities: frozenset[str] = frozenset(),
    ) -> object: ...

    def resolve_managed_skill_revision(
        self, *, skill_id: int, revision_id: int, skill_name: str
    ) -> object: ...

    def resolve_operation_skill(self, name: str) -> object: ...


class ExecutionTerminalResolver(Protocol):
    def __call__(self, execution_id: str) -> bool: ...


class ExecutionTerminalResolverRegistry:
    """Resolve terminal state in the execution's authoritative fact source."""

    def __init__(
        self,
        resolvers: Mapping[str, ExecutionTerminalResolver],
    ) -> None:
        self._resolvers = dict(resolvers)

    def is_terminal(self, execution_kind: str, execution_id: str) -> bool:
        resolver = self._resolvers.get(execution_kind)
        if resolver is None:
            return False
        return bool(resolver(execution_id))


@dataclass(frozen=True)
class _PlannedInstant:
    task_version: int
    scheduled_for: datetime


class AgentCronScheduler:
    """Create future trigger rows without replaying missed wall-clock instants."""

    def __init__(
        self,
        *,
        store: AutoReplyStore,
        option_service: ScheduledTaskOptions,
        terminal_resolver: ExecutionTerminalResolverRegistry,
        dispatcher_wake: Callable[[], None] | None = None,
    ) -> None:
        self._store = store
        self._option_service = option_service
        self._terminal_resolver = terminal_resolver
        self._dispatcher_wake = dispatcher_wake or (lambda: None)
        self._planned: dict[int, _PlannedInstant] = {}
        self._lock = RLock()

    def start(self, now: datetime) -> None:
        with self._lock:
            self._planned = {}
        self.reload(now)

    def reload(self, now: datetime) -> None:
        current = _utc(now)
        with self._lock:
            existing = dict(self._planned)
        replacement: dict[int, _PlannedInstant] = {}
        for task in self._store.list_scheduled_tasks():
            if not task.enabled:
                continue
            current_plan = existing.get(task.id)
            if (
                current_plan is not None
                and current_plan.task_version == task.version
            ):
                replacement[task.id] = current_plan
                continue
            schedule = CronSchedule.parse(task.cron_expression, task.timezone_name)
            replacement[task.id] = _PlannedInstant(
                task_version=task.version,
                scheduled_for=schedule.next_after(current),
            )
        with self._lock:
            self._planned = replacement

    def next_run_at(self, task_id: int) -> datetime | None:
        with self._lock:
            planned = self._planned.get(task_id)
        return None if planned is None else planned.scheduled_for

    def tick(self, now: datetime) -> int:
        current = _utc(now)
        with self._lock:
            due = tuple(
                sorted(
                    (
                        (task_id, planned)
                        for task_id, planned in self._planned.items()
                        if planned.scheduled_for <= current
                    ),
                    key=lambda item: (item[1].scheduled_for, item[0]),
                )
            )

        created_count = 0
        for task_id, planned in due:
            task = self._store.get_scheduled_task(task_id)
            if task is None or not task.enabled:
                with self._lock:
                    self._planned.pop(task_id, None)
                continue
            if task.version != planned.task_version:
                self._set_next(task, current)
                continue

            while True:
                reason, detail, overlap_run_id = self._skip_reason(task)
                result = self._store.create_scheduled_task_run_if_current(
                    task.id,
                    expected_version=planned.task_version,
                    expected_overlap_run_id=overlap_run_id,
                    scheduled_for=planned.scheduled_for,
                    now=current,
                    reason=reason or "",
                )
                if result.state is not ScheduledTaskRunCreateState.RETRY:
                    break
            if result.state is ScheduledTaskRunCreateState.STALE_TASK:
                self.reload(current)
                continue
            self._set_next(task, current)
            if result.state is ScheduledTaskRunCreateState.DUPLICATE:
                continue
            run = result.run
            assert run is not None
            created_count += 1
            if reason is not None:
                if reason != PREVIOUS_EXECUTION_ACTIVE:
                    self._store.record_error(
                        f"scheduled-task:{task.id}",
                        run.event_id,
                        reason,
                        self._attention_detail(run, detail),
                    )
                continue
            self._dispatcher_wake()
        return created_count

    def seconds_until_next(self, now: datetime, *, maximum: float = 60.0) -> float:
        current = _utc(now)
        with self._lock:
            instants = tuple(item.scheduled_for for item in self._planned.values())
        if not instants:
            return maximum
        return max(0.0, min(maximum, (min(instants) - current).total_seconds()))

    def run_forever(
        self,
        *,
        wake_event: Event,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        clock = now or (lambda: datetime.now(UTC))
        self.start(clock())
        while True:
            current = clock()
            self.tick(current)
            signaled = wake_event.wait(self.seconds_until_next(current))
            if signaled:
                wake_event.clear()
            # The API may live in another process, where it cannot set this
            # Event. The bounded wait therefore also refreshes persisted task
            # edits without treating any missed instant as catch-up work.
            self.reload(clock())

    def _set_next(self, task: ScheduledTask, now: datetime) -> None:
        schedule = CronSchedule.parse(task.cron_expression, task.timezone_name)
        planned = _PlannedInstant(
            task_version=task.version,
            scheduled_for=schedule.next_after(now),
        )
        with self._lock:
            self._planned[task.id] = planned

    def _skip_reason(
        self,
        task: ScheduledTask,
    ) -> tuple[str | None, str, int | None]:
        prior = self._store.latest_scheduled_task_overlap_candidate(task.id)
        if prior is not None and not self._run_is_terminal(prior):
            return (
                PREVIOUS_EXECUTION_ACTIVE,
                PREVIOUS_EXECUTION_ACTIVE,
                prior.id,
            )
        overlap_run_id = prior.id if prior is not None else None

        try:
            self._option_service.resolve_runtime_route(
                task.runtime_id,
                required_capabilities=frozenset(
                    task.required_runtime_capabilities
                ),
            )
        except ValueError as exc:
            return RUNTIME_UNAVAILABLE, str(exc), overlap_run_id

        for ref in task.skill_refs:
            try:
                if ref.skill_source == "managed":
                    assert ref.managed_skill_id is not None
                    assert ref.managed_revision_id is not None
                    self._option_service.resolve_managed_skill_revision(
                        skill_id=ref.managed_skill_id,
                        revision_id=ref.managed_revision_id,
                        skill_name=ref.skill_name,
                    )
                else:
                    self._option_service.resolve_operation_skill(ref.skill_name)
            except ValueError as exc:
                reason = (
                    MANAGED_SKILL_UNAVAILABLE
                    if ref.skill_source == "managed"
                    else OPERATION_SKILL_UNAVAILABLE
                )
                return reason, str(exc), overlap_run_id
        return None, "", overlap_run_id

    def _run_is_terminal(self, run: ScheduledTaskRun) -> bool:
        if run.dispatch_status in {"skipped", "failed"}:
            return True
        if run.dispatch_status == "pending":
            return False
        if not run.execution_kind or not run.execution_id:
            return False
        return self._terminal_resolver.is_terminal(
            run.execution_kind,
            run.execution_id,
        )

    @staticmethod
    def _attention_detail(run: ScheduledTaskRun, error: str) -> str:
        snapshot = run.snapshot
        return (
            f"Scheduled task {snapshot.task_id} ({snapshot.name}) skipped event "
            f"{run.event_id}: {error}"
        )


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("scheduler time must be timezone-aware")
    return value.astimezone(UTC).replace(microsecond=0)
