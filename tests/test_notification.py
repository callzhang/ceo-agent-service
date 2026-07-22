import pytest

from app.audit_security import (
    NOTIFICATION_BRIDGE_HEADER_NAME,
    NOTIFICATION_BRIDGE_HEADER_VALUE,
)
from app.notification import (
    _send_browser_notification,
    dingtalk_conversation_notification_url,
    send_macos_notification,
)


def test_browser_notification_uses_internal_bridge_protocol(monkeypatch):
    captured = []

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return b'{"delivered": true}'

    monkeypatch.setattr(
        "app.notification.request.urlopen",
        lambda http_request, timeout: captured.append((http_request, timeout))
        or Response(),
    )

    assert _send_browser_notification("CEO", "done", None) is True
    http_request, timeout = captured[0]
    request_headers = {key.lower(): value for key, value in http_request.header_items()}
    assert http_request.get_header("Origin") is None
    assert http_request.get_header("X-ceo-audit-csrf") is None
    assert request_headers[NOTIFICATION_BRIDGE_HEADER_NAME.lower()] == (
        NOTIFICATION_BRIDGE_HEADER_VALUE
    )
    assert http_request.get_header("Content-type") == "application/json"
    assert timeout == 0.5


@pytest.mark.parametrize(
    "base_url",
    [
        "https://127.0.0.1:8765",
        "http://worker:password@127.0.0.1:8765",
        "http://audit.example:8765",
        "http://localhost.evil.example:8765",
        "http://127.0.0.1:8765/evil",
        "http://127.0.0.1:8765/evil/",
        "http://127.0.0.1:8765?target=evil",
        "http://127.0.0.1:8765?",
        "http://127.0.0.1:8765#fragment",
        "http://127.0.0.1:8765#",
        "http://127.0.0.1:not-a-port",
        "http://127.0.0.1:0",
        "//127.0.0.1:8765",
        " http://127.0.0.1:8765",
    ],
)
def test_browser_notification_rejects_invalid_bridge_url_without_network(
    monkeypatch,
    base_url,
):
    network_calls = []
    monkeypatch.setenv("CEO_NOTIFICATION_BRIDGE_BASE_URL", base_url)
    monkeypatch.setattr(
        "app.notification.request.urlopen",
        lambda *args, **kwargs: network_calls.append((args, kwargs)),
    )

    assert _send_browser_notification("CEO", "done", None) is False
    assert dingtalk_conversation_notification_url("cid-1") is None
    assert network_calls == []


@pytest.mark.parametrize(
    ("base_url", "expected_endpoint"),
    [
        ("http://localhost:8765/", "http://localhost:8765/browser-notifications"),
        ("http://127.0.0.2:8765", "http://127.0.0.2:8765/browser-notifications"),
        ("http://[::1]:8765", "http://[::1]:8765/browser-notifications"),
    ],
)
def test_browser_notification_accepts_http_loopback_base_urls(
    monkeypatch,
    base_url,
    expected_endpoint,
):
    captured = []

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return b'{"delivered": true}'

    monkeypatch.setenv("CEO_NOTIFICATION_BRIDGE_BASE_URL", base_url)
    monkeypatch.setattr(
        "app.notification.request.urlopen",
        lambda http_request, timeout: captured.append((http_request, timeout))
        or Response(),
    )

    assert _send_browser_notification("CEO", "done", None) is True
    assert captured[0][0].full_url == expected_endpoint


def test_notification_uses_valid_escaped_applescript_literals(monkeypatch):
    commands = []
    monkeypatch.setattr("app.notification.shutil.which", lambda name: None)
    monkeypatch.setattr(
        "app.notification._send_browser_notification",
        lambda **_: False,
    )
    monkeypatch.setattr(
        "app.notification.subprocess.run",
        lambda command, check: commands.append((command, check)),
    )

    send_macos_notification(
        title='CEO "urgent"',
        message='Question with "quotes"',
        url='https://ceo.stardust.ai/threads/thread-1?q="question-1"',
    )

    assert commands == [
        (
            [
                "osascript",
                "-e",
                'display notification "Question with \\"quotes\\"" with title "CEO \\"urgent\\""',
            ],
            False,
        )
    ]


