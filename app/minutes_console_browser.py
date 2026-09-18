"""The signed-in browser behind `MinutesConsole`.

Kept apart from `app.minutes_access` so the rules there are testable without a
browser, and so everything that knows about page structure sits in one file.
"""

from __future__ import annotations

from contextlib import contextmanager
import json
from pathlib import Path
from typing import Any

from app.minutes_access import (
    MINUTES_CONSOLE_HOST,
    MinutesAccessRequest,
    MinutesBrowserSessionExpired,
    MinutesConsoleUnavailable,
)


HISTORY_URL = f"https://{MINUTES_CONSOLE_HOST}/history"
TRANSCRIBE_URL = "https://shanji.dingtalk.com/app/transcribes/{task_uuid}"
LOGIN_HOST = "login.dingtalk.com"

# The console keeps ten rows a page and its own page number changes before the
# table re-renders, so a walk that waits on the number re-reads the page it was
# already on.  Every wait below is on the first row's key instead.
_PAGE_SETTLE_ATTEMPTS = 60
_PAGE_SETTLE_MS = 400
# The minute page resolves the owner to ask after the panel renders. A page
# still offering no owner has not settled: 149 of 253 minutes looked
# unrequestable at 18 seconds and every one of them resolved when given 36.
_PANEL_SETTLE_ATTEMPTS = 30
_PANEL_SETTLE_MS = 1200
_MAX_CONSOLE_PAGES = 400

_ROWS_JS = r"""
() => {
  const rows = Array.from(document.querySelectorAll('tr[data-row-key]')).map((tr) => {
    const cells = Array.from(tr.querySelectorAll('td'));
    const button = tr.querySelector('button');
    return {
      row_key: tr.getAttribute('data-row-key') || '',
      masked_title: button ? (button.innerText || '').trim() : '',
      role: cells[2] ? (cells[2].innerText || '').trim() : '',
      initiator: cells[3] ? (cells[3].innerText || '').trim() : '',
      size: cells[4] ? (cells[4].innerText || '').trim() : '',
      last_active: cells[5] ? (cells[5].innerText || '').trim() : ''
    };
  });
  return {
    current_page: parseInt(document.querySelector('li.dtd-pagination-item-active')?.getAttribute('title') || '0', 10),
    next_enabled: !!document.querySelector('li.dtd-pagination-next:not(.dtd-pagination-disabled) button:not([disabled])'),
    rows
  };
}
"""

_FIRST_PAGE_JS = """
() => {
  const link = document.querySelector('li.dtd-pagination-item-1 a');
  if (!link) return false;
  link.click();
  return true;
}
"""

_NEXT_PAGE_JS = """
() => {
  const button = document.querySelector('li.dtd-pagination-next:not(.dtd-pagination-disabled) button:not([disabled])');
  if (!button) return false;
  button.click();
  return true;
}
"""

_PANEL_JS = r"""
() => {
  const norm = (value) => ((value || '').replace(/\s+/g, ' ')).trim();
  const button = Array.from(document.querySelectorAll('button')).find(
    (el) => /^(发送申请|Send Application)$/i.test(norm(el.innerText || el.textContent)));
  const body = (document.body.innerText || '').replace(/ /g, ' ').trim();
  const title = body.match(/(?:暂无权限访问|No permission to access)\s*["“”「]?\s*([\s\S]*?)\s*["“”」]?\s*(?:当前账号|Current account|Request permission from)/);
  const owner = body.match(/(?:Request permission from|Applied to|向谁申请)\s*\n?\s*([^\n]+)/);
  return {
    title: title ? title[1].replace(/\s+/g, ' ').trim() : '',
    owner: owner ? owner[1].trim() : '',
    can_request: !!button,
    button_disabled: !!(button && button.disabled),
    owner_unresolved: /(?:Request permission from|向谁申请|申请给)\s*\n?\s*(?:Please select|请选择)/.test(body),
    already_requested: /已发送|申请已发送|等待审批|审批中|已向|Applied to|Reapply|Refresh Page/.test(body),
    permission_panel: location.pathname.includes('/app/permission/'),
    body_head: body.slice(0, 200)
  };
}
"""

