import json

from app.wechat.decision_runner import WechatDecisionRunner


class CapturingExecutor:
    def __init__(self):
        self.commands: list[list[str]] = []

    def __call__(self, command: list[str], _prompt: str) -> str:
        self.commands.append(command)
        return json.dumps(
            {
                "kind": "reply",
                "user_response": {
                    "mode": "no_reply",
                    "text": "",
                    "sensitivity_kind": "general",
                },
                "system_actions": [],
                "domain_payload": {},
                "audit": {"summary": "无需回复。", "documents": [], "confidence": 1},
            }
        )


def test_wechat_decision_runner_uses_read_only_memory_only_command(tmp_path):
    executor = CapturingExecutor()
    runner = WechatDecisionRunner(workspace=tmp_path, executor=executor)

    runner.decide("decide this WeChat turn", None)

    command = executor.commands[0]
    command_text = " ".join(command)
    assert "--dangerously-bypass-approvals-and-sandbox" not in command
    assert "--sandbox read-only" in command_text
    assert 'approval_policy="never"' in command_text
    assert "features.plugins=false" not in command_text
    assert "features.apps=false" not in command_text
    assert 'web_search="disabled"' in command_text
    assert 'mcp_servers.memory_connector.enabled_tools=["memory_get","memory_recall","timeline_get","user_get"]' in command_text
