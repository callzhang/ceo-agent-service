from ceo_agent_service.notification import send_macos_notification


def test_notification_uses_valid_escaped_applescript_literals(monkeypatch):
    commands = []
    monkeypatch.setattr(
        "ceo_agent_service.notification.subprocess.run",
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
                'display notification "Question with \\"quotes\\"" with title "CEO \\"urgent\\""\n'
                'open location "https://ceo.stardust.ai/threads/thread-1?q=\\"question-1\\""',
            ],
            False,
        )
    ]


def test_notification_keeps_unicode_literals_for_applescript(monkeypatch):
    commands = []
    monkeypatch.setattr(
        "ceo_agent_service.notification.subprocess.run",
        lambda command, check: commands.append((command, check)),
    )

    send_macos_notification(
        title="CEO question",
        message="请总结候选人张三的售前能力和风险",
    )

    assert commands[0][0][2] == 'display notification "请总结候选人张三的售前能力和风险" with title "CEO question"'
