from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from app import minutes_console_browser as browser
from app import service_browser
from app.minutes_access import MinutesBrowserSessionExpired


@pytest.mark.parametrize('expired', [True, False])
def test_expired_console_opens_user_chrome_and_keeps_failure(monkeypatch, tmp_path, expired):
    (tmp_path / 'Default').mkdir()
    (tmp_path / 'Default' / 'Preferences').write_text('{}')
    page = Mock()
    page.url = 'https://login.dingtalk.com/oauth2/challenge.htm' if expired else browser.HISTORY_URL
    context = Mock()
    context.pages = [page]
    context.new_page.return_value = page
    monkeypatch.setattr('playwright.sync_api.sync_playwright', lambda: nullcontext(object()))
    monkeypatch.setattr(service_browser, 'launch_service_chrome', lambda *args, **kwargs: context)
    def pick(*args):
        raise MinutesBrowserSessionExpired('scan login required')
    monkeypatch.setattr(browser, '_pick_organisation', pick)
    open_page = Mock(return_value=SimpleNamespace(returncode=0))
    monkeypatch.setattr('subprocess.run', open_page)
    if expired:
        with pytest.raises(MinutesBrowserSessionExpired, match='scan login required'):
            with browser.signed_in_console(tmp_path):
                pytest.fail('unauthenticated console must not be yielded')
        open_page.assert_called_once_with(
            [
                '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
                '--new-window',
                browser.HISTORY_URL,
            ],
            check=True,
        )
    else:
        with browser.signed_in_console(tmp_path) as console:
            assert console is not None
        open_page.assert_not_called()
    context.close.assert_called_once()


@pytest.mark.parametrize('initialized', [True, False])
def test_copied_session_cookies_are_restored_after_native_profile_initialization(
    monkeypatch, tmp_path, initialized,
):
    preferences = tmp_path / 'Default' / 'Preferences'
    if initialized:
        preferences.parent.mkdir()
        preferences.write_text('{}')
    events = []
    page = Mock()
    page.url = browser.HISTORY_URL
    context = Mock()
    context.pages = [page]
    context.new_page.side_effect = lambda: events.append('navigate') or page
    context.close.side_effect = lambda: events.append('close')
    warmup = Mock()
    warmup.close.side_effect = lambda: events.append('initialized')

    def launch(_playwright, profile, **kwargs):
        assert profile == tmp_path
        events.append(kwargs)
        if not preferences.exists():
            preferences.parent.mkdir(exist_ok=True)
            preferences.write_text('{}')
            return warmup
        return context

    monkeypatch.setattr('playwright.sync_api.sync_playwright', lambda: nullcontext(object()))
    monkeypatch.setattr(service_browser, 'launch_service_chrome', launch)
    with browser.signed_in_console(tmp_path) as console:
        assert console is not None
    if initialized:
        assert events == [{'args': ['--restore-last-session']}, 'navigate', 'close']
        warmup.close.assert_not_called()
    else:
        assert events == [{}, 'initialized', {'args': ['--restore-last-session']}, 'navigate', 'close']
        warmup.new_page.assert_not_called()


def test_repeated_restores_save_only_blank_tabs(monkeypatch, tmp_path):
    (tmp_path / 'Default').mkdir()
    (tmp_path / 'Default' / 'Preferences').write_text('{}')
    saved = [browser.HISTORY_URL, browser.TRANSCRIBE_URL.format(task_uuid='old')]
    starts = []

    class Page:
        def __init__(self, context, url):
            self.context, self.url = context, url

        def goto(self, url, **kwargs):
            self.url = url

        def wait_for_timeout(self, _timeout):
            pass

        def close(self):
            self.context.pages.remove(self)

    class Context:
        def __init__(self):
            starts.append(tuple(saved))
            self.pages = [Page(self, url) for url in saved]

        def new_page(self):
            page = Page(self, 'about:blank')
            self.pages.append(page)
            return page

        def close(self):
            saved[:] = [page.url for page in self.pages]

    monkeypatch.setattr('playwright.sync_api.sync_playwright', lambda: nullcontext(object()))
    monkeypatch.setattr(service_browser, 'launch_service_chrome', lambda *args, **kwargs: Context())
    for _ in range(3):
        with browser.signed_in_console(tmp_path) as console:
            assert len(console._page.context.pages) == 1
            console._page.goto(browser.TRANSCRIBE_URL.format(task_uuid='current'))
        assert saved == ['about:blank']
    assert starts[1:] == [('about:blank',), ('about:blank',)]
