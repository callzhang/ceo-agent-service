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


def test_audit_can_directly_call_only_the_enabled_ocr_tool_and_consumer_all_service_tools() -> None:
    consumer = _command()
    audit = _command()
    make_consumer_agent_command(consumer, task_workspace="/tmp/oa-ocr-task")
    make_audit_agent_command(audit)

    for command in (consumer, audit):
        assert (
            'features.code_mode.direct_only_tool_namespaces='
            '["mcp__agent_cli", "mcp__ai_services"]'
        ) in command
    assert 'mcp_servers.ai_services.enabled_tools=["ocr_document", "transcribe_audio", "create_audio_upload", "get_audio_transcription"]' in audit
    # Derek 2026-10-24: the Consumer's tool surface is not a whitelist.
    assert not any(o.startswith("mcp_servers.ai_services.enabled_tools=") for o in consumer)
    assert 'mcp_servers.ai_services.default_tools_approval_mode="approve"' in consumer
