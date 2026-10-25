"""The native copy harness must not create transport-less MCP server entries."""
import json
import io
import hashlib
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tarfile

import pytest

from scripts.validate_native_copy_lifecycle import (
    _backup_database,
    _exercise_current_email,
    _publish_email_pair,
    original_rows_unchanged,
    restricted_command,
)


def command(db_path):
    return ["codex", "exec", "-c", 'mcp_servers.agent_cli.command="python"',
        "-c", "mcp_servers.agent_cli.args=" + json.dumps(["-m", "app.agent_cli", "--db", str(db_path)]),
        "-c", 'sandbox_mode="workspace-write"', "--json", "-"]


def test_empty_service_manifest_does_not_create_unconfigured_mcp_transports(monkeypatch):
    monkeypatch.setattr("app.wechat.codex_safety.inherited_native_server_names", lambda: ())
    db = Path("/private/tmp/copy/candidate.sqlite3")
    args = restricted_command(command(db), workspace=db.parent, db_path=db)
    assert not any(arg.startswith("mcp_servers.exa.") for arg in args)
    assert not any(arg.startswith("mcp_servers.memory_connector.") for arg in args)
    assert 'mcp_servers.agent_cli.enabled_tools=["read_task_artifact","read_system_action_contract"]' in args


def test_only_real_inherited_servers_are_disabled_and_native_roles_are_preserved(monkeypatch):
    monkeypatch.setattr("app.wechat.codex_safety.inherited_native_server_names", lambda: ("actual_server",))
    db = Path("/private/tmp/copy/candidate.sqlite3")
    args = restricted_command(command(db), workspace=db.parent, db_path=db)
    assert "mcp_servers.actual_server.enabled=false" in args
    assert 'sandbox_mode="workspace-write"' in args
    assert "--skip-git-repo-check" in args
    env = next(arg for arg in args if arg.startswith("mcp_servers.agent_cli.env="))
    assert str(db) in env and '"CEO_WORKSPACE"' in env


def test_resume_scoped_options_follow_the_resume_subcommand(monkeypatch):
    monkeypatch.setattr("app.wechat.codex_safety.inherited_native_server_names", lambda: ())
    db = Path("/private/tmp/copy/candidate.sqlite3")
    cmd = command(db)
    cmd.insert(cmd.index("exec") + 1, "resume")
    args = restricted_command(cmd, workspace=db.parent, db_path=db)
    env_index = next(i for i, value in enumerate(args) if value.startswith("mcp_servers.agent_cli.env="))
    assert env_index > args.index("resume")


def test_scoped_tool_list_overrides_existing_role_tool_list(monkeypatch):
    monkeypatch.setattr("app.wechat.codex_safety.inherited_native_server_names", lambda: ())
    db = Path("/private/tmp/copy/candidate.sqlite3")
    cmd = command(db)
    cmd[-1:-1] = ["-c", 'mcp_servers.agent_cli.enabled_tools=["read_dingtalk_conversations"]']
    args = restricted_command(cmd, workspace=db.parent, db_path=db)
    lists = [x for x in args if x.startswith("mcp_servers.agent_cli.enabled_tools=")]
    assert lists[-1] == 'mcp_servers.agent_cli.enabled_tools=["read_task_artifact","read_system_action_contract"]'


@pytest.fixture
def old_email_copy(tmp_path):
    repo = Path(__file__).resolve().parents[1]
    old = tmp_path / "published-old"
    old.mkdir()
    archive = subprocess.check_output([
        "git", "-C", str(repo), "archive", "7dadc492c1c62d5b0624f2f97dd0c89f4f358646",
        "app", "docs/superpowers/specs/2026-10-11-database-redesign-phase4-inventory.json",
    ])
    with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
        tar.extractall(old, filter="data")
    path = tmp_path / "old.sqlite3"
    subprocess.run([
        sys.executable, "-c",
        "from pathlib import Path; import sys; from app.store import AutoReplyStore; "
        "from app.email_store import EmailStore; p=Path(sys.argv[1]); AutoReplyStore(p); EmailStore(p)",
        str(path),
    ], cwd=old, env={**os.environ, "PYTHONPATH": str(old)},
        capture_output=True, text=True, check=True, timeout=300)
    return repo, path


