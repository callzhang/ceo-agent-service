from app.minutes_console_browser import PlaywrightMinutesConsole


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
