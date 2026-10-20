from app.wechat.codex_safety import (
    make_audit_agent_command,
    make_consumer_agent_command,
)


def _command() -> list[str]:
    return [
        "codex",
        "exec",
        "--json",
        "-c",
        'mcp_servers.ai_services.url="https://ai.example/mcp"',
        "-c",
        'features.code_mode.direct_only_tool_namespaces=["functions"]',
        "-",
    ]


def test_consumer_and_audit_can_directly_call_only_the_enabled_ocr_tool() -> None:
    consumer = _command()
    audit = _command()
    make_consumer_agent_command(consumer, task_workspace="/tmp/oa-ocr-task")
    make_audit_agent_command(audit)

    for command in (consumer, audit):
        assert (
            'features.code_mode.direct_only_tool_namespaces='
            '["mcp__agent_cli", "mcp__ai_services"]'
        ) in command
        assert 'mcp_servers.ai_services.enabled_tools=["ocr_document"]' in command
