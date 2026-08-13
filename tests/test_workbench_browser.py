import os
import socket
import threading
import time
from pathlib import Path

import pytest
import uvicorn

from app.audit_web import create_audit_app
from app.setup_wizard import SETUP_WIZARD_STEPS
from app.workbench.store import WorkbenchStore


pytestmark = [
    pytest.mark.browser,
    pytest.mark.skipif(
        os.environ.get("WORKBENCH_BROWSER_TESTS") != "1",
        reason="set WORKBENCH_BROWSER_TESTS=1 to run real browser regressions",
    ),
]


class _NonExecutingExecutor:
    def __init__(self, store: WorkbenchStore, workspace: Path):
        self.store = store
        self.workspace = workspace

    def recover(self):
        return 0

    def run_once(self):
        return []

    def stop(self, turn_id):
        return self.store.request_stop(turn_id)

    def confirm(self, confirmation_id):
        raise AssertionError(f"unexpected confirmation: {confirmation_id}")

    def cancel(self, confirmation_id):
        raise AssertionError(f"unexpected cancellation: {confirmation_id}")

    def close(self):
        return True


def _unused_loopback_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def test_virtualized_task_list_has_visible_scrollable_browser_viewport(tmp_path: Path):
    sync_api = pytest.importorskip("playwright.sync_api")
    project_root = Path(__file__).resolve().parents[1]
    asset_dir = project_root / "app" / "static" / "workbench"
    if not (asset_dir / "index.html").is_file():
        pytest.fail("workbench build missing; run npm run build:workbench")

    store = WorkbenchStore(tmp_path / "workbench.sqlite3")
    for step in SETUP_WIZARD_STEPS:
        store.upsert_setup_wizard_step(
            step_id=step.id,
            status="done",
            summary="complete",
        )
    for index in range(86):
        store.create_task(title=f"Browser task {index + 1:02d}", runtime_kind="codex")

    app = create_audit_app(
        store.path,
        workbench_asset_dir=asset_dir,
        workbench_workspace=tmp_path,
        workbench_executor=_NonExecutingExecutor(store, tmp_path),
        workbench_scheduler_interval_seconds=60,
    )
    port = _unused_loopback_port()
    server = uvicorn.Server(
        uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error")
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started and thread.is_alive() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert server.started

    try:
        with sync_api.sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True, channel="chrome")
            try:
                page = browser.new_page(viewport={"width": 1280, "height": 800})
                page.goto(f"http://127.0.0.1:{port}/", wait_until="networkidle")
                viewport = page.locator(".task-virtuoso")
                viewport.wait_for(state="visible")
                dimensions = viewport.evaluate(
                    "element => ({ height: element.getBoundingClientRect().height, "
                    "clientHeight: element.clientHeight, scrollHeight: element.scrollHeight })"
                )
                assert dimensions["height"] >= 180
                assert dimensions["scrollHeight"] > dimensions["clientHeight"]

                first_visible = page.get_by_role("button", name="打开任务").first
                assert first_visible.bounding_box()["height"] > 0
                viewport.evaluate("element => { element.scrollTop = element.scrollHeight; }")
                page.wait_for_function(
                    "element => element.scrollTop > 0",
                    arg=viewport.element_handle(),
                )
                assert page.get_by_role("button", name="打开任务").last.is_visible()
            finally:
                browser.close()
    finally:
        server.should_exit = True
        thread.join(timeout=10)
        assert not thread.is_alive()
