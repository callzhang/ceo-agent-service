"""The native Agent turns must receive only the tools their role can use."""

import asyncio

import pytest

from app import agent_cli
from app.agent_runtime_contracts import (
    CredentialMode, ROLE_BOUND_AGENT_TOOLS, RuntimeKind, RuntimeRoute,
    runtime_route_surface_capabilities,
)
from app.dws_client import DwsClient
from app.native_cli_metadata import AgentReadOnlyViolationError
from app.service_codex_config import ServiceMcpServer, service_mcp_config_options
from app.system_action_handlers import native_dws_handlers
from app.wechat.codex_safety import make_audit_agent_command, make_consumer_agent_command


def _codex_command() -> list[str]:
    return [
        "codex", "exec", "--json", "-c",
        'mcp_servers.memory_connector.url="https://memory.example/mcp"',
        "-c", 'mcp_servers.xiaoqing_interview.url="https://interview.example/mcp"',
        "-c", "features.browser_use=true",
        "-c", "features.image_generation=true",
        "-c", "features.code_mode_host=false",
        "-c", "features.code_mode_only=false",
        "-c", 'features.code_mode.excluded_tool_namespaces=[]',
        "-c", 'features.code_mode.direct_only_tool_namespaces=["mcp__agent_cli"]',
        "-",
    ]


