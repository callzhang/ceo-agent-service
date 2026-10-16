from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from app import minutes_console_browser as browser
from app import service_browser
from app.minutes_access import MinutesBrowserSessionExpired


@pytest.mark.parametrize('expired', [True, False])
def test_expired_console_opens_user_chrome_and_keeps_failure(monkeypatch, tmp_path, expired):
    page = Mock()
    page.url = 'https://login.dingtalk.com/oauth2/challenge.htm' if expired else browser.HISTORY_URL
    context = Mock()
    context.new_page.return_value = page
    monkeypatch.setattr('playwright.sync_api.sync_playwright', lambda: nullcontext(object()))
    monkeypatch.setattr(service_browser, 'launch_service_chrome', lambda *args: context)
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
            ['/usr/bin/open', '-a', 'Google Chrome', browser.HISTORY_URL], check=True,
        )
    else:
        with browser.signed_in_console(tmp_path) as console:
            assert console is not None
        open_page.assert_not_called()
    context.close.assert_called_once()