_FILL_REASON_JS = """
(reason) => {
  const area = Array.from(document.querySelectorAll('textarea')).find(
    (el) => /Reason|理由|原因/i.test(el.placeholder || '')) || document.querySelector('textarea');
  if (!area) return false;
  const setter = Object.getOwnPropertyDescriptor(window.HTMLTextAreaElement.prototype, 'value').set;
  setter.call(area, reason);
  area.dispatchEvent(new Event('input', {bubbles: true}));
  area.dispatchEvent(new Event('change', {bubbles: true}));
  return true;
}
"""

_SEND_JS = r"""
() => {
  const norm = (value) => ((value || '').replace(/\s+/g, ' ')).trim();
  const button = Array.from(document.querySelectorAll('button')).find(
    (el) => /^(发送申请|Send Application)$/i.test(norm(el.innerText || el.textContent)));
  if (!button || button.disabled) return false;
  button.click();
  return true;
}
"""


class PlaywrightMinutesConsole:
    """Reads the console and sends requests through one headless browser."""

    def __init__(self, page) -> None:
        self._page = page

    def list_backend_minutes(self) -> list[dict[str, Any]]:
        self._page.goto(HISTORY_URL, wait_until="domcontentloaded")
        state = self._settle_table()
        if state is None:
            # Which of the two it is, is decided by where the browser ended up,
            # not by guessing at page text: sent back to the sign-in host means
            # the session; still on the console with no listing means this
            # account cannot see one.
            if LOGIN_HOST in self._page.url:
                raise MinutesBrowserSessionExpired(
                    "the 听记 console sent us back to sign in; renew the session"
                )
            raise MinutesConsoleUnavailable(
                "the 听记 console served no minutes listing to this account; "
                f"at {self._page.url}: {self._page_head()}"
            )
        if state["current_page"] != 1:
            # A previous walk can leave the console on its last page, where
            # "next" is disabled and an enumeration would read seven rows.
            if self._page.evaluate(_FIRST_PAGE_JS):
                state = self._settle_table(state["rows"]) or state

        rows: list[dict[str, Any]] = []
        seen: set[str] = set()
        for _ in range(_MAX_CONSOLE_PAGES):
            for row in state["rows"]:
                key = row.get("row_key")
                if key and key not in seen:
                    seen.add(key)
                    rows.append(row)
            if not state["next_enabled"]:
                break
            previous = state["rows"]
            if not self._page.evaluate(_NEXT_PAGE_JS):
                break
            settled = self._settle_table(previous)
            if settled is None:
                break
            state = settled
        return rows

    def request_access(self, task_uuid: str, *, reason: str) -> MinutesAccessRequest:
        self._page.goto(
            TRANSCRIBE_URL.format(task_uuid=task_uuid), wait_until="domcontentloaded"
        )
        panel = self._settle_panel()
        if panel is None:
            return MinutesAccessRequest("failed", detail="the minute page never settled")
        if LOGIN_HOST in self._page.url:
            raise MinutesBrowserSessionExpired(
                "the 听记 session was rejected; sign in again to renew it"
            )
        if panel["already_requested"]:
            return MinutesAccessRequest(
                "already_requested", panel["title"], panel["owner"]
            )
        if not panel["permission_panel"] and not panel["can_request"]:
            return MinutesAccessRequest("readable", panel["title"])
        if panel["owner_unresolved"]:
            return MinutesAccessRequest(
                "unresolved", panel["title"], detail=panel["body_head"]
            )
        if not panel["can_request"] or panel["button_disabled"]:
            return MinutesAccessRequest(
                "failed", panel["title"], detail=panel["body_head"]
            )

        self._page.evaluate(_FILL_REASON_JS, reason)
        if not self._page.evaluate(_SEND_JS):
            return MinutesAccessRequest(
                "failed", panel["title"], panel["owner"], "the send control refused"
            )
        confirmed = self._settle_panel(confirm=True)
        if confirmed is not None and confirmed["already_requested"]:
            return MinutesAccessRequest(
                "requested", confirmed["title"] or panel["title"], confirmed["owner"]
            )
        # The click is not the evidence; the page reading the request back is.
        return MinutesAccessRequest(
            "failed",
            panel["title"],
            panel["owner"],
            "the page never read the request back",
        )

    def _page_head(self) -> str:
        """The console's own words, so the report does not invent a reason."""
        try:
            return str(
                self._page.evaluate("() => (document.body.innerText || '').trim()")
            )[:200]
        except Exception:
            return ""

    def _settle_table(self, previous_rows: list[dict] | None = None) -> dict | None:
        previous_first = ""
        if previous_rows:
            previous_first = str(previous_rows[0].get("row_key") or "")
        for _ in range(_PAGE_SETTLE_ATTEMPTS):
            self._page.wait_for_timeout(_PAGE_SETTLE_MS)
            state = self._page.evaluate(_ROWS_JS)
            if not state["rows"]:
                continue
            first = str(state["rows"][0].get("row_key") or "")
            if first and first != previous_first:
                return state
        return None

    def _settle_panel(self, *, confirm: bool = False) -> dict | None:
        panel = None
        for _ in range(_PANEL_SETTLE_ATTEMPTS):
            self._page.wait_for_timeout(_PANEL_SETTLE_MS)
            panel = self._page.evaluate(_PANEL_JS)
            if panel["already_requested"]:
                return panel
            if confirm:
                if not panel["can_request"]:
                    return panel
                continue
            if panel["can_request"] and not panel["owner_unresolved"]:
                return panel
            if not panel["permission_panel"] and panel["body_head"]:
                return panel
        return panel


