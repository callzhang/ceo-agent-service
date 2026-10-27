"""Artifact metadata listing is independent of the bounded body reader."""

import hashlib
import json

import pytest

from app import agent_cli


@pytest.fixture(params=("consumer", "audit"))
def artifact_tools(tmp_path, monkeypatch, request):
    root = tmp_path / "artifacts"
    root.mkdir()
    monkeypatch.setattr(agent_cli, "_task_file_root", lambda *args, **kwargs: root)
    server = agent_cli.build_role_server(request.param)
    return (
        root,
        server._tool_manager.get_tool("list_task_artifacts").fn,
        server._tool_manager.get_tool("read_task_artifact").fn,
    )


def test_large_artifact_does_not_prevent_listing_or_reading_small_file(artifact_tools):
    root, list_files, read_file = artifact_tools
    large = b"x" * (agent_cli.MAX_TASK_FILE_BYTES + 1)
    small = b"available facts\n"
    (root / "large.json").write_bytes(large)
    (root / "notes.md").write_bytes(small)

    assert list_files() == {"files": [
        {"name": "large.json", "sha256": hashlib.sha256(large).hexdigest()},
        {"name": "notes.md", "sha256": hashlib.sha256(small).hexdigest()},
    ]}
    result = read_file("notes.md")
    assert result["content"] == small.decode()
    assert result["sha256"] == hashlib.sha256(small).hexdigest()
    assert result["offset"] == 0
    assert result["next_offset"] is None
    with pytest.raises(ValueError, match="^task file is too large$"):
        read_file("large.json")


def test_artifact_listing_keeps_output_size_limit(artifact_tools, monkeypatch):
    root, list_files, _read_file = artifact_tools
    content = b"facts"
    (root / "notes.md").write_bytes(content)
    expected = {"files": [{
        "name": "notes.md", "sha256": hashlib.sha256(content).hexdigest(),
    }]}
    size = len(json.dumps(expected).encode("utf-8"))
    monkeypatch.setattr(agent_cli, "MAX_CLI_OUTPUT_BYTES", size)
    assert list_files() == expected
    monkeypatch.setattr(agent_cli, "MAX_CLI_OUTPUT_BYTES", size - 1)
    with pytest.raises(ValueError, match="^task file listing is too large$"):
        list_files()
