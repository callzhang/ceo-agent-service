import sqlite3
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from uuid import uuid4

import pytest

from app.workbench.executor import WorkbenchExecutor
from app.workbench.models import TurnStatus
from app.workbench.runtime import (
    RuntimeCapabilities,
    RuntimeEvent,
    RuntimeHandle,
    RuntimeRegistry,
    RuntimeRequest,
    RuntimeResult,
    _release_runtime_owner,
)

from app.workbench.store import WORKBENCH_ATTACHMENT_SQL, WORKBENCH_EVENT_SQL, WORKBENCH_TURN_SQL
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
        self.on_event = None

    def capabilities(self):
        return RuntimeCapabilities(True, True, True, True, True, True, True, True)

    def start(self, request, *, on_event):
        self.requests.append(request)
        self.on_event = on_event
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


def test_recover_retries_a_transient_sqlite_lock(tmp_path: Path, monkeypatch):
    store = _store(tmp_path)
    calls = 0

    def locked_once(*, now=None):
        nonlocal calls
        del now
        calls += 1
        if calls == 1:
            raise sqlite3.OperationalError("database is locked")
        return 0

    monkeypatch.setattr(store, "recover_expired_turns", locked_once)
    executor = WorkbenchExecutor(store, RuntimeRegistry(), workspace=tmp_path)

    assert executor.recover() == 0
    assert calls == 2
    executor.close()


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
    assert "不可用" in persisted.final_text
    assert store.get_task(task.id).provider_session_ref == "session-1"
    events = store.events_after(turn.id)
    assert [(event.sequence, event.event_type) for event in events] == [
        (1, "status_changed"),
        (2, "text_delta"),
        (3, "turn_completed"),
    ]
    executor.close()


def test_runtime_artifact_event_persists_artifact_and_safe_event_atomically(tmp_path: Path):
    store = _store(tmp_path)
    task, turn = _queued(store)
    artifact_path = tmp_path / "output" / "report.txt"
    artifact_path.parent.mkdir()
    artifact_path.write_text("report", encoding="utf-8")
    runtime = FakeRuntime([
        RuntimeEvent("artifact_created", {
            "label": "Report", "path": str(artifact_path), "media_type": "text/plain"
        })
    ])
    executor = WorkbenchExecutor(store, RuntimeRegistry([runtime]), workspace=tmp_path)

    executor.run_once()

    artifacts = store.list_artifacts(task.id)
    event = store.events_after(turn.id)[1]
    assert len(artifacts) == 1
    assert event.event_type == "artifact_created"
    assert event.payload["artifact_id"] == artifacts[0].id
    assert "path" not in event.payload
    executor.close()


@pytest.mark.parametrize("kind", ["outside", "symlink"])
def test_runtime_artifact_event_rejects_unsafe_path_without_artifact_row(
    tmp_path: Path, kind: str
):
    store = _store(tmp_path)
    task, turn = _queued(store)
    outside = tmp_path.parent / f"outside-{uuid4()}.txt"
    outside.write_text("private", encoding="utf-8")
    path = outside
    if kind == "symlink":
        path = tmp_path / "linked.txt"
        path.symlink_to(outside)
    runtime = FakeRuntime([
        RuntimeEvent("artifact_created", {
            "label": "Bad", "path": str(path), "media_type": "text/plain"
        })
    ])
    executor = WorkbenchExecutor(store, RuntimeRegistry([runtime]), workspace=tmp_path)

    executor.run_once()

    assert store.list_artifacts(task.id) == []
    assert all(event.event_type != "artifact_created" for event in store.events_after(turn.id))
    assert store.get_turn(turn.id).status is TurnStatus.FAILED
    executor.close()


def test_recover_requeues_only_expired_running_turns(tmp_path: Path):
    store = _store(tmp_path)
    _, running = _queued(store)
    store.claim_next_turn(owner="dead", lease_seconds=1, now="2026-08-13T00:00:00Z")
    task2, waiting = _queued(store)
    store.claim_next_turn(owner="seed", lease_seconds=10, now="2026-08-13T00:00:00Z")
    executor = WorkbenchExecutor(store, RuntimeRegistry(), workspace=tmp_path)

    assert executor.recover(now="2026-08-13T00:00:02Z") == 1
    assert store.get_turn(running.id).status is TurnStatus.QUEUED
    assert store.get_turn(waiting.id).status is TurnStatus.RUNNING
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
            raise sqlite3.OperationalError("database is busy")

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
    assert [event.event_type for event in store.events_after(turn.id)] == [
        "status_changed"
    ]
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


def test_white_box_runtime_result_and_tool_payload_require_native_source(
    tmp_path: Path,
):
    store = _store(tmp_path)
    task, turn = _queued(store)
    runtime = FakeRuntime(
        events=[
            RuntimeEvent(
                "tool_completed",
                {
                    "tool_call_id": "tool-call-1",
                    "kind": "command",
                    "name": "printf",
                    "native_id": "native-1",
                    "status": "completed",
                    "command": "/usr/bin/printf api_token=local-value",
                    "output": "api_token=local-value",
                },
            )
        ],
        result=RuntimeResult(
            status="completed",
            final_text="Bearer local-workbench-value",
            provider_session_ref="session-safe",
        )
    )
    executor = WorkbenchExecutor(store, RuntimeRegistry([runtime]), workspace=tmp_path)

    executor.run_once(max_turns=1)

    persisted = store.get_turn(turn.id)
    assert persisted.status is TurnStatus.COMPLETED
    assert "不可用" in persisted.final_text
    assert store.get_task(task.id).provider_session_ref == "session-safe"
    [tool_event] = [
        event
        for event in store.events_after(turn.id)
        if event.event_type == "tool_completed"
    ]
    assert "不可用" in tool_event.payload["summary"]
    with store._connect() as db:
        event_row = db.execute(
            f"select payload_json from ({WORKBENCH_EVENT_SQL}) as workbench_events where id=?", (tool_event.id,)
        ).fetchone()
        final_row = db.execute(
            f"select final_text from ({WORKBENCH_TURN_SQL}) as workbench_turns where id=?", (turn.id,)
        ).fetchone()
    assert "api_token=local-value" not in event_row["payload_json"]
    assert final_row["final_text"] == ""
    executor.close()


