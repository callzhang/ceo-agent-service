from ceo_agent_service.notification import send_macos_notification


def test_notification_uses_valid_escaped_applescript_literals(monkeypatch):
    commands = []
    monkeypatch.setattr(
        "ceo_agent_service.notification._send_browser_notification",
        lambda **_: False,
    )
    monkeypatch.setattr(
        "ceo_agent_service.notification.subprocess.run",
        lambda command, check: commands.append((command, check)),
    )
    monkeypatch.setattr("ceo_agent_service.notification.shutil.which", lambda _: None)

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


def test_notification_binds_click_url_with_terminal_notifier(tmp_path, monkeypatch):
    commands = []
    monkeypatch.setattr(
        "ceo_agent_service.notification._send_browser_notification",
        lambda **_: False,
    )
    monkeypatch.setattr(
        "ceo_agent_service.notification.subprocess.run",
        lambda command, check: commands.append((command, check)),
    )
    monkeypatch.setattr(
        "ceo_agent_service.notification.shutil.which",
        lambda command: "/opt/homebrew/bin/terminal-notifier"
        if command == "terminal-notifier"
        else None,
    )
    monkeypatch.setattr(
        "ceo_agent_service.notification.DEFAULT_NOTIFICATION_ICON_PATH",
        tmp_path / "missing-logo.png",
    )
    monkeypatch.setattr(
        "ceo_agent_service.notification._notification_group_id",
        lambda: "ceo-agent-service-test",
    )

    send_macos_notification(
        title="CEO auto reply",
        message="已回复",
        url="dingtalk://dingtalkclient/page/conversation?cid=75217569357",
    )

    assert commands == [
        (
            [
                "/opt/homebrew/bin/terminal-notifier",
                "-title",
                "CEO auto reply",
                "-message",
                "已回复",
                "-group",
                "ceo-agent-service-test",
                "-sound",
                "default",
                "-subtitle",
                "点击打开钉钉",
                "-open",
                "dingtalk://dingtalkclient/page/conversation?cid=75217569357",
            ],
            False,
        )
    ]


def test_notification_uses_logo_as_terminal_notifier_icon(tmp_path, monkeypatch):
    commands = []
    icon_path = tmp_path / "logo.png"
    icon_path.write_bytes(b"png")
    monkeypatch.setattr(
        "ceo_agent_service.notification._send_browser_notification",
        lambda **_: False,
    )
    monkeypatch.setattr(
        "ceo_agent_service.notification.subprocess.run",
        lambda command, check: commands.append((command, check)),
    )
    monkeypatch.setattr(
        "ceo_agent_service.notification.shutil.which",
        lambda command: "/opt/homebrew/bin/terminal-notifier"
        if command == "terminal-notifier"
        else None,
    )
    monkeypatch.setattr(
        "ceo_agent_service.notification.DEFAULT_NOTIFICATION_ICON_PATH",
        icon_path,
    )
    monkeypatch.setattr(
        "ceo_agent_service.notification._notification_group_id",
        lambda: "ceo-agent-service-test",
    )

    send_macos_notification(
        title="CEO auto reply",
        message="已回复",
        url="dingtalk://dingtalkclient/page/conversation?cid=75217569357",
    )

    assert commands == [
        (
            [
                "/opt/homebrew/bin/terminal-notifier",
                "-title",
                "CEO auto reply",
                "-message",
                "已回复",
                "-group",
                "ceo-agent-service-test",
                "-sound",
                "default",
                "-appIcon",
                str(icon_path),
                "-subtitle",
                "点击打开钉钉",
                "-open",
                "dingtalk://dingtalkclient/page/conversation?cid=75217569357",
            ],
            False,
        )
    ]


def test_notification_uses_terminal_notifier_without_click_url(tmp_path, monkeypatch):
    commands = []
    icon_path = tmp_path / "logo.png"
    icon_path.write_bytes(b"png")
    monkeypatch.setattr(
        "ceo_agent_service.notification._send_browser_notification",
        lambda **_: False,
    )
    monkeypatch.setattr(
        "ceo_agent_service.notification.subprocess.run",
        lambda command, check: commands.append((command, check)),
    )
    monkeypatch.setattr(
        "ceo_agent_service.notification.shutil.which",
        lambda command: "/opt/homebrew/bin/terminal-notifier"
        if command == "terminal-notifier"
        else None,
    )
    monkeypatch.setattr(
        "ceo_agent_service.notification.DEFAULT_NOTIFICATION_ICON_PATH",
        icon_path,
    )
    monkeypatch.setattr(
        "ceo_agent_service.notification._notification_group_id",
        lambda: "ceo-agent-service-test",
    )

    send_macos_notification(title="CEO auto reply", message="已回复")

    assert commands == [
        (
            [
                "/opt/homebrew/bin/terminal-notifier",
                "-title",
                "CEO auto reply",
                "-message",
                "已回复",
                "-group",
                "ceo-agent-service-test",
                "-sound",
                "default",
                "-appIcon",
                str(icon_path),
            ],
            False,
        )
    ]


def test_notification_keeps_unicode_literals_for_applescript(monkeypatch):
    commands = []
    monkeypatch.setattr(
        "ceo_agent_service.notification._send_browser_notification",
        lambda **_: False,
    )
    monkeypatch.setattr(
        "ceo_agent_service.notification.subprocess.run",
        lambda command, check: commands.append((command, check)),
    )
    monkeypatch.setattr("ceo_agent_service.notification.shutil.which", lambda _: None)

    send_macos_notification(
        title="CEO question",
        message="请总结候选人张三的售前能力和风险",
    )

    assert commands[0][0][2] == 'display notification "请总结候选人张三的售前能力和风险" with title "CEO question"'


def test_notification_prefers_browser_notification(monkeypatch):
    commands = []
    browser_payloads = []
    monkeypatch.setattr(
        "ceo_agent_service.notification._send_browser_notification",
        lambda **kwargs: browser_payloads.append(kwargs) or True,
    )
    monkeypatch.setattr(
        "ceo_agent_service.notification.subprocess.run",
        lambda command, check: commands.append((command, check)),
    )

    send_macos_notification(
        title="CEO auto reply",
        message="已回复",
        url="http://127.0.0.1:8765/open-dingtalk?cid=75217569357",
    )

    assert browser_payloads == [
        {
            "title": "CEO auto reply",
            "message": "已回复",
            "url": "http://127.0.0.1:8765/open-dingtalk?cid=75217569357",
        }
    ]
    assert commands == []
