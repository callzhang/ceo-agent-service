import fcntl
import os
import stat
import uuid
from pathlib import Path

import pytest

from app.workbench.isolated_home import (
    create_isolated_codex_home,
    reconcile_isolated_codex_homes,
    remove_verified_isolated_home,
)


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
