import sqlite3
from pathlib import Path

import pytest

from app.chrome_cookie_snapshot import (
    prepare_profile_from_snapshot,
    snapshot_cookies_path,
    sync_chrome_cookie_snapshot,
)


def _chrome_cookies(path: Path, hosts: list[str]) -> Path:
    with sqlite3.connect(path) as db:
        db.execute("create table cookies (host_key text, name text, encrypted_value blob)")
        db.executemany(
            "insert into cookies values (?, 'sid', x'763130')", [(host,) for host in hosts]
        )
    return path


def _hosts(path: Path) -> list[str]:
    with sqlite3.connect(path) as db:
        return sorted(row[0] for row in db.execute("select host_key from cookies"))


def test_the_copy_drops_only_the_denied_domains_and_their_subdomains(tmp_path: Path):
    source = _chrome_cookies(
        tmp_path / "Cookies",
        [
            "github.com", ".github.com", "chase.com", ".chase.com", "www.chase.com",
            ".secure.chase.com", "notchase.com", "chase.com.evil.example", ".paypal.com",
        ],
    )

    counts = sync_chrome_cookie_snapshot(
        source=source, root=tmp_path / "snap", deny=("chase.com", "paypal.com")
    )

    assert _hosts(snapshot_cookies_path(tmp_path / "snap")) == [
        ".github.com", "chase.com.evil.example", "github.com", "notchase.com",
    ]
    assert counts == {"source_cookies": 9, "kept_cookies": 4, "removed_cookies": 5}
    assert len(_hosts(source)) == 9, "the source is never modified"


def test_a_sync_replaces_the_previous_copy_whole(tmp_path: Path):
    source = _chrome_cookies(tmp_path / "Cookies", ["github.com"])
    root = tmp_path / "snap"
    sync_chrome_cookie_snapshot(source=source, root=root, deny=())
    with sqlite3.connect(source) as db:
        db.execute("insert into cookies values ('luma.com', 'sid', x'763130')")

    sync_chrome_cookie_snapshot(source=source, root=root, deny=())

    assert _hosts(snapshot_cookies_path(root)) == ["github.com", "luma.com"]
    assert [p.name for p in snapshot_cookies_path(root).parent.iterdir()] == ["Cookies"]


def test_a_missing_chrome_store_is_an_error_not_an_empty_copy(tmp_path: Path):
    with pytest.raises(FileNotFoundError):
        sync_chrome_cookie_snapshot(source=tmp_path / "nope", root=tmp_path / "snap", deny=())
    assert not snapshot_cookies_path(tmp_path / "snap").exists()


def test_a_profile_gets_the_current_copy_and_no_stale_sidecars(tmp_path: Path, monkeypatch):
    source = _chrome_cookies(tmp_path / "Cookies", ["github.com"])
    root = tmp_path / "snap"
    sync_chrome_cookie_snapshot(source=source, root=root, deny=())
    monkeypatch.setattr("app.chrome_cookie_snapshot.snapshot_root", lambda: root)
    profile = tmp_path / "profile"
    (profile / "Default").mkdir(parents=True)
    (profile / "Default" / "Cookies-journal").write_bytes(b"stale")

    assert prepare_profile_from_snapshot(profile) is True

    assert _hosts(profile / "Default" / "Cookies") == ["github.com"]
    assert not (profile / "Default" / "Cookies-journal").exists()


def test_without_a_copy_the_profile_is_left_alone(tmp_path: Path, monkeypatch):
    monkeypatch.setattr("app.chrome_cookie_snapshot.snapshot_root", lambda: tmp_path / "none")

    assert prepare_profile_from_snapshot(tmp_path / "profile") is False
    assert not (tmp_path / "profile").exists()


def test_the_daily_command_refuses_until_the_finance_list_exists(tmp_path: Path, monkeypatch):
    from app.cli import sync_chrome_cookies_command

    source = _chrome_cookies(tmp_path / "Cookies", ["github.com", "chase.com"])
    monkeypatch.setattr("app.config.effective_env_values", lambda *a, **k: {"CEO_CHROME_COOKIES_PATH": str(source)})
    monkeypatch.setattr("app.chrome_cookie_snapshot.snapshot_root", lambda: tmp_path / "snap")

    with pytest.raises(RuntimeError, match="CEO_CHROME_COOKIE_DENY_DOMAINS"):
        sync_chrome_cookies_command(None)
    assert not snapshot_cookies_path(tmp_path / "snap").exists()


def test_the_daily_command_copies_everything_but_the_listed_domains(tmp_path: Path, monkeypatch, capsys):
    from app.cli import sync_chrome_cookies_command

    source = _chrome_cookies(tmp_path / "Cookies", ["github.com", ".luma.com", "chase.com", "www.paypal.com"])
    monkeypatch.setattr(
        "app.config.effective_env_values",
        lambda *a, **k: {
            "CEO_CHROME_COOKIES_PATH": str(source),
            "CEO_CHROME_COOKIE_DENY_DOMAINS": " Chase.com, .paypal.com ,",
        },
    )
    monkeypatch.setattr("app.chrome_cookie_snapshot.snapshot_root", lambda: tmp_path / "snap")

    summary = sync_chrome_cookies_command(None)

    assert summary == "kept=2 removed=2 of=4 denied_domains=2"
    assert _hosts(snapshot_cookies_path(tmp_path / "snap")) == [".luma.com", "github.com"]
    assert "sync-chrome-cookies kept=2" in capsys.readouterr().out
