"""A copy of the owner's Chrome cookies for the service's headless browsers.

Derek, 2026-09-25: several tasks are stopped by a login wall the owner is
already past in Chrome (unsubscribe links, the DingTalk AI-minutes access
request), so the service keeps a full copy of his Chrome cookies, refreshed
daily, that any headless task can reuse. Banks, brokers and payment services
are left out; that list is a setting (`CEO_CHROME_COOKIE_DENY_DOMAINS`), not
code.

What makes this work without a keychain prompt: cookie values are encrypted
with a key in the "Chrome Safe Storage" keychain item, which real Chrome may
read on its own. So the copy is opened by real Chrome (Playwright's
`channel="chrome"`) with Playwright's default `--use-mock-keychain` removed;
Chrome decrypts what it needs and nothing here decrypts anything. Rows are
filtered by the plaintext `host_key` column only.

Chrome 136+ refuses remote debugging on its default profile directory, so the
running Chrome is never attached to: only this copy is opened.
"""

from __future__ import annotations

import os
from pathlib import Path
import sqlite3
import tempfile

from app import config as app_config

DENY_DOMAINS_SETTING = "CEO_CHROME_COOKIE_DENY_DOMAINS"
SOURCE_SETTING = "CEO_CHROME_COOKIES_PATH"
_DEFAULT_SOURCE = (
    Path.home() / "Library" / "Application Support" / "Google" / "Chrome" / "Default" / "Cookies"
)

#: Playwright adds these so bundled Chromium never touches the keychain; with
#: them Chrome cannot decrypt the copied cookies.
CHROME_LAUNCH_IGNORED_DEFAULT_ARGS = ("--use-mock-keychain", "--password-store=basic")


def snapshot_root() -> Path:
    """Where the copy lives, beside the service database."""

    return app_config.worker_db_path().parent / "chrome-cookies"


def snapshot_cookies_path(root: Path | None = None) -> Path:
    return (root or snapshot_root()) / "Default" / "Cookies"


def source_cookies_path() -> Path:
    configured = app_config.effective_env_values().get(SOURCE_SETTING, "").strip()
    return Path(configured).expanduser() if configured else _DEFAULT_SOURCE


def deny_domains() -> tuple[str, ...]:
    raw = app_config.effective_env_values().get(DENY_DOMAINS_SETTING, "")
    return tuple(
        sorted({item.strip().lower().lstrip(".") for item in raw.split(",") if item.strip()})
    )


def sync_chrome_cookie_snapshot(
    *,
    source: Path,
    root: Path,
    deny: tuple[str, ...],
) -> dict[str, int]:
    """Replace the copy with the current cookies minus the denied domains.

    The source is read through SQLite's backup API, which is safe while Chrome
    has the file open. The new copy is built beside the old one and swapped in
    whole, so a task that launches Chrome mid-sync sees the old or the new copy,
    never half of one.
    """

    if not source.is_file():
        raise FileNotFoundError(f"Chrome cookie store not found: {source}")
    target = snapshot_cookies_path(root)
    target.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary_name = tempfile.mkstemp(
        prefix=".Cookies-", suffix=".tmp", dir=target.parent
    )
    os.close(handle)
    temporary = Path(temporary_name)
    try:
        origin = sqlite3.connect(f"file:{source}?mode=ro", uri=True, timeout=30)
        try:
            copy = sqlite3.connect(temporary)
            try:
                origin.backup(copy)
                total = int(copy.execute("select count(*) from cookies").fetchone()[0])
                for domain in deny:
                    copy.execute(
                        "delete from cookies where host_key=? or host_key=? "
                        "or host_key like ?",
                        (domain, "." + domain, "%." + domain),
                    )
                copy.commit()
                kept = int(copy.execute("select count(*) from cookies").fetchone()[0])
                copy.execute("vacuum")
            finally:
                copy.close()
        finally:
            origin.close()
        os.chmod(temporary, 0o600)
        for stale in target.parent.glob("Cookies-journal"):
            stale.unlink()
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)
    return {"source_cookies": total, "kept_cookies": kept, "removed_cookies": total - kept}


def prepare_profile_from_snapshot(profile_dir: Path) -> bool:
    """Put the current copy into a headless profile before Chrome opens it.

    Returns whether a copy exists. A profile that never had one keeps working
    as it did (bundled Chromium, no owner cookies).
    """

    source = snapshot_cookies_path()
    if not source.is_file():
        return False
    destination = profile_dir / "Default" / "Cookies"
    destination.parent.mkdir(parents=True, exist_ok=True)
    for sidecar in ("Cookies-journal", "Cookies-wal", "Cookies-shm"):
        (destination.parent / sidecar).unlink(missing_ok=True)
    temporary = destination.with_suffix(".tmp")
    temporary.write_bytes(source.read_bytes())
    os.chmod(temporary, 0o600)
    os.replace(temporary, destination)
    return True
