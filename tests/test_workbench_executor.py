import os
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

import app.agent_cli as agent_cli
from app.agent_result import EffectKind
from app.native_cli_metadata import NativeCliMetadataClassifier
from app.workbench.executor import WorkbenchExecutor
from app.workbench.models import ConfirmationStatus, TurnStatus
from app.workbench.runtime import (
    RuntimeCapabilities,
    RuntimeEvent,
    RuntimeHandle,
    RuntimeRegistry,
    RuntimeRequest,
    RuntimeResult,
    _release_runtime_owner,
)
from app.workbench.store import WorkbenchStore


class FakeRuntime:
    kind = "fake"

    def __init__(self, events=(), result=None, *, block=False, block_start=False):
        self.events = list(events)
        self.result = result or RuntimeResult(status="completed", final_text="done")
        self.block = block
        self.block_start = block_start
        self.start_entered = threading.Event()
        self.release_start = threading.Event()
        self.wait_entered = threading.Event()
        self.release_wait = threading.Event()
        self.stop_calls = 0
        self.requests: list[RuntimeRequest] = []

    def capabilities(self):
        return RuntimeCapabilities(True, True, True, True, True, True, True, True)

    def start(self, request, *, on_event):
        self.requests.append(request)
        self.start_entered.set()
        if self.block_start:
            assert self.release_start.wait(5)
        owner = {"on_event": on_event}
        handle = RuntimeHandle.create(run_id=f"run-{request.turn_id}", owner=owner)
        for event in self.events:
            on_event(event)
        return handle

    def wait(self, handle):
        self.wait_entered.set()
        if self.block:
            assert self.release_wait.wait(5)
        _release_runtime_owner(handle)
        return self.result

    def stop(self, handle):
        self.stop_calls += 1
        self.release_wait.set()


def _store(tmp_path: Path) -> WorkbenchStore:
    return WorkbenchStore(tmp_path / "workbench.sqlite3")


def _queued(store: WorkbenchStore, *, runtime_kind="fake"):
    task = store.create_task(title="Task", runtime_kind=runtime_kind)
    turn = store.create_turn(
        task.id, user_text="Do work", client_request_id=f"request-{task.id}"
    )
    return task, turn


def _write_classifier():
    return NativeCliMetadataClassifier(
        reviewed_effects={("dws", "chat message send"): EffectKind.EFFECTFUL}
    )


def _pending_confirmation(store: WorkbenchStore):
    task, turn = _queued(store)
    assert store.claim_next_turn(owner="seed") is not None
    confirmation = store.create_confirmation(
        turn.id,
        action_kind="reviewed_cli",
        target="Executive group",
        summary="Send update",
        risk="External message",
        arguments_json={
            "argv": [
                "dws",
                "chat",
                "message",
                "send",
                "--group",
                "cid-1",
                "--text",
                "hello",
                "--yes",
            ],
            "action_index": 0,
        },
        owner="seed",
    )
    return task, turn, confirmation


def test_run_once_persists_stream_session_and_one_terminal_event(tmp_path: Path):
    store = _store(tmp_path)
    task, turn = _queued(store)
    runtime = FakeRuntime(
        [RuntimeEvent("text_delta", {"text": "hello"})],
        RuntimeResult(
            status="completed", final_text="done", provider_session_ref="session-1"
        ),
    )
    executor = WorkbenchExecutor(store, RuntimeRegistry([runtime]), workspace=tmp_path)

    assert executor.run_once() == [turn.id]

    persisted = store.get_turn(turn.id)
    assert persisted.status is TurnStatus.COMPLETED
    assert persisted.final_text == "done"
    assert store.get_task(task.id).provider_session_ref == "session-1"
    events = store.events_after(turn.id)
    assert [(event.sequence, event.event_type) for event in events] == [
        (1, "text_delta"),
        (2, "turn_completed"),
    ]
    executor.close()


def test_recover_requeues_only_expired_running_turns(tmp_path: Path):
    store = _store(tmp_path)
    _, running = _queued(store)
    store.claim_next_turn(owner="dead", lease_seconds=1, now="2026-08-13T00:00:00Z")
    task2, waiting = _queued(store)
    store.claim_next_turn(owner="seed", lease_seconds=10, now="2026-08-13T00:00:00Z")
    store.create_confirmation(
        waiting.id,
        action_kind="reviewed_cli",
        target="target",
        summary="summary",
        risk="risk",
        arguments_json={
            "argv": ["dws", "chat", "message", "send", "--yes"],
            "action_index": 0,
        },
        owner="seed",
        now="2026-08-13T00:00:01Z",
    )
    executor = WorkbenchExecutor(store, RuntimeRegistry(), workspace=tmp_path)

    assert executor.recover(now="2026-08-13T00:00:02Z") == 1
    assert store.get_turn(running.id).status is TurnStatus.QUEUED
    assert store.get_turn(waiting.id).status is TurnStatus.WAITING_CONFIRMATION
    assert store.get_task(task2.id) is not None
    executor.close()


