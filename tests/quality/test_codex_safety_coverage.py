from app.wechat.codex_safety import (
    completed_mcp_tool_calls,
    completed_tool_events,
    configured_transport_server_names,
    disable_configured_mcp_servers,
    has_any_tool_event,
    make_read_only_without_tools,
)
from app.codex_runner import CODEX_BYPASS_APPROVALS_AND_SANDBOX


def test_tool_event_parsing_ignores_invalid_and_non_object_items(monkeypatch) -> None:
    raw = "\n".join(
        [
            "not-json",
            '{"type":"item.completed","item":"bad"}',
            '{"type":"item.started","item":{"type":"web_search"}}',
        ]
    )
    assert completed_tool_events(raw) == []
    assert has_any_tool_event(raw) is True

    monkeypatch.setattr("app.codex_runner._codex_config", lambda: {"mcp_servers": {1: {}, "bad": None}})
    monkeypatch.setattr("app.codex_runner._passthrough_mcp_server_names", lambda: ("bad",))
    assert configured_transport_server_names(["codex", "exec"]) == ()


def test_completed_tools_and_mcp_filtering() -> None:
    raw = "\n".join(
        [
            '{"type":"item.completed","item":{"type":"mcp_tool_call","name":"read"}}',
            '{"type":"item.completed","item":{"type":"custom_tool_call"}}',
            '{"type":"item.completed","item":{"type":"message"}}',
        ]
    )

    assert len(completed_tool_events(raw)) == 2
    assert [item["name"] for item in completed_mcp_tool_calls(raw)] == ["read"]
    assert has_any_tool_event('{"type":"message"}') is False


def test_transport_discovery_and_disable_are_fail_closed(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.codex_runner._codex_config",
        lambda: {
            "mcp_servers": {
                "memory": {"command": "memory-server"},
                "exa": {"url": "https://example.invalid"},
                "empty": {"command": ""},
            }
        },
    )
    monkeypatch.setattr(
        "app.codex_runner._passthrough_mcp_server_names",
        lambda: ("memory", "exa", "empty"),
    )
    command = [
        "codex",
        "-c",
        "mcp_servers.xiaoqing.command=adapter",
        "-c",
        "mcp_servers.exa.url=https://default.invalid",
        "exec",
    ]

    assert configured_transport_server_names(command) == ("exa", "memory", "xiaoqing")
    disable_configured_mcp_servers(command, except_names=frozenset({"memory"}))

    assert "mcp_servers.memory.enabled=false" not in command
    assert "mcp_servers.exa.enabled=false" in command
    assert "mcp_servers.xiaoqing.enabled=false" in command


def test_unconfigured_default_exa_is_not_treated_as_workflow_transport(monkeypatch) -> None:
    monkeypatch.setattr("app.codex_runner._codex_config", lambda: {})
    monkeypatch.setattr("app.codex_runner._passthrough_mcp_server_names", lambda: ())

    names = configured_transport_server_names(
        ["codex", "-c", "mcp_servers.exa.url=https://default.invalid", "exec"]
    )

    assert names == ()


def test_read_only_command_removes_bypass_and_disables_memory(monkeypatch) -> None:
    monkeypatch.setattr("app.codex_runner._codex_config", lambda: {})
    monkeypatch.setattr("app.codex_runner._passthrough_mcp_server_names", lambda: ())
    command = [
        "codex",
        CODEX_BYPASS_APPROVALS_AND_SANDBOX,
        CODEX_BYPASS_APPROVALS_AND_SANDBOX,
        "exec",
    ]

    make_read_only_without_tools(command)
    make_read_only_without_tools(command)

    assert CODEX_BYPASS_APPROVALS_AND_SANDBOX not in command
    assert command.count("mcp_servers.memory_connector.enabled=false") == 1
    assert "read-only" in command
    assert "tools.enabled_tools=[]" in command
