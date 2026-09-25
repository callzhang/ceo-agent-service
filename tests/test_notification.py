from types import SimpleNamespace
from app.notification import attempt_notification_url, send_macos_notification


def test_attempt_notification_url_is_generic_and_traceable():
    assert attempt_notification_url(123) == (
        "http://127.0.0.1:8765/open-attempt?attempt_id=123"
    )


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
        "/usr/bin/curl -fsS -X POST 'http://127.0.0.1:8765/open-dingtalk?cid=75217569357' >/dev/null 2>&1",
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


def test_a_notification_click_reuses_the_open_console_tab(monkeypatch):
    """Derek, 2026-09-25: show the attempt in the tab he has open, not a new one."""
    from app import notification

    calls = []

    def fake_run(command, **kwargs):
        calls.append(command)
        return SimpleNamespace(returncode=0, stdout="focused\n")

    monkeypatch.setattr(notification.subprocess, "run", fake_run)

    assert notification.focus_console_tab(
        "http://127.0.0.1:8765", "http://127.0.0.1:8765/attempts/9997"
    )
    script = calls[0][2]
    assert '"http://127.0.0.1:8765/attempts/9997"' in script
    assert "Google Chrome" in script
    assert len(calls) == 1


def test_no_open_console_tab_falls_back_to_opening_one(monkeypatch):
    from app import notification

    monkeypatch.setattr(
        notification.subprocess,
        "run",
        lambda command, **kwargs: SimpleNamespace(returncode=0, stdout="none\n"),
    )

    assert not notification.focus_console_tab(
        "http://127.0.0.1:8765", "http://127.0.0.1:8765/attempts/9997"
    )