def test_heartbeat_renews_during_blocked_wait(tmp_path: Path):
    store = _store(tmp_path)
    _, turn = _queued(store)
    runtime = FakeRuntime(block=True)
    executor = WorkbenchExecutor(
        store,
        RuntimeRegistry([runtime]),
        workspace=tmp_path,
        lease_seconds=1,
        heartbeat_interval_seconds=0.1,
    )
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(executor.run_once)
        assert runtime.wait_entered.wait(2)
        time.sleep(1.2)
        assert store.recover_expired_turns() == 0
        runtime.release_wait.set()
        assert future.result(3) == [turn.id]
    assert store.get_turn(turn.id).status is TurnStatus.COMPLETED
    executor.close()


def test_lost_lease_stops_runtime_and_does_not_write_after_loss(tmp_path: Path):
    class LostLeaseStore(WorkbenchStore):
        def renew_turn_lease(self, *args, **kwargs):
            raise ValueError("turn lease is stale")

    store = LostLeaseStore(tmp_path / "workbench.sqlite3")
    _, turn = _queued(store)
    runtime = FakeRuntime(block=True)
    executor = WorkbenchExecutor(
        store,
        RuntimeRegistry([runtime]),
        workspace=tmp_path,
        lease_seconds=5,
        heartbeat_interval_seconds=0.05,
    )

    executor.run_once()

    assert runtime.stop_calls == 1
    assert store.events_after(turn.id) == []
    assert store.get_turn(turn.id).status is TurnStatus.RUNNING
    executor.close()


def test_unknown_runtime_and_malformed_event_fail_safely(tmp_path: Path):
    store = _store(tmp_path)
    _, unknown = _queued(store, runtime_kind="missing")
    executor = WorkbenchExecutor(store, RuntimeRegistry(), workspace=tmp_path)
    executor.run_once(max_turns=1)
    assert store.get_turn(unknown.id).status is TurnStatus.FAILED
    assert store.get_turn(unknown.id).error_code == "runtime_unavailable"
    executor.close()

    store2 = _store(tmp_path / "malformed")
    _, malformed = _queued(store2)
    runtime = FakeRuntime([object()])
    executor2 = WorkbenchExecutor(
        store2, RuntimeRegistry([runtime]), workspace=tmp_path
    )
    executor2.run_once(max_turns=1)
    persisted = store2.get_turn(malformed.id)
    assert persisted.status is TurnStatus.FAILED
    assert persisted.error_code == "runtime_failure"
    assert "object" not in persisted.error_detail
    executor2.close()


def test_sensitive_runtime_result_is_not_persisted(tmp_path: Path):
    store = _store(tmp_path)
    task, turn = _queued(store)
    runtime = FakeRuntime(
        result=RuntimeResult(
            status="completed",
            final_text="Bearer abcdefghijklmnop",
            provider_session_ref="session-safe",
        )
    )
    executor = WorkbenchExecutor(store, RuntimeRegistry([runtime]), workspace=tmp_path)

    executor.run_once(max_turns=1)

    persisted = store.get_turn(turn.id)
    assert persisted.status is TurnStatus.FAILED
    assert "Bearer" not in persisted.final_text
    assert store.get_task(task.id).provider_session_ref == ""
    executor.close()


def test_stop_before_handle_publication_stops_once_and_never_completes(tmp_path: Path):
    store = _store(tmp_path)
    _, turn = _queued(store)
    runtime = FakeRuntime(block=True, block_start=True)
    executor = WorkbenchExecutor(store, RuntimeRegistry([runtime]), workspace=tmp_path)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(executor.run_once)
        assert runtime.start_entered.wait(2)
        assert executor.stop(turn.id).stop_requested
        runtime.release_start.set()
        assert future.result(3) == [turn.id]
    assert store.get_turn(turn.id).status is TurnStatus.STOPPED
    assert runtime.stop_calls == 1
    assert [event.event_type for event in store.events_after(turn.id)].count(
        "turn_completed"
    ) == 1
    executor.stop(turn.id)
    assert runtime.stop_calls == 1
    executor.close()


