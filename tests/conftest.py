import json
import os
from pathlib import Path
import sys

import pytest


# Derek, 2026-09-25: tests never run in the production checkout. A session
# ran them there, committed a test fix in place, and every deploy stopped.
def _refuse_the_production_checkout() -> None:
    from app.config import PRODUCTION_CHECKOUT_MESSAGE, is_production_checkout

    root = Path(__file__).resolve().parents[1]
    if is_production_checkout(root):
        pytest.exit(PRODUCTION_CHECKOUT_MESSAGE.format(root=root), returncode=4)


_refuse_the_production_checkout()


# Finder/iCloud conflict copies are local recovery artifacts, not test modules.
# Keep them untouched while preventing pytest from collecting stale duplicates.
collect_ignore_glob = ["* 2.py"]


os.environ["CEO_ENV_FILE"] = "/private/tmp/ceo-agent-service-test.env.missing"
os.environ["CEO_PRINCIPAL_NAME"] = "Alex"
os.environ["USER_ALIAS"] = "明哥"
os.environ["CEO_MENTION_ALIASES"] = "@Alex Chen,@明哥"
os.environ["DOCUMENT_EXTRACTION_IDS"] = "明哥,Alex"
os.environ["CEO_ASSISTANT_SIGNATURE"] = "（by明哥分身）"
os.environ["CEO_HANDOFF_ACK"] = "我让明哥本人看一下。（by明哥分身）"
os.environ["CEO_FEEDBACK_SPIKE_VERCEL_BASE_URL"] = ""
os.environ["CEO_PROMPT_VAR_RESPONSIBILITY_SUMMARY"] = (
    "Alex 的组织职责包括算法负责人；凡是询问算法团队、算法同学、算法分享、算法资源或算法方向是否参与的消息，"
    "如果明确 @ Alex，即使同时 @ 了别人，也应视为需要 Alex 回复。"
)
os.environ["CEO_DING_ROBOT_NAME"] = "极简云机器人"
os.environ["CEO_FORBIDDEN_PATH_PREFIXES"] = "/Users/principal/,/home/principal/"
os.environ["FAST_PATH_UNREAD_BACKOFF"] = "0s"


def pytest_addoption(parser):
    parser.addoption(
        "--run-live",
        action="store_true",
        default=False,
        help="collect and run tests marked live",
    )


def pytest_collection_modifyitems(config, items):
    if config.getoption("--run-live"):
        return
    live_items = [item for item in items if item.get_closest_marker("live")]
    if not live_items:
        return
    items[:] = [item for item in items if item not in live_items]
    config.hook.pytest_deselected(items=live_items)


@pytest.fixture(autouse=True)
def block_real_notifications_in_tests(monkeypatch, request):
    if request.path.name == "test_notification.py":
        return

    def noop_notification(**kwargs):
        del kwargs

    def noop_browser_notification(**kwargs):
        del kwargs
        return False

    for module_name in (
        "app.notification",
        "app.worker",
        "app.meeting_alignment",
        "app.cli",
    ):
        module = sys.modules.get(module_name)
        if module is not None:
            monkeypatch.setattr(
                module,
                "send_macos_notification",
                noop_notification,
                raising=False,
            )
    monkeypatch.setattr(
        "app.notification._send_browser_notification",
        noop_browser_notification,
    )


@pytest.fixture(autouse=True)
def block_real_memory_writes_in_tests(monkeypatch):
    """A test must never reach the real Memory connector.

    Seven copies of one meeting fixture arrived in Derek's Memory on 2026-09-18
    that way, and they cannot be deleted from the console. Failing loudly here
    costs a test author one line; the alternative costs him an unexplained entry
    he has to find and remove through an admin endpoint.

    Every name the write path can reach is stubbed, not just the current one. A
    write that stops going through one of them does not make that name safe to
    drop from this list, and a seam this fixture does not know about is a
    guardrail that silently stops guarding: since 2026-09-19 the write calls the
    connector directly, which lands in Memory in under three seconds instead of
    spawning a runtime first.
    """

    def refuse(*args, **kwargs):
        del args, kwargs
        raise AssertionError(
            "a test reached the real memory writer: pass routed_execution= or "
            "memory_writer= so the write stays inside the test"
        )

    blocked = (
        ("app.codex_memory_write", "run_codex_memory_write"),
        ("app.meeting_memory_write", "run_codex_memory_write"),
        ("app.meeting_memory_write", "write_memory"),
        ("app.task_memory_write", "write_memory"),
        ("app.memory_connector_client", "write_memory"),
        ("app.memory_connector_client", "load_credential"),
    )
    for module_name, attribute in blocked:
        module = sys.modules.get(module_name)
        if module is not None:
            monkeypatch.setattr(module, attribute, refuse, raising=False)


@pytest.fixture(autouse=True)
def isolate_service_mcp_manifest(tmp_path, monkeypatch):
    manifest = tmp_path / "service-mcp.json"
    manifest.write_text(
        json.dumps(
            {"servers": {"exa": {"url": "https://mcp.exa.ai/mcp"}}}
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("CEO_SERVICE_MCP_CONFIG_PATH", str(manifest))
