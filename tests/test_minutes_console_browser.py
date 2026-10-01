from app.minutes_console_browser import PlaywrightMinutesConsole


def test_history_scan_sets_a_rolling_window_before_querying():
    from datetime import datetime
    from zoneinfo import ZoneInfo

    class Locator:
        def __init__(self, name, calls):
            self.name, self.calls = name, calls

        def click(self):
            self.calls.append((self.name, "click"))

        def fill(self, value):
            self.calls.append((self.name, "fill", value))

        @property
        def last(self):
            return self

    class Page:
        def __init__(self):
            self.calls = []

        def get_by_placeholder(self, name):
            return Locator(name, self.calls)

        def get_by_text(self, name, exact=False):
            return Locator(name, self.calls)

        def get_by_role(self, role, name, exact=False):
            return Locator(name, self.calls)

    page = Page()
    PlaywrightMinutesConsole(page)._apply_history_window(
        now=datetime(2026, 10, 1, tzinfo=ZoneInfo("Asia/Shanghai"))
    )
    assert ("开始日期", "fill", "2026-09-01 00:00:00") in page.calls
    assert ("结束日期", "fill", "2026-10-02 00:00:00") in page.calls
    assert page.calls[-1] == ("查询", "click")


class _PanelPage:
    def __init__(self):
        self.reads = 0
        self.waits = 0

    def wait_for_timeout(self, _milliseconds):
        self.waits += 1

    def evaluate(self, _script):
        self.reads += 1
        return {
            "already_requested": False,
            "can_request": self.reads != 1,
            "button_disabled": self.reads == 2,
            "owner_unresolved": False,
            "permission_panel": self.reads != 1,
            "body_head": "Loading" if self.reads == 1 else "permission page",
        }


def test_permission_panel_waits_until_request_button_is_enabled():
    page = _PanelPage()

    panel = PlaywrightMinutesConsole(page)._settle_panel()

    assert page.reads == 3
    assert panel["button_disabled"] is False


class _UnresolvedPage:
    url = "https://shanji.dingtalk.com/app/permission/minute"

    def goto(self, _url, *, wait_until):
        assert wait_until == "domcontentloaded"

    def wait_for_timeout(self, _milliseconds):
        pass

    def evaluate(self, _script):
        return {
            "already_requested": False,
            "can_request": True,
            "button_disabled": True,
            "owner_unresolved": False,
            "permission_panel": True,
            "body_head": "permission page with no selectable owner",
            "title": "minute",
            "owner": "",
        }


def test_disabled_request_control_is_unresolved_not_a_failed_send():
    result = PlaywrightMinutesConsole(_UnresolvedPage()).request_access(
        "minute", reason="work follow-up"
    )

    assert result.outcome == "unresolved"


class _LoadingPage:
    url = "https://shanji.dingtalk.com/app/transcribes/minute"

    def goto(self, _url, *, wait_until):
        assert wait_until == "domcontentloaded"

    def wait_for_timeout(self, _milliseconds):
        pass

    def evaluate(self, _script):
        return {
            "already_requested": False,
            "can_request": False,
            "button_disabled": False,
            "owner_unresolved": False,
            "permission_panel": False,
            "body_head": "Loading",
            "title": "",
            "owner": "",
        }


def test_loading_page_does_not_count_as_readable():
    result = PlaywrightMinutesConsole(_LoadingPage()).request_access(
        "minute", reason="work follow-up"
    )

    assert result.outcome == "failed"
