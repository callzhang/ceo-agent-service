"""Task-bound ordinary files for Consumer, with read-only Audit access."""
from tests.task_input_fixtures import bind_claimed_input

import hashlib
import sqlite3

import pytest

from app.agent_cli import MAX_CLI_OUTPUT_BYTES, build_role_server
from app import task_artifacts
from app.current_database_schema import create_current_database_schema
from app.task_entity_storage import (
    append_task_input,
    bind_current_input,
    update_task_cas,
)
from tests.support.native_task_seed import seed_native_task


def _task_db(path, generation="first"):
    with sqlite3.connect(path) as db:
        db.row_factory = sqlite3.Row
        create_current_database_schema(db)
        _insert_task(db, 7, generation)


def _insert_task(db, task_id, generation):
    db.row_factory = sqlite3.Row
    seed_native_task(db, task_id, "reply", "pending")
    task = update_task_cas(
        db,
        task_id=task_id,
        expected={"status": "pending"},
        changes={"execution_generation": generation},
    )
    assert task is not None
    task_input, inserted = append_task_input(db, task_id=task_id, values={
        "channel": "dingtalk",
        "conversation_id": f"conversation-{task_id}",
        "conversation_title": "Artifact test",
        "single_chat": 0,
        "trigger_message_id": f"message-{task_id}",
        "trigger_create_time": "",
        "trigger_sender": "",
        "trigger_text": "",
        "trigger_message_json": "{}",
        "oa_url": "",
        "business_object_key": f"artifact-test-{task_id}",
        "input_revision_key": f"artifact-test-{task_id}",
        "input_provenance_json": "{}",
        "input_compacted": 0,
    })
    assert inserted
    assert bind_current_input(
        db,
        task_id=task_id,
        input_id=task_input["id"],
        expected_input_version=None,
        input_version=1,
    ) is not None
    assert bind_claimed_input(
        db,
        task_id=task_id,
        input_id=task_input["id"],
        claimed_input_version=1,
    ) is not None


def _set_generation(db, value):
    db.row_factory = sqlite3.Row
    assert update_task_cas(
        db,
        task_id=7,
        expected={"execution_generation": "first"},
        changes={"execution_generation": value},
    ) is not None


def _tool(role, name, db):
    return build_role_server(role, task_id=7, db_path=db)._tool_manager.get_tool(name).fn


def _patch_artifact_root(tmp_path, monkeypatch):
    root = tmp_path / "task-artifacts-tmp"
    monkeypatch.setattr(task_artifacts, "TASK_ARTIFACT_ROOT", root)
    return root


def test_consumer_writes_and_reads_back_only_its_task_generation(tmp_path, monkeypatch):
    db = tmp_path / "service.sqlite3"
    _task_db(db)
    monkeypatch.setattr("app.config.workspace_path", lambda: tmp_path / "workspace")
    task_artifact_root = _patch_artifact_root(tmp_path, monkeypatch)

    write = _tool("consumer", "consumer_artifact_write", db)
    read = _tool("consumer", "read_task_artifact", db)
    list_files = _tool("consumer", "list_task_artifacts", db)

    written = write("analysis.py", "print(2 + 2)\n")
    assert written["content"] == "print(2 + 2)\n"
    assert written["sha256"] == read("analysis.py")["sha256"]
    assert read("analysis.py")["content"] == written["content"]
    assert list_files()["files"] == [{"name": "analysis.py", "sha256": written["sha256"]}]
    assert (task_artifact_root / "7/first/analysis.py").read_text() == written["content"]
    assert not (tmp_path / "workspace/consumer-artifacts").exists()

    with sqlite3.connect(db) as connection:
        _set_generation(connection, "second")
    for old_call in (lambda: write("new.md", "stale"), list_files,
                     lambda: read("analysis.py")):
        with pytest.raises(ValueError, match="generation changed"):
            old_call()
    assert _tool("consumer", "list_task_artifacts", db)()["files"] == []
    new_write = _tool("consumer", "consumer_artifact_write", db)
    assert new_write("new.md", "current")["content"] == "current"
    assert (task_artifact_root / "7/second/new.md").read_text() == "current"
    assert not (task_artifact_root / "7/second/analysis.py").exists()


