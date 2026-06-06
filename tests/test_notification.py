from app.notification import send_macos_notification


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
