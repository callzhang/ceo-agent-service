from pathlib import Path


SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "dingteam_okr_headless_source.py"


def load_module():
    import importlib.util

    spec = importlib.util.spec_from_file_location("dingteam_okr_headless_source", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_service_entrypoint_disables_visible_browser_fallback():
    source = SCRIPT_PATH.read_text(encoding="utf-8")

    assert "_capture_stable_headless_headers" in source
    assert '"--headless=new"' in source
    assert "headless" in source.casefold()
    assert "headful" not in source.casefold()


def test_expired_cache_is_refreshed_headlessly(monkeypatch):
    module = load_module()
    calls = []

    monkeypatch.setattr(module.browser, "_read_cache", lambda: None)
    monkeypatch.setattr(
        module,
        "_capture_stable_headless_headers",
        lambda **kwargs: calls.append(kwargs) or {"Authorization": "Bearer test"},
    )
    written = []
    monkeypatch.setattr(module.browser, "_write_cache", written.append)

    assert module._get_headless_headers() == {"Authorization": "Bearer test"}
    assert calls == [{}]
    assert written == [{"Authorization": "Bearer test"}]


def test_valid_cache_skips_browser_refresh(monkeypatch):
    module = load_module()
    cached = {"Authorization": "Bearer cached"}

    monkeypatch.setattr(module.browser, "_read_cache", lambda: cached)
    monkeypatch.setattr(
        module,
        "_capture_stable_headless_headers",
        lambda **kwargs: (_ for _ in ()).throw(AssertionError("browser should not start")),
    )

    assert module._get_headless_headers() == cached


def test_unmounted_okr_shell_is_checked_only_after_auth_wait():
    source = SCRIPT_PATH.read_text(encoding="utf-8")

    wait_marker = 'deadline = time.monotonic() + HEADLESS_REFRESH_SECONDS'
    state_check = 'if app_state.get("root") and not app_state.get("mounted"):'

    assert source.index(wait_marker) < source.index(state_check)


def test_headless_cdp_launch_uses_playwright_browser_binary_and_loopback_only():
    module = load_module()

    class Chromium:
        executable_path = "/tmp/playwright-chrome"

    class Playwright:
        chromium = Chromium()

    assert module._headless_cdp_command(
        Playwright(), port=9222, profile_dir="/tmp/ceo-okr-profile"
    ) == [
        "/tmp/playwright-chrome",
        "--headless=new",
        "--no-first-run",
        "--no-default-browser-check",
        "--remote-debugging-address=127.0.0.1",
        "--remote-debugging-port=9222",
        "--user-data-dir=/tmp/ceo-okr-profile",
        "about:blank",
    ]


def test_headless_browser_uses_process_lock():
    source = SCRIPT_PATH.read_text(encoding="utf-8")

    assert "_headless_browser_lock" in source
    assert "fcntl.LOCK_EX" in source
    assert "fcntl.LOCK_NB" in source
    assert "HEADLESS_LOCK_TIMEOUT_SECONDS" in source


def test_headless_browser_force_kills_chrome_after_bounded_shutdown_wait():
    source = SCRIPT_PATH.read_text(encoding="utf-8")

    terminate_marker = "process.terminate()"
    timeout_marker = "except subprocess.TimeoutExpired:"
    kill_marker = "process.kill()"

    assert source.index(terminate_marker) < source.index(timeout_marker) < source.index(kill_marker)