def test_scheduled_consumer_retry_carries_prior_generation_artifacts(
    tmp_path, monkeypatch
):
    db = tmp_path / "service.sqlite3"
    _task_db(db)
    with sqlite3.connect(db) as connection:
        connection.execute(
            "update task_inputs set channel='scheduled', conversation_id=? where task_id=7",
            ("scheduled-task-run:411947",),
        )
    monkeypatch.setattr("app.config.workspace_path", lambda: tmp_path / "workspace")
    task_artifact_root = _patch_artifact_root(tmp_path, monkeypatch)

    first = build_role_server("consumer", task_id=7, db_path=db)
    first._tool_manager.get_tool("consumer_artifact_write").fn(
        "analysis.json", '{"source":"verified"}'
    )
    with sqlite3.connect(db) as connection:
        _set_generation(connection, "second")

    second = build_role_server("consumer", task_id=7, db_path=db)
    files = second._tool_manager.get_tool("list_task_artifacts").fn()["files"]
    assert files == [{
        "name": "analysis.json",
        "sha256": hashlib.sha256(b'{"source":"verified"}').hexdigest(),
    }]
    assert second._tool_manager.get_tool("read_task_artifact").fn(
        "analysis.json"
    )["content"] == '{"source":"verified"}'
    assert (task_artifact_root / "7/first/analysis.json").exists()
    assert (task_artifact_root / "7/second/analysis.json").read_text() == (
        '{"source":"verified"}'
    )


def test_legacy_task_artifacts_move_to_tmp_before_role_read(tmp_path, monkeypatch):
    db = tmp_path / "service.sqlite3"
    _task_db(db)
    workspace = tmp_path / "workspace"
    monkeypatch.setattr("app.config.workspace_path", lambda: workspace)
    task_artifact_root = _patch_artifact_root(tmp_path, monkeypatch)
    legacy = workspace / "consumer-artifacts/7/first"
    legacy.mkdir(parents=True)
    (legacy / "notes.md").write_text("existing task evidence")

    result = _tool("audit", "read_task_artifact", db)("notes.md")

    assert result["content"] == "existing task evidence"
    assert (task_artifact_root / "7/first/notes.md").read_text() == result["content"]
    assert not (workspace / "consumer-artifacts/7").exists()


def test_audit_can_read_and_list_but_cannot_write_task_files(tmp_path, monkeypatch):
    db = tmp_path / "service.sqlite3"
    _task_db(db)
    _patch_artifact_root(tmp_path, monkeypatch)
    monkeypatch.setattr("app.config.workspace_path", lambda: tmp_path / "workspace")
    _tool("consumer", "consumer_artifact_write", db)("notes.md", "facts\n")

    audit = build_role_server("audit", task_id=7, db_path=db)
    assert audit._tool_manager.get_tool("read_task_artifact").fn("notes.md")["content"] == "facts\n"
    assert audit._tool_manager.get_tool("list_task_artifacts").fn()["files"][0]["name"] == "notes.md"
    assert audit._tool_manager.get_tool("consumer_artifact_write") is None


def test_task_files_reject_unbound_and_escaping_paths(tmp_path, monkeypatch):
    db = tmp_path / "service.sqlite3"
    _task_db(db)
    _patch_artifact_root(tmp_path, monkeypatch)
    monkeypatch.setattr("app.config.workspace_path", lambda: tmp_path / "workspace")
    write = _tool("consumer", "consumer_artifact_write", db)

    for name in ("../outside", "/tmp/outside", "nested/file", "."):
        with pytest.raises(ValueError):
            write(name, "bad")
    with pytest.raises(ValueError):
        build_role_server("consumer", task_id=8, db_path=db)
    assert not (tmp_path / "outside").exists()


def test_task_artifacts_do_not_cross_task_or_follow_file_links(tmp_path, monkeypatch):
    db = tmp_path / "service.sqlite3"
    _task_db(db)
    with sqlite3.connect(db) as connection:
        _insert_task(connection, 8, "first")
    task_artifact_root = _patch_artifact_root(tmp_path, monkeypatch)
    monkeypatch.setattr("app.config.workspace_path", lambda: tmp_path / "workspace")
    _tool("consumer", "consumer_artifact_write", db)("own.md", "task seven")
    other = build_role_server("consumer", task_id=8, db_path=db)
    assert other._tool_manager.get_tool("list_task_artifacts").fn()["files"] == []
    with pytest.raises(ValueError):
        other._tool_manager.get_tool("read_task_artifact").fn("own.md")

    outside = tmp_path / "outside.md"
    outside.write_text("untouched")
    artifact_dir = task_artifact_root / "7/first"
    (artifact_dir / "linked.md").symlink_to(outside)
    with pytest.raises(ValueError, match="linked"):
        _tool("consumer", "read_task_artifact", db)("linked.md")
    with pytest.raises(ValueError, match="linked"):
        _tool("consumer", "consumer_artifact_write", db)("linked.md", "changed")
    assert outside.read_text() == "untouched"


