"""Architecture guard for service-owned human-facing text delivery."""
from __future__ import annotations

import ast
from pathlib import Path


APP_ROOT = Path(__file__).parents[1] / "app"

# These modules are transport adapters (or the one required facade), rather than
# business senders. Keep this list exact: adding a business module is a bypass.
DINGTALK_ADAPTERS = {
    "app/dws_client.py",
    "app/org_cache.py",
    "app/service_message_sender.py",
}
WECHAT_ADAPTERS = {
    "app/service_message_sender.py",
    "app/wechat/sender_ipc.py",
}
DINGTALK_SEND_METHODS = {
    "send_direct_message_by_bot",
    "send_group_message_by_bot",
    "send_message",
    "send_reply_to_trigger",
    "reply_message",
}


def _relative(source: Path) -> str:
    return source.relative_to(APP_ROOT.parent).as_posix()


def _raw_send_violations() -> list[str]:
    violations: list[str] = []
    for source in sorted(APP_ROOT.rglob("*.py")):
        relative = _relative(source)
        tree = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                continue
            method = node.func.attr
            if method in DINGTALK_SEND_METHODS and relative not in DINGTALK_ADAPTERS:
                violations.append(f"{relative}:{node.lineno}:{method}")
                continue
            # The Accessibility/IPC runner accepts a plain string. Only the
            # unified facade and IPC adapter may call a runner's raw send method.
            if (
                method == "send"
                and isinstance(node.func.value, ast.Attribute)
                and node.func.value.attr == "runner"
                and relative not in WECHAT_ADAPTERS
            ):
                violations.append(f"{relative}:{node.lineno}:runner.send")
    return violations


def test_first_party_message_senders_do_not_bypass_service_message_sender():
    assert _raw_send_violations() == []

