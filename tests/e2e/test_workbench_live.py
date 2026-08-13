"""Deterministic end-to-end coverage for the local Agent workbench."""

from __future__ import annotations

import asyncio
import queue
import subprocess
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from fastapi.testclient import TestClient

import app.agent_cli as agent_cli
from app.agent_result import EffectKind
from app.audit_web import create_audit_app
from app.native_cli_metadata import NativeCliMetadataClassifier
from app.setup_wizard import SETUP_WIZARD_STEPS
from app.workbench.executor import WorkbenchExecutor
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


_SAFE_PROMPT = "Run the deterministic safe workflow"
_STOP_PROMPT = "Run until I stop this turn"
_CONFIRM_PROMPT = "Prepare the reviewed action"


@dataclass
class _Run:
    turn_id: str
    mode: str
    on_event: Callable[[RuntimeEvent], None]
    release: threading.Event = field(default_factory=threading.Event)


class FixtureRuntime:
    kind = "fixture"

    def __init__(self) -> None:
        self.safe_wait_entered = threading.Event()
        self.stop_wait_entered = threading.Event()
        self.confirmation_wait_entered = threading.Event()
        self.stop_calls: dict[str, int] = {}
        self.requests: list[RuntimeRequest] = []
        self._runs: dict[RuntimeHandle, _Run] = {}
        self._lock = threading.Lock()

    def capabilities(self) -> RuntimeCapabilities:
        return RuntimeCapabilities(
            session_resume=True,
            streamed_text=True,
            structured_tools=True,
            image_input=True,
            model_selection=True,
            mcp_configuration=True,
            stoppable=True,
            recoverable=True,
        )

    def start(self, request: RuntimeRequest, *, on_event) -> RuntimeHandle:
        self.requests.append(request)
        mode = self._mode(request.prompt)
        run = _Run(turn_id=request.turn_id, mode=mode, on_event=on_event)
        handle = RuntimeHandle.create(
            run_id=f"fixture-{request.turn_id}-{len(self.requests)}", owner=run
        )
        with self._lock:
            self._runs[handle] = run
        try:
            if mode == "safe":
                on_event(RuntimeEvent("text_delta", {"text": "Hello "}))
                on_event(
                    RuntimeEvent(
                        "tool_started",
                        {
                            "tool": "fixture_reader",
                            "tool_call_id": "read-1",
                            "summary": "Read local fixture",
                        },
                    )
                )
                on_event(
                    RuntimeEvent(
                        "tool_completed",
                        {
                            "tool": "fixture_reader",
                            "tool_call_id": "read-1",
                            "summary": "Fixture read complete",
                        },
                    )
                )
            elif mode == "confirmation":
                on_event(
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
                                "fixture-group",
                                "--text",
                                "fixture update",
                                "--yes",
                            ],
                            "target": "Untrusted display target",
                            "summary": "Send the fixture update",
                            "risk": "Writes to a fake external channel",
                            "executed": False,
                        },
                    )
                )
            elif mode == "resumed":
                on_event(
                    RuntimeEvent("text_delta", {"text": "Action receipt accepted."})
                )
        except BaseException:
            with self._lock:
                self._runs.pop(handle, None)
            _release_runtime_owner(handle)
            raise
        return handle

    def wait(self, handle: RuntimeHandle) -> RuntimeResult:
        with self._lock:
            run = self._runs[handle]
        try:
            if run.mode == "safe":
                self.safe_wait_entered.set()
                assert run.release.wait(5), "safe runtime was not released"
                run.on_event(RuntimeEvent("text_delta", {"text": "workbench"}))
                return RuntimeResult(
                    status="completed",
                    final_text="Hello workbench",
                    provider_session_ref="fixture-session",
                )
            if run.mode == "stop":
                self.stop_wait_entered.set()
                assert run.release.wait(5), "stop signal was not delivered"
                return RuntimeResult(status="stopped")
            if run.mode == "confirmation":
                self.confirmation_wait_entered.set()
                assert run.release.wait(5), "confirmation run did not quiesce"
                return RuntimeResult(status="stopped")
            return RuntimeResult(
                status="completed", final_text="Reviewed action completed"
            )
        finally:
            with self._lock:
                self._runs.pop(handle, None)
            _release_runtime_owner(handle)

    def stop(self, handle: RuntimeHandle) -> None:
        with self._lock:
            run = self._runs[handle]
            self.stop_calls[run.turn_id] = self.stop_calls.get(run.turn_id, 0) + 1
        if run.mode == "stop":
            run.release.set()

    def release(self, turn_id: str) -> None:
        with self._lock:
            runs = tuple(run for run in self._runs.values() if run.turn_id == turn_id)
        assert len(runs) == 1, f"expected one active fixture run for {turn_id}"
        runs[0].release.set()

    def release_all(self) -> None:
        with self._lock:
            runs = tuple(self._runs.values())
        for run in runs:
            run.release.set()

    def assert_clean(self) -> None:
        with self._lock:
            assert self._runs == {}

    @staticmethod
    def _mode(prompt: str) -> str:
        if "Resume context:" in prompt:
            return "resumed"
        if prompt == _SAFE_PROMPT:
            return "safe"
        if prompt == _STOP_PROMPT:
            return "stop"
        if prompt == _CONFIRM_PROMPT:
            return "confirmation"
        raise AssertionError(f"unexpected fixture prompt: {prompt}")


