import fcntl
import os
import stat
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
