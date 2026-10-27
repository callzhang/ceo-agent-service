from types import SimpleNamespace

import pytest

from app import notification
from app.notification import send_macos_notification as default_notification_sender


@pytest.mark.parametrize("terminal_notifier_available", [True, False])
def test_autouse_fixture_blocks_native_notification_transports(
    monkeypatch, terminal_notifier_available
):
    calls = []
    monkeypatch.setattr(
        notification.shutil,
        "which",
        lambda _name: "/test/terminal-notifier" if terminal_notifier_available else None,
    )
    monkeypatch.setattr(
        notification.subprocess,
        "run",
        lambda *args, **kwargs: calls.append((args, kwargs))
        or SimpleNamespace(returncode=0),
    )

    default_notification_sender(
        title="test only", message="must not reach desktop"
    )

    assert calls == []