def test_notification_falls_back_to_applescript_when_no_browser_page(monkeypatch):
    commands = []
    monkeypatch.setattr("app.notification.shutil.which", lambda name: None)
    monkeypatch.setattr(
        "app.notification._send_browser_notification",
        lambda **_: False,
    )
    monkeypatch.setattr(
        "app.notification.subprocess.run",
        lambda command, check: commands.append((command, check)),
    )

    send_macos_notification(
        title="CEO auto reply",
        message="已回复",
        url="http://127.0.0.1:8765/open-dingtalk?cid=75217569357",
    )

    assert commands == [
        (
            [
                "osascript",
                "-e",
                'display notification "已回复" with title "CEO auto reply"',
            ],
            False,
        )
    ]


def test_notification_keeps_unicode_literals_for_applescript(monkeypatch):
    commands = []
    monkeypatch.setattr("app.notification.shutil.which", lambda name: None)
    monkeypatch.setattr(
        "app.notification._send_browser_notification",
        lambda **_: False,
    )
    monkeypatch.setattr(
        "app.notification.subprocess.run",
        lambda command, check: commands.append((command, check)),
    )

    send_macos_notification(
        title="CEO question",
        message="请总结候选人张三的售前能力和风险",
    )

    assert commands[0][0][2] == 'display notification "请总结候选人张三的售前能力和风险" with title "CEO question"'


def test_notification_prefers_terminal_notifier(monkeypatch):
    commands = []
    browser_payloads = []
    monkeypatch.setattr(
        "app.notification.shutil.which",
        lambda name: "/opt/homebrew/bin/terminal-notifier",
    )
    monkeypatch.setattr(
        "app.notification._send_browser_notification",
        lambda **kwargs: browser_payloads.append(kwargs) or True,
    )
    monkeypatch.setattr(
        "app.notification.subprocess.run",
        lambda command, check: commands.append((command, check))
        or type("Completed", (), {"returncode": 0})(),
    )

    send_macos_notification(
        title="CEO auto reply",
        message="已回复",
        url="http://127.0.0.1:8765/open-dingtalk?cid=75217569357",
    )

    assert browser_payloads == []
    assert commands[0][0][:6] == [
        "/opt/homebrew/bin/terminal-notifier",
        "-title",
        "CEO auto reply",
        "-message",
        "已回复",
        "-group",
    ]
    assert commands[0][0][-2:] == [
        "-execute",
        "/usr/bin/curl -fsS 'http://127.0.0.1:8765/open-dingtalk?cid=75217569357' >/dev/null 2>&1",
    ]


def test_notification_falls_back_to_browser_when_terminal_notifier_fails(monkeypatch):
    commands = []
    browser_payloads = []
    monkeypatch.setattr(
        "app.notification.shutil.which",
        lambda name: "/opt/homebrew/bin/terminal-notifier",
    )
    monkeypatch.setattr(
        "app.notification._send_browser_notification",
        lambda **kwargs: browser_payloads.append(kwargs) or True,
    )
    monkeypatch.setattr(
        "app.notification.subprocess.run",
        lambda command, check: commands.append((command, check))
        or type("Completed", (), {"returncode": 1})(),
    )

    send_macos_notification(
        title="CEO auto reply",
        message="已回复",
        url="http://127.0.0.1:8765/open-dingtalk?cid=75217569357",
    )

    assert commands[0][0][0] == "/opt/homebrew/bin/terminal-notifier"
    assert browser_payloads == [
        {
            "title": "CEO auto reply",
            "message": "已回复",
            "url": "http://127.0.0.1:8765/open-dingtalk?cid=75217569357",
        }
    ]