def test_native_candidate_email_public_receipt_context_and_history_survive_reopen(tmp_path):
    from app.email_store import EmailStore
    from app.store import AutoReplyStore

    path = tmp_path / "current.sqlite3"
    AutoReplyStore(path)
    EmailStore(path)
    baseline = tmp_path / "pre-native-target.sqlite3"
    _backup_database(path, baseline)
    baseline_hash = hashlib.sha256(baseline.read_bytes()).digest()
    evidence = _exercise_current_email(path)
    assert evidence == {"context_messages": 1, "prior_context_receipts": 0,
                        "direct_attempts": 1, "direct_status": "done",
                        "provider_result_persisted": True, "history_locator_valid": True}
    assert original_rows_unchanged(baseline, path) > 0
    assert hashlib.sha256(baseline.read_bytes()).digest() == baseline_hash


@pytest.mark.parametrize("missing", ("context", "attempt", "history"))
def test_native_candidate_rejects_missing_email_public_readback(tmp_path, monkeypatch, missing):
    from app.email_store import EmailStore
    from app.store import AutoReplyStore

    path = tmp_path / "current.sqlite3"
    AutoReplyStore(path)
    EmailStore(path)
    owner, method = {
        "context": (EmailStore, "list_email_context_thread"),
        "attempt": (EmailStore, "list_action_attempts"),
        "history": (AutoReplyStore, "list_operation_logs"),
    }[missing]
    monkeypatch.setattr(owner, method, lambda *_args, **_kwargs: [])
    with pytest.raises(AssertionError):
        _exercise_current_email(path)


def test_native_candidate_rejects_incomplete_email_pair(tmp_path):
    from app.store import AutoReplyStore

    path = tmp_path / "store-only.sqlite3"
    AutoReplyStore(path)
    with pytest.raises((AssertionError, sqlite3.IntegrityError)):
        _publish_email_pair(Path(__file__).resolve().parents[1], path)


@pytest.mark.parametrize("damage", ("change", "delete"))
def test_native_target_baseline_rejects_changed_or_lost_original_row(tmp_path, damage):
    from app.email_store import EmailStore
    from app.store import AutoReplyStore

    path = tmp_path / "current.sqlite3"
    AutoReplyStore(path)
    EmailStore(path)
    baseline = tmp_path / "pre-native-target.sqlite3"
    _backup_database(path, baseline)
    with sqlite3.connect(path) as db:
        if damage == "change":
            db.execute(
                "update service_state set value='changed' where key='store_schema_version'"
            )
        else:
            db.execute("delete from service_state where key='store_schema_version'")
    with pytest.raises(AssertionError, match="service_state"):
        original_rows_unchanged(baseline, path)


def _identity_fixture_task(store, key):
    return store.ensure_reply_task(
        conversation_id=key, conversation_title="Identity isolation", single_chat=False,
        trigger_message_id=key, trigger_create_time="2026-10-15T00:00:00Z",
        trigger_sender="Synthetic", trigger_text="Synthetic artifact identity fixture",
    )


