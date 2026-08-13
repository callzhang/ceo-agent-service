"""Deterministic end-to-end coverage for the local Agent workbench."""

from __future__ import annotations

import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

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
    release: threading.Event = field(default_factory=threading.Event)


class FixtureRuntime:
    kind = "fixture"

    def __init__(self) -> None:
        self.stop_wait_entered = threading.Event()
        self.confirmation_wait_entered = threading.Event()
        self.confirmation_release = threading.Event()
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
        run = _Run(turn_id=request.turn_id, mode=mode)
        handle = RuntimeHandle.create(
            run_id=f"fixture-{request.turn_id}-{len(self.requests)}", owner=run
        )
        self._runs[handle] = run
        if mode == "safe":
            on_event(RuntimeEvent("text_delta", {"text": "Hello "}))
            on_event(RuntimeEvent("text_delta", {"text": "workbench"}))
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
            on_event(RuntimeEvent("text_delta", {"text": "Action receipt accepted."}))
        return handle

    def wait(self, handle: RuntimeHandle) -> RuntimeResult:
        run = self._runs[handle]
        if run.mode == "stop":
            self.stop_wait_entered.set()
            assert run.release.wait(5), "stop signal was not delivered"
        elif run.mode == "confirmation":
            self.confirmation_wait_entered.set()
            assert self.confirmation_release.wait(5), "confirmation run did not quiesce"
        self._runs.pop(handle)
        _release_runtime_owner(handle)
        if run.mode in {"stop", "confirmation"}:
            return RuntimeResult(status="stopped")
        if run.mode == "safe":
            return RuntimeResult(
                status="completed",
                final_text="Hello workbench",
                provider_session_ref="fixture-session",
            )
        return RuntimeResult(status="completed", final_text="Reviewed action completed")

    def stop(self, handle: RuntimeHandle) -> None:
        run = self._runs[handle]
        with self._lock:
            self.stop_calls[run.turn_id] = self.stop_calls.get(run.turn_id, 0) + 1
        if run.mode == "stop":
            run.release.set()

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
        safe_terminal = _wait_for_json(
            client,
            f"/api/workbench/turns/{safe_turn_id}",
            lambda payload: payload["status"] == "completed",
        )
        assert safe_terminal["final_text"] == "Hello workbench"

        initial_stream = client.get(
            f"/api/workbench/turns/{safe_turn_id}/events/stream?after=0"
        )
        assert initial_stream.status_code == 200
        all_ids = _sse_ids(initial_stream.text)
        cursor = all_ids[2]
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
        stitched_ids = [
            event_id for event_id in all_ids if event_id <= cursor
        ] + reconnect_ids
        assert reconnect_ids == replay_ids == [
            event_id for event_id in all_ids if event_id > cursor
        ]
        assert stitched_ids == all_ids
        assert len(all_ids) == len(set(all_ids))
        persisted = client.get(
            f"/api/workbench/turns/{safe_turn_id}/events",
            params={"after": 0, "limit": 100},
        ).json()
        assert [event["event_type"] for event in persisted] == [
            "status_changed",
            "text_delta",
            "text_delta",
            "tool_started",
            "tool_completed",
            "turn_completed",
        ]
        tool_events = persisted[3:5]
        assert {event["payload"]["tool_call_id"] for event in tool_events} == {"read-1"}

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
        assert [event["event_type"] for event in stop_events].count("turn_completed") == 1

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
                    turn for turn in payload["turns"] if turn["id"] == confirm_turn_id
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

        runtime.confirmation_release.set()
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
        assert [event["event_type"] for event in confirm_events].count("turn_completed") == 1

    assert app.state.workbench_shutdown_complete is True