@contextmanager
def signed_in_console(storage_state_path: Path):
    """Open the console in a headless browser carrying the saved session."""
    from playwright.sync_api import sync_playwright

    if not storage_state_path.exists():
        raise MinutesBrowserSessionExpired(
            f"no 听记 console session at {storage_state_path}; sign in to create it"
        )
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel="chrome", headless=True)
        try:
            context = browser.new_context(storage_state=str(storage_state_path))
            page = context.new_page()
            try:
                yield PlaywrightMinutesConsole(page)
            finally:
                context.close()
        finally:
            browser.close()


def carry_signed_in_session(cdp_endpoint: str, storage_state_path: Path) -> int:
    """Save the session from a browser a person just signed in to.

    Playwright cannot attach to this Chrome (`connect_over_cdp` fails with
    "Browser context management is not supported"), so the cookies come
    straight off the DevTools protocol.
    """
    import asyncio
    from urllib.request import urlopen

    import websockets

    async def read_cookies() -> list[dict[str, Any]]:
        version = json.loads(urlopen(f"{cdp_endpoint}/json/version", timeout=5).read())
        async with websockets.connect(
            version["webSocketDebuggerUrl"], max_size=32 * 1024 * 1024
        ) as socket:
            await socket.send(
                json.dumps({"id": 1, "method": "Storage.getCookies", "params": {}})
            )
            while True:
                message = json.loads(await socket.recv())
                if message.get("id") == 1:
                    if "error" in message:
                        raise MinutesBrowserSessionExpired(str(message["error"]))
                    return message["result"]["cookies"]

    cookies = asyncio.run(read_cookies())
    carried = []
    for cookie in cookies:
        entry = {
            "name": cookie["name"],
            "value": cookie["value"],
            "domain": cookie["domain"],
            "path": cookie.get("path", "/"),
            "httpOnly": bool(cookie.get("httpOnly")),
            "secure": bool(cookie.get("secure")),
        }
        expires = cookie.get("expires")
        if isinstance(expires, (int, float)) and expires > 0:
            entry["expires"] = expires
        if cookie.get("sameSite") in ("Strict", "Lax", "None"):
            entry["sameSite"] = cookie["sameSite"]
        carried.append(entry)
    storage_state_path.parent.mkdir(parents=True, exist_ok=True)
    storage_state_path.write_text(
        json.dumps({"cookies": carried, "origins": []}, ensure_ascii=False),
        encoding="utf-8",
    )
    return len(carried)
