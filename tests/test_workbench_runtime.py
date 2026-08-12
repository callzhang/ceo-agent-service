import json
from collections.abc import Iterable, Mapping
from dataclasses import FrozenInstanceError
from pathlib import Path
from typing import Any

import pytest

from app.workbench.runtime import (
    AgentRuntime,
    RuntimeCapabilities,
    RuntimeEvent,
    RuntimeHandle,
    RuntimeRegistry,
    RuntimeRequest,
    RuntimeResult,
)


FIXTURE_DIR = Path(__file__).parent / "fixtures" / "workbench_runtime"
EXPECTED_EVENT_TYPES = [
    "text_delta",
    "tool_started",
    "tool_completed",
    "text_delta",
    "turn_completed",
]
_SESSION_REFERENCE_KEYS = {
    "session",
    "session_id",
    "thread",
    "thread_id",
    "conversation",
    "conversation_id",
    "resume_id",
}


class FixtureRuntime:
    kind = "fixture"

    def capabilities(self) -> RuntimeCapabilities:
        return RuntimeCapabilities(
            session_resume=True,
            streamed_text=True,
            structured_tools=True,
            image_input=False,
            model_selection=False,
            mcp_configuration=False,
            stoppable=True,
            recoverable=True,
        )

    def start(
        self,
        request: RuntimeRequest,
        *,
        on_event: object,
    ) -> RuntimeHandle:
        raise AssertionError("fixture runtime is not executable")

    def wait(self, handle: RuntimeHandle) -> RuntimeResult:
        raise AssertionError("fixture runtime is not executable")

    def stop(self, handle: RuntimeHandle) -> None:
        raise AssertionError("fixture runtime is not executable")


def test_registry_resolves_only_registered_runtime():
    runtime: AgentRuntime = FixtureRuntime()
    registry = RuntimeRegistry([runtime])

    assert registry.get("fixture").kind == "fixture"
    with pytest.raises(KeyError, match="unsupported runtime"):
        registry.get("unknown")


def test_runtime_event_rejects_provider_native_event_name():
    with pytest.raises(ValueError, match="unsupported runtime event type"):
        RuntimeEvent(event_type="item.completed", payload={})


def test_runtime_event_defensively_freezes_payload():
    source_payload = {"text": "Hello", "metadata": {"lines": ["one"]}}

    event = RuntimeEvent(event_type="text_delta", payload=source_payload)
    source_payload["text"] = "Changed"
    source_payload["metadata"]["lines"].append("two")

    assert event.payload["text"] == "Hello"
    assert event.payload["metadata"]["lines"] == ("one",)
    with pytest.raises(TypeError):
        event.payload["text"] = "Nope"  # type: ignore[index]
    with pytest.raises(FrozenInstanceError):
        event.event_type = "turn_completed"  # type: ignore[misc]


def test_runtime_request_converts_path_collections_to_tuples(tmp_path: Path):
    request = RuntimeRequest(
        turn_id="turn-1",
        workspace=tmp_path,
        prompt="Summarize the report",
        attachment_paths=[tmp_path / "report.txt"],
        image_paths=[tmp_path / "chart.png"],
    )

    assert request.attachment_paths == (tmp_path / "report.txt",)
    assert request.image_paths == (tmp_path / "chart.png",)
    with pytest.raises(FrozenInstanceError):
        request.prompt = "Changed"  # type: ignore[misc]


def test_runtime_result_rejects_non_terminal_status():
    with pytest.raises(ValueError, match="unsupported runtime result status"):
        RuntimeResult(status="running")


@pytest.mark.parametrize("kind", ["", "   "])
def test_registry_rejects_blank_runtime_kind(kind: str):
    class BlankRuntime(FixtureRuntime):
        pass

    BlankRuntime.kind = kind

    with pytest.raises(ValueError, match="runtime kind must not be blank"):
        RuntimeRegistry([BlankRuntime()])


def test_registry_rejects_duplicate_runtime_kind():
    with pytest.raises(ValueError, match="duplicate runtime kind"):
        RuntimeRegistry([FixtureRuntime(), FixtureRuntime()])


