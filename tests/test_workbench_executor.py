import json
import os
import sqlite3
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from uuid import uuid4

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


def _write_classifier():
    return NativeCliMetadataClassifier(
        reviewed_effects={("dws", "chat message send"): EffectKind.EFFECTFUL}
    )


def _pending_confirmation(store: WorkbenchStore):
    task, turn = _queued(store)
    assert store.claim_next_turn(owner="seed") is not None
    confirmation_id = str(uuid4())
    argv = [
        "dws", "chat", "message", "send", "--group", "cid-1", "--text",
        "hello", "--yes",
    ]
    authorization = agent_cli.review_write_authorization(
        argv,
        authorization_id=confirmation_id,
        action_index=0,
        classifier=_write_classifier(),
    )
    confirmation = store.create_confirmation(
        turn.id,
        action_kind="reviewed_cli",
        target="Executive group",
        summary="Send update",
        risk="External message",
        arguments_json={
            "argv": argv,
            "action_index": 0,
        },
        confirmation_id=confirmation_id,
        canonical_capability=authorization.capability,
        canonical_operation=authorization.operation,
        canonical_targets=authorization.target_identifiers,
        canonical_operation_digest=authorization.operation_digest,
        canonical_arguments_digest=authorization.arguments_digest,
        owner="seed",
    )
    store.mark_confirmation_proposer_quiesced(
        turn.id,
        owner="seed",
        proposer_run_id=store.execution_run_id_for_executor(turn.id, owner="seed"),
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


def test_stop_and_confirmation_callback_race_has_one_authoritative_terminal_state(
    tmp_path: Path,
):
    store = _store(tmp_path)
    control_store = WorkbenchStore(store.path)
    task, turn = _queued(store)
    runtime = FakeRuntime(block=True)
    executor = WorkbenchExecutor(
        store,
        RuntimeRegistry([runtime]),
        workspace=tmp_path,
        classifier=_write_classifier(),
    )
    proposal = RuntimeEvent(
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
    barrier = threading.Barrier(2)

    with ThreadPoolExecutor(max_workers=1) as runs:
        run = runs.submit(executor.run_once)
        assert runtime.wait_entered.wait(2)

        def stop():
            barrier.wait()
            return executor.stop(turn.id)

        def confirm_callback():
            barrier.wait()
            runtime.on_event(proposal)

        with ThreadPoolExecutor(max_workers=2) as races:
            stop_future = races.submit(stop)
            confirm_future = races.submit(confirm_callback)
            assert stop_future.result(3).status is TurnStatus.STOPPED
            confirm_future.result(3)
        assert run.result(3) == [turn.id]

    persisted = control_store.get_turn(turn.id)
    assert persisted.status is TurnStatus.STOPPED
    assert runtime.stop_calls == 1
    events = control_store.events_after(turn.id)
    assert [event.sequence for event in events] == list(range(1, len(events) + 1))
    assert [event.event_type for event in events].count("turn_completed") == 1
    confirmations = control_store.list_confirmations(task.id)
    assert len(confirmations) <= 1
    assert all(item.status is ConfirmationStatus.CANCELLED for item in confirmations)
    executor.stop(turn.id)
    assert runtime.stop_calls == 1
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


def test_confirmation_uses_canonical_target_and_waits_for_runtime_quiescence(
    tmp_path: Path, monkeypatch
):
    class AsyncRuntime(FakeRuntime):
        def wait(self, handle):
            self.wait_entered.set()
            assert self.release_wait.wait(5)
            _release_runtime_owner(handle)
            return self.result

        def stop(self, handle):
            self.stop_calls += 1

    store = _store(tmp_path)
    task, turn = _queued(store)
    runtime = AsyncRuntime()
    writer_calls = []
    monkeypatch.setattr(agent_cli.shutil, "which", lambda _: "/usr/local/bin/dws")
    executor = WorkbenchExecutor(
        store,
        RuntimeRegistry([runtime]),
        workspace=tmp_path,
        classifier=_write_classifier(),
        write_runner=lambda argv, **_: (
            writer_calls.append(argv)
            or subprocess.CompletedProcess(argv, 0, "ok", "")
        ),
    )
    other = WorkbenchExecutor(
        WorkbenchStore(store.path),
        RuntimeRegistry(),
        workspace=tmp_path,
        classifier=_write_classifier(),
        write_runner=lambda argv, **_: (
            writer_calls.append(argv)
            or subprocess.CompletedProcess(argv, 0, "ok", "")
        ),
    )
    event = RuntimeEvent(
        "confirmation_required",
        {
            "kind": "reviewed_cli",
            "argv": [
                "dws", "chat", "message", "send", "--group", "executive-group",
                "--text", "hello", "--yes",
            ],
            "target": "Test group",
            "summary": "Send update",
            "risk": "External message",
            "executed": False,
        },
    )
    with ThreadPoolExecutor(max_workers=1) as pool:
        running = pool.submit(executor.run_once)
        assert runtime.wait_entered.wait(2)
        runtime.on_event(event)
        confirmation = store.list_confirmations(task.id)[0]
        assert confirmation.target == "group=executive-group"
        assert confirmation.canonical_operation == "chat message send"
        assert runtime.stop_calls == 1
        in_progress = other.confirm(confirmation.id)
        assert in_progress.status is ConfirmationStatus.PENDING
        assert in_progress.decision_requested == "confirm"
        assert writer_calls == []
        with pytest.raises(ValueError, match="conflicting decision intent"):
            other.cancel(confirmation.id)
        assert other.run_once() == []
        runtime.release_wait.set()
        assert running.result(3) == [turn.id]
    assert store.get_confirmation(confirmation.id).status is ConfirmationStatus.EXECUTED
    assert len(writer_calls) == 1
    executor.close()
    other.close()


def test_cancel_intent_waits_for_quiescence_then_requeues_without_second_click(
    tmp_path: Path,
):
    class AsyncRuntime(FakeRuntime):
        def wait(self, handle):
            self.wait_entered.set()
            assert self.release_wait.wait(5)
            _release_runtime_owner(handle)
            return self.result

        def stop(self, handle):
            self.stop_calls += 1

    store = _store(tmp_path)
    task, turn = _queued(store)
    runtime = AsyncRuntime()
    executor = WorkbenchExecutor(
        store, RuntimeRegistry([runtime]), workspace=tmp_path, classifier=_write_classifier()
    )
    other = WorkbenchExecutor(
        WorkbenchStore(store.path), RuntimeRegistry(), workspace=tmp_path
    )
    event = RuntimeEvent(
        "confirmation_required",
        {
            "kind": "reviewed_cli",
            "argv": [
                "dws", "chat", "message", "send", "--group", "executive-group",
                "--text", "hello", "--yes",
            ],
            "target": "Test group",
            "summary": "Send update",
            "risk": "No risk",
            "executed": False,
        },
    )
    with ThreadPoolExecutor(max_workers=1) as pool:
        running = pool.submit(executor.run_once)
        assert runtime.wait_entered.wait(2)
        runtime.on_event(event)
        confirmation = store.list_confirmations(task.id)[0]
        assert confirmation.risk == "[Untrusted agent risk] No risk"
        pending = other.cancel(confirmation.id)
        assert pending.status is ConfirmationStatus.PENDING
        assert pending.decision_requested == "cancel"
        assert store.get_turn(turn.id).status is TurnStatus.WAITING_CONFIRMATION
        runtime.release_wait.set()
        assert running.result(3) == [turn.id]
    assert store.get_confirmation(confirmation.id).status is ConfirmationStatus.CANCELLED
    assert store.get_turn(turn.id).status is TurnStatus.QUEUED
    executor.close()
    other.close()


def test_unquiesced_crashed_proposer_recovery_fails_closed(tmp_path: Path):
    store = _store(tmp_path)
    task, turn = _queued(store)
    store.claim_next_turn(
        owner="crashed",
        execution_run_id="run-crashed",
        lease_seconds=1,
        now="2026-08-13T00:00:00Z",
    )
    confirmation = store.create_confirmation(
        turn.id,
        action_kind="reviewed_cli",
        target="group=executive-group",
        summary="[Untrusted agent description] Send update",
        risk="[Untrusted agent risk] No risk",
        arguments_json={"argv": ["dws", "chat", "message", "send", "--yes"]},
        owner="crashed",
        now="2026-08-13T00:00:00Z",
    )
    calls = []
    executor = WorkbenchExecutor(
        WorkbenchStore(store.path),
        RuntimeRegistry(),
        workspace=tmp_path,
        write_runner=lambda *args, **kwargs: calls.append((args, kwargs)),
    )

    assert executor.confirm(confirmation.id).decision_requested == "confirm"
    assert executor.recover(now="2026-08-13T00:00:02Z") == 1
    assert store.get_confirmation(confirmation.id).status is ConfirmationStatus.FAILED
    assert store.get_turn(turn.id).status is TurnStatus.FAILED
    assert calls == []
    assert store.get_task(task.id) is not None
    executor.close()


def test_other_executor_picks_persisted_quiesced_confirm_intent(
    tmp_path: Path, monkeypatch
):
    store = _store(tmp_path)
    _, turn, confirmation = _pending_confirmation(store)
    run_id = store.execution_run_id_for_executor(turn.id, owner="seed")
    with store._connect() as db:
        db.execute(
            """
            update workbench_confirmations
            set proposer_quiesced_at='', decision_requested='confirm',
                decision_requested_at=current_timestamp
            where id=?
            """,
            (confirmation.id,),
        )
        db.execute(
            "update workbench_turns set runtime_quiesced_run_id='' where id=?",
            (turn.id,),
        )
    store.mark_confirmation_proposer_quiesced(
        turn.id, owner="seed", proposer_run_id=run_id
    )
    calls = []
    monkeypatch.setattr(agent_cli.shutil, "which", lambda _: "/usr/local/bin/dws")
    executor = WorkbenchExecutor(
        WorkbenchStore(store.path),
        RuntimeRegistry([FakeRuntime()]),
        workspace=tmp_path,
        classifier=_write_classifier(),
        write_runner=lambda argv, **_: (
            calls.append(argv) or subprocess.CompletedProcess(argv, 0, "ok", "")
        ),
    )

    assert executor.run_once() == [turn.id]
    assert len(calls) == 1
    assert store.get_confirmation(confirmation.id).status is ConfirmationStatus.EXECUTED
    assert store.get_turn(turn.id).status is TurnStatus.COMPLETED
    executor.close()


def test_confirmation_rejects_private_argv_drift_before_writer(tmp_path: Path, monkeypatch):
    store = _store(tmp_path)
    task, _, confirmation = _pending_confirmation(store)
    with store._connect() as db:
        db.execute(
            "update workbench_confirmations set arguments_json=? where id=?",
            (
                json.dumps(
                    {
                        "argv": [
                            "dws", "chat", "message", "send", "--group",
                            "different-group", "--text", "hello", "--yes",
                        ],
                        "action_index": 0,
                    }
                ),
                confirmation.id,
            ),
        )
    calls = []
    monkeypatch.setattr(agent_cli.shutil, "which", lambda _: "/usr/local/bin/dws")
    executor = WorkbenchExecutor(
        store,
        RuntimeRegistry(),
        workspace=tmp_path,
        classifier=_write_classifier(),
        write_runner=lambda *args, **kwargs: calls.append((args, kwargs)),
    )
    result = executor.confirm(confirmation.id)
    assert result.status is ConfirmationStatus.FAILED
    assert calls == []
    assert store.get_task(task.id) is not None
    executor.close()


def test_confirm_executes_once_redacts_receipt_and_requeues(
    tmp_path: Path, monkeypatch
):
    store = _store(tmp_path)
    task, turn, confirmation = _pending_confirmation(store)
    calls = []
    monkeypatch.setattr(agent_cli.shutil, "which", lambda _: "/usr/local/bin/dws")
    runtime = FakeRuntime()
    executor = WorkbenchExecutor(
        store,
        RuntimeRegistry([runtime]),
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
    with WorkbenchStore(store.path)._connect() as db:
        consumed_at = db.execute(
            "select authorization_consumed_at from workbench_confirmations where id=?",
            (confirmation.id,),
        ).fetchone()["authorization_consumed_at"]
    assert consumed_at
    assert store.get_turn(turn.id).status is TurnStatus.QUEUED
    assert "secret provider output" not in first.result_json
    assert "dws chat" not in first.result_json
    assert dict(os.environ) == before
    assert store.get_task(task.id) is not None
    executor.run_once(max_turns=1)
    prompt = runtime.requests[0].prompt
    assert '"status":"executed"' in prompt
    assert '"operation_digest":' in prompt
    assert '"receipt_digest":' in prompt
    assert '"result_digest":' in prompt
    assert '"retryable":false' in prompt
    assert '"target_summary":"Executive group"' in prompt
    assert "secret provider output" not in prompt
    assert "dws chat message send" not in prompt
    assert "cid-1" not in prompt
    assert "session-" not in prompt
    executor.close()


def test_cancel_never_runs_and_conflicting_decision_rejects(tmp_path: Path):
    store = _store(tmp_path)
    _, turn, confirmation = _pending_confirmation(store)
    calls = []
    runtime = FakeRuntime()
    executor = WorkbenchExecutor(
        store,
        RuntimeRegistry([runtime]),
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
    executor.run_once(max_turns=1)
    assert "The user cancelled the reviewed external action." in runtime.requests[0].prompt
    assert "dws chat" not in runtime.requests[0].prompt
    executor.close()


def test_writer_failure_is_sanitized_and_requeued_for_agent_report(
    tmp_path: Path, monkeypatch
):
    store = _store(tmp_path)
    _, turn, confirmation = _pending_confirmation(store)
    monkeypatch.setattr(agent_cli.shutil, "which", lambda _: "/usr/local/bin/dws")
    runtime = FakeRuntime()
    writer_calls = 0

    def failed_writer(*_args, **_kwargs):
        nonlocal writer_calls
        writer_calls += 1
        raise OSError("secret-token")

    executor = WorkbenchExecutor(
        store,
        RuntimeRegistry([runtime]),
        workspace=tmp_path,
        classifier=_write_classifier(),
        write_runner=failed_writer,
    )

    result = executor.confirm(confirmation.id)
    repeated = executor.confirm(confirmation.id)

    assert result.status is ConfirmationStatus.FAILED
    assert repeated == result
    assert writer_calls == 1
    assert "secret-token" not in result.result_json
    assert store.get_turn(turn.id).status is TurnStatus.QUEUED
    assert "failed" in store.get_turn(turn.id).error_detail.lower()
    executor.run_once(max_turns=1)
    prompt = runtime.requests[0].prompt
    assert '"status":"failed"' in prompt
    assert '"code":"agent_cli_start_unavailable"' in prompt
    assert '"retryable":true' in prompt
    assert '"result_digest":' in prompt
    assert "secret-token" not in prompt
    assert "dws chat" not in prompt
    executor.close()


def test_restart_reconciles_confirmed_without_result_without_execution(tmp_path: Path):
    store = _store(tmp_path)
    _, turn, confirmation = _pending_confirmation(store)
    claim = store.claim_confirmation_execution(
        confirmation.id,
        owner="crashed-executor",
        lease_seconds=1,
        now="2026-08-13T00:00:00Z",
    )
    assert claim is not None
    calls = []

    executor = WorkbenchExecutor(
        WorkbenchStore(store.path),
        RuntimeRegistry(),
        workspace=tmp_path,
        write_runner=lambda *args, **kwargs: calls.append((args, kwargs)),
    )

    assert store.get_turn(turn.id).status is TurnStatus.WAITING_CONFIRMATION
    assert executor.recover(now="2026-08-13T00:00:02Z") == 1
    assert calls == []
    assert store.get_turn(turn.id).status is TurnStatus.FAILED
    confirmation_result = store.list_confirmations(store.get_turn(turn.id).task_id)[0]
    assert confirmation_result.status is ConfirmationStatus.FAILED
    assert any(
        event.event_type == "turn_failed" for event in store.events_after(turn.id)
    )
    executor.close()


@pytest.mark.parametrize(
    "status",
    [
        TurnStatus.QUEUED,
        TurnStatus.RUNNING,
        TurnStatus.WAITING_CONFIRMATION,
        TurnStatus.COMPLETED,
        TurnStatus.STOPPED,
        TurnStatus.FAILED,
    ],
)
def test_explicit_recovery_reconciles_legacy_resultless_confirmed_states_without_execution(
    tmp_path: Path, status: TurnStatus
):
    store = _store(tmp_path)
    _, turn, confirmation = _pending_confirmation(store)
    with store._connect() as db:
        db.execute(
            "update workbench_confirmations set status='confirmed' where id=?",
            (confirmation.id,),
        )
        db.execute(
            """
            update workbench_turns
            set status=?, lease_owner=?, lease_expires_at=?, completed_at=?
            where id=?
            """,
            (
                status.value,
                "dead-worker" if status is TurnStatus.RUNNING else "",
                "2099-01-01 00:00:00" if status is TurnStatus.RUNNING else "",
                "2026-08-13 00:00:02"
                if status
                in {TurnStatus.COMPLETED, TurnStatus.STOPPED, TurnStatus.FAILED}
                else "",
                turn.id,
            ),
        )
    runtime = FakeRuntime()
    writer_calls = []

    executor = WorkbenchExecutor(
        WorkbenchStore(store.path),
        RuntimeRegistry([runtime]),
        workspace=tmp_path,
        write_runner=lambda *args, **kwargs: writer_calls.append((args, kwargs)),
    )

    assert (
        store.get_confirmation(confirmation.id).status is ConfirmationStatus.CONFIRMED
    )
    executor.recover()

    confirmation_after = store.get_confirmation(confirmation.id)
    turn_after = store.get_turn(turn.id)
    assert confirmation_after.status is ConfirmationStatus.FAILED
    assert json.loads(confirmation_after.result_json) == {
        "code": "confirmation_execution_ambiguous",
        "retryable": False,
        "status": "failed",
    }
    if status in {
        TurnStatus.QUEUED,
        TurnStatus.RUNNING,
        TurnStatus.WAITING_CONFIRMATION,
    }:
        assert turn_after.status is TurnStatus.FAILED
        assert store.events_after(turn.id)[-1].event_type == "turn_failed"
    else:
        assert turn_after.status is status
        assert store.events_after(turn.id)[-1].event_type == "status_changed"
    assert runtime.requests == []
    assert writer_calls == []
    assert executor.run_once() == []
    executor.close()


def test_claim_and_run_once_defend_against_unreconciled_confirmed_action(
    tmp_path: Path,
):
    store = _store(tmp_path)
    _, turn, confirmation = _pending_confirmation(store)
    executor = WorkbenchExecutor(
        store, RuntimeRegistry([FakeRuntime()]), workspace=tmp_path
    )
    with store._connect() as db:
        db.execute(
            "update workbench_confirmations set status='confirmed' where id=?",
            (confirmation.id,),
        )
        db.execute("update workbench_turns set status='queued' where id=?", (turn.id,))

    assert store.claim_next_turn(owner="other-worker") is None
    assert executor.run_once() == []
    assert store.get_turn(turn.id).status is TurnStatus.QUEUED
    assert (
        store.get_confirmation(confirmation.id).status is ConfirmationStatus.CONFIRMED
    )
    executor.close()


def test_live_confirmation_claim_survives_other_executor_recovery_and_runs_once(
    tmp_path: Path, monkeypatch
):
    store = _store(tmp_path)
    _, _, confirmation = _pending_confirmation(store)
    monkeypatch.setattr(agent_cli.shutil, "which", lambda _: "/usr/local/bin/dws")
    entered = threading.Event()
    release = threading.Event()
    calls = 0

    def runner(argv, **_):
        nonlocal calls
        calls += 1
        entered.set()
        assert release.wait(5)
        return subprocess.CompletedProcess(argv, 0, "ok", "")

    first = WorkbenchExecutor(
        WorkbenchStore(store.path),
        RuntimeRegistry(),
        workspace=tmp_path,
        classifier=_write_classifier(),
        write_runner=runner,
        confirmation_lease_seconds=5,
        confirmation_heartbeat_interval_seconds=0.05,
    )
    second = WorkbenchExecutor(
        WorkbenchStore(store.path),
        RuntimeRegistry(),
        workspace=tmp_path,
        classifier=_write_classifier(),
        write_runner=runner,
    )
    with ThreadPoolExecutor(max_workers=2) as pool:
        first_result = pool.submit(first.confirm, confirmation.id)
        assert entered.wait(2)
        duplicate = pool.submit(second.confirm, confirmation.id).result(2)
        assert duplicate.status is ConfirmationStatus.CONFIRMED
        assert second.run_once() == []
        assert second.recover() == 0
        assert (
            store.get_confirmation(confirmation.id).status
            is ConfirmationStatus.CONFIRMED
        )
        release.set()
        assert first_result.result(3).status is ConfirmationStatus.EXECUTED
    assert calls == 1
    first.close()
    second.close()


def test_stop_during_blocked_writer_keeps_turn_stopped_and_persists_receipt(
    tmp_path: Path, monkeypatch
):
    store = _store(tmp_path)
    _, turn, confirmation = _pending_confirmation(store)
    monkeypatch.setattr(agent_cli.shutil, "which", lambda _: "/usr/local/bin/dws")
    entered = threading.Event()
    release = threading.Event()

    def runner(argv, **_):
        entered.set()
        assert release.wait(5)
        return subprocess.CompletedProcess(argv, 0, "ok", "")

    executor = WorkbenchExecutor(
        store,
        RuntimeRegistry(),
        workspace=tmp_path,
        classifier=_write_classifier(),
        write_runner=runner,
    )
    with ThreadPoolExecutor(max_workers=1) as pool:
        result = pool.submit(executor.confirm, confirmation.id)
        assert entered.wait(2)
        assert executor.stop(turn.id).status is TurnStatus.STOPPED
        assert (
            store.get_confirmation(confirmation.id).status
            is ConfirmationStatus.CONFIRMED
        )
        release.set()
        assert result.result(3).status is ConfirmationStatus.EXECUTED
    assert store.get_turn(turn.id).status is TurnStatus.STOPPED
    assert store.get_confirmation(confirmation.id).status is ConfirmationStatus.EXECUTED
    executor.close()


def test_confirmation_heartbeat_prevents_short_lease_recovery(
    tmp_path: Path, monkeypatch
):
    store = _store(tmp_path)
    _, _, confirmation = _pending_confirmation(store)
    monkeypatch.setattr(agent_cli.shutil, "which", lambda _: "/usr/local/bin/dws")
    entered = threading.Event()
    release = threading.Event()

    def runner(argv, **_):
        entered.set()
        assert release.wait(5)
        return subprocess.CompletedProcess(argv, 0, "ok", "")

    first = WorkbenchExecutor(
        store,
        RuntimeRegistry(),
        workspace=tmp_path,
        classifier=_write_classifier(),
        write_runner=runner,
        confirmation_lease_seconds=1,
        confirmation_heartbeat_interval_seconds=0.05,
    )
    second = WorkbenchExecutor(
        WorkbenchStore(store.path), RuntimeRegistry(), workspace=tmp_path
    )
    with ThreadPoolExecutor(max_workers=1) as pool:
        result = pool.submit(first.confirm, confirmation.id)
        assert entered.wait(2)
        time.sleep(1.2)
        assert second.recover() == 0
        release.set()
        assert result.result(3).status is ConfirmationStatus.EXECUTED
    first.close()
    second.close()


def test_lost_confirmation_lease_never_persists_writer_result(
    tmp_path: Path, monkeypatch
):
    class LostClaimStore(WorkbenchStore):
        def renew_confirmation_execution_lease(self, *args, **kwargs):
            raise ValueError("confirmation execution lease is stale")

    store = LostClaimStore(tmp_path / "workbench.sqlite3")
    _, _, confirmation = _pending_confirmation(store)
    monkeypatch.setattr(agent_cli.shutil, "which", lambda _: "/usr/local/bin/dws")
    entered = threading.Event()
    release = threading.Event()
    calls = 0

    def runner(argv, **_):
        nonlocal calls
        calls += 1
        entered.set()
        assert release.wait(5)
        return subprocess.CompletedProcess(argv, 0, "ok", "")

    executor = WorkbenchExecutor(
        store,
        RuntimeRegistry(),
        workspace=tmp_path,
        classifier=_write_classifier(),
        write_runner=runner,
        confirmation_lease_seconds=1,
        confirmation_heartbeat_interval_seconds=0.01,
    )
    with ThreadPoolExecutor(max_workers=1) as pool:
        result = pool.submit(executor.confirm, confirmation.id)
        assert entered.wait(2)
        time.sleep(0.05)
        release.set()
        assert result.result(3).status is ConfirmationStatus.CONFIRMED
    assert calls == 1
    assert store.get_confirmation(confirmation.id).result_json == ""
    assert executor.recover(now="2099-01-01T00:00:00Z") == 1
    assert store.get_confirmation(confirmation.id).status is ConfirmationStatus.FAILED
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
