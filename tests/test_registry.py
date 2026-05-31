from pathlib import Path

from ceo_agent_service.registry import ThreadRegistry


def test_registry_persists_thread_session_mapping(tmp_path: Path):
    registry = ThreadRegistry(tmp_path / "registry.json")
    registry.set_session("thread-1", "session-1")

    loaded = ThreadRegistry(tmp_path / "registry.json")

    assert loaded.get_session("thread-1") == "session-1"
