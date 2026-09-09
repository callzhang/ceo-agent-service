from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
import threading

import pytest

from app.agent_cron.commands import SERVICE_COMMAND_EXECUTION_KIND
from app.agent_cron.models import ScheduledTaskSkillRef
from app.agent_cron.scheduler import (
    AgentCronScheduler,
    ExecutionTerminalResolverRegistry,
    MANAGED_SKILL_UNAVAILABLE,
    OPERATION_SKILL_UNAVAILABLE,
    PREVIOUS_EXECUTION_ACTIVE,
    RUNTIME_UNAVAILABLE,
    SERVICE_COMMAND_UNAVAILABLE,
)
from app.store import AutoReplyStore


NOW = datetime(2026, 9, 8, 12, 0, 0, tzinfo=UTC)


class AvailableOptions:
    def resolve_runtime_route(self, route_name: str, **_kwargs: object) -> object:
        assert route_name == "codex_oauth"
        return object()

    def resolve_managed_skill_revision(self, **_kwargs: object) -> object:
        return object()

    def resolve_operation_skill(self, name: str) -> object:
        assert name == "dingtalk-chat"
        return object()

    def resolve_service_command(self, name: str) -> object:
        assert name == "produce-once"
        return object()


class MissingServiceCommand(AvailableOptions):
    def resolve_service_command(self, name: str) -> object:
        raise ValueError(f"service command {name}: service_command_not_registered")


class MissingRuntime(AvailableOptions):
    def resolve_runtime_route(self, route_name: str, **_kwargs: object) -> object:
        raise ValueError(f"{route_name} is not healthy")


class MissingOperationSkill(AvailableOptions):
    def resolve_operation_skill(self, name: str) -> object:
        raise ValueError(f"{name} is missing")


class MissingManagedSkill(AvailableOptions):
    def resolve_managed_skill_revision(self, **_kwargs: object) -> object:
        raise ValueError("exact revision is not loaded")


class FirstResolutionBarrier(AvailableOptions):
    def __init__(self, parties: int) -> None:
        self._barrier = threading.Barrier(parties)
        self._local = threading.local()

    def resolve_runtime_route(self, route_name: str, **kwargs: object) -> object:
        if not getattr(self._local, "resolved", False):
            self._local.resolved = True
            self._barrier.wait(timeout=5)
        return super().resolve_runtime_route(route_name, **kwargs)


class PausedResolution(AvailableOptions):
    def __init__(self) -> None:
        self.reached = threading.Event()
        self.release = threading.Event()

    def resolve_runtime_route(self, route_name: str, **kwargs: object) -> object:
        self.reached.set()
        assert self.release.wait(timeout=5)
        return super().resolve_runtime_route(route_name, **kwargs)


def _task(
    store: AutoReplyStore,
    *,
    expression: str = "0 * * * * *",
    skill_refs: tuple[ScheduledTaskSkillRef, ...] = (),
):
    return store.create_scheduled_task(
        name="Scheduled check",
        prompt="Check the configured source.",
        cron_expression=expression,
        timezone_name="UTC",
        runtime_id="codex_oauth",
        runtime_options={},
        working_directory="/tmp",
        enabled=True,
        skill_refs=skill_refs,
        now=NOW,
    )


def _command_task(store: AutoReplyStore, *, expression: str = "0 * * * * *"):
    return store.create_scheduled_task(
        name="Producer",
        command="produce-once",
        cron_expression=expression,
        timezone_name="UTC",
        enabled=True,
        now=NOW,
    )


def _scheduler(
    store: AutoReplyStore,
    *,
    options: object | None = None,
    resolver: ExecutionTerminalResolverRegistry | None = None,
    wakes: list[str] | None = None,
    ticks: list[datetime] | None = None,
) -> AgentCronScheduler:
    return AgentCronScheduler(
        store=store,
        option_service=options or AvailableOptions(),
        terminal_resolver=resolver or ExecutionTerminalResolverRegistry({}),
        dispatcher_wake=(lambda: wakes.append("wake")) if wakes is not None else None,
        tick_observer=(lambda now: ticks.append(now)) if ticks is not None else None,
    )


