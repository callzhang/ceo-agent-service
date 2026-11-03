"""Read a view-only DingTalk Drive file through its web preview page.

Derek, 2026-11-02: a file shared with us as "仅可查看" (READER) cannot be
downloaded -- the Drive API refuses it under leak protection -- yet the same
account can open its preview page. The page is the alidocs pdf.js viewer: one
`.page` element per document page, each rendered to a canvas only once it is
scrolled into view, and no text layer. So the readable form is an image of
each page, which the Agent passes to OCR.

The preview opens in the service's shared headless Chrome
(`launch_service_chrome`), on a profile of its own. Chrome allows one process
per profile, so captures on this machine take turns on a file lock.
"""

from __future__ import annotations

import fcntl
import re
from dataclasses import dataclass
from pathlib import Path

PREVIEW_URL = "https://alidocs.dingtalk.com/i/nodes/{node_id}"
LOGIN_HOST = "login.dingtalk.com"
MAX_PREVIEW_PAGES = 30
_NODE_ID = re.compile(r"[A-Za-z0-9]{8,64}")
_PAGE_SELECTOR = ".pdf-container .page"
_LOAD_SETTLE_MS = 12_000
_PAGE_SETTLE_MS = 1_500


class DrivePreviewError(RuntimeError):
    """The preview page could not be read; the message says why."""


@dataclass(frozen=True)
class DrivePreview:
    title: str
    page_count: int
    pages: tuple[bytes, ...]

    @property
    def truncated(self) -> bool:
        return self.page_count > len(self.pages)


def _profile_dir() -> Path:
    from app import config as app_config

    return app_config.worker_db_path().parent / "drive-preview-profile"


def capture_drive_preview(
    node_id: str, *, profile_dir: Path | None = None, playwright_factory=None,
) -> DrivePreview:
    """Open one Drive node's preview and return a PNG of each rendered page."""
    if not _NODE_ID.fullmatch(node_id or ""):
        raise DrivePreviewError("drive_preview_node_id_invalid")
    if playwright_factory is None:
        from playwright.sync_api import sync_playwright as playwright_factory
    from app.service_browser import launch_service_chrome

    profile = profile_dir or _profile_dir()
    profile.parent.mkdir(parents=True, exist_ok=True)
    with open(profile.with_name(profile.name + ".lock"), "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        with playwright_factory() as playwright:
            # Chrome ignores --restore-last-session for a new profile, and
            # without it the copied session cookies are dropped (see
            # minutes_console_browser.signed_in_console).
            if not (profile / "Default" / "Preferences").is_file():
                launch_service_chrome(playwright, profile).close()
            context = launch_service_chrome(
                playwright, profile, args=["--restore-last-session"],
                viewport={"width": 1400, "height": 1800}, device_scale_factor=2,
            )
            try:
                page = context.new_page()
                for restored in tuple(context.pages):
                    if restored is not page:
                        restored.close()
                return _read_preview(page, node_id)
            finally:
                try:
                    for open_page in context.pages:
                        open_page.goto("about:blank", wait_until="domcontentloaded")
                finally:
                    context.close()


def _read_preview(page, node_id: str) -> DrivePreview:
    page.goto(
        PREVIEW_URL.format(node_id=node_id),
        wait_until="domcontentloaded", timeout=60_000,
    )
    page.wait_for_timeout(_LOAD_SETTLE_MS)
    if LOGIN_HOST in page.url:
        raise DrivePreviewError(
            "drive_preview_login_required: the copy of Derek's Chrome cookies "
            "carries no DingTalk login"
        )
    title = page.title()
    elements = page.query_selector_all(_PAGE_SELECTOR)
    if not elements:
        raise DrivePreviewError(
            f"drive_preview_pages_unavailable: no document pages at {page.url[:120]}"
        )
    images = []
    for element in elements[:MAX_PREVIEW_PAGES]:
        element.scroll_into_view_if_needed()
        page.wait_for_timeout(_PAGE_SETTLE_MS)
        images.append(element.screenshot(type="png"))
    return DrivePreview(title=title, page_count=len(elements), pages=tuple(images))