def test_task_artifact_list_omits_atomic_write_temporary_files(tmp_path, monkeypatch):
    db = tmp_path / "service.sqlite3"
    _task_db(db)
    task_artifact_root = _patch_artifact_root(tmp_path, monkeypatch)
    monkeypatch.setattr("app.config.workspace_path", lambda: tmp_path / "workspace")
    server = build_role_server("consumer", task_id=7, db_path=db)
    server._tool_manager.get_tool("consumer_artifact_write").fn("ready.md", "complete")
    artifact_dir = task_artifact_root / "7/first"
    (artifact_dir / ".task-artifact-incomplete").write_text("partial")
    assert [item["name"] for item in server._tool_manager.get_tool("list_task_artifacts").fn()["files"]] == ["ready.md"]


def test_old_consumer_server_cannot_write_report_after_generation_changes(
    tmp_path, monkeypatch
):
    db = tmp_path / "service.sqlite3"
    _task_db(db)
    called = []
    monkeypatch.setattr(
        "app.agent_cli._write_document_artifact",
        lambda **kwargs: called.append(kwargs) or {"operation": "updated"},
    )
    monkeypatch.setattr(
        "app.agent_cli._bound_scheduled_run",
        lambda db_path, task_id: (object(), object()),
    )
    old = build_role_server("consumer", task_id=7, db_path=db)
    write_report = old._tool_manager.get_tool("consumer_document_write").fn

    with sqlite3.connect(db) as connection:
        _set_generation(connection, "second")
    with pytest.raises(ValueError, match="generation changed"):
        write_report(artifact_name="report.md", title="Any")
    assert called == []

    current = build_role_server("consumer", task_id=7, db_path=db)
    assert current._tool_manager.get_tool("consumer_document_write").fn(
        artifact_name="report.md", title="Any"
    ) == {"operation": "updated"}
    assert len(called) == 1


@pytest.mark.parametrize(
    "content",
    ["x" * MAX_CLI_OUTPUT_BYTES, "\n" * (MAX_CLI_OUTPUT_BYTES // 2)],
    ids=["plain", "escaped"],
)
def test_task_artifact_returns_large_content_in_complete_pages(
    tmp_path,
    monkeypatch,
    content,
):
    db = tmp_path / "service.sqlite3"
    _task_db(db)
    task_artifact_root = _patch_artifact_root(tmp_path, monkeypatch)
    monkeypatch.setattr("app.config.workspace_path", lambda: tmp_path / "workspace")
    server = build_role_server("consumer", task_id=7, db_path=db)
    write = server._tool_manager.get_tool("consumer_artifact_write").fn
    first = write("large.md", content)
    chunks = [first["content"]]
    next_offset = first["next_offset"]
    read = server._tool_manager.get_tool("read_task_artifact").fn
    while next_offset is not None:
        page = read("large.md", offset=next_offset)
        chunks.append(page["content"])
        next_offset = page["next_offset"]
    assert "".join(chunks) == content
    assert (task_artifact_root / "7/first/large.md").read_text() == content


def test_task_artifact_read_pages_escaped_content_within_output_budget(
    tmp_path,
    monkeypatch,
):
    db = tmp_path / "service.sqlite3"
    _task_db(db)
    root = _patch_artifact_root(tmp_path, monkeypatch) / "7/first"
    root.mkdir(parents=True)
    (root / "large.md").write_text("\n" * (MAX_CLI_OUTPUT_BYTES // 2))
    read = _tool("audit", "read_task_artifact", db)
    page = read("large.md")
    assert page["content"] == "\n" * 20000
    assert page["next_offset"] == 20000
    assert page["total_chars"] == MAX_CLI_OUTPUT_BYTES // 2


def test_role_startup_cannot_rebind_an_old_claim_to_new_generation(tmp_path):
    db = tmp_path / "service.sqlite3"
    _task_db(db, generation="rotated")
    with pytest.raises(ValueError, match="generation changed"):
        build_role_server("consumer", task_id=7, db_path=db, execution_generation="original")
    with pytest.raises(ValueError, match="generation changed"):
        build_role_server("audit", task_id=7, db_path=db, execution_generation="original")
