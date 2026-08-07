import json

from app.wechat.codex_safety import make_read_only_with_memory_tools
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


def test_wechat_decision_runner_uses_read_only_memory_only_command(
    tmp_path, monkeypatch
):
    manifest = tmp_path / "service-mcp.json"
    manifest.write_text(
        json.dumps(
            {
                "servers": {
                    "memory_connector": {"url": "https://memory.example/mcp"}
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("CEO_SERVICE_MCP_CONFIG_PATH", str(manifest))
    executor = CapturingExecutor()
    runner = WechatDecisionRunner(workspace=tmp_path, executor=executor)

    runner.decide("decide this WeChat turn", None)

    command = executor.commands[0]
    command_text = " ".join(command)
    assert "--dangerously-bypass-approvals-and-sandbox" not in command
    assert "--sandbox read-only" in command_text
    assert 'approval_policy="never"' in command_text
    assert "features.plugins=false" in command_text
    assert "features.apps=false" in command_text
    assert 'web_search="disabled"' in command_text
    assert 'mcp_servers.memory_connector.enabled_tools=["memory_get","memory_recall","timeline_get","user_get"]' in command_text


def test_read_only_command_does_not_add_an_unconfigured_memory_transport():
    command = [
        "codex",
        "exec",
        "-c",
        'mcp_servers.exa.url="https://mcp.exa.ai/mcp"',
    ]

    make_read_only_with_memory_tools(command)

    command_text = " ".join(command)
    assert "mcp_servers.exa.enabled=false" in command_text
    assert "mcp_servers.memory_connector" not in command_text
