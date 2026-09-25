from pathlib import Path

import pytest

from app.chrome_cookie_snapshot import sync_chrome_cookie_snapshot
from app.service_browser import launch_service_chrome
import sqlite3


class _Chromium:
    def __init__(self):
        self.calls: list[dict] = []

    def launch_persistent_context(self, **kwargs):
        self.calls.append(kwargs)
        return "context"


class _Playwright:
    def __init__(self):
        self.chromium = _Chromium()


def test_every_service_browser_is_headless_real_chrome_with_the_keychain_left_alone(
    tmp_path: Path, monkeypatch
):
    monkeypatch.setattr("app.chrome_cookie_snapshot.snapshot_root", lambda: tmp_path / "none")
    playwright = _Playwright()

    assert launch_service_chrome(playwright, tmp_path / "profile", extra="kept") == "context"

    (call,) = playwright.chromium.calls
    assert call["channel"] == "chrome" and call["headless"] is True
    assert "--use-mock-keychain" in call["ignore_default_args"]
    assert call["user_data_dir"] == str(tmp_path / "profile") and call["extra"] == "kept"
    with pytest.raises(ValueError, match="headless"):
        launch_service_chrome(playwright, tmp_path / "profile", headless=False)


def test_the_profile_holds_the_current_cookie_copy_when_chrome_opens(tmp_path: Path, monkeypatch):
    source = tmp_path / "Cookies"
    with sqlite3.connect(source) as db:
        db.execute("create table cookies (host_key text, name text, encrypted_value blob)")
        db.execute("insert into cookies values ('github.com', 'sid', x'763130')")
    root = tmp_path / "snap"
    sync_chrome_cookie_snapshot(source=source, root=root, deny=())
    monkeypatch.setattr("app.chrome_cookie_snapshot.snapshot_root", lambda: root)
    seen: list[list[str]] = []

    class Chromium:
        def launch_persistent_context(self, user_data_dir, **_kwargs):
            with sqlite3.connect(Path(user_data_dir) / "Default" / "Cookies") as db:
                seen.append([row[0] for row in db.execute("select host_key from cookies")])
            return "context"

    class Playwright:
        chromium = Chromium()

    launch_service_chrome(Playwright(), tmp_path / "profile")

    assert seen == [["github.com"]]
