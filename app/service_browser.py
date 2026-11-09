"""The one way a service task starts a headless browser that may need a login.

Derek, 2026-09-25: the service keeps a daily copy of his Chrome cookies (see
`chrome_cookie_snapshot`) and every task that drives a browser starts it here,
so a login he already has is not asked for again by each task on its own.

The browser is real Chrome, always headless, on a profile directory of the
caller's own. By default that profile receives the current cookie copy.
Callers that maintain an existing session explicitly skip this initialization.
"""

from __future__ import annotations

from pathlib import Path

from app.chrome_cookie_snapshot import (
    CHROME_LAUNCH_IGNORED_DEFAULT_ARGS,
    prepare_profile_from_snapshot,
)


def launch_service_chrome(
    playwright: object,
    profile_dir: Path,
    *,
    headless: bool = True,
    initialize_from_snapshot: bool = True,
    **kwargs: object,
) -> object:
    """Open real headless Chrome, optionally initializing the owner's cookies."""

    if headless is not True:
        raise ValueError("service browsers must be headless")
    chromium = getattr(playwright, "chromium", None)
    launch = getattr(chromium, "launch_persistent_context", None)
    if not callable(launch):
        raise ValueError("Chromium persistent context is unavailable")
    if initialize_from_snapshot:
        prepare_profile_from_snapshot(profile_dir)
    return launch(
        user_data_dir=str(profile_dir),
        channel="chrome",
        headless=True,
        # Playwright's defaults keep Chrome off the keychain, which is what
        # makes the copied cookies unreadable.
        ignore_default_args=list(CHROME_LAUNCH_IGNORED_DEFAULT_ARGS),
        **kwargs,
    )
