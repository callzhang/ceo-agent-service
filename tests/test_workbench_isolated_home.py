import fcntl
import json
import os
import signal
import stat
import subprocess
import sys
import time
import uuid
from pathlib import Path

import pytest

import app.workbench.isolated_home as isolated_home_module
from app.workbench.isolated_home import (
    create_isolated_codex_home,
    reconcile_isolated_codex_homes,
    remove_verified_isolated_home,
)


def _assert_no_sync_artifacts(sessions: Path) -> None:
    if not sessions.exists():
        return
    assert not any(
        path.name.startswith(".workbench-sync-")
        for path in sessions.rglob("*")
    )


def _wait_for_path(path: Path, process: subprocess.Popen[bytes]) -> None:
    deadline = time.monotonic() + 8
    while time.monotonic() < deadline:
        if path.exists():
            return
        if process.poll() is not None:
            raise AssertionError(
                f"sync subprocess exited before checkpoint: {process.returncode}"
            )
        time.sleep(0.02)
    raise AssertionError("sync subprocess did not reach checkpoint")


def _start_crashing_sync(
    source: Path,
    root: Path,
    checkpoint: Path,
    *,
    checkpoint_kind: str,
) -> subprocess.Popen[bytes]:
    script = "\n".join(
        (
            "import os, sys, time",
            "from pathlib import Path",
            "import app.workbench.isolated_home as module",
            "source, root, checkpoint, session_id, kind = "
            "Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3]), sys.argv[4], sys.argv[5]",
            "home = module.create_isolated_codex_home("
            "source, '', root=root, provider_session_ref=session_id)",
            "isolated = next((home.path / 'sessions').rglob('*' + session_id + '.jsonl'))",
            "isolated.write_text('first after\\n', encoding='utf-8')",
            "(isolated.parent / 'second.jsonl').write_text('second after\\n', encoding='utf-8')",
            "if kind == 'first_replace':",
            "    real_replace = module.os.replace",
            "    count = [0]",
            "    def replace(source_name, destination_name, *args, **kwargs):",
            "        result = real_replace(source_name, destination_name, *args, **kwargs)",
            "        if str(source_name).startswith('.workbench-sync-stage-'):",
            "            count[0] += 1",
            "            if count[0] == 1:",
            "                checkpoint.write_text('ready', encoding='utf-8')",
            "                while True: time.sleep(1)",
            "        return result",
            "    module.os.replace = replace",
            "else:",
            "    def cleanup(*args, **kwargs):",
            "        checkpoint.write_text('ready', encoding='utf-8')",
            "        while True: time.sleep(1)",
            "    module._cleanup_committed_session_sync = cleanup",
            "home.cleanup()",
        )
    )
    return subprocess.Popen(
        [
            sys.executable,
            "-c",
            script,
            str(source),
            str(root),
            str(checkpoint),
            "019ff6ad-c139-7411-9169-6220e8b39688",
            checkpoint_kind,
        ],
        cwd=Path(__file__).resolve().parents[1],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


def _start_preparation_crash(
    source: Path,
    root: Path,
    checkpoint: Path,
    *,
    checkpoint_kind: str,
) -> subprocess.Popen[bytes]:
    script = "\n".join(
        (
            "import os, sys, time",
            "from pathlib import Path",
            "import app.workbench.isolated_home as module",
            "source, root, checkpoint, session_id, kind = "
            "Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3]), sys.argv[4], sys.argv[5]",
            "home = module.create_isolated_codex_home("
            "source, '', root=root, provider_session_ref=session_id)",
            "isolated = next((home.path / 'sessions').rglob('*' + session_id + '.jsonl'))",
            "isolated.write_text('first after\\n', encoding='utf-8')",
            "new_dir = home.path / 'sessions' / 'new-tree' / 'nested'",
            "new_dir.mkdir(parents=True, mode=0o700)",
            "(new_dir / 'new.jsonl').write_text('new\\n', encoding='utf-8')",
            "def stop_here():",
            "    checkpoint.write_text('ready', encoding='utf-8')",
            "    while True: time.sleep(1)",
            "if kind == 'after_intent':",
            "    original = module._create_missing_session_directories",
            "    def create_dirs(plan, **kwargs):",
            "        stop_here()",
            "    module._create_missing_session_directories = create_dirs",
            "elif kind == 'first_dir':",
            "    original = module.os.mkdir",
            "    fired = [False]",
            "    def mkdir(path, *args, **kwargs):",
            "        result = original(path, *args, **kwargs)",
            "        if not fired[0] and str(path).startswith('directory-'):",
            "            fired[0] = True",
            "            stop_here()",
            "        return result",
            "    module.os.mkdir = mkdir",
            "else:",
            "    original = module._copy_validated_file_to_new",
            "    fired = [False]",
            "    def copy_file(source_path, expected, destination_fd, "
            "destination_name, **kwargs):",
            "        result = original(source_path, expected, destination_fd, "
            "destination_name, **kwargs)",
            "        wanted = ('.workbench-sync-stage-' if kind == 'first_stage' "
            "else '.workbench-sync-backup-')",
            "        if not fired[0] and str(destination_name).startswith(wanted):",
            "            fired[0] = True",
            "            stop_here()",
            "        return result",
            "    module._copy_validated_file_to_new = copy_file",
            "home.cleanup()",
        )
    )
    return subprocess.Popen(
        [
            sys.executable,
            "-c",
            script,
            str(source),
            str(root),
            str(checkpoint),
            "019ff6ad-c139-7411-9169-6220e8b39688",
            checkpoint_kind,
        ],
        cwd=Path(__file__).resolve().parents[1],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


def _two_session_home(tmp_path: Path):
    source = _source_home(tmp_path)
    session_id = "019ff6ad-c139-7411-9169-6220e8b39688"
    session_dir = source / "sessions" / "2026" / "08" / "13"
    session_dir.mkdir(parents=True, mode=0o700)
    first = session_dir / f"rollout-2026-08-13T00-00-00-{session_id}.jsonl"
    second = session_dir / "second.jsonl"
    first.write_text("first before\n", encoding="utf-8")
    second.write_text("second before\n", encoding="utf-8")
    first.chmod(0o600)
    second.chmod(0o640)
    home = create_isolated_codex_home(
        source,
        "",
        root=tmp_path / "isolated-root",
        provider_session_ref=session_id,
    )
    isolated_dir = home.path / first.parent.relative_to(source)
    isolated_second = isolated_dir / second.name
    isolated_second.write_bytes(second.read_bytes())
    isolated_second.chmod(0o600)
    (isolated_dir / first.name).write_text("first after\n", encoding="utf-8")
    isolated_second.write_text("second after\n", encoding="utf-8")
    return source, home, first, second


def _source_home(tmp_path: Path) -> Path:
    source = tmp_path / "source-codex"
    source.mkdir(mode=0o700)
    (source / "config.toml").write_text("model = 'inherited'\n", encoding="utf-8")
    auth = source / "auth.json"
    auth.write_text('{"credential":"not-a-real-secret"}', encoding="utf-8")
    auth.chmod(0o600)
    ignored_database = source / "state_5.sqlite"
    ignored_database.write_text("large mutable state", encoding="utf-8")
    skills = source / "skills"
    skills.mkdir(mode=0o700)
    skill = skills / "SKILL.md"
    skill.write_text("preserved", encoding="utf-8")
    skill.chmod(0o600)
    skill_alias = skills / "latest"
    skill_alias.symlink_to(skill)
    rules = source / "rules"
    rules.mkdir(mode=0o700)
    rule = rules / "default.rules"
    rule.write_text("allow = true\n", encoding="utf-8")
    rule.chmod(0o600)
    plugins = source / "plugins"
    plugins.mkdir(mode=0o700)
    plugin = plugins / "tool.sh"
    plugin.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    plugin.chmod(0o700)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "do-not-copy").write_text("outside", encoding="utf-8")
    (source / "escaping-link").symlink_to(outside, target_is_directory=True)
    return source


def test_isolated_home_is_private_copies_state_without_following_symlinks(tmp_path: Path):
    source = _source_home(tmp_path)
    root = tmp_path / "isolated-root"

    home = create_isolated_codex_home(
        source,
        "model = 'safe'\n",
        root=root,
    )
    try:
        assert stat.S_IMODE(root.lstat().st_mode) == 0o700
        assert stat.S_IMODE(home.path.lstat().st_mode) == 0o700
        assert stat.S_IMODE((home.path / "config.toml").lstat().st_mode) == 0o600
        assert (home.path / "auth.json").read_text(encoding="utf-8") == (
            source / "auth.json"
        ).read_text(encoding="utf-8")
        assert not (home.path / "auth.json").is_symlink()
        assert not (home.path / "state_5.sqlite").exists()
        assert not (home.path / "skills" / "latest").is_symlink()
        assert (home.path / "skills" / "latest").read_text(encoding="utf-8") == "preserved"
        assert not (home.path / "escaping-link").exists()
    finally:
        home.cleanup()

    assert not home.path.exists()


def test_reconciliation_skips_active_concurrent_and_untrusted_entries(tmp_path: Path):
    source = _source_home(tmp_path)
    root = tmp_path / "isolated-root"
    first = create_isolated_codex_home(source, "", root=root)
    second = create_isolated_codex_home(source, "", root=root)
    outside = tmp_path / "outside-sentinel"
    outside.mkdir()
    sentinel = outside / "sentinel"
    sentinel.write_text("keep", encoding="utf-8")
    symlink_entry = root / uuid.uuid4().hex
    symlink_entry.symlink_to(outside, target_is_directory=True)
    unmarked = root / uuid.uuid4().hex
    unmarked.mkdir(mode=0o700)
    foreign_marker = root / uuid.uuid4().hex
    foreign_marker.mkdir(mode=0o700)
    (foreign_marker / ".owner.json").write_text(
        '{"version":1,"uid":999999,"token":"invalid"}', encoding="utf-8"
    )
    (foreign_marker / ".owner.json").chmod(0o600)
    (foreign_marker / ".active").write_text("", encoding="utf-8")
    (foreign_marker / ".active").chmod(0o600)

    reconcile_isolated_codex_homes(root=root)

    assert first.path.exists()
    assert second.path.exists()
    assert symlink_entry.is_symlink()
    assert sentinel.exists()
    assert unmarked.exists()
    assert foreign_marker.exists()
    first.cleanup()
    second.cleanup()


def test_reconciliation_removes_only_marker_valid_abandoned_home(tmp_path: Path):
    source = _source_home(tmp_path)
    root = tmp_path / "isolated-root"
    home = create_isolated_codex_home(source, "", root=root)
    abandoned_path = home.path
    fcntl.flock(home.lock_fd, fcntl.LOCK_UN)
    os.close(home.lock_fd)
    home.lock_fd = -1

    removed = reconcile_isolated_codex_homes(root=root)

    assert removed == 1
    assert not abandoned_path.exists()


def test_isolated_root_refuses_symlink_or_insecure_mode(tmp_path: Path):
    source = _source_home(tmp_path)
    insecure = tmp_path / "insecure-root"
    insecure.mkdir(mode=0o777)
    insecure.chmod(0o777)
    linked = tmp_path / "linked-root"
    linked.symlink_to(insecure, target_is_directory=True)

    with pytest.raises(ValueError, match="could not be isolated safely"):
        create_isolated_codex_home(source, "", root=insecure)
    with pytest.raises(ValueError, match="could not be isolated safely"):
        create_isolated_codex_home(source, "", root=linked)

    assert stat.S_IMODE(insecure.lstat().st_mode) == 0o777


def test_normal_cleanup_syncs_new_and_updated_session_files(tmp_path: Path):
    source = _source_home(tmp_path)
    session_id = "019ff6ad-c139-7411-9169-6220e8b39688"
    session_dir = source / "sessions" / "2026" / "08" / "13"
    session_dir.mkdir(parents=True, mode=0o700)
    existing = session_dir / f"rollout-2026-08-13T00-00-00-{session_id}.jsonl"
    existing.write_text("before\n", encoding="utf-8")
    existing.chmod(0o600)
    original_inode = existing.stat().st_ino
    root = tmp_path / "isolated-root"
    home = create_isolated_codex_home(
        source,
        "",
        root=root,
        provider_session_ref=session_id,
    )
    isolated_session_dir = home.path / "sessions" / "2026" / "08" / "13"
    (isolated_session_dir / existing.name).write_text(
        "before\nafter\n", encoding="utf-8"
    )
    (isolated_session_dir / "new.jsonl").write_text("new\n", encoding="utf-8")

    home.cleanup()

    assert existing.read_text(encoding="utf-8") == "before\nafter\n"
    assert existing.stat().st_ino != original_inode
    assert (session_dir / "new.jsonl").read_text(encoding="utf-8") == "new\n"


def test_session_sync_prevalidates_entire_tree_before_any_destination_change(
    tmp_path: Path,
):
    source, home, first, second = _two_session_home(tmp_path)
    initial = {
        first: (
            first.read_bytes(),
            first.stat().st_ino,
            stat.S_IMODE(first.stat().st_mode),
        ),
        second: (
            second.read_bytes(),
            second.stat().st_ino,
            stat.S_IMODE(second.stat().st_mode),
        ),
    }
    late_source_dir = home.path / "sessions" / "zz-late"
    late_source_dir.mkdir(mode=0o700)
    (late_source_dir / "session.jsonl").write_text("new\n", encoding="utf-8")
    late_collision = source / "sessions" / "zz-late"
    late_collision.write_text("not a directory\n", encoding="utf-8")

    with pytest.raises(ValueError, match="could not be isolated safely"):
        home.cleanup()

    for path, expected in initial.items():
        actual = (
            path.read_bytes(),
            path.stat().st_ino,
            stat.S_IMODE(path.stat().st_mode),
        )
        assert actual == expected
    assert late_collision.read_text(encoding="utf-8") == "not a directory\n"
    _assert_no_sync_artifacts(source / "sessions")


def test_session_sync_rolls_back_first_file_when_second_replace_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    source, home, first, second = _two_session_home(tmp_path)
    initial = {
        first: (first.read_bytes(), stat.S_IMODE(first.stat().st_mode)),
        second: (second.read_bytes(), stat.S_IMODE(second.stat().st_mode)),
    }
    real_replace = isolated_home_module.os.replace
    stage_replacements = 0

    def fail_second_stage(source_name, destination_name, *args, **kwargs):
        nonlocal stage_replacements
        if str(source_name).startswith(".workbench-sync-stage-"):
            stage_replacements += 1
            if stage_replacements == 2:
                raise OSError("injected second replacement failure")
        return real_replace(source_name, destination_name, *args, **kwargs)

    monkeypatch.setattr(isolated_home_module.os, "replace", fail_second_stage)

    with pytest.raises(ValueError, match="could not be isolated safely"):
        home.cleanup()

    for path, expected in initial.items():
        assert (path.read_bytes(), stat.S_IMODE(path.stat().st_mode)) == expected
    _assert_no_sync_artifacts(source / "sessions")


def test_session_sync_staging_failure_leaves_every_original_unchanged(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    source, home, first, second = _two_session_home(tmp_path)
    initial = {
        first: (
            first.read_bytes(),
            first.stat().st_ino,
            stat.S_IMODE(first.stat().st_mode),
        ),
        second: (
            second.read_bytes(),
            second.stat().st_ino,
            stat.S_IMODE(second.stat().st_mode),
        ),
    }
    real_fsync = isolated_home_module.os.fsync
    regular_file_fsyncs = 0

    def fail_second_staged_file_fsync(fd):
        nonlocal regular_file_fsyncs
        if stat.S_ISREG(os.fstat(fd).st_mode):
            regular_file_fsyncs += 1
            if regular_file_fsyncs == 2:
                raise OSError("injected staging failure")
        return real_fsync(fd)

    monkeypatch.setattr(isolated_home_module.os, "fsync", fail_second_staged_file_fsync)

    with pytest.raises(ValueError, match="could not be isolated safely"):
        home.cleanup()

    for path, expected in initial.items():
        actual = (
            path.read_bytes(),
            path.stat().st_ino,
            stat.S_IMODE(path.stat().st_mode),
        )
        assert actual == expected
    _assert_no_sync_artifacts(source / "sessions")


def test_session_sync_commits_all_files_and_removes_journal_artifacts(tmp_path: Path):
    source, home, first, second = _two_session_home(tmp_path)

    home.cleanup()

    assert first.read_text(encoding="utf-8") == "first after\n"
    assert second.read_text(encoding="utf-8") == "second after\n"
    assert stat.S_IMODE(first.stat().st_mode) == 0o600
    assert stat.S_IMODE(second.stat().st_mode) == 0o600
    _assert_no_sync_artifacts(source / "sessions")


def test_session_sync_rolls_back_when_commit_directory_fsync_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    source, home, first, second = _two_session_home(tmp_path)
    initial = {first: first.read_bytes(), second: second.read_bytes()}
    real_replace = isolated_home_module.os.replace
    real_fsync = isolated_home_module.os.fsync
    committed = False
    failed = False

    def observe_replace(source_name, destination_name, *args, **kwargs):
        nonlocal committed
        result = real_replace(source_name, destination_name, *args, **kwargs)
        if str(source_name).startswith(".workbench-sync-stage-"):
            committed = True
        return result

    def fail_first_commit_directory_fsync(fd):
        nonlocal failed
        if committed and not failed and stat.S_ISDIR(os.fstat(fd).st_mode):
            failed = True
            raise OSError("injected commit fsync failure")
        return real_fsync(fd)

    monkeypatch.setattr(isolated_home_module.os, "replace", observe_replace)
    monkeypatch.setattr(isolated_home_module.os, "fsync", fail_first_commit_directory_fsync)

    with pytest.raises(ValueError, match="could not be isolated safely"):
        home.cleanup()

    assert first.read_bytes() == initial[first]
    assert second.read_bytes() == initial[second]
    _assert_no_sync_artifacts(source / "sessions")


def test_session_sync_detects_concurrent_destination_change_and_does_not_overwrite_it(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    source, home, first, second = _two_session_home(tmp_path)
    first_before = first.read_bytes()
    real_replace = isolated_home_module.os.replace
    changed = False

    def change_second_after_first_commit(source_name, destination_name, *args, **kwargs):
        nonlocal changed
        result = real_replace(source_name, destination_name, *args, **kwargs)
        if str(source_name).startswith(".workbench-sync-stage-") and not changed:
            second.write_text("external concurrent change\n", encoding="utf-8")
            changed = True
        return result

    monkeypatch.setattr(
        isolated_home_module.os,
        "replace",
        change_second_after_first_commit,
    )

    with pytest.raises(ValueError, match="could not be isolated safely"):
        home.cleanup()

    assert first.read_bytes() == first_before
    assert second.read_text(encoding="utf-8") == "external concurrent change\n"
    _assert_no_sync_artifacts(source / "sessions")


def test_prepared_journal_recovers_after_sigkill_then_allows_complete_sync(
    tmp_path: Path,
):
    source, initial_home, first, second = _two_session_home(tmp_path)
    root = initial_home.root
    initial_home.cleanup(sync_sessions=False)
    checkpoint = tmp_path / "first-replace.checkpoint"
    process = _start_crashing_sync(
        source,
        root,
        checkpoint,
        checkpoint_kind="first_replace",
    )
    try:
        _wait_for_path(checkpoint, process)
        journal = source / ".workbench-session-sync" / "journal.json"
        payload = journal.read_text(encoding="utf-8")
        parsed_payload = json.loads(payload)
        assert all(
            len(entry["stage_digest"]) == 64
            and entry["stage_digest"] == entry["stage_digest"].lower()
            for entry in parsed_payload["entries"]
        )
        assert "first before" not in payload
        assert "second before" not in payload
        assert "first after" not in payload
        assert "second after" not in payload
        assert stat.S_IMODE(journal.stat().st_mode) == 0o600
        assert stat.S_IMODE(journal.parent.stat().st_mode) == 0o700
        process.send_signal(signal.SIGKILL)
        assert process.wait(timeout=5) < 0

        recovery_home = create_isolated_codex_home(
            source,
            "",
            root=root,
            provider_session_ref="019ff6ad-c139-7411-9169-6220e8b39688",
        )
        [isolated_first] = list(
            (recovery_home.path / "sessions").rglob(
                "*019ff6ad-c139-7411-9169-6220e8b39688.jsonl"
            )
        )
        isolated_first.write_text("final first\n", encoding="utf-8")
        (isolated_first.parent / second.name).write_text(
            "final second\n", encoding="utf-8"
        )
        recovery_home.cleanup()

        assert first.read_text(encoding="utf-8") == "final first\n"
        assert second.read_text(encoding="utf-8") == "final second\n"
        assert not journal.exists()
        _assert_no_sync_artifacts(source / "sessions")
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)


@pytest.mark.parametrize(
    "checkpoint_kind",
    ["after_intent", "first_dir", "first_stage", "first_backup"],
)
def test_preparing_journal_recovers_crashes_before_commit_without_partial_state(
    tmp_path: Path,
    checkpoint_kind: str,
):
    source, initial_home, first, second = _two_session_home(tmp_path)
    root = initial_home.root
    initial_home.cleanup(sync_sessions=False)
    checkpoint = tmp_path / f"{checkpoint_kind}.checkpoint"
    process = _start_preparation_crash(
        source,
        root,
        checkpoint,
        checkpoint_kind=checkpoint_kind,
    )
    try:
        _wait_for_path(checkpoint, process)
        journal = source / ".workbench-session-sync" / "journal.json"
        preparing = json.loads(journal.read_text(encoding="utf-8"))
        assert preparing["phase"] == "preparing"
        assert {entry["relative"] for entry in preparing["directories"]} >= {
            ".",
            "new-tree",
            "new-tree/nested",
        }
        process.send_signal(signal.SIGKILL)
        assert process.wait(timeout=5) < 0

        with isolated_home_module._session_sync_lock(source, root):
            pass

        assert first.read_text(encoding="utf-8") == "first before\n"
        assert second.read_text(encoding="utf-8") == "second before\n"
        assert not (source / "sessions" / "new-tree").exists()
        assert not journal.exists()
        _assert_no_sync_artifacts(source / "sessions")
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)


def test_no_journal_never_deletes_user_file_with_reserved_artifact_name(tmp_path: Path):
    source = _source_home(tmp_path)
    sessions = source / "sessions"
    sessions.mkdir(mode=0o700)
    reserved = sessions / f".workbench-sync-stage-{uuid.uuid4().hex}-00000000"
    reserved.write_text("user-owned content\n", encoding="utf-8")
    reserved.chmod(0o600)
    root = tmp_path / "isolated-root"

    with isolated_home_module._session_sync_lock(source, root):
        pass

    home = create_isolated_codex_home(source, "", root=root)
    home.cleanup(sync_sessions=False)
    reconcile_isolated_codex_homes(root=root)

    assert reserved.read_text(encoding="utf-8") == "user-owned content\n"


def test_external_exact_missing_directory_collision_is_preserved(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    source, home, first, _second = _two_session_home(tmp_path)
    isolated_new = home.path / "sessions" / "collision-dir"
    isolated_new.mkdir(mode=0o700)
    (isolated_new / "new.jsonl").write_text("new\n", encoding="utf-8")
    external = source / "sessions" / "collision-dir"
    original = isolated_home_module._create_missing_session_directories
    original_identity = isolated_home_module._directory_identity_or_missing
    created = False
    hid_collision = False

    def collide(plan, **kwargs):
        nonlocal created
        external.mkdir(mode=0o700)
        created = True
        return original(plan, **kwargs)

    def hide_collision_once(path):
        nonlocal hid_collision
        if path == external and path.exists() and not hid_collision:
            hid_collision = True
            return None
        return original_identity(path)

    monkeypatch.setattr(
        isolated_home_module,
        "_create_missing_session_directories",
        collide,
    )
    monkeypatch.setattr(
        isolated_home_module,
        "_directory_identity_or_missing",
        hide_collision_once,
    )

    with pytest.raises(ValueError, match="could not be isolated safely"):
        home.cleanup()

    assert created
    assert hid_collision
    assert external.is_dir()
    assert first.read_text(encoding="utf-8") == "first before\n"


def test_external_exact_preparation_artifact_collision_is_preserved(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    source, home, first, _second = _two_session_home(tmp_path)
    original = isolated_home_module._copy_validated_file_to_new
    collided_name = ""

    def collide(source_path, expected, destination_fd, destination_name, **kwargs):
        nonlocal collided_name
        if not collided_name and str(destination_name).startswith(
            ".workbench-sync-stage-"
        ):
            collided_name = str(destination_name)
            fd = os.open(
                collided_name,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                0o600,
                dir_fd=destination_fd,
            )
            try:
                os.write(fd, b"external artifact\n")
                os.fsync(fd)
            finally:
                os.close(fd)
        return original(
            source_path,
            expected,
            destination_fd,
            destination_name,
            **kwargs,
        )

    monkeypatch.setattr(
        isolated_home_module,
        "_copy_validated_file_to_new",
        collide,
    )

    with pytest.raises(ValueError, match="could not be isolated safely"):
        home.cleanup()

    assert collided_name
    assert any(
        path.read_bytes() == b"external artifact\n"
        for path in source.rglob(collided_name)
    )
    assert first.read_text(encoding="utf-8") == "first before\n"


def test_null_artifact_identity_never_authorizes_existing_object_deletion(
    tmp_path: Path,
):
    source = _source_home(tmp_path)
    sessions = source / "sessions"
    sessions.mkdir(mode=0o700)
    transaction_id = uuid.uuid4().hex
    artifact_name = f".workbench-sync-stage-{transaction_id}-00000000"
    artifact = sessions / artifact_name
    artifact.write_text("external\n", encoding="utf-8")
    artifact.chmod(0o600)
    root = tmp_path / "isolated-root"
    with isolated_home_module._session_sync_lock(source, root) as sync_state:
        transaction = isolated_home_module._create_session_sync_transaction(
            sync_state, transaction_id
        )
    state = source / ".workbench-session-sync"
    source_metadata = source.stat()
    sessions_metadata = sessions.stat()
    payload = {
        "version": 2,
        "transaction_id": transaction_id,
        "transaction_root": transaction.name,
        "transaction_root_identity": {
            "device": transaction.identity[0],
            "inode": transaction.identity[1],
        },
        "phase": "preparing",
        "source_device": source_metadata.st_dev,
        "source_inode": source_metadata.st_ino,
        "created_directories": [],
        "directories": [
            {
                "relative": ".",
                "existed": True,
                "initial_identity": {
                    "device": sessions_metadata.st_dev,
                    "inode": sessions_metadata.st_ino,
                },
                "prepared_identity": None,
            }
        ],
        "entries": [
            {
                "relative": "provider.jsonl",
                "existed": False,
                "original_identity": None,
                "backup_identity": None,
                "stage_identity": None,
                "stage_digest": None,
                "backup_name": f".workbench-sync-backup-{transaction_id}-00000000",
                "stage_name": artifact_name,
            }
        ],
    }
    journal = state / "journal.json"
    journal.write_text(
        json.dumps(payload, sort_keys=True, separators=(",", ":")),
        encoding="utf-8",
    )
    journal.chmod(0o600)
    os.close(transaction.fd)

    with isolated_home_module._session_sync_lock(source, root):
        pass

    assert artifact.read_text(encoding="utf-8") == "external\n"


def test_unmarked_transaction_like_state_child_survives_reconciliation(tmp_path: Path):
    source = _source_home(tmp_path)
    root = tmp_path / "isolated-root"
    with isolated_home_module._session_sync_lock(source, root):
        pass
    state = source / ".workbench-session-sync"
    unmarked = state / f"tx-{uuid.uuid4().hex}"
    unmarked.mkdir(mode=0o700)
    sentinel = unmarked / "sentinel"
    sentinel.write_text("keep\n", encoding="utf-8")

    with isolated_home_module._session_sync_lock(source, root):
        pass

    assert sentinel.read_text(encoding="utf-8") == "keep\n"


def test_committed_journal_after_sigkill_keeps_all_updates_and_only_cleans(
    tmp_path: Path,
):
    source, initial_home, first, second = _two_session_home(tmp_path)
    root = initial_home.root
    initial_home.cleanup(sync_sessions=False)
    checkpoint = tmp_path / "committed.checkpoint"
    process = _start_crashing_sync(
        source,
        root,
        checkpoint,
        checkpoint_kind="committed",
    )
    try:
        _wait_for_path(checkpoint, process)
        journal = source / ".workbench-session-sync" / "journal.json"
        assert json.loads(journal.read_text(encoding="utf-8"))["phase"] == "committed"
        process.send_signal(signal.SIGKILL)
        assert process.wait(timeout=5) < 0

        with isolated_home_module._session_sync_lock(source, root):
            pass

        assert first.read_text(encoding="utf-8") == "first after\n"
        assert second.read_text(encoding="utf-8") == "second after\n"
        assert not journal.exists()
        _assert_no_sync_artifacts(source / "sessions")
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)


def test_prepared_recovery_preserves_same_inode_external_writer_with_spoofed_mtime(
    tmp_path: Path,
):
    source, initial_home, first, second = _two_session_home(tmp_path)
    root = initial_home.root
    initial_home.cleanup(sync_sessions=False)
    checkpoint = tmp_path / "external-writer.checkpoint"
    process = _start_crashing_sync(
        source,
        root,
        checkpoint,
        checkpoint_kind="first_replace",
    )
    try:
        _wait_for_path(checkpoint, process)
        installed = first.stat()
        external_content = b"external!!!\n"
        assert len(external_content) == len(first.read_bytes())
        with first.open("r+b") as stream:
            stream.write(external_content)
            stream.flush()
            os.fsync(stream.fileno())
        first.chmod(stat.S_IMODE(installed.st_mode))
        os.utime(first, ns=(installed.st_atime_ns, installed.st_mtime_ns))
        modified = first.stat()
        assert modified.st_ino == installed.st_ino
        assert modified.st_size == installed.st_size
        assert stat.S_IMODE(modified.st_mode) == stat.S_IMODE(installed.st_mode)
        assert modified.st_mtime_ns == installed.st_mtime_ns
        process.send_signal(signal.SIGKILL)
        assert process.wait(timeout=5) < 0

        with pytest.raises(ValueError, match="could not be isolated safely"):
            with isolated_home_module._session_sync_lock(source, root):
                pass

        assert first.read_bytes() == external_content
        assert second.read_text(encoding="utf-8") == "second before\n"
        assert not (source / ".workbench-session-sync" / "journal.json").exists()
        _assert_no_sync_artifacts(source / "sessions")
        with isolated_home_module._session_sync_lock(source, root):
            pass
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)


def test_stable_session_hash_fails_closed_when_file_changes_during_read(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    session = tmp_path / "session.jsonl"
    session.write_bytes(b"a" * (128 * 1024))
    session.chmod(0o600)
    parent_fd = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    identity = isolated_home_module._file_identity(session)
    real_read = isolated_home_module.os.read
    changed = False

    def mutate_after_first_chunk(fd: int, length: int) -> bytes:
        nonlocal changed
        chunk = real_read(fd, length)
        if fd != parent_fd and chunk and not changed:
            with session.open("r+b") as stream:
                stream.seek(70 * 1024)
                stream.write(b"different")
                stream.flush()
                os.fsync(stream.fileno())
            changed = True
        return chunk

    monkeypatch.setattr(isolated_home_module.os, "read", mutate_after_first_chunk)
    try:
        with pytest.raises(ValueError, match="could not be isolated safely"):
            isolated_home_module._stable_file_digest_at(
                parent_fd,
                session.name,
                expected=identity,
            )
    finally:
        os.close(parent_fd)

    assert changed


def test_invalid_symlink_journal_is_quarantined_without_touching_outside(
    tmp_path: Path,
):
    source = _source_home(tmp_path)
    root = tmp_path / "isolated-root"
    home = create_isolated_codex_home(source, "", root=root)
    home.cleanup(sync_sessions=False)
    state = source / ".workbench-session-sync"
    state.mkdir(mode=0o700)
    state.chmod(0o700)
    outside = tmp_path / "outside-journal"
    outside.write_text("do not touch\n", encoding="utf-8")
    (state / "journal.json").symlink_to(outside)

    with pytest.raises(ValueError, match="could not be isolated safely"):
        with isolated_home_module._session_sync_lock(source, root):
            pass

    assert outside.read_text(encoding="utf-8") == "do not touch\n"
    assert not (state / "journal.json").exists()
    assert any(path.name.startswith("journal.invalid-") for path in state.iterdir())

    source_metadata = source.stat()
    foreign_journal = state / "journal.json"
    foreign_journal.write_text(
        json.dumps(
            {
                "version": 1,
                "transaction_id": uuid.uuid4().hex,
                "phase": "prepared",
                "source_device": source_metadata.st_dev,
                "source_inode": source_metadata.st_ino + 1,
                "created_directories": ["../../outside-journal"],
                "entries": [],
            },
            sort_keys=True,
            separators=(",", ":"),
        ),
        encoding="utf-8",
    )
    foreign_journal.chmod(0o600)

    with pytest.raises(ValueError, match="could not be isolated safely"):
        with isolated_home_module._session_sync_lock(source, root):
            pass

    assert outside.read_text(encoding="utf-8") == "do not touch\n"
    assert not foreign_journal.exists()
    assert sum(
        path.name.startswith("journal.invalid-") for path in state.iterdir()
    ) == 2

    transaction_id = uuid.uuid4().hex
    invalid_target = {
        "version": 2,
        "transaction_id": transaction_id,
        "phase": "prepared",
        "source_device": source_metadata.st_dev,
        "source_inode": source_metadata.st_ino,
        "created_directories": [],
        "directories": [],
        "entries": [
            {
                "relative": "../../outside-journal",
                "existed": False,
                "original_identity": None,
                "backup_identity": None,
                "stage_identity": None,
                "stage_digest": None,
                "backup_name": (
                    f".workbench-sync-backup-{transaction_id}-00000000"
                ),
                "stage_name": f".workbench-sync-stage-{transaction_id}-00000000",
            }
        ],
    }
    foreign_journal.write_text(
        json.dumps(invalid_target, sort_keys=True, separators=(",", ":")),
        encoding="utf-8",
    )
    foreign_journal.chmod(0o600)

    with pytest.raises(ValueError, match="could not be isolated safely"):
        with isolated_home_module._session_sync_lock(source, root):
            pass

    assert outside.read_text(encoding="utf-8") == "do not touch\n"
    assert sum(
        path.name.startswith("journal.invalid-") for path in state.iterdir()
    ) == 3


@pytest.mark.parametrize("relative", ["bad\0name.jsonl", f"{'x' * 256}.jsonl"])
def test_invalid_journal_component_is_quarantined_once_without_filesystem_access(
    tmp_path: Path,
    relative: str,
):
    source = _source_home(tmp_path)
    root = tmp_path / "isolated-root"
    home = create_isolated_codex_home(source, "", root=root)
    home.cleanup(sync_sessions=False)
    state = source / ".workbench-session-sync"
    state.mkdir(mode=0o700)
    state.chmod(0o700)
    source_metadata = source.stat()
    transaction_id = uuid.uuid4().hex
    payload = {
        "version": 2,
        "transaction_id": transaction_id,
        "phase": "prepared",
        "source_device": source_metadata.st_dev,
        "source_inode": source_metadata.st_ino,
        "created_directories": ["."],
        "directories": [
            {"relative": ".", "existed": False, "initial_identity": None}
        ],
        "entries": [
            {
                "relative": relative,
                "existed": False,
                "original_identity": None,
                "backup_identity": None,
                "stage_identity": None,
                "stage_digest": None,
                "backup_name": f".workbench-sync-backup-{transaction_id}-00000000",
                "stage_name": f".workbench-sync-stage-{transaction_id}-00000000",
            }
        ],
    }
    journal = state / "journal.json"
    journal.write_text(
        json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":")),
        encoding="utf-8",
    )
    journal.chmod(0o600)

    with pytest.raises(ValueError, match="could not be isolated safely"):
        with isolated_home_module._session_sync_lock(source, root):
            pass

    assert not journal.exists()
    assert len(list(state.glob("journal.invalid-*"))) == 1
    with isolated_home_module._session_sync_lock(source, root):
        pass


