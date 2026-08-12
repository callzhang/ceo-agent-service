import gc
import json
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import FrozenInstanceError, asdict
from pathlib import Path
from typing import Any
from weakref import ref

import pytest
from fastapi.encoders import jsonable_encoder

from app.workbench.runtime import (
    AgentRuntime,
    RuntimeCapabilities,
    RuntimeEvent,
    RuntimeHandle,
    RuntimeRegistry,
    RuntimeRequest,
    RuntimeResult,
    _release_runtime_owner,
    _runtime_owner,
)


FIXTURE_DIR = Path(__file__).parent / "fixtures" / "workbench_runtime"
EXPECTED_EVENT_TYPES = [
    "text_delta",
    "tool_started",
    "tool_completed",
    "text_delta",
    "turn_completed",
]
_SESSION_REFERENCE_KEY_STEMS = {
    "session",
    "sessionid",
    "thread",
    "threadid",
    "conversation",
    "conversationid",
    "resumeid",
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
    with pytest.raises(TypeError):
        event.payload["metadata"]["lines"] = ()  # type: ignore[index]
    with pytest.raises(FrozenInstanceError):
        event.event_type = "turn_completed"  # type: ignore[misc]


def test_runtime_event_projects_immutable_payload_as_plain_json():
    event = RuntimeEvent(
        event_type="tool_completed",
        payload={"tool": "shell", "details": {"lines": ["one", "two"]}},
    )

    projection = event.payload_json_value()

    assert projection == {
        "tool": "shell",
        "details": {"lines": ["one", "two"]},
    }
    assert json.loads(json.dumps(projection)) == projection
    assert jsonable_encoder(projection) == projection
    projection["details"]["lines"].append("three")
    assert event.payload_json_value()["details"]["lines"] == ["one", "two"]


def test_runtime_event_uses_an_immutable_non_dict_payload_mapping():
    first = RuntimeEvent(event_type="text_delta", payload={"text": "Hello"})
    second = RuntimeEvent(event_type="text_delta", payload={"text": "Hello"})

    assert first == second
    assert not isinstance(first.payload, dict)
    with pytest.raises(TypeError):
        first.payload["text"] = "Changed"  # type: ignore[index]
    with pytest.raises(AttributeError):
        first.payload.update({"text": "Changed"})  # type: ignore[attr-defined]
    with pytest.raises(TypeError):
        dict.__setitem__(first.payload, "text", "Changed")
    with pytest.raises(TypeError, match="unhashable type: 'RuntimeEvent'"):
        hash(first)


def test_runtime_event_rejects_cyclic_payload_containers():
    direct_cycle: dict[str, object] = {}
    direct_cycle["self"] = direct_cycle
    indirect_mapping: dict[str, object] = {}
    indirect_list: list[object] = [indirect_mapping]
    indirect_mapping["items"] = indirect_list

    for payload in (direct_cycle, indirect_mapping):
        with pytest.raises(ValueError, match="cyclic container"):
            RuntimeEvent(event_type="text_delta", payload=payload)


def test_runtime_event_accepts_repeated_non_cyclic_payload_aliases():
    alias = {"label": "shared"}

    event = RuntimeEvent(
        event_type="text_delta",
        payload={"first": alias, "second": alias},
    )

    assert event.payload_json_value() == {
        "first": {"label": "shared"},
        "second": {"label": "shared"},
    }


class _CustomInteger(int):
    pass


@pytest.mark.parametrize(
    "payload",
    [
        {"binary": b"not json"},
        {"binary": bytearray(b"not json")},
        {"values": {"not", "json"}},
        {"nested": {"invalid": object()}},
        {"custom": _CustomInteger(1)},
        {1: "non-string key"},
        {"not_finite": float("nan")},
        {"not_finite": float("inf")},
        {"not_finite": float("-inf")},
    ],
)
def test_runtime_event_rejects_non_json_payload_values(payload: dict[object, object]):
    with pytest.raises(ValueError, match="JSON-compatible"):
        RuntimeEvent(event_type="text_delta", payload=payload)  # type: ignore[arg-type]


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


def test_registry_rejects_runtime_kind_with_surrounding_whitespace():
    class WhitespaceRuntime(FixtureRuntime):
        kind = " codex "

    with pytest.raises(ValueError, match="canonical"):
        RuntimeRegistry([WhitespaceRuntime()])


def test_runtime_handle_keeps_owner_private_from_serialization():
    owner = {"secret": "must not serialize"}
    handle = RuntimeHandle.create(run_id="run-1", owner=owner)

    assert handle.run_id == "run-1"
    assert _runtime_owner(handle) is owner
    assert not hasattr(handle, "owner")
    assert "owner" not in repr(handle)
    assert not hasattr(handle, "to_dict")
    assert asdict(handle) == {"run_id": "run-1"}
    assert jsonable_encoder(handle) == {"run_id": "run-1"}
    with pytest.raises(FrozenInstanceError):
        handle.run_id = "run-2"  # type: ignore[misc]


def test_runtime_handle_requires_factory_with_valid_run_and_owner():
    with pytest.raises(TypeError):
        RuntimeHandle(run_id="run-1")
    with pytest.raises(ValueError, match="run id must not be blank"):
        RuntimeHandle.create(run_id="   ", owner=object())
    with pytest.raises(ValueError, match="owner must not be None"):
        RuntimeHandle.create(run_id="run-1", owner=None)


def test_runtime_handle_release_removes_private_owner_and_breaks_owner_cycle():
    class CyclicOwner:
        handle: RuntimeHandle | None = None

    owner = CyclicOwner()
    handle = RuntimeHandle.create(run_id="run-1", owner=owner)
    owner.handle = handle
    owner_reference = ref(owner)
    handle_reference = ref(handle)

    assert _release_runtime_owner(handle) is owner
    with pytest.raises(ValueError, match="owner is unavailable"):
        _runtime_owner(handle)

    del owner
    del handle
    gc.collect()

    assert owner_reference() is None
    assert handle_reference() is None


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


def _session_key_stem(key: str) -> str:
    return "".join(character for character in key if character.isalnum()).casefold()


def _native_session_values(value: Any) -> set[str]:
    values: set[str] = set()
    if isinstance(value, Mapping):
        for key, item in value.items():
            if isinstance(key, str) and _session_key_stem(key) in _SESSION_REFERENCE_KEY_STEMS:
                if isinstance(item, str):
                    values.add(item)
            values.update(_native_session_values(item))
    elif isinstance(value, Sequence) and not isinstance(value, str):
        for item in value:
            values.update(_native_session_values(item))
    return values


def _assert_no_provider_session_reference(
    payload: Mapping[str, Any], native_session_values: set[str]
) -> None:
    def walk(value: Any) -> None:
        if isinstance(value, Mapping):
            for key, item in value.items():
                assert isinstance(key, str)
                assert _session_key_stem(key) not in _SESSION_REFERENCE_KEY_STEMS
                assert all(native_value not in key for native_value in native_session_values)
                walk(item)
        elif isinstance(value, str):
            assert all(native_value not in value for native_value in native_session_values)
        elif isinstance(value, Sequence):
            for item in value:
                walk(item)

    walk(payload)


def _assert_normalized_payload_shape(event: RuntimeEvent) -> None:
    assert isinstance(event.payload_json_value(), dict)
    if event.event_type == "text_delta":
        assert isinstance(event.payload["text"], str)
        assert event.payload["text"]
    elif event.event_type in {"tool_started", "tool_completed"}:
        assert isinstance(event.payload["tool"], str)
        assert isinstance(event.payload["summary"], str)
    elif event.event_type == "turn_completed":
        assert isinstance(event.payload["summary"], str)


@pytest.mark.parametrize(
    "key",
    ["session_id", "sessionId", "thread_id", "threadId", "conversation_id", "conversationId"],
)
def test_provider_session_guard_rejects_known_key_variants(key: str):
    with pytest.raises(AssertionError):
        _assert_no_provider_session_reference({key: "redacted"}, set())


def test_provider_session_guard_rejects_nested_native_session_value():
    with pytest.raises(AssertionError):
        _assert_no_provider_session_reference(
            {"details": {"label": "session-native-123"}},
            {"session-native-123"},
        )


def test_provider_session_guard_rejects_embedded_native_value_at_any_depth():
    with pytest.raises(AssertionError):
        _assert_no_provider_session_reference(
            {
                "outer": [
                    ("safe", {"details": ["resuming session-native-123"]}),
                ]
            },
            {"session-native-123"},
        )


@pytest.mark.parametrize("provider", ["codex", "claude", "pi"])
def test_provider_fixture_contract(provider: str, runtime_fixture):
    native_session_values = _native_session_values(list(_read_jsonl(provider)))
    events = runtime_fixture(provider)

    assert [event.event_type for event in events] == EXPECTED_EVENT_TYPES
    assert native_session_values
    for event in events:
        _assert_no_provider_session_reference(event.payload, native_session_values)
        _assert_normalized_payload_shape(event)