class _AsgiEventStream:
    """Consume streaming HTTP chunks without TestClient's response buffering."""

    def __init__(self, app: Any, path: str) -> None:
        target = urlsplit(path)
        self._app = app
        self._path = target.path
        self._query = target.query.encode("ascii")
        self._disconnect = threading.Event()
        self._messages: queue.Queue[dict[str, Any] | BaseException | None] = (
            queue.Queue()
        )
        self._thread = threading.Thread(
            target=self._run, name="workbench-e2e-asgi-stream", daemon=True
        )
        self._thread.start()

    def _run(self) -> None:
        async def request() -> None:
            request_sent = False

            async def receive() -> dict[str, Any]:
                nonlocal request_sent
                if not request_sent:
                    request_sent = True
                    return {"type": "http.request", "body": b"", "more_body": False}
                while not self._disconnect.is_set():
                    await asyncio.sleep(0.005)
                return {"type": "http.disconnect"}

            async def send(message: dict[str, Any]) -> None:
                self._messages.put(message)

            scope = {
                "type": "http",
                "asgi": {"version": "3.0", "spec_version": "2.3"},
                "http_version": "1.1",
                "method": "GET",
                "scheme": "http",
                "path": self._path,
                "raw_path": self._path.encode("ascii"),
                "query_string": self._query,
                "root_path": "",
                "headers": [(b"host", b"127.0.0.1:8765")],
                "client": ("127.0.0.1", 50001),
                "server": ("127.0.0.1", 8765),
                "state": {},
            }
            await self._app(scope, receive, send)

        try:
            asyncio.run(request())
        except BaseException as exc:
            self._messages.put(exc)
        finally:
            self._messages.put(None)

    def read_event_ids(self, minimum: int, *, timeout: float = 2) -> tuple[str, list[int]]:
        deadline = time.monotonic() + timeout
        status = 0
        body = bytearray()
        while True:
            remaining = deadline - time.monotonic()
            assert remaining > 0, "timed out waiting for live SSE data"
            message = self._messages.get(timeout=remaining)
            if isinstance(message, BaseException):
                raise message
            assert message is not None, "SSE stream ended before live events arrived"
            if message["type"] == "http.response.start":
                status = message["status"]
            elif message["type"] == "http.response.body":
                body.extend(message.get("body", b""))
            text = body.decode("utf-8")
            event_ids = _sse_ids(text)
            if status and len(event_ids) >= minimum:
                assert status == 200
                return text, event_ids

    def close(self) -> None:
        self._disconnect.set()
        self._thread.join(timeout=2)
        assert not self._thread.is_alive(), "live SSE request did not disconnect"
        while not self._messages.empty():
            message = self._messages.get_nowait()
            if isinstance(message, BaseException):
                raise message


def _wait_for_json(client: TestClient, path: str, predicate, *, timeout: float = 5):
    deadline = time.monotonic() + timeout
    while True:
        response = client.get(path)
        assert response.status_code == 200
        payload = response.json()
        if predicate(payload):
            return payload
        remaining = deadline - time.monotonic()
        assert remaining > 0, f"timed out waiting for {path}: {payload}"
        threading.Event().wait(min(0.01, remaining))


def _wait_for(predicate, *, message: str, timeout: float = 2) -> None:
    deadline = time.monotonic() + timeout
    while not predicate():
        remaining = deadline - time.monotonic()
        assert remaining > 0, message
        threading.Event().wait(min(0.01, remaining))