def test_stop_during_wait_stops_once(tmp_path: Path):
    store = _store(tmp_path)
    _, turn = _queued(store)
    runtime = FakeRuntime(block=True)
    executor = WorkbenchExecutor(store, RuntimeRegistry([runtime]), workspace=tmp_path)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(executor.run_once)
        assert runtime.wait_entered.wait(2)
        executor.stop(turn.id)
        assert future.result(3) == [turn.id]
    assert runtime.stop_calls == 1
    assert store.get_turn(turn.id).status is TurnStatus.STOPPED
    executor.close()


def test_stop_after_runtime_wait_before_atomic_completion_wins(tmp_path: Path):
    class StopRaceStore(WorkbenchStore):
        before_completion = threading.Event()
        release_completion = threading.Event()

        def complete_turn(self, turn_id, **kwargs):
            if kwargs.get("status") is TurnStatus.COMPLETED:
                self.before_completion.set()
                assert self.release_completion.wait(5)
            return super().complete_turn(turn_id, **kwargs)

    store = StopRaceStore(tmp_path / "workbench.sqlite3")
    _, turn = _queued(store)
    runtime = FakeRuntime()
    executor = WorkbenchExecutor(store, RuntimeRegistry([runtime]), workspace=tmp_path)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(executor.run_once)
        assert store.before_completion.wait(2)
        executor.stop(turn.id)
        store.release_completion.set()
        assert future.result(3) == [turn.id]
    assert store.get_turn(turn.id).status is TurnStatus.STOPPED
    assert [event.event_type for event in store.events_after(turn.id)].count(
        "turn_completed"
    ) == 1
    executor.close()


def test_runtime_request_uses_validated_attachment_and_image_paths(tmp_path: Path):
    store = _store(tmp_path)
    task, turn = _queued(store)
    document = store.save_attachment(
        task.id, filename="report.txt", media_type="text/plain", content=b"report"
    )
    image = store.save_attachment(
        task.id, filename="chart.png", media_type="image/png", content=b"png"
    )
    runtime = FakeRuntime()
    executor = WorkbenchExecutor(store, RuntimeRegistry([runtime]), workspace=tmp_path)

    executor.run_once()

    request = runtime.requests[0]
    assert len(request.attachment_paths) == 1
    assert request.attachment_paths[0].name == document.id
    assert len(request.image_paths) == 1
    assert request.image_paths[0].name == image.id
    executor.close()


def test_confirmation_event_atomically_waits_and_runtime_completion_cannot_overwrite(
    tmp_path: Path,
):
    store = _store(tmp_path)
    _, turn = _queued(store)
    runtime = FakeRuntime(
        [
            RuntimeEvent(
                "confirmation_required",
                {
                    "kind": "reviewed_cli",
                    "argv": [
                        "dws",
                        "chat",
                        "message",
                        "send",
                        "--group",
                        "cid-1",
                        "--text",
                        "hello",
                        "--yes",
                    ],
                    "target": "Executive group",
                    "summary": "Send update",
                    "risk": "External message",
                    "executed": False,
                },
            )
        ],
        RuntimeResult(status="completed", final_text="must not win"),
    )
    executor = WorkbenchExecutor(
        store,
        RuntimeRegistry([runtime]),
        workspace=tmp_path,
        classifier=_write_classifier(),
    )

    executor.run_once()

    assert store.get_turn(turn.id).status is TurnStatus.WAITING_CONFIRMATION
    confirmations = store.list_confirmations(store.get_turn(turn.id).task_id)
    assert len(confirmations) == 1
    assert confirmations[0].arguments_json == ""
    assert runtime.stop_calls == 1
    assert [event.event_type for event in store.events_after(turn.id)] == [
        "confirmation_required"
    ]
    executor.close()


def test_confirm_executes_once_redacts_receipt_and_requeues(
    tmp_path: Path, monkeypatch
):
    store = _store(tmp_path)
    task, turn, confirmation = _pending_confirmation(store)
    calls = []
    monkeypatch.setattr(agent_cli.shutil, "which", lambda _: "/usr/local/bin/dws")
    executor = WorkbenchExecutor(
        store,
        RuntimeRegistry(),
        workspace=tmp_path,
        classifier=_write_classifier(),
        write_runner=lambda argv, **_: (
            calls.append(argv)
            or subprocess.CompletedProcess(argv, 0, "secret provider output", "")
        ),
    )
    before = dict(os.environ)

    first = executor.confirm(confirmation.id)
    second = executor.confirm(confirmation.id)

    assert first.status is ConfirmationStatus.EXECUTED
    assert second.status is ConfirmationStatus.EXECUTED
    assert len(calls) == 1
    assert store.get_turn(turn.id).status is TurnStatus.QUEUED
    assert "secret provider output" not in first.result_json
    assert "dws chat" not in first.result_json
    assert dict(os.environ) == before
    assert store.get_task(task.id) is not None
    executor.close()