def test_scheduler_reports_successful_start_and_each_scan_without_creating_runs(
    tmp_path: Path,
) -> None:
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    task = _task(store)
    ticks: list[datetime] = []
    scheduler = _scheduler(store, ticks=ticks)

    scheduler.start(NOW)
    scheduler.tick(NOW + timedelta(seconds=30))

    assert ticks == [NOW, NOW + timedelta(seconds=30)]
    assert store.list_scheduled_task_runs(task.id) == ()


def test_tick_observer_failure_never_stops_scheduler_work(tmp_path: Path, caplog) -> None:
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    task = _task(store, expression="0 * * * * *")
    wakes: list[str] = []
    observed: list[datetime] = []
    failures = 0

    def observe(now: datetime) -> None:
        nonlocal failures
        if failures < 2:
            failures += 1
            raise OSError("health database unavailable")
        observed.append(now)
        store.set_service_health_component(
            "agent-cron-scheduler",
            state="healthy",
            status="running",
            latest_tick_at=now.isoformat(),
        )

    scheduler = AgentCronScheduler(
        store=store,
        option_service=AvailableOptions(),
        terminal_resolver=ExecutionTerminalResolverRegistry({}),
        dispatcher_wake=lambda: wakes.append("wake"),
        tick_observer=observe,
    )

    scheduler.start(NOW)
    assert scheduler.next_run_at(task.id) == NOW + timedelta(minutes=1)
    assert scheduler.tick(NOW + timedelta(minutes=1)) == 1
    assert wakes == ["wake"]
    assert len(store.list_scheduled_task_runs(task.id)) == 1

    scheduler.tick(NOW + timedelta(minutes=1, seconds=30))
    assert observed == [NOW + timedelta(minutes=1, seconds=30)]
    [health] = store.list_service_health_components()
    assert health["latest_tick_at"] == observed[0].isoformat()
    assert [record.message for record in caplog.records].count(
        "agent_cron_scheduler_tick_observer_failed"
    ) == 2
    assert all(
        hasattr(record, "scheduler_tick_at")
        for record in caplog.records
        if record.message == "agent_cron_scheduler_tick_observer_failed"
    )


def test_start_tracks_only_the_first_future_instant_and_never_backfills(
    tmp_path: Path,
) -> None:
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    task = _task(store)
    scheduler = _scheduler(store)

    scheduler.start(NOW + timedelta(seconds=30))

    assert scheduler.next_run_at(task.id) == NOW + timedelta(minutes=1)
    assert store.list_scheduled_task_runs(task.id) == ()

    scheduler.tick(NOW + timedelta(minutes=5, seconds=30))

    runs = store.list_scheduled_task_runs(task.id)
    assert tuple(run.scheduled_for for run in runs) == (NOW + timedelta(minutes=1),)
    assert scheduler.next_run_at(task.id) == NOW + timedelta(minutes=6)


def test_service_restart_after_missed_instants_does_not_create_catch_up_runs(
    tmp_path: Path,
) -> None:
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    task = _task(store)
    first_process = _scheduler(store)
    first_process.start(NOW)

    restarted = _scheduler(store)
    restarted.start(NOW + timedelta(hours=3, seconds=10))

    assert store.list_scheduled_task_runs(task.id) == ()
    assert restarted.next_run_at(task.id) == NOW + timedelta(hours=3, minutes=1)


