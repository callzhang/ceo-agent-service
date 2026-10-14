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
        "git", "-C", str(repo), "archive", "d06c148b90aaeee74c14bbabf0183ffd65f34048",
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


def test_native_candidate_publishes_real_pair_before_immutable_baseline(old_email_copy, tmp_path):
    from app.email_store import EmailStore
    from app.store import AutoReplyStore

    repo, path = old_email_copy
    baseline = tmp_path / "pre-native-target.sqlite3"
    with sqlite3.connect(path) as db:
        assert db.execute("select value from service_state where key='store_schema_version'").fetchone() == ("2026-10-13.5",)
    published = _publish_email_pair(repo, path)
    assert published == {"store": "2026-10-14.1", "email": 52,
                         "action_types": 29, "retired_email_tables": 13}
    _backup_database(path, baseline)
    assert AutoReplyStore(path) and EmailStore(path)
    with sqlite3.connect(baseline) as db:
        assert db.execute("select count(*) from email_schema_migrations where version=52").fetchone() == (1,)
        assert db.execute("select count(*) from sqlite_schema where type='table' and name='email_actions'").fetchone() == (0,)
    with sqlite3.connect(path) as db:
        trigger = db.execute("select sql from sqlite_schema where name='trg_email_direct_action_blocks_account_delete'").fetchone()
        assert trigger is not None and "main.actions" in trigger[0]
        with pytest.raises(sqlite3.DatabaseError, match="cannot reference objects in database main"):
            db.execute("attach database ? as baseline", (str(baseline),))
    assert original_rows_unchanged(baseline, path) > 0


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
