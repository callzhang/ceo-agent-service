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


def test_wechat_decision_runner_uses_normal_runtime_command(
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
    monkeypatch.setenv("CEO_AGENT_RUNTIME_ROUTES", "codex_oauth")
    executor = CapturingExecutor()
    runner = WechatDecisionRunner(workspace=tmp_path, executor=executor)

    runner.decide("decide this WeChat turn", None)

    command = executor.commands[0]
    command_text = " ".join(command)
    assert "--dangerously-bypass-approvals-and-sandbox" not in command
    assert "--sandbox read-only" not in command_text
    assert 'approval_policy="on-failure"' in command_text
    assert "features.plugins=false" not in command_text
    # Every service Codex command pins the interactive-only features off, so
    # the WeChat decision runner carries the same flags as the normal command.
    assert "features.multi_agent=false" in command_text
    assert "features.apps=false" in command_text
