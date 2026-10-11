import json
from pathlib import Path

import pytest

from app import native_trajectory
from app.process_runner import ProcessRunResult
from tests.support.native_protocol import install_protocol_native_trajectories


@pytest.mark.parametrize("stdout", ["not JSON", '[1,2]\n{"type":"error","message":"fixture"}'])
def test_protocol_fixture_keeps_bad_output_and_does_not_invent_session(tmp_path, monkeypatch, stdout):
    class Executor:
        def __call__(self, command, **kwargs):
            return ProcessRunResult(1, stdout, "fixture stderr")

    install_protocol_native_trajectories(tmp_path, monkeypatch, Executor, allow_nonprotocol_output=True)
    result = Executor()([])
    assert result == ProcessRunResult(1, stdout, "fixture stderr")
    assert native_trajectory.find_codex_session_path("invented") is None
    assert not list(tmp_path.rglob("*.jsonl"))


def test_protocol_fixture_uses_owned_path_and_preserves_start_before_header(tmp_path, monkeypatch):
    session = "../../not-a-real-session"
    records = [
        {"type": "item.started", "item": {"type": "command_execution", "id": "read", "command": "read fixture"}},
        {"type": "thread.started", "thread_id": session},
        {"type": "item.completed", "item": {"type": [], "id": "invalid"}},
        {"type": "item.completed", "item": {"type": "agent_message", "text": "fixture"}},
    ]
    stdout = "\n".join(json.dumps(record) for record in records)

    class Executor:
        def __call__(self, command, **kwargs):
            return ProcessRunResult(0, stdout, "")

    install_protocol_native_trajectories(tmp_path, monkeypatch, Executor, allow_nonprotocol_output=True)
    assert Executor()([]).stdout == stdout
    path = native_trajectory.find_codex_session_path(session)
    assert path is not None
    assert path.resolve().is_relative_to(tmp_path.resolve())
    assert path.name == Path(path.name).name
    native = [json.loads(line) for line in path.read_text().splitlines()]
    assert native[1]["payload"]["type"] == "item_started"
    assert native[1]["payload"]["item"]["command"] == "read fixture"
    assert all(record.get("payload", {}).get("item", {}).get("id") != "invalid" for record in native)


def test_protocol_fixture_default_remains_strict(tmp_path, monkeypatch):
    class Executor:
        def __call__(self, command, **kwargs):
            return ProcessRunResult(0, '[]\n{"type":"thread.started","thread_id":"fixture"}', "")

    install_protocol_native_trajectories(tmp_path, monkeypatch, Executor)
    with pytest.raises(ValueError, match="must be an object"):
        Executor()([])
    assert not list(tmp_path.rglob("*.jsonl"))


@pytest.mark.parametrize("invalid_type", [[], {}])
def test_protocol_fixture_preserves_invalid_outer_types_for_caller(tmp_path, monkeypatch, invalid_type):
    stdout = "\n".join(json.dumps(record) for record in (
        {"type": "thread.started", "thread_id": "fixture"}, {"type": invalid_type},
    ))

    class Executor:
        def __call__(self, command, **kwargs):
            return ProcessRunResult(1, stdout, "fixture error")

    install_protocol_native_trajectories(tmp_path, monkeypatch, Executor, allow_nonprotocol_output=True)
    assert Executor()([]) == ProcessRunResult(1, stdout, "fixture error")