def test_reserved_candidate_task_survives_other_database_terminal_cleanup(tmp_path, monkeypatch):
    from app import task_artifacts
    from app.agent_cli import _task_file_root, _read_task_file
    from app.store import AutoReplyStore
    from scripts.validate_native_copy_lifecycle import _reserve_fixture_task_ids

    monkeypatch.setenv("CEO_WORKSPACE", str(tmp_path / "workspace"))
    monkeypatch.setattr(task_artifacts, "TASK_ARTIFACT_ROOT", tmp_path / "artifacts")
    source = tmp_path / "source.sqlite3"
    source_store = AutoReplyStore(source)
    _identity_fixture_task(source_store, "original")
    baseline_hash = hashlib.sha256(source.read_bytes()).digest()
    candidate = tmp_path / "candidate.sqlite3"
    other = tmp_path / "other.sqlite3"
    _backup_database(source, candidate)
    _backup_database(source, other)
    candidate_store, other_store = AutoReplyStore(candidate), AutoReplyStore(other)

    # Control: ordinary copies allocate the same identity and share a native path.
    ordinary = _identity_fixture_task(candidate_store, "ordinary-candidate")
    other_task = _identity_fixture_task(other_store, "ordinary-other")
    assert ordinary.id == other_task.id
    ordinary_path = _task_file_root(candidate, ordinary.id, ordinary.execution_generation, create=True)
    (ordinary_path / "case-input.json").write_text("ordinary")
    with other_store._connect() as db:
        db.execute("update tasks set status='done' where id=?", (other_task.id,))
    task_artifacts.cleanup_terminal_task_artifacts(other_store, other_task.id)
    assert not ordinary_path.exists()

    with sqlite3.connect(candidate) as db:
        original_rows = db.execute("select * from tasks order by id").fetchall()
        other_sequences = db.execute("select name,seq from sqlite_sequence where name!='tasks' order by name").fetchall()
    reservation = _reserve_fixture_task_ids(candidate)
    isolated = _identity_fixture_task(candidate_store, "isolated-candidate")
    isolated_path = _task_file_root(candidate, isolated.id, isolated.execution_generation, create=True)
    (isolated_path / "case-input.json").write_text("isolated")
    # Another naturally allocated task also reaches terminal cleanup after reserve.
    next_other = _identity_fixture_task(other_store, "next-other")
    with other_store._connect() as db:
        db.execute("update tasks set status='done' where id=?", (next_other.id,))
    task_artifacts.cleanup_terminal_task_artifacts(other_store, next_other.id)
    assert isolated_path.exists(), "another database's terminal cleanup removed candidate artifacts"
    assert _read_task_file(isolated_path, "case-input.json")["content"] == "isolated"
    assert 2**52 <= reservation < isolated.id < 2**53
    assert isolated.id == reservation + 1 and isolated.id != next_other.id
    assert _task_file_root(other, next_other.id, next_other.execution_generation, create=False) != isolated_path
    assert candidate_store.claim_reply_task(isolated.id).status == "processing"
    with sqlite3.connect(candidate) as db:
        assert db.execute("select * from tasks where id<=? order by id", (max(row[0] for row in original_rows),)).fetchall() == original_rows
        assert db.execute("select name,seq from sqlite_sequence where name!='tasks' order by name").fetchall() == other_sequences
    assert original_rows_unchanged(source, candidate) > 0
    assert hashlib.sha256(source.read_bytes()).digest() == baseline_hash
    with candidate_store._connect() as db:
        db.execute("update tasks set status='failed' where id=?", (isolated.id,))
    task_artifacts.cleanup_terminal_task_artifacts(candidate_store, isolated.id)
    assert not isolated_path.parent.exists()


def test_fixture_task_reservation_handles_empty_allocator_and_separate_copies(tmp_path):
    from scripts.validate_native_copy_lifecycle import _reserve_fixture_task_ids

    reservations = []
    for name in ("first", "second"):
        path = tmp_path / (name + ".sqlite3")
        with sqlite3.connect(path) as db:
            db.execute("create table tasks(id integer primary key autoincrement, content text)")
            db.execute("create table unrelated(id integer primary key autoincrement)")
            db.execute("insert into unrelated default values")
        reservation = _reserve_fixture_task_ids(path)
        reservations.append(reservation)
        with sqlite3.connect(path) as db:
            cursor = db.execute("insert into tasks(content) values('fixture')")
            assert cursor.lastrowid == reservation + 1
            assert 2**52 <= reservation < cursor.lastrowid < 2**53
            assert db.execute("select seq from sqlite_sequence where name='unrelated'").fetchone() == (1,)
    assert len(set(reservations)) == 2


def test_fixture_task_reservation_refuses_nonordinary_source_without_mutation(tmp_path):
    from scripts.validate_native_copy_lifecycle import _reserve_fixture_task_ids

    path = tmp_path / "high-source.sqlite3"
    with sqlite3.connect(path) as db:
        db.execute("create table tasks(id integer primary key autoincrement)")
        db.execute("insert into tasks(id) values(?)", (2**52,))
    before = hashlib.sha256(path.read_bytes()).digest()
    with pytest.raises(AssertionError, match="ordinary source Task allocator"):
        _reserve_fixture_task_ids(path)
    assert hashlib.sha256(path.read_bytes()).digest() == before