def test_failed_runtime_keeps_service_code_without_copying_native_diagnostic(tmp_path: Path):
    store = _store(tmp_path)
    _, turn = _queued(store)
    runtime = FakeRuntime(
        events=[],
        result=RuntimeResult(
            status="failed",
            error_code="provider_process_failed",
            error_detail="native stderr PRIVATE_TOKEN",
        ),
    )
    executor = WorkbenchExecutor(store, RuntimeRegistry([runtime]), workspace=tmp_path)
    executor.run_once(max_turns=1)
    persisted = store.get_turn(turn.id)
    assert persisted.status is TurnStatus.FAILED
    assert persisted.error_code == "provider_process_failed"
    assert persisted.error_detail
    assert "PRIVATE_TOKEN" not in persisted.error_detail
    with store._connect() as db:
        row = db.execute(
            f"select error_code,error_detail from ({WORKBENCH_TURN_SQL}) as workbench_turns where id=?", (turn.id,)
        ).fetchone()
    assert row["error_code"] == "provider_process_failed"
    assert "PRIVATE_TOKEN" not in row["error_detail"]
    with store._connect() as db:
        db.execute("update workbench_turns set error_detail='' where task_id=?", (int(turn.id),))
    assert store.get_turn(turn.id).error_detail
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
    control_store = WorkbenchStore(store.path)
    _, turn = _queued(store)
    runtime = FakeRuntime()
    executor = WorkbenchExecutor(store, RuntimeRegistry([runtime]), workspace=tmp_path)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(executor.run_once)
        assert store.before_completion.wait(2)
        assert control_store.request_stop(turn.id).status is TurnStatus.STOPPED
        executor.stop(turn.id)
        store.release_completion.set()
        assert future.result(3) == [turn.id]
    assert control_store.get_turn(turn.id).status is TurnStatus.STOPPED
    assert [event.event_type for event in control_store.events_after(turn.id)].count(
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
    with store._connect() as db:
        locator = {
            row["id"]: Path(row["storage_path"])
            for row in db.execute(f"select id,storage_path from ({WORKBENCH_ATTACHMENT_SQL}) as workbench_attachments")
        }
    assert request.attachment_paths[0] == locator[document.id]
    assert len(request.image_paths) == 1
    assert request.image_paths[0] == locator[image.id]
    executor.close()


def test_runtime_failure_keeps_code_and_public_detail_without_native_copy(
    tmp_path: Path,
):
    store = _store(tmp_path)
    _, turn = _queued(store)
    runtime = FakeRuntime(
        result=RuntimeResult(
            status="failed",
            error_code="provider_output_limit",
            error_detail="provider output exceeded the safe limit",
        )
    )
    executor = WorkbenchExecutor(store, RuntimeRegistry([runtime]), workspace=tmp_path)

    executor.run_once(max_turns=1)

    persisted = store.get_turn(turn.id)
    assert persisted.status is TurnStatus.FAILED
    assert persisted.error_code == "provider_output_limit"
    assert persisted.error_detail == (
        "Codex provider output exceeded the 16 MiB Workbench safety limit."
    )
    executor.close()


def test_close_is_bounded_and_idempotent_without_heartbeat_thread_leak(tmp_path: Path):
    store = _store(tmp_path)
    executor = WorkbenchExecutor(store, RuntimeRegistry(), workspace=tmp_path)
    executor.close()
    executor.close()
    assert not any(
        thread.is_alive() and thread.name.startswith("workbench-heartbeat-")
        for thread in threading.enumerate()
    )


def test_close_during_blocked_start_stops_late_handle_once(tmp_path: Path):
    store = _store(tmp_path)
    _, turn = _queued(store)
    runtime = FakeRuntime(block_start=True)
    executor = WorkbenchExecutor(store, RuntimeRegistry([runtime]), workspace=tmp_path)
    with ThreadPoolExecutor(max_workers=1) as pool:
        running = pool.submit(executor.run_once)
        assert runtime.start_entered.wait(2)
        assert executor.close() is False
        assert store.get_turn(turn.id).status is TurnStatus.STOPPED
        runtime.release_start.set()
        assert running.result(3) == [turn.id]
    assert runtime.stop_calls == 1
    assert store.get_turn(turn.id).status is TurnStatus.STOPPED
    assert executor.close() is True


def test_concurrent_run_once_respects_global_two_worker_capacity(tmp_path: Path):
    store = _store(tmp_path)
    turns = [_queued(store)[1] for _ in range(4)]
    runtime = FakeRuntime(block=True)
    executor = WorkbenchExecutor(store, RuntimeRegistry([runtime]), workspace=tmp_path)
    with ThreadPoolExecutor(max_workers=2) as callers:
        first = callers.submit(executor.run_once)
        assert runtime.wait_entered.wait(2)
        second = callers.submit(executor.run_once)
        time.sleep(0.1)
        statuses = [store.get_turn(turn.id).status for turn in turns]
        assert statuses.count(TurnStatus.RUNNING) == 2
        assert statuses.count(TurnStatus.QUEUED) == 2
        assert second.result(2) == []
        runtime.release_wait.set()
        assert len(first.result(3)) == 2
    executor.close()