def test_all_isolated_state_files_are_distinct_and_mutations_do_not_touch_sources(
    tmp_path: Path,
):
    source = _source_home(tmp_path)
    session_id = "019ff6ad-c139-7411-9169-6220e8b39688"
    session_dir = source / "sessions" / "2026" / "08" / "13"
    session_dir.mkdir(parents=True, mode=0o700)
    session = session_dir / f"rollout-2026-08-13T00-00-00-{session_id}.jsonl"
    session.write_text("original session\n", encoding="utf-8")
    session.chmod(0o600)
    root = tmp_path / "isolated-root"
    home = create_isolated_codex_home(
        source,
        "model = 'safe'\n",
        root=root,
        provider_session_ref=session_id,
    )
    relative_paths = (
        Path("config.toml"),
        Path("auth.json"),
        Path("skills/SKILL.md"),
        Path("rules/default.rules"),
        Path("plugins/tool.sh"),
        session.relative_to(source),
    )
    original_state = {
        relative: (
            (source / relative).read_bytes(),
            stat.S_IMODE((source / relative).stat().st_mode),
        )
        for relative in relative_paths
    }

    for index, relative in enumerate(relative_paths):
        original = source / relative
        isolated = home.path / relative
        assert (isolated.stat().st_dev, isolated.stat().st_ino) != (
            original.stat().st_dev,
            original.stat().st_ino,
        )
        expected_mode = (
            0o700 if stat.S_IMODE(original.stat().st_mode) & 0o111 else 0o600
        )
        assert stat.S_IMODE(isolated.stat().st_mode) == expected_mode
        if index % 3 == 0:
            isolated.write_bytes(b"")
        elif index % 3 == 1:
            isolated.chmod(0o400)
        else:
            isolated.unlink()

    for relative, (content, mode) in original_state.items():
        original = source / relative
        assert original.read_bytes() == content
        assert stat.S_IMODE(original.stat().st_mode) == mode

    assert remove_verified_isolated_home(
        home.path,
        home.marker_token,
        home.lock_fd,
        root=root,
    )
    fcntl.flock(home.lock_fd, fcntl.LOCK_UN)
    os.close(home.lock_fd)
    home.lock_fd = -1

    assert session.read_text(encoding="utf-8") == "original session\n"