def test_runtime_handle_keeps_owner_private_from_public_representation():
    owner = object()
    handle = RuntimeHandle(run_id="run-1", _owner=owner)

    assert handle.run_id == "run-1"
    assert not hasattr(handle, "owner")
    assert "owner" not in repr(handle)
    assert not hasattr(handle, "to_dict")


def _read_jsonl(provider: str) -> Iterable[dict[str, Any]]:
    fixture_path = FIXTURE_DIR / f"{provider}.jsonl"
    return [json.loads(line) for line in fixture_path.read_text().splitlines() if line]


def _normalize_codex(records: Iterable[dict[str, Any]]) -> list[RuntimeEvent]:
    events: list[RuntimeEvent] = []
    for record in records:
        item = record.get("item", {})
        if record["type"] == "item.delta" and item.get("type") == "assistant_message":
            events.append(RuntimeEvent("text_delta", {"text": item["text"]}))
        elif record["type"] == "item.started" and item.get("type") == "command_execution":
            events.append(
                RuntimeEvent("tool_started", {"tool": "command", "summary": item["command"]})
            )
        elif record["type"] == "item.completed" and item.get("type") == "command_execution":
            events.append(
                RuntimeEvent("tool_completed", {"tool": "command", "summary": "Command finished"})
            )
        elif record["type"] == "turn.completed":
            events.append(RuntimeEvent("turn_completed", {"summary": "Run completed"}))
    return events


def _normalize_claude(records: Iterable[dict[str, Any]]) -> list[RuntimeEvent]:
    events: list[RuntimeEvent] = []
    for record in records:
        if record["type"] == "assistant":
            for content in record["message"]["content"]:
                if content["type"] == "text":
                    events.append(RuntimeEvent("text_delta", {"text": content["text"]}))
                elif content["type"] == "tool_use":
                    events.append(
                        RuntimeEvent(
                            "tool_started",
                            {"tool": content["name"], "summary": "Tool started"},
                        )
                    )
        elif record["type"] == "user":
            for content in record["message"]["content"]:
                if content["type"] == "tool_result":
                    events.append(
                        RuntimeEvent(
                            "tool_completed",
                            {"tool": "Bash", "summary": "Tool finished"},
                        )
                    )
        elif record["type"] == "result":
            events.append(RuntimeEvent("turn_completed", {"summary": "Run completed"}))
    return events


def _normalize_pi(records: Iterable[dict[str, Any]]) -> list[RuntimeEvent]:
    events: list[RuntimeEvent] = []
    for record in records:
        if record["event"] == "message_update":
            events.append(RuntimeEvent("text_delta", {"text": record["delta"]["content"]}))
        elif record["event"] == "tool_call":
            events.append(
                RuntimeEvent(
                    "tool_started",
                    {"tool": record["call"]["toolName"], "summary": "Tool started"},
                )
            )
        elif record["event"] == "tool_result":
            events.append(
                RuntimeEvent("tool_completed", {"tool": "bash", "summary": "Tool finished"})
            )
        elif record["event"] == "agent_end":
            events.append(RuntimeEvent("turn_completed", {"summary": "Run completed"}))
    return events


_FIXTURE_NORMALIZERS = {
    "codex": _normalize_codex,
    "claude": _normalize_claude,
    "pi": _normalize_pi,
}


@pytest.fixture
def runtime_fixture():
    def load(provider: str) -> list[RuntimeEvent]:
        return _FIXTURE_NORMALIZERS[provider](_read_jsonl(provider))

    return load


def _assert_no_provider_session_reference(payload: Mapping[str, Any]) -> None:
    for key, value in payload.items():
        assert key.lower() not in _SESSION_REFERENCE_KEYS
        if isinstance(value, Mapping):
            _assert_no_provider_session_reference(value)
        elif isinstance(value, tuple):
            for item in value:
                if isinstance(item, Mapping):
                    _assert_no_provider_session_reference(item)


@pytest.mark.parametrize("provider", ["codex", "claude", "pi"])
def test_provider_fixture_contract(provider: str, runtime_fixture):
    events = runtime_fixture(provider)

    assert [event.event_type for event in events] == EXPECTED_EVENT_TYPES
    for event in events:
        _assert_no_provider_session_reference(event.payload)
