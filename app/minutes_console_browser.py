"""The signed-in browser behind `MinutesConsole`.

Kept apart from `app.minutes_access` so the rules there are testable without a
browser, and so everything that knows about page structure sits in one file.
"""

from __future__ import annotations

from contextlib import contextmanager
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
            # Readability is established by the API before opening this page.
            # A non-permission page can still be a loading or error shell.
            return MinutesAccessRequest(
                "failed", panel["title"], detail=panel["body_head"] or "minute page did not settle"
            )
        if panel["owner_unresolved"] or (
            panel["can_request"] and panel["button_disabled"]
        ):
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
            if (
                panel["can_request"]
                and not panel["button_disabled"]
                and not panel["owner_unresolved"]
            ):
                return panel
        return panel


# The console is an organisation's management view, so the sign-in ends on a
# picker of the organisations this account administers. Only one of them owns
# the minutes this service archives; the others are Derek's own and would serve
# an empty console.
MINUTES_CONSOLE_ORG = "北京星尘纪元智能科技有限公司"
_ORG_PICK_TIMEOUT_MS = 45_000
_CONSOLE_SETTLE_MS = 6_000


def _service_profile_dir() -> Path:
    from app import config as app_config

    return app_config.worker_db_path().parent / "minutes-console-profile"


@contextmanager
def signed_in_console(profile_dir: Path | None = None, *, org: str = ""):
    """Open the console in the service's headless Chrome, signed in as Derek.

    The browser carries the daily copy of Derek's own Chrome cookies, which
    authenticates him as far as the organisation picker. The copy is written
    over the profile on every launch, so the console's own `access_token` never
    survives a run and the organisation is chosen every time -- which is why
    there is no stored console session here, and nothing to renew.
    """
    from playwright.sync_api import sync_playwright

    from app.service_browser import launch_service_chrome

    wanted = org or MINUTES_CONSOLE_ORG
    with sync_playwright() as playwright:
        context = launch_service_chrome(
            playwright, profile_dir or _service_profile_dir()
        )
        try:
            page = context.new_page()
            page.goto(HISTORY_URL, wait_until="domcontentloaded", timeout=45_000)
            page.wait_for_timeout(_CONSOLE_SETTLE_MS)
            if LOGIN_HOST in page.url:
                _pick_organisation(page, wanted)
            if MINUTES_CONSOLE_HOST not in page.url:
                raise MinutesBrowserSessionExpired(
                    "the copy of Derek's Chrome cookies no longer carries a "
                    f"DingTalk login (stopped at {page.url[:120]}); open "
                    "DingTalk in Chrome and let `sync-chrome-cookies` run"
                )
            yield PlaywrightMinutesConsole(page)
        finally:
            context.close()


def _pick_organisation(page, org: str) -> None:
    """Choose the organisation whose console holds this service's minutes."""
    entry = page.get_by_text(org, exact=False).first
    try:
        entry.click(timeout=_ORG_PICK_TIMEOUT_MS)
    except Exception as exc:  # the picker is a page; it fails in many ways
        raise MinutesBrowserSessionExpired(
            f"the 听记 sign-in stopped without offering {org!r}: {exc}"
        ) from exc
    page.wait_for_url(f"**{MINUTES_CONSOLE_HOST}/**", timeout=_ORG_PICK_TIMEOUT_MS)
    page.wait_for_timeout(_CONSOLE_SETTLE_MS)