def test_two_schedulers_atomically_deduplicate_the_same_planned_instant(
    tmp_path: Path,
) -> None:
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    task = _task(store)
    wakes: list[str] = []
    schedulers = [
        _scheduler(store, wakes=wakes),
        _scheduler(store, wakes=wakes),
    ]
    for scheduler in schedulers:
        scheduler.start(NOW)

    threads = [
        threading.Thread(target=scheduler.tick, args=(NOW + timedelta(minutes=1),))
        for scheduler in schedulers
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    runs = store.list_scheduled_task_runs(task.id)
    assert len(runs) == 1
    assert runs[0].dispatch_status == "pending"
    assert wakes == ["wake"]


def test_different_due_instants_atomically_serialize_overlap_decisions(
    tmp_path: Path,
) -> None:
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    task = _task(store)
    options = FirstResolutionBarrier(2)
    wakes: list[str] = []
    first = _scheduler(store, options=options, wakes=wakes)
    second = _scheduler(store, options=options, wakes=wakes)
    first.start(NOW)
    second.start(NOW + timedelta(minutes=1))

    threads = [
        threading.Thread(target=first.tick, args=(NOW + timedelta(minutes=2),)),
        threading.Thread(target=second.tick, args=(NOW + timedelta(minutes=2),)),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    runs = store.list_scheduled_task_runs(task.id)
    assert len(runs) == 2
    assert sum(run.dispatch_status == "pending" for run in runs) == 1
    skipped = next(run for run in runs if run.dispatch_status == "skipped")
    assert skipped.skip_or_error_reason == PREVIOUS_EXECUTION_ACTIVE
    assert wakes == ["wake"]


@pytest.mark.parametrize("mutation", ["update", "disable", "delete"])
def test_task_mutation_between_read_and_insert_is_a_stale_noop(
    tmp_path: Path,
    mutation: str,
) -> None:
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    task = _task(store)
    options = PausedResolution()
    scheduler = _scheduler(store, options=options)
    scheduler.start(NOW)
    errors: list[BaseException] = []

    def tick() -> None:
        try:
            scheduler.tick(NOW + timedelta(minutes=1))
        except BaseException as exc:  # test captures thread failures explicitly
            errors.append(exc)

    thread = threading.Thread(target=tick)
    thread.start()
    assert options.reached.wait(timeout=5)
    if mutation == "update":
        store.update_scheduled_task(
            task.id,
            expected_version=task.version,
            cron_expression="0 0 * * * *",
            now=NOW + timedelta(seconds=30),
        )
    elif mutation == "disable":
        store.set_scheduled_task_enabled(
            task.id,
            enabled=False,
            expected_version=task.version,
            now=NOW + timedelta(seconds=30),
        )
    else:
        store.delete_scheduled_task(
            task.id,
            expected_version=task.version,
            now=NOW + timedelta(seconds=30),
        )
    options.release.set()
    thread.join(timeout=5)

    assert not thread.is_alive()
    assert errors == []
    assert store.list_scheduled_task_runs(task.id) == ()
    if mutation == "update":
        assert scheduler.next_run_at(task.id) == NOW + timedelta(hours=1)
    else:
        assert scheduler.next_run_at(task.id) is None


def test_active_previous_execution_skips_without_creating_business_execution(
    tmp_path: Path,
) -> None:
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    task = _task(store)
    prior = store.create_scheduled_task_run(
        task.id,
        trigger_kind="manual",
        scheduled_for=NOW,
        now=NOW,
    )
    claimed = store.claim_scheduled_task_run(
        prior.id,
        owner="dispatcher",
        lease_seconds=60,
        now=NOW,
    )
    assert claimed is not None
    store.link_scheduled_task_run_execution(
        prior.id,
        owner="dispatcher",
        execution_kind="scheduled_agent",
        execution_id="execution-1",
        now=NOW,
    )
    store.finish_scheduled_task_dispatch(
        prior.id,
        owner="dispatcher",
        status="dispatched",
        now=NOW,
    )
    resolver = ExecutionTerminalResolverRegistry(
        {"scheduled_agent": lambda execution_id: execution_id != "execution-1"}
    )
    scheduler = _scheduler(store, resolver=resolver)
    scheduler.start(NOW)

    scheduler.tick(NOW + timedelta(minutes=1))

    run = store.list_scheduled_task_runs(task.id)[-1]
    assert run.dispatch_status == "skipped"
    assert run.skip_or_error_reason == PREVIOUS_EXECUTION_ACTIVE
    assert run.execution_kind == ""
    assert run.execution_id == ""

    scheduler.tick(NOW + timedelta(minutes=2))

    later = store.list_scheduled_task_runs(task.id)[-1]
    assert later.scheduled_for == NOW + timedelta(minutes=2)
    assert later.dispatch_status == "skipped"
    assert later.skip_or_error_reason == PREVIOUS_EXECUTION_ACTIVE


def test_manual_run_does_not_move_the_schedulers_future_instant(tmp_path: Path) -> None:
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    task = _task(store)
    scheduler = _scheduler(store)
    scheduler.start(NOW)
    planned = scheduler.next_run_at(task.id)

    store.create_scheduled_task_run(
        task.id,
        trigger_kind="manual",
        scheduled_for=NOW + timedelta(seconds=10),
        now=NOW + timedelta(seconds=10),
    )

    assert scheduler.next_run_at(task.id) == planned


def test_unavailable_runtime_is_skipped_and_recorded_in_existing_attention(
    tmp_path: Path,
) -> None:
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    task = _task(store)
    scheduler = _scheduler(store, options=MissingRuntime())
    scheduler.start(NOW)

    scheduler.tick(NOW + timedelta(minutes=1))

    run = store.list_scheduled_task_runs(task.id)[0]
    assert run.dispatch_status == "skipped"
    assert run.skip_or_error_reason == RUNTIME_UNAVAILABLE
    error = store.list_errors()[0]
    assert error.kind == RUNTIME_UNAVAILABLE
    assert error.conversation_id == f"scheduled-task:{task.id}"
    assert error.message_id == run.event_id


def test_unavailable_exact_skill_is_skipped_without_runtime_fallback(
    tmp_path: Path,
) -> None:
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    task = _task(
        store,
        skill_refs=(
            ScheduledTaskSkillRef(
                skill_source="operation",
                skill_name="dingtalk-chat",
                position=0,
            ),
        ),
    )
    scheduler = _scheduler(store, options=MissingOperationSkill())
    scheduler.start(NOW)

    scheduler.tick(NOW + timedelta(minutes=1))

    run = store.list_scheduled_task_runs(task.id)[0]
    assert run.dispatch_status == "skipped"
    assert run.skip_or_error_reason == OPERATION_SKILL_UNAVAILABLE
    assert store.list_errors()[0].kind == OPERATION_SKILL_UNAVAILABLE


def test_unavailable_exact_managed_revision_is_skipped_and_enters_attention(
    tmp_path: Path,
) -> None:
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    skill = store.create_managed_skill("ceo-test", "CEO test")
    revision = store.create_managed_skill_revision(
        skill.id,
        """---
name: ceo-test
description: Test exact managed revision
metadata:
  managed_by: ceo-agent-service
---

# Test
""",
        source="settings",
    )
    task = _task(
        store,
        skill_refs=(
            ScheduledTaskSkillRef(
                skill_source="managed",
                skill_name=skill.name,
                managed_skill_id=skill.id,
                managed_revision_id=revision.id,
                position=0,
            ),
        ),
    )
    scheduler = _scheduler(store, options=MissingManagedSkill())
    scheduler.start(NOW)

    scheduler.tick(NOW + timedelta(minutes=1))

    run = store.list_scheduled_task_runs(task.id)[0]
    assert run.dispatch_status == "skipped"
    assert run.skip_or_error_reason == MANAGED_SKILL_UNAVAILABLE
    assert store.list_errors()[0].kind == MANAGED_SKILL_UNAVAILABLE


def test_wake_reload_replaces_stale_schedule_after_task_update(tmp_path: Path) -> None:
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    task = _task(store)
    scheduler = _scheduler(store)
    scheduler.start(NOW)
    assert scheduler.next_run_at(task.id) == NOW + timedelta(minutes=1)

    store.update_scheduled_task(
        task.id,
        expected_version=task.version,
        cron_expression="0 0 * * * *",
        now=NOW + timedelta(seconds=10),
    )
    scheduler.reload(NOW + timedelta(seconds=10))

    assert scheduler.next_run_at(task.id) == NOW + timedelta(hours=1)


def test_bounded_wait_reload_observes_updates_from_another_process(
    tmp_path: Path,
) -> None:
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    task = _task(store)
    scheduler = _scheduler(store)
    clock_calls = 0

    class StopLoop(Exception):
        pass

    class CrossProcessEvent:
        wait_calls = 0

        def wait(self, timeout: float) -> bool:
            assert 0 < timeout <= 60
            self.wait_calls += 1
            if self.wait_calls == 1:
                store.update_scheduled_task(
                    task.id,
                    expected_version=task.version,
                    cron_expression="0 0 * * * *",
                    now=NOW + timedelta(seconds=5),
                )
                return False
            raise StopLoop

        def clear(self) -> None:
            raise AssertionError("a timeout is not a local wake signal")

    def clock() -> datetime:
        nonlocal clock_calls
        clock_calls += 1
        return NOW if clock_calls <= 2 else NOW + timedelta(seconds=10)

    with pytest.raises(StopLoop):
        scheduler.run_forever(wake_event=CrossProcessEvent(), now=clock)

    assert scheduler.next_run_at(task.id) == NOW + timedelta(hours=1)


def test_bounded_reload_preserves_an_unchanged_due_instant(tmp_path: Path) -> None:
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    task = _task(store)
    scheduler = _scheduler(store)
    clock_calls = 0

    class StopLoop(Exception):
        pass

    class TimeoutEvent:
        wait_calls = 0

        def wait(self, _timeout: float) -> bool:
            self.wait_calls += 1
            if self.wait_calls == 1:
                return False
            raise StopLoop

        def clear(self) -> None:
            raise AssertionError("timeout must not be cleared")

    def clock() -> datetime:
        nonlocal clock_calls
        clock_calls += 1
        return NOW if clock_calls <= 2 else NOW + timedelta(minutes=1)

    with pytest.raises(StopLoop):
        scheduler.run_forever(wake_event=TimeoutEvent(), now=clock)

    runs = store.list_scheduled_task_runs(task.id)
    assert len(runs) == 1
    assert runs[0].scheduled_for == NOW + timedelta(minutes=1)


def test_command_task_is_gated_only_by_its_command_not_by_runtime_or_skills(
    tmp_path: Path,
) -> None:
    store = AutoReplyStore(tmp_path / "command.sqlite3")
    task = _command_task(store)
    wakes: list[str] = []
    scheduler = _scheduler(store, options=MissingRuntime(), wakes=wakes)

    scheduler.start(NOW)
    assert scheduler.tick(NOW + timedelta(minutes=1)) == 1

    (run,) = store.list_scheduled_task_runs(task.id)
    assert run.dispatch_status == "pending"
    assert run.snapshot.command == "produce-once"
    assert wakes == ["wake"]


def test_command_task_with_unregistered_command_is_skipped_into_attention(
    tmp_path: Path,
) -> None:
    store = AutoReplyStore(tmp_path / "command-missing.sqlite3")
    task = _command_task(store)
    wakes: list[str] = []
    scheduler = _scheduler(store, options=MissingServiceCommand(), wakes=wakes)

    scheduler.start(NOW)
    assert scheduler.tick(NOW + timedelta(minutes=1)) == 1

    (run,) = store.list_scheduled_task_runs(task.id)
    assert run.dispatch_status == "skipped"
    assert run.skip_or_error_reason == SERVICE_COMMAND_UNAVAILABLE
    assert wakes == []
    with store._connect() as db:
        rows = db.execute("select kind, detail from errors").fetchall()
    assert [row["kind"] for row in rows] == [SERVICE_COMMAND_UNAVAILABLE]
    assert "service_command_not_registered" in rows[0]["detail"]


def test_completed_command_run_is_terminal_so_the_next_minute_is_not_skipped(
    tmp_path: Path,
) -> None:
    store = AutoReplyStore(tmp_path / "command-terminal.sqlite3")
    task = _command_task(store)
    resolver = ExecutionTerminalResolverRegistry(
        {SERVICE_COMMAND_EXECUTION_KIND: lambda _execution_id: True}
    )
    scheduler = _scheduler(store, resolver=resolver)
    scheduler.start(NOW)
    assert scheduler.tick(NOW + timedelta(minutes=1)) == 1
    (first,) = store.list_scheduled_task_runs(task.id)

    # The trigger is still pending while the command runs inside its claim.
    assert scheduler.tick(NOW + timedelta(minutes=2)) == 1
    runs = store.list_scheduled_task_runs(task.id)
    assert [run.dispatch_status for run in runs] == ["pending", "skipped"]
    assert runs[1].skip_or_error_reason == PREVIOUS_EXECUTION_ACTIVE

    claimed = store.claim_scheduled_task_run(
        first.id, owner="trigger", lease_seconds=60, now=NOW + timedelta(minutes=2)
    )
    assert claimed is not None
    store.link_scheduled_task_run_execution(
        first.id, owner="trigger", execution_kind=SERVICE_COMMAND_EXECUTION_KIND,
        execution_id="produce-once", now=NOW + timedelta(minutes=2),
    )
    store.finish_scheduled_task_dispatch(
        first.id, owner="trigger", status="dispatched", now=NOW + timedelta(minutes=2)
    )

    assert scheduler.tick(NOW + timedelta(minutes=3)) == 1
    latest = store.list_scheduled_task_runs(task.id)[-1]
    assert latest.dispatch_status == "pending"
    assert latest.skip_or_error_reason == ""
