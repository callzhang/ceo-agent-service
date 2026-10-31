"""Local DOM regression for modal ownership; no provider network or account data."""

import pytest
from playwright.sync_api import Error, sync_playwright

from app.email_unsubscribe import PlaywrightUnsubscribeBrowser


@pytest.fixture(scope="module")
def chrome():
    with sync_playwright() as playwright:
        try:
            browser = playwright.chromium.launch(channel="chrome", headless=True)
        except Error:
            pytest.skip("Chrome is unavailable for the local DOM regression")
        try:
            yield browser
        finally:
            browser.close()


@pytest.mark.parametrize("inside_modal", [True, False], ids=["own-confirmation", "blocking-overlay"])
def test_confirmation_target_keeps_its_modal_but_separate_overlay_is_closed(chrome, inside_modal):
    page = chrome.new_page()
    try:
        target = "<button id='target' style='position:absolute;left:100px;top:120px' onclick=\"document.querySelector('#result').textContent='confirmed'\">Confirm</button>"
        page.set_content(
            "<div id='result'></div>"
            + ("" if inside_modal else target)
            + "<div id='modal' role='dialog' aria-modal='true' style='position:absolute;left:0;top:0;width:300px;height:200px;background:white;z-index:10'>"
            + "<button aria-label='Close' onclick=\"window.closeCount++;document.querySelector('#modal').remove()\">Close</button>"
            + (target if inside_modal else "Unrelated notice")
            + "</div><script>window.closeCount=0</script>"
        )
        adapter = PlaywrightUnsubscribeBrowser.__new__(PlaywrightUnsubscribeBrowser)
        adapter.page = page
        adapter.timeout_ms = 1000
        adapter._dismiss_unique_blocking_dialog(page.locator("#target"))
        assert page.evaluate("window.closeCount") == (0 if inside_modal else 1)
        assert page.locator("#modal").count() == (1 if inside_modal else 0)
        page.locator("#target").click(timeout=1000)
        assert page.locator("#result").inner_text() == "confirmed"
    finally:
        page.close()