def _sse_ids(body: str) -> list[int]:
    return [
        int(line.removeprefix("id: "))
        for line in body.splitlines()
        if line.startswith("id: ")
    ]


def _complete_setup(store: WorkbenchStore) -> None:
    for step in SETUP_WIZARD_STEPS:
        store.upsert_setup_wizard_step(
            step_id=step.id, status="done", summary="fixture setup complete"
        )


def test_workbench_stream_stop_and_reviewed_confirmation_are_end_to_end(
    tmp_path: Path, monkeypatch
) -> None:
    db_path = tmp_path / "workbench.sqlite3"
    store = WorkbenchStore(db_path)
    _complete_setup(store)
    runtime = FixtureRuntime()
    registry = RuntimeRegistry([runtime])
    writer_calls: list[list[str]] = []
    classifier = NativeCliMetadataClassifier(
        reviewed_effects={("dws", "chat message send"): EffectKind.EFFECTFUL}
    )
    monkeypatch.setattr(agent_cli.shutil, "which", lambda _: "/fixture/bin/dws")

    def fake_writer(argv, **_kwargs):
        writer_calls.append(list(argv))
        return subprocess.CompletedProcess(argv, 0, "fixture write complete", "")

    executor = WorkbenchExecutor(
        store,
        registry,
        workspace=tmp_path,
        classifier=classifier,
        write_runner=fake_writer,
    )
    app = create_audit_app(
        db_path,
        workbench_asset_dir=tmp_path / "missing-assets",
        workbench_workspace=tmp_path,
        workbench_runtime_registry=registry,
        workbench_executor=executor,
        workbench_scheduler_interval_seconds=0.01,
    )

    with TestClient(
        app,
        client=("127.0.0.1", 50000),
        headers={"Host": "127.0.0.1:8765"},
    ) as client:
        try:
            task_response = client.post(
                "/api/workbench/tasks",
                json={"title": "Deterministic workflow", "runtime_kind": "fixture"},
            )
            assert task_response.status_code == 201
            task_id = task_response.json()["id"]

            safe_turn = client.post(
                f"/api/workbench/tasks/{task_id}/turns",
                json={"text": _SAFE_PROMPT, "client_request_id": "live-safe"},
            )
            assert safe_turn.status_code == 201
            safe_turn_id = safe_turn.json()["id"]
            assert runtime.safe_wait_entered.wait(2)
            running = client.get(f"/api/workbench/turns/{safe_turn_id}")
            assert running.json()["status"] == "running"

            live_stream = _AsgiEventStream(
                app,
                f"/api/workbench/turns/{safe_turn_id}/events/stream?after=0",
            )
            try:
                _wait_for(
                    lambda: app.state.workbench_event_broker.subscriber_count == 1,
                    message="SSE endpoint did not attach its live broker subscriber",
                )
                live_text, live_ids = live_stream.read_event_ids(4)
                assert "event: turn_completed" not in live_text
                assert len(live_ids) == len(set(live_ids))
                cursor = live_ids[-1]
                assert client.get(
                    f"/api/workbench/turns/{safe_turn_id}"
                ).json()["status"] == "running"
            finally:
                live_stream.close()
            _wait_for(
                lambda: app.state.workbench_event_broker.subscriber_count == 0,
                message="disconnected SSE subscriber was not removed",
            )

            runtime.release(safe_turn_id)
            safe_terminal = _wait_for_json(
                client,
                f"/api/workbench/turns/{safe_turn_id}",
                lambda payload: payload["status"] == "completed",
            )
            assert safe_terminal["final_text"] == "Hello workbench"

            reconnected_stream = client.get(
                f"/api/workbench/turns/{safe_turn_id}/events/stream",
                headers={"Last-Event-ID": str(cursor)},
            )
            replay = client.get(
                f"/api/workbench/turns/{safe_turn_id}/events",
                params={"after": cursor, "limit": 100},
            )
            reconnect_ids = _sse_ids(reconnected_stream.text)
            replay_ids = [event["id"] for event in replay.json()]
            assert reconnect_ids == replay_ids
            assert reconnect_ids and all(event_id > cursor for event_id in reconnect_ids)
            all_ids = live_ids + reconnect_ids
            assert len(all_ids) == len(set(all_ids))
            persisted = client.get(
                f"/api/workbench/turns/{safe_turn_id}/events",
                params={"after": 0, "limit": 100},
            ).json()
            assert [event["id"] for event in persisted] == all_ids
            assert [event["event_type"] for event in persisted] == [
                "status_changed",
                "text_delta",
                "tool_started",
                "tool_completed",
                "text_delta",
                "turn_completed",
            ]
            tool_events = persisted[2:4]
            assert {event["payload"]["tool_call_id"] for event in tool_events} == {
                "read-1"
            }

            stop_turn = client.post(
                f"/api/workbench/tasks/{task_id}/turns",
                json={"text": _STOP_PROMPT, "client_request_id": "live-stop"},
            )
            assert stop_turn.status_code == 201
            stop_turn_id = stop_turn.json()["id"]
            assert runtime.stop_wait_entered.wait(2)
            stopped = client.post(
                f"/api/workbench/tasks/{task_id}/turns/{stop_turn_id}/stop", json={}
            )
            assert stopped.status_code == 200
            _wait_for_json(
                client,
                f"/api/workbench/turns/{stop_turn_id}",
                lambda payload: payload["status"] == "stopped",
            )
            duplicate_stop = client.post(
                f"/api/workbench/tasks/{task_id}/turns/{stop_turn_id}/stop", json={}
            )
            assert duplicate_stop.status_code == 200
            assert runtime.stop_calls[stop_turn_id] == 1
            stop_events = client.get(
                f"/api/workbench/turns/{stop_turn_id}/events", params={"after": 0}
            ).json()
            assert [event["event_type"] for event in stop_events].count(
                "turn_completed"
            ) == 1

            confirm_turn = client.post(
                f"/api/workbench/tasks/{task_id}/turns",
                json={"text": _CONFIRM_PROMPT, "client_request_id": "live-confirm"},
            )
            assert confirm_turn.status_code == 201
            confirm_turn_id = confirm_turn.json()["id"]
            assert runtime.confirmation_wait_entered.wait(2)
            waiting_timeline = _wait_for_json(
                client,
                f"/api/workbench/tasks/{task_id}/timeline",
                lambda payload: (
                    next(
                        turn
                        for turn in payload["turns"]
                        if turn["id"] == confirm_turn_id
                    )["status"]
                    == "waiting_confirmation"
                    and len(payload["confirmations"]) == 1
                ),
            )
            confirmation = waiting_timeline["confirmations"][0]
            confirmation_id = confirmation["id"]
            assert confirmation["target"] == "group=fixture-group"
            assert confirmation["summary"].startswith("[Untrusted agent description]")
            assert confirmation["proposer_quiesced"] is False

            confirm_path = (
                f"/api/workbench/tasks/{task_id}/turns/{confirm_turn_id}"
                f"/confirmations/{confirmation_id}/confirm"
            )
            requested = client.post(confirm_path, json={})
            duplicate_before_quiescence = client.post(confirm_path, json={})
            assert (
                requested.status_code
                == duplicate_before_quiescence.status_code
                == 200
            )
            assert requested.json()["status"] == "pending"
            assert requested.json()["decision_requested"] == "confirm"
            assert writer_calls == []

            runtime.release(confirm_turn_id)
            confirmed_terminal = _wait_for_json(
                client,
                f"/api/workbench/turns/{confirm_turn_id}",
                lambda payload: payload["status"] == "completed",
            )
            assert confirmed_terminal["final_text"] == "Reviewed action completed"
            duplicate_after_execution = client.post(confirm_path, json={})
            assert duplicate_after_execution.status_code == 200
            assert duplicate_after_execution.json()["status"] == "executed"
            assert writer_calls == [
                [
                    "/fixture/bin/dws",
                    "chat",
                    "message",
                    "send",
                    "--group",
                    "fixture-group",
                    "--text",
                    "fixture update",
                    "--yes",
                ]
            ]
            assert (
                [request.turn_id for request in runtime.requests].count(confirm_turn_id)
                == 2
            )
            confirm_events = client.get(
                f"/api/workbench/turns/{confirm_turn_id}/events", params={"after": 0}
            ).json()
            assert [event["event_type"] for event in confirm_events].count(
                "turn_completed"
            ) == 1
        finally:
            runtime.release_all()

    assert app.state.workbench_shutdown_complete is True
    runtime.assert_clean()