def test_cancel_never_runs_and_conflicting_decision_rejects(tmp_path: Path):
    store = _store(tmp_path)
    _, turn, confirmation = _pending_confirmation(store)
    calls = []
    executor = WorkbenchExecutor(
        store,
        RuntimeRegistry(),
        workspace=tmp_path,
        write_runner=lambda *args, **kwargs: calls.append((args, kwargs)),
    )

    cancelled = executor.cancel(confirmation.id)
    assert cancelled.status is ConfirmationStatus.CANCELLED
    assert executor.cancel(confirmation.id).status is ConfirmationStatus.CANCELLED
    with pytest.raises(ValueError, match="already been decided"):
        executor.confirm(confirmation.id)
    assert calls == []
    assert store.get_turn(turn.id).status is TurnStatus.QUEUED
    executor.close()


def test_writer_failure_is_sanitized_and_requeued_for_agent_report(
    tmp_path: Path, monkeypatch
):
    store = _store(tmp_path)
    _, turn, confirmation = _pending_confirmation(store)
    monkeypatch.setattr(agent_cli.shutil, "which", lambda _: "/usr/local/bin/dws")
    executor = WorkbenchExecutor(
        store,
        RuntimeRegistry(),
        workspace=tmp_path,
        classifier=_write_classifier(),
        write_runner=lambda *_args, **_kwargs: (_ for _ in ()).throw(
            OSError("secret-token")
        ),
    )

    result = executor.confirm(confirmation.id)

    assert result.status is ConfirmationStatus.FAILED
    assert "secret-token" not in result.result_json
    assert store.get_turn(turn.id).status is TurnStatus.QUEUED
    assert "failed" in store.get_turn(turn.id).error_detail.lower()
    executor.close()


def test_restart_reconciles_confirmed_without_result_without_execution(tmp_path: Path):
    store = _store(tmp_path)
    _, turn, confirmation = _pending_confirmation(store)
    claim = store.claim_confirmation_execution(confirmation.id)
    assert claim is not None
    calls = []

    executor = WorkbenchExecutor(
        WorkbenchStore(store.path),
        RuntimeRegistry(),
        workspace=tmp_path,
        write_runner=lambda *args, **kwargs: calls.append((args, kwargs)),
    )

    recovered = executor.recover()
    assert recovered >= 1
    assert calls == []
    assert store.get_turn(turn.id).status is TurnStatus.FAILED
    confirmation_result = store.list_confirmations(store.get_turn(turn.id).task_id)[0]
    assert confirmation_result.status is ConfirmationStatus.FAILED
    assert any(
        event.event_type == "turn_failed" for event in store.events_after(turn.id)
    )
    executor.close()


def test_two_executors_racing_confirm_run_writer_at_most_once(
    tmp_path: Path, monkeypatch
):
    store = _store(tmp_path)
    _, _, confirmation = _pending_confirmation(store)
    monkeypatch.setattr(agent_cli.shutil, "which", lambda _: "/usr/local/bin/dws")
    calls = 0
    lock = threading.Lock()

    def runner(argv, **_):
        nonlocal calls
        with lock:
            calls += 1
        time.sleep(0.1)
        return subprocess.CompletedProcess(argv, 0, "ok", "")

    first = WorkbenchExecutor(
        WorkbenchStore(store.path),
        RuntimeRegistry(),
        workspace=tmp_path,
        classifier=_write_classifier(),
        write_runner=runner,
    )
    second = WorkbenchExecutor(
        WorkbenchStore(store.path),
        RuntimeRegistry(),
        workspace=tmp_path,
        classifier=_write_classifier(),
        write_runner=runner,
    )
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(
            pool.map(
                lambda executor: executor.confirm(confirmation.id), (first, second)
            )
        )
    assert calls == 1
    assert {result.status for result in results} <= {
        ConfirmationStatus.CONFIRMED,
        ConfirmationStatus.EXECUTED,
    }
    first.close()
    second.close()


def test_close_is_bounded_and_idempotent_without_heartbeat_thread_leak(tmp_path: Path):
    store = _store(tmp_path)
    executor = WorkbenchExecutor(store, RuntimeRegistry(), workspace=tmp_path)
    executor.close()
    executor.close()
    assert not any(
        thread.is_alive() and thread.name.startswith("workbench-heartbeat-")
        for thread in threading.enumerate()
    )
