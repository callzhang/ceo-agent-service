"""Read a view-only DingTalk Drive file through its web preview page.

Derek, 2026-11-02: a file shared with us as "仅可查看" (READER) cannot be
downloaded -- the Drive API refuses it under leak protection -- yet the same
account can open its preview page. The page embeds its PDF viewer in a child frame: one `.w-page` element
per document page, rendered to a canvas, with no text layer. So the readable form is an image of
each page, which the Agent passes to OCR.

The preview uses a dedicated Chrome profile signed in directly to DingTalk.
Chrome allows one process per profile, so captures on this machine take turns
on a file lock.
"""

from __future__ import annotations

import argparse
import fcntl
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

DINGTALK_DOCS_URL = "https://alidocs.dingtalk.com"
PREVIEW_URL = DINGTALK_DOCS_URL + "/i/nodes/{node_id}"
LOGIN_HOST = "login.dingtalk.com"
MAX_PREVIEW_PAGES = 30
_NODE_ID = re.compile(r"[A-Za-z0-9]{8,64}")
_PAGE_SELECTOR = ".w-page"
_PREVIEW_READY_TIMEOUT_MS = 60_000
_PAGE_POLL_MS = 500
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

    return app_config.worker_db_path().parent / "oa-drive-preview-profile"


def _main() -> None:
    parser = argparse.ArgumentParser(description="Sign in to the OA Drive preview browser.")
    parser.add_argument("--login", action="store_true", required=True)
    parser.parse_args()
    profile = _profile_dir()
    profile.mkdir(mode=0o700, parents=True, exist_ok=True)
    subprocess.run(
        [
            "/usr/bin/open", "-n", "-a", "Google Chrome", "--args",
            f"--user-data-dir={profile}", "--no-first-run", "--no-default-browser-check",
            DINGTALK_DOCS_URL,
        ],
        check=True,
    )


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
            context = launch_service_chrome(
                playwright, profile, initialize_from_snapshot=False,
                args=["--restore-last-session"],
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
    elements = []
    for _ in range(_PREVIEW_READY_TIMEOUT_MS // _PAGE_POLL_MS):
        if LOGIN_HOST in page.url:
            raise DrivePreviewError(
                "drive_preview_login_required: sign in to the dedicated OA DingTalk browser"
            )
        for frame in page.frames:
            elements = frame.query_selector_all(_PAGE_SELECTOR)
            if elements:
                break
        if elements:
            break
        page.wait_for_timeout(_PAGE_POLL_MS)
    if not elements:
        raise DrivePreviewError("drive_preview_pages_unavailable: no rendered document pages")
    title = page.title()
    images = []
    for element in elements[:MAX_PREVIEW_PAGES]:
        element.scroll_into_view_if_needed()
        page.wait_for_timeout(_PAGE_SETTLE_MS)
        images.append(element.screenshot(type="png"))
    return DrivePreview(title=title, page_count=len(elements), pages=tuple(images))


if __name__ == "__main__":
    _main()
