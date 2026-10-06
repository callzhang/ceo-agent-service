"""Task-bound ordinary files for Consumer, with read-only Audit access."""

import sqlite3

import pytest

from app.agent_cli import MAX_CLI_OUTPUT_BYTES, build_role_server


def _task_db(path, generation="first"):
    with sqlite3.connect(path) as db:
        db.execute("create table reply_tasks (id integer primary key, execution_generation text not null)")
        db.execute("insert into reply_tasks values (7, ?)", (generation,))


def _tool(role, name, db):
    return build_role_server(role, task_id=7, db_path=db)._tool_manager.get_tool(name).fn


def test_consumer_writes_and_reads_back_only_its_task_generation(tmp_path, monkeypatch):
    db = tmp_path / "service.sqlite3"
    _task_db(db)
    monkeypatch.setattr("app.config.workspace_path", lambda: tmp_path / "workspace")

    write = _tool("consumer", "consumer_artifact_write", db)
    read = _tool("consumer", "read_task_artifact", db)
    list_files = _tool("consumer", "list_task_artifacts", db)

    written = write("analysis.py", "print(2 + 2)\n")
    assert written["content"] == "print(2 + 2)\n"
    assert written["sha256"] == read("analysis.py")["sha256"]
    assert read("analysis.py")["content"] == written["content"]
    assert list_files()["files"] == [{"name": "analysis.py", "sha256": written["sha256"]}]
    assert (tmp_path / "workspace/consumer-artifacts/7/first/analysis.py").read_text() == written["content"]

    with sqlite3.connect(db) as connection:
        connection.execute("update reply_tasks set execution_generation='second' where id=7")
    for old_call in (lambda: write("new.md", "stale"), list_files,
                     lambda: read("analysis.py")):
        with pytest.raises(ValueError, match="generation changed"):
            old_call()
    assert _tool("consumer", "list_task_artifacts", db)()["files"] == []
    new_write = _tool("consumer", "consumer_artifact_write", db)
    assert new_write("new.md", "current")["content"] == "current"
    assert (tmp_path / "workspace/consumer-artifacts/7/second/new.md").read_text() == "current"
    assert not (tmp_path / "workspace/consumer-artifacts/7/second/analysis.py").exists()


def test_audit_can_read_and_list_but_cannot_write_task_files(tmp_path, monkeypatch):
    db = tmp_path / "service.sqlite3"
    _task_db(db)
    monkeypatch.setattr("app.config.workspace_path", lambda: tmp_path / "workspace")
    _tool("consumer", "consumer_artifact_write", db)("notes.md", "facts\n")

    audit = build_role_server("audit", task_id=7, db_path=db)
    assert audit._tool_manager.get_tool("read_task_artifact").fn("notes.md")["content"] == "facts\n"
    assert audit._tool_manager.get_tool("list_task_artifacts").fn()["files"][0]["name"] == "notes.md"
    assert audit._tool_manager.get_tool("consumer_artifact_write") is None


def test_task_files_reject_unbound_and_escaping_paths(tmp_path, monkeypatch):
    db = tmp_path / "service.sqlite3"
    _task_db(db)
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
        connection.execute("insert into reply_tasks values (8, 'first')")
    monkeypatch.setattr("app.config.workspace_path", lambda: tmp_path / "workspace")
    _tool("consumer", "consumer_artifact_write", db)("own.md", "task seven")
    other = build_role_server("consumer", task_id=8, db_path=db)
    assert other._tool_manager.get_tool("list_task_artifacts").fn()["files"] == []
    with pytest.raises(ValueError):
        other._tool_manager.get_tool("read_task_artifact").fn("own.md")

    outside = tmp_path / "outside.md"
    outside.write_text("untouched")
    artifact_dir = tmp_path / "workspace/consumer-artifacts/7/first"
    (artifact_dir / "linked.md").symlink_to(outside)
    with pytest.raises(ValueError, match="linked"):
        _tool("consumer", "read_task_artifact", db)("linked.md")
    with pytest.raises(ValueError, match="linked"):
        _tool("consumer", "consumer_artifact_write", db)("linked.md", "changed")
    assert outside.read_text() == "untouched"


def test_task_artifact_list_omits_atomic_write_temporary_files(tmp_path, monkeypatch):
    db = tmp_path / "service.sqlite3"
    _task_db(db)
    monkeypatch.setattr("app.config.workspace_path", lambda: tmp_path / "workspace")
    server = build_role_server("consumer", task_id=7, db_path=db)
    server._tool_manager.get_tool("consumer_artifact_write").fn("ready.md", "complete")
    artifact_dir = tmp_path / "workspace/consumer-artifacts/7/first"
    (artifact_dir / ".task-artifact-incomplete").write_text("partial")
    assert [item["name"] for item in server._tool_manager.get_tool("list_task_artifacts").fn()["files"]] == ["ready.md"]


def test_old_consumer_server_cannot_write_report_after_generation_changes(tmp_path, monkeypatch):
    db = tmp_path / "service.sqlite3"
    _task_db(db)
    called = []
    monkeypatch.setattr(
        "app.agent_cli._write_bound_report_document",
        lambda **kwargs: called.append(kwargs) or {"operation": "updated"},
    )
    old = build_role_server("consumer", task_id=7, db_path=db)
    write_report = old._tool_manager.get_tool("consumer_document_write").fn

    with sqlite3.connect(db) as connection:
        connection.execute("update reply_tasks set execution_generation='second' where id=7")
    with pytest.raises(ValueError, match="generation changed"):
        write_report("# Report")
    assert called == []

    current = build_role_server("consumer", task_id=7, db_path=db)
    assert current._tool_manager.get_tool("consumer_document_write").fn("# Report") == {
        "operation": "updated"
    }
    assert len(called) == 1


@pytest.mark.parametrize("content", ["x" * MAX_CLI_OUTPUT_BYTES, "\n" * (MAX_CLI_OUTPUT_BYTES // 2)])
def test_task_artifact_rejects_content_that_cannot_fit_complete_tool_output(
    tmp_path, monkeypatch, content,
):
    db = tmp_path / "service.sqlite3"
    _task_db(db)
    monkeypatch.setattr("app.config.workspace_path", lambda: tmp_path / "workspace")
    server = build_role_server("consumer", task_id=7, db_path=db)
    write = server._tool_manager.get_tool("consumer_artifact_write").fn
    with pytest.raises(ValueError, match="too large"):
        write("large.md", content)
    assert not (tmp_path / "workspace/consumer-artifacts/7/first/large.md").exists()


def test_task_artifact_read_rejects_external_file_whose_escaped_content_exceeds_budget(
    tmp_path, monkeypatch,
):
    db = tmp_path / "service.sqlite3"
    _task_db(db)
    monkeypatch.setattr("app.config.workspace_path", lambda: tmp_path / "workspace")
    root = tmp_path / "workspace/consumer-artifacts/7/first"
    root.mkdir(parents=True)
    (root / "large.md").write_text("\n" * (MAX_CLI_OUTPUT_BYTES // 2))
    read = _tool("audit", "read_task_artifact", db)
    with pytest.raises(ValueError, match="too large"):
        read("large.md")


def test_role_startup_cannot_rebind_an_old_claim_to_new_generation(tmp_path):
    db = tmp_path / "service.sqlite3"
    _task_db(db, generation="rotated")
    with pytest.raises(ValueError, match="generation changed"):
        build_role_server("consumer", task_id=7, db_path=db, execution_generation="original")
    with pytest.raises(ValueError, match="generation changed"):
        build_role_server("audit", task_id=7, db_path=db, execution_generation="original")
