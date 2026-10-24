import json

import pytest

from app.agent_envelope import AgentEnvelope, AgentKind, UserResponseMode
from app.agent_runtime_contracts import ROLE_BOUND_AGENT_TOOLS
from app.agent_runtime_router import CodexCommandFactory, RoutedCodexExecution
from app.wechat.decision_runner import (
    WECHAT_DECISION_DEVELOPER_INSTRUCTIONS,
    WechatDecisionRunner,
)


def test_wechat_first_turn_instruction_contains_valid_envelope_example():
    marker = "Example AgentEnvelope for no reply: "
    assert marker in WECHAT_DECISION_DEVELOPER_INSTRUCTIONS
    example = WECHAT_DECISION_DEVELOPER_INSTRUCTIONS.split(marker, 1)[1].splitlines()[0]
    decision = AgentEnvelope.model_validate_json(example)

    assert decision.kind is AgentKind.NO_ACTION
    assert decision.user_response.mode is UserResponseMode.NO_REPLY
    assert decision.system_actions == []
    assert decision.audit.summary


def test_wechat_instruction_describes_reply_using_same_envelope():
    instruction = WECHAT_DECISION_DEVELOPER_INSTRUCTIONS
    assert "kind=reply" in instruction
    assert "user_response.mode=send_reply" in instruction
    assert "user_response.text" in instruction
    assert "delivery remains a separate persisted service step" in instruction
    from app.wechat.decision_runner import CONSUMER_OUTBOUND_INSTRUCTION

    assert instruction.count(CONSUMER_OUTBOUND_INSTRUCTION) == 1


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


def test_wechat_decision_runner_has_no_direct_effect_tools(tmp_path, monkeypatch):
    manifest = tmp_path / "service-mcp.json"
    manifest.write_text(
        json.dumps(
            {"servers": {"memory_connector": {"url": "https://memory.example/mcp"}}}
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
    assert "--skip-git-repo-check" in command
    assert 'sandbox_mode="read-only"' in command_text
    assert 'approval_policy="never"' in command_text
    assert "tools.enabled_tools=[]" in command_text
    assert "features.plugins=false" in command_text
    assert "features.shell_tool=false" in command_text
    assert "features.unified_exec=false" in command_text
    assert "features.code_mode_only=false" in command_text
    assert "features.browser_use=false" in command_text
    assert "mcp_servers.memory_connector.enabled=false" in command_text
    assert "features.multi_agent=false" in command_text
    assert "features.apps=false" in command_text
    assert "Example AgentEnvelope for no reply" in command_text

    factory = runner._routed_command_factory([])
    route = runner._legacy_adapter.config.routes[0]
    routed_command, _ = factory.build(
        adapter=runner._legacy_adapter, route=route,
        prompt="decide this WeChat turn", session_id=None,
    )
    assert 'sandbox_mode="read-only"' in routed_command
    assert "features.shell_tool=false" in routed_command
    assert "mcp_servers.memory_connector.enabled=false" in routed_command
    assert factory.model_only is True
    assert ROLE_BOUND_AGENT_TOOLS in runner._required_capabilities()

    resumed = runner._build_command(
        "continue the anonymous decision", "00000000-0000-0000-0000-000000000000"
    )
    assert resumed.index("--skip-git-repo-check") > resumed.index("resume")
    assert resumed[resumed.index("--cd") + 1] == str(tmp_path)


def test_wechat_model_only_route_disables_claude_tools():
    class StopBuild(Exception):
        pass

    class CapturingClaudeAdapter:
        def build_command(self, **kwargs):
            self.policy = kwargs["policy"]
            raise StopBuild

    adapter = CapturingClaudeAdapter()
    execution = object.__new__(RoutedCodexExecution)
    execution._claude_adapter = adapter
    factory = CodexCommandFactory.standard(
        developer_instructions="Decide the reply", model_only=True,
    )

    with pytest.raises(StopBuild):
        execution._run_claude_turn(
            route=object(), session_id=None, command_factory=factory,
            prompt="anonymous reply", on_stdout_line=lambda _: None,
        )

    assert adapter.policy.tools_enabled is False