def test_codex_roles_scope_native_execution_and_select_service_tools(tmp_path, monkeypatch) -> None:
    home = tmp_path / "codex-home"
    home.mkdir()
    (home / "config.toml").write_text(
        '[mcp_servers.node_repl]\ncommand = "/usr/bin/node"\n'
        '[mcp_servers.plaud]\ncommand = "/usr/bin/npx"\n'
        '[mcp_servers.future_executor]\ncommand = "/usr/bin/sh"\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("CODEX_HOME", str(home))
    consumer = _codex_command()
    audit = _codex_command()
    make_consumer_agent_command(consumer, task_workspace=str(tmp_path / "task-workspace"))
    make_audit_agent_command(audit)

    for command in (consumer, audit):
        assert "features.apps=false" in command
        assert "features.plugins=false" in command
        assert "features.computer_use=false" in command
        for feature in (
            "browser_use", "browser_use_external", "browser_use_full_cdp_access",
            "in_app_browser", "in_app_local_automation", "in_app_chat",
            "image_generation", "multi_agent",
            "skill_mcp_dependency_install", "memories",
        ):
            assert f"features.{feature}=false" in command
            assert f"features.{feature}=true" not in command
        assert "features.code_mode_host=true" in command
        assert "features.code_mode_host=false" not in command
        assert "features.code_mode_only=true" in command
        assert "features.code_mode_only=false" not in command
        assert 'features.code_mode.direct_only_tool_namespaces=[]' in command
        assert 'features.code_mode.direct_only_tool_namespaces=["mcp__agent_cli"]' not in command
        assert "mcp_servers.node_repl.enabled=false" in command
        assert "mcp_servers.plaud.enabled=false" in command
        assert "mcp_servers.future_executor.enabled=false" in command
        assert "mcp_servers.memory_connector.enabled=false" not in command
        assert 'approval_policy="never"' in command
        assert 'mcp_servers.memory_connector.enabled_tools=["user_get", "memory_recall", "memory_get", "timeline_get"]' in command
        assert 'mcp_servers.xiaoqing_interview.enabled_tools=["search_candidates", "get_dashboard_stats", "get_interview_context", "download_attachment", "list_candidate_interviews"]' in command
        assert not any(option.startswith("tools.enabled_tools=") for option in command)
        assert "--dangerously-bypass-approvals-and-sandbox" not in command
        assert not any("execute_reviewed_read" in option for option in command)
    assert "--skip-git-repo-check" in consumer
    assert "features.shell_tool=true" in consumer
    assert "features.unified_exec=true" in consumer
    assert 'sandbox_mode="workspace-write"' in consumer
    assert 'sandbox_workspace_write.network_access=false' in consumer
    assert 'sandbox_workspace_write.writable_roots=[]' in consumer
    assert consumer[consumer.index("--cd") + 1] == str(tmp_path / "task-workspace")
    assert 'features.code_mode.excluded_tool_namespaces=[]' in consumer
    assert "features.shell_tool=false" in audit
    assert "features.unified_exec=false" in audit
    assert 'sandbox_mode="read-only"' in audit
    assert 'features.code_mode.excluded_tool_namespaces=["functions"]' in audit
    consumer_tools = next(
        option for option in consumer if option.startswith("mcp_servers.agent_cli.enabled_tools=")
    )
    audit_tools = next(
        option for option in audit if option.startswith("mcp_servers.agent_cli.enabled_tools=")
    )
    assert "consumer_document_write" in consumer_tools
    assert "consumer_document_write" not in audit_tools
    assert "consumer_artifact_write" in consumer_tools
    assert "consumer_artifact_write" not in audit_tools
    assert "read_task_artifact" in consumer_tools + audit_tools
    assert "list_task_artifacts" in consumer_tools + audit_tools
    assert "send_approved_dingtalk_message" not in consumer_tools + audit_tools
    assert "unsubscribe_email" not in consumer_tools + audit_tools


def test_codex_manifest_restricts_direct_third_party_tools() -> None:
    servers = (
        ServiceMcpServer(name="memory_connector", url="https://memory.example/mcp"),
        ServiceMcpServer(name="xiaoqing_interview", url="https://interview.example/mcp"),
    )
    options = service_mcp_config_options(
        servers=servers,
        enabled_tools_by_server={
            "memory_connector": ("memory_recall", "user_get"),
            "xiaoqing_interview": ("get_interview_context",),
        },
    )
    assert 'mcp_servers.memory_connector.enabled_tools=["memory_recall", "user_get"]' in options
    assert 'mcp_servers.xiaoqing_interview.enabled_tools=["get_interview_context"]' in options


def test_friday_route_has_no_role_bound_tool_policy() -> None:
    for kind, expected in (
        (RuntimeKind.CODEX_CLI, True),
        (RuntimeKind.CLAUDE_CLI, True),
        (RuntimeKind.FRIDAY_RUNTIME, False),
    ):
        route = RuntimeRoute(
            name=kind.value, runtime_kind=kind,
            credential_mode=CredentialMode.LOCAL_OAUTH, model="test-model",
        )
        assert (ROLE_BOUND_AGENT_TOOLS in runtime_route_surface_capabilities(route)) is expected


def test_agent_cli_role_catalogue_excludes_controlled_effects() -> None:
    audit_catalogue = {
        tool.name: tool for tool in asyncio.run(agent_cli.build_role_server("audit").list_tools())
    }
    consumer_catalogue = {
        tool.name: tool for tool in asyncio.run(agent_cli.build_role_server("consumer").list_tools())
    }
    audit_tools = set(audit_catalogue)
    consumer_tools = set(consumer_catalogue)
    assert "read_skill" in audit_tools
    assert "read_dingtalk_document" in audit_tools
    assert {
        "list_dingtalk_minutes", "read_dingtalk_minutes", "search_dingtalk_messages",
        "search_dingtalk_conversations", "search_dingtalk_contacts",
        "list_dingtalk_calendar_events", "list_dingtalk_pending_oa",
        "read_dingtalk_oa_records", "read_dingtalk_oa_tasks",
        "read_dingtalk_oa_revert_activities", "search_dingtalk_documents",
        "read_dingtalk_document_info", "read_dingtalk_document_permissions",
        "read_dingtalk_drive_info", "list_dingtalk_sheets",
        "read_dingtalk_sheet_info", "read_dingtalk_sheet_range",
        "read_dingtalk_thread_replies",
    } <= audit_tools
    assert "consumer_document_write" not in audit_tools
    assert "consumer_document_write" in consumer_tools
    assert {"read_task_artifact", "list_task_artifacts"} <= audit_tools
    assert "consumer_artifact_write" not in audit_tools
    assert "consumer_artifact_write" in consumer_tools
    for tools in (audit_tools, consumer_tools):
        registered_operations = {operation for _capability, operation in native_dws_handlers(None, None)}
        assert registered_operations.isdisjoint(tools)
        assert "execute_reviewed_read" not in tools
        assert "send_approved_dingtalk_message" not in tools
        assert "unsubscribe_email" not in tools
    assert all(tool.annotations.readOnlyHint for tool in audit_catalogue.values())
    assert consumer_catalogue["consumer_document_write"].annotations.readOnlyHint is False
    assert consumer_catalogue["consumer_artifact_write"].annotations.readOnlyHint is False


def test_role_oa_pending_read_has_no_date_filter(monkeypatch) -> None:
    commands: list[list[str]] = []

    class RecordingDws:
        dws_bin = "dws"

        def run_json(self, command: list[str]) -> dict[str, object]:
            commands.append(command)
            return {"result": {"values": []}}

    monkeypatch.setattr("app.dws_client.DwsClient", RecordingDws)
    tool = agent_cli.build_role_server("audit")._tool_manager.get_tool(
        "list_dingtalk_pending_oa"
    )
    assert tool.fn(page=2) == {"result": {"values": []}}
    assert commands == [[
        "dws", "oa", "approval", "list-pending", "--page", "2",
        "--limit", "20", "--format", "json",
    ]]
    with pytest.raises(ValueError, match="page is invalid"):
        tool.fn(page=0)


def test_role_minutes_search_is_fixed_read_operation(monkeypatch) -> None:
    commands: list[list[str]] = []

    class RecordingDws:
        dws_bin = "dws"

        def run_json(self, command: list[str]) -> dict[str, object]:
            commands.append(command)
            return {"items": []}

    monkeypatch.setattr("app.dws_client.DwsClient", RecordingDws)
    tool = agent_cli.build_role_server("consumer")._tool_manager.get_tool(
        "list_dingtalk_minutes"
    )
    assert tool.fn(query="周会") == {"items": []}
    assert commands == [[
        "dws", "minutes", "+search", "--scope", "all", "--query", "周会",
        "--page-all", "--page-limit", "100", "--format", "json",
    ]]


def test_report_document_client_has_only_fixed_dws_commands() -> None:
    class RecordingDws(DwsClient):
        def __init__(self) -> None:
            super().__init__(dws_bin="dws")
            self.commands: list[list[str]] = []

        def run_json(self, command: list[str]) -> dict[str, object]:
            self.commands.append(command)
            return {"ok": True}

    client = RecordingDws()
    client.create_report_document(
        name="CEO 每日总结 2026-10-04", content="# 今日", doc_format="markdown",
        folder_id="folder-1",
    )
    assert client.commands[-1] == [
        "dws", "doc", "+create", "--name", "CEO 每日总结 2026-10-04",
        "--content", "# 今日", "--doc-format", "markdown", "--folder",
        "folder-1", "--format", "json",
    ]
    client.overwrite_report_document(
        node_id="doc-1", content='[{"name":"paragraph"}]',
        doc_format="jsonml", expected_revision=7,
    )
    assert client.commands[-1] == [
        "dws", "doc", "+update", "--node", "doc-1", "--command",
        "overwrite", "--content", '[{"name":"paragraph"}]', "--doc-format",
        "jsonml", "--expected-revision", "7", "--yes", "--format", "json",
    ]
    with pytest.raises(ValueError, match="expected revision"):
        client.overwrite_report_document(
            node_id="doc-1", content="[]", doc_format="jsonml",
        )
    client.fetch_report_document_full("doc-1")
    assert client.commands[-1] == [
        "dws", "doc", "+fetch", "--node", "doc-1", "--detail", "full",
        "--scope", "full", "--format", "json",
    ]
    client.validate_report_jsonml("[]")
    assert client.commands[-1] == [
        "dws", "doc", "+script", "--command", "parse", "--doc-format",
        "jsonml", "--content", "[]", "--format", "json",
    ]
    client.save_report_document_version("doc-1")
    assert client.commands[-1] == [
        "dws", "doc", "+version-save", "--node", "doc-1", "--format",
        "json", "--yes",
    ]


def test_consumer_document_write_requires_a_bound_scheduled_report(tmp_path) -> None:
    with pytest.raises(AgentReadOnlyViolationError, match="report_task_invalid"):
        agent_cli._write_bound_report_document(
            db_path=tmp_path / "empty.sqlite3", task_id=1,
            content="# Unbound report", expected_revision=None,
        )


def test_consumer_resume_uses_global_cwd_and_keeps_session_prompt_positions(tmp_path, monkeypatch):
    monkeypatch.setattr("app.wechat.codex_safety.inherited_native_server_names", lambda: ())
    command = ["codex", "exec", "resume", "--json", "session-id", "-"]
    make_consumer_agent_command(command, task_workspace=str(tmp_path))
    assert command[:5] == ["codex", "--cd", str(tmp_path), "exec", "resume"]
    assert command[-2:] == ["session-id", "-"]
    assert "--cd" not in command[5:]
