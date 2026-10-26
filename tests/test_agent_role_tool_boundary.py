"""The native Agent turns must receive only the tools their role can use."""

import asyncio
import json
from pathlib import Path

import pytest

from app import agent_cli
from app.agent_runtime_contracts import (
    CredentialMode, ROLE_BOUND_AGENT_TOOLS, RuntimeKind, RuntimeRoute,
    runtime_route_surface_capabilities,
)
from app.dws_client import DwsClient
from app.consumer_agent import (
    AGENT_CAPABILITY_INSTRUCTIONS,
    audit_developer_sections,
    consumer_developer_sections,
)
from app.service_codex_config import ServiceMcpServer, service_mcp_config_options
from app.system_action_handlers import native_dws_handlers
from app.wechat.codex_safety import make_audit_agent_command, make_consumer_agent_command


@pytest.fixture(autouse=True)
def isolate_task_artifact_roots(tmp_path, monkeypatch):
    from app import task_artifacts

    monkeypatch.setattr(task_artifacts, "TASK_ARTIFACT_ROOT", tmp_path / "task-artifacts")
    monkeypatch.setattr("app.config.workspace_path", lambda: tmp_path / "workspace")


def _codex_command() -> list[str]:
    return [
        "codex", "exec", "--json", "-c",
        'mcp_servers.memory_connector.url="https://memory.example/mcp"',
        "-c", 'mcp_servers.xiaoqing_interview.url="https://interview.example/mcp"',
        "-c", 'mcp_servers.ai_services.url="https://ai.example/mcp"',
        "-c", "features.browser_use=true",
        "-c", "features.image_generation=true",
        "-c", "features.code_mode_host=false",
        "-c", "features.code_mode_only=false",
        "-c", "features.code_mode.enabled=false",
        "-c", 'features.code_mode.excluded_tool_namespaces=[]',
        "-c", 'features.code_mode.direct_only_tool_namespaces=["functions", "mcp__future_executor"]',
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
    # The turn builder gives every role the native auto-review approval.
    approval = ["-c", 'approval_policy="on-failure"', "-c", 'approvals_reviewer="auto_review"']
    consumer = _codex_command()
    audit = _codex_command()
    consumer[2:2] = approval
    audit[2:2] = approval
    make_consumer_agent_command(consumer, task_workspace=str(tmp_path / "task-workspace"))
    make_audit_agent_command(audit)

    # Derek 2026-10-24: the Consumer inherits app connectors, plugins and every
    # MCP server from the CLI home; Audit keeps them disabled.
    for feature in ("apps", "plugins", "remote_plugin"):
        assert f"features.{feature}=false" in audit
        assert not any(o.startswith(f"features.{feature}=") for o in consumer)
    for command in (consumer, audit):
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
        assert "features.code_mode.enabled=true" in command
        assert "features.code_mode.enabled=false" not in command
        assert 'features.code_mode.direct_only_tool_namespaces=["mcp__agent_cli", "mcp__ai_services"]' in command
        assert sum(option.startswith("features.code_mode.direct_only_tool_namespaces=") for option in command) == 1
        assert "mcp_servers.memory_connector.enabled=false" not in command
        assert "mcp_servers.ai_services.enabled=false" not in command
        assert not any(option.startswith("tools.enabled_tools=") for option in command)
        assert "--dangerously-bypass-approvals-and-sandbox" not in command
        assert not any("execute_reviewed_read" in option for option in command)
    for name in ("node_repl", "plaud", "future_executor"):
        assert f"mcp_servers.{name}.enabled=false" in audit
        assert f"mcp_servers.{name}.enabled=false" not in consumer
        assert f'mcp_servers.{name}.default_tools_approval_mode="approve"' in consumer
    assert 'mcp_servers.memory_connector.enabled_tools=["user_get", "memory_recall", "memory_get", "timeline_get"]' in audit
    assert 'mcp_servers.ai_services.enabled_tools=["ocr_document"]' in audit
    assert any(o.startswith("mcp_servers.xiaoqing_interview.enabled_tools=") for o in audit)
    assert not any(".enabled_tools=" in o and not o.startswith("mcp_servers.agent_cli.")
                   for o in consumer)
    for name in ("memory_connector", "xiaoqing_interview", "ai_services"):
        assert f'mcp_servers.{name}.default_tools_approval_mode="approve"' in consumer
    # Derek 2026-10-24: the Consumer runs in the CLI's native auto mode, so a
    # command the sandbox breaks can ask for escalation and the automatic
    # reviewer decides; Audit never asks.
    assert 'approval_policy="on-failure"' in consumer
    assert 'approvals_reviewer="auto_review"' in consumer
    assert 'approval_policy="never"' not in consumer
    assert 'approval_policy="never"' in audit
    assert 'approvals_reviewer="auto_review"' not in audit
    assert "--skip-git-repo-check" in consumer
    assert "features.shell_tool=true" in consumer
    assert "features.unified_exec=true" in consumer
    assert 'sandbox_mode="workspace-write"' in consumer
    # Derek 2026-10-23: the Consumer shell runs `dws`, so it needs the network
    # and a writable dws state directory (and nothing else).
    assert 'sandbox_workspace_write.network_access=true' in consumer
    assert (
        'sandbox_workspace_write.writable_roots='
        + json.dumps([str(Path.home() / ".dws")])
    ) in consumer
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
    assert "read_dingtalk_oa_process_attachment" in consumer_tools
    assert "read_dingtalk_oa_process_attachment" in audit_tools
    assert "read_dingtalk_oa_form_image" in consumer_tools
    assert "read_dingtalk_oa_form_image" in audit_tools
    assert "send_approved_dingtalk_message" not in consumer_tools + audit_tools
    assert "unsubscribe_email" not in consumer_tools + audit_tools


def test_oa_consumer_can_handoff_ocr_bytes_inside_code_mode(tmp_path, monkeypatch) -> None:
    home = tmp_path / "codex-home"
    home.mkdir()
    (home / "config.toml").write_text("", encoding="utf-8")
    monkeypatch.setenv("CODEX_HOME", str(home))
    consumer = _codex_command()
    audit = _codex_command()

    make_consumer_agent_command(
        consumer, task_workspace=str(tmp_path / "task-workspace"), purpose_name="oa",
    )
    make_audit_agent_command(audit)

    # The same role-scoped MCP tools can now be composed in one V8 cell, so
    # the model never needs to receive and recopy a large base64 image.
    assert "features.code_mode.direct_only_tool_namespaces=[]" in consumer
    assert not any(o.startswith("mcp_servers.ai_services.enabled_tools=") for o in consumer)
    assert 'mcp_servers.ai_services.default_tools_approval_mode="approve"' in consumer
    assert "mcp_servers.ai_services.enabled=false" not in consumer
    assert 'features.code_mode.excluded_tool_namespaces=[]' in consumer
    assert (
        'features.code_mode.direct_only_tool_namespaces='
        '["mcp__agent_cli", "mcp__ai_services"]'
    ) in audit
    assert 'features.code_mode.excluded_tool_namespaces=["functions"]' in audit


def test_oa_audit_composes_only_declared_read_and_ocr_mcp_tools(tmp_path, monkeypatch) -> None:
    home = tmp_path / "codex-home"
    home.mkdir()
    (home / "config.toml").write_text("", encoding="utf-8")
    monkeypatch.setenv("CODEX_HOME", str(home))
    ordinary = _codex_command()
    oa = _codex_command()
    make_audit_agent_command(ordinary)
    make_audit_agent_command(oa, purpose_name="oa")

    assert 'features.code_mode.direct_only_tool_namespaces=["mcp__agent_cli", "mcp__ai_services"]' in ordinary
    assert 'features.code_mode.excluded_tool_namespaces=["functions"]' in ordinary
    assert 'features.code_mode.direct_only_tool_namespaces=["mcp__memory_connector", "mcp__xiaoqing_interview"]' in oa
    assert 'features.code_mode.excluded_tool_namespaces=["functions", "image_gen", "multi_agent_v1", "web"]' in oa
    assert 'sandbox_mode="read-only"' in oa
    assert 'mcp_servers.ai_services.enabled_tools=["ocr_document"]' in oa
    assert next(option for option in ordinary if option.startswith("mcp_servers.agent_cli.enabled_tools=")) == next(
        option for option in oa if option.startswith("mcp_servers.agent_cli.enabled_tools=")
    )


def test_service_agent_instructions_use_native_dingtalk_read_tools() -> None:
    audit = "\n".join(section.text for section in audit_developer_sections("rules"))
    oa_consumer = "\n".join(
        section.text for section in consumer_developer_sections(purpose_name="oa")
    )

    consumer_normalized = " ".join(AGENT_CAPABILITY_INSTRUCTIONS.replace("`", "").split()).lower()
    assert "mcp__agent_cli" in consumer_normalized
    assert "consumer may use local dws commands through shell for source reads" in consumer_normalized
    assert "never use dws through shell to send dingtalk messages or email" in consumer_normalized
    assert "oa approve, reject, return, or comment actions" in consumer_normalized

    audit_normalized = " ".join(audit.replace("`", "").split()).lower()
    assert "call" in audit_normalized
    assert "mcp__agent_cli" in audit_normalized
    assert "audit must use declared read tools only" in audit_normalized
    assert "must not use shell or local dws commands" in audit_normalized
    assert "ai_services.ocr_document" in audit_normalized
    assert "call the native" in audit_normalized
    assert "do not infer" in audit_normalized
    assert "all_tools" in audit_normalized

    oa_normalized = " ".join(oa_consumer.replace("`", "").split()).lower()
    assert "functions.exec" in oa_normalized
    assert "mcp__agent_cli" in oa_normalized
    assert "mcp__ai_services__ocr_document" in oa_normalized
    assert "same js cell" in oa_normalized
    assert "do not print" in oa_normalized
    assert "do not pass its original pdf page number as ocr page_numbers" in oa_normalized
    assert "confidence, warnings" in oa_normalized
    assert "call the native ai_services.ocr_document mcp directly" not in oa_normalized
    assert "only the oa consumer may use shell for the exact attachment fallback" in oa_normalized
    assert "authorize-preview" in oa_normalized
    assert "oa consumer may also use dws through shell for other source reads" in oa_normalized
    assert "never use dws to send dingtalk messages or email" in oa_normalized
    assert "or for oa approve, reject, return, or comment actions" in oa_normalized
    assert "never run local dws commands through shell" not in oa_normalized

    oa_audit = " ".join(
        " ".join(section.text.split())
        for section in audit_developer_sections("rules", purpose_name="oa")
    ).replace("`", "").lower()
    assert "same js cell" in oa_audit
    assert "mcp__ai_services__ocr_document" in oa_audit
    assert "call the native ai_services.ocr_document mcp directly" not in oa_audit
    assert "audit must not use shell or local dws commands" in oa_audit
    assert "never use dws to send dingtalk messages or email" in oa_audit


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
    assert "exact DingTalk Drive attachment for OA review" in audit_catalogue[
        "read_dingtalk_drive_file"
    ].description
    assert "ocr_page_numbers" in audit_catalogue[
        "read_dingtalk_drive_file"
    ].description
    assert "ocr_original_page_number" in audit_catalogue[
        "read_dingtalk_drive_file"
    ].description
    assert "premium OA comment attachment" in audit_catalogue[
        "read_dingtalk_oa_comment_attachment"
    ].description
    assert "OA form attachment" in audit_catalogue[
        "read_dingtalk_oa_process_attachment"
    ].description
    assert "exact OA form" in audit_catalogue[
        "read_dingtalk_oa_form_image"
    ].description
    assert "ocr_page_numbers" in audit_catalogue[
        "read_dingtalk_oa_comment_attachment"
    ].description
    assert "authorize_dingtalk_oa_attachment_preview" not in audit_tools
    assert "authorize_dingtalk_oa_attachment_preview" not in consumer_tools
    assert "instead of a file_id" in audit_catalogue[
        "read_dingtalk_oa_comment_image"
    ].description
    assert {
        "list_dingtalk_minutes", "read_dingtalk_minutes", "search_dingtalk_messages",
        "search_dingtalk_conversations", "search_dingtalk_contacts",
        "list_dingtalk_calendar_events", "list_dingtalk_pending_oa",
        "read_dingtalk_oa_records", "read_dingtalk_oa_tasks",
        "read_dingtalk_oa_comment_attachment",
        "read_dingtalk_oa_comment_image",
        "read_dingtalk_oa_form_image",
        "read_dingtalk_oa_revert_activities", "search_dingtalk_documents",
        "read_dingtalk_document_info", "read_dingtalk_document_permissions",
        "read_dingtalk_drive_info", "read_dingtalk_drive_file", "list_dingtalk_sheets",
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
    assert not {
        name for name, tool in audit_catalogue.items()
        if not tool.annotations.readOnlyHint
    }
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
        "dws", "doc", "create", "--name", "CEO 每日总结 2026-10-04",
        "--content", "# 今日", "--content-format", "markdown", "--folder",
        "folder-1", "--format", "json",
    ]
    client.overwrite_report_document(
        node_id="doc-1", content='[{"name":"paragraph"}]',
        doc_format="jsonml", expected_revision=7,
    )
    assert client.commands[-1] == [
        "dws", "doc", "update", "--node", "doc-1", "--mode",
        "overwrite", "--content", '[{"name":"paragraph"}]', "--content-format",
        "jsonml", "--revision", "7", "--yes", "--format", "json",
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


def test_consumer_document_write_requires_a_bound_scheduled_task(tmp_path) -> None:
    with pytest.raises(ValueError, match="scheduled task required"):
        agent_cli._bound_scheduled_run(tmp_path / "empty.sqlite3", 1)


def test_consumer_resume_uses_global_cwd_and_keeps_session_prompt_positions(tmp_path, monkeypatch):
    monkeypatch.setattr("app.wechat.codex_safety.inherited_native_server_names", lambda: ())
    command = ["codex", "exec", "resume", "--json", "session-id", "-"]
    make_consumer_agent_command(command, task_workspace=str(tmp_path))
    assert command[:5] == ["codex", "--cd", str(tmp_path), "exec", "resume"]
    assert command[-2:] == ["session-id", "-"]
    assert "--cd" not in command[5:]


@pytest.mark.parametrize("role", ["consumer", "audit"])
@pytest.mark.parametrize("resume", [False, True])
def test_codex_role_preserves_long_skill_output(tmp_path, monkeypatch, role, resume):
    from app.wechat.codex_safety import make_role_agent_command

    home = tmp_path / "codex-home"
    home.mkdir()
    (home / "config.toml").write_text("")
    monkeypatch.setenv("CODEX_HOME", str(home))
    command = ["codex", "exec", *(["resume", "session-id"] if resume else []), "--json", "-"]
    make_role_agent_command(command, role=role,
                            task_workspace=str(tmp_path) if role == "consumer" else None)

    assert command.count("mcp_servers.agent_cli.tools.read_task_skill.output_token_limit=30000") == 1
    assert command.count("mcp_servers.agent_cli.tools.read_skill.output_token_limit=30000") == 1
    assert not any(value.startswith("tool_output_token_limit=") for value in command)
    assert command[-1] == "-"
    assert ("resume" in command) is resume


@pytest.mark.parametrize("role", ["consumer", "audit"])
@pytest.mark.parametrize("resume", [False, True])
def test_codex_role_preserves_full_document_output(tmp_path, monkeypatch, role, resume):
    from app.wechat.codex_safety import make_role_agent_command

    monkeypatch.setattr("app.wechat.codex_safety.inherited_native_server_names", lambda: ())
    command = ["codex", "exec", *(["resume", "session-id"] if resume else []), "--json", "-"]
    make_role_agent_command(command, role=role,
                            task_workspace=str(tmp_path) if role == "consumer" else None)

    assert command.count("mcp_servers.agent_cli.tools.read_dingtalk_document_full.output_token_limit=30000") == 1
    assert not any(value.startswith("tool_output_token_limit=") for value in command)
    assert command[-1] == "-"
    assert ("resume" in command) is resume


@pytest.mark.parametrize("role", ["consumer", "audit"])
@pytest.mark.parametrize("resume", [False, True])
def test_codex_role_preserves_aitable_record_page_output(tmp_path, monkeypatch, role, resume):
    from app.wechat.codex_safety import make_role_agent_command

    monkeypatch.setattr("app.wechat.codex_safety.inherited_native_server_names", lambda: ())
    command = ["codex", "exec", *(["resume", "session-id"] if resume else []), "--json", "-"]
    make_role_agent_command(command, role=role,
                            task_workspace=str(tmp_path) if role == "consumer" else None)

    assert command.count(
        "mcp_servers.agent_cli.tools.read_dingtalk_aitable_records.output_token_limit=30000"
    ) == 1
    assert not any(value.startswith("tool_output_token_limit=") for value in command)
    assert command[-1] == "-"
    assert ("resume" in command) is resume


@pytest.mark.parametrize("role", ["consumer", "audit"])
@pytest.mark.parametrize("resume", [False, True])
def test_codex_role_preserves_message_list_output(tmp_path, monkeypatch, role, resume):
    from app.wechat.codex_safety import make_role_agent_command

    monkeypatch.setattr("app.wechat.codex_safety.inherited_native_server_names", lambda: ())
    command = ["codex", "exec", *(["resume", "session-id"] if resume else []), "--json", "-"]
    make_role_agent_command(command, role=role,
                            task_workspace=str(tmp_path) if role == "consumer" else None)

    assert command.count("mcp_servers.agent_cli.tools.read_dingtalk_messages.output_token_limit=30000") == 1
    assert not any(value.startswith("tool_output_token_limit=") for value in command)
    assert command[-1] == "-"
    assert ("resume" in command) is resume


def test_consumer_inherits_cli_home_servers_but_not_manifest_disabled_ones(
    tmp_path, monkeypatch
) -> None:
    """A server the manifest disables stays off; every other local server is usable."""
    home = tmp_path / "codex-home"
    home.mkdir()
    (home / "config.toml").write_text(
        '[mcp_servers.figma_local]\ncommand = "/usr/bin/figma"\n'
        '[mcp_servers.brightdata]\ncommand = "/usr/bin/npx"\n'
        '[mcp_servers.cua_repl]\ncommand = "/usr/bin/cua"\n'
        '[mcp_servers.computer-use]\ncommand = "/usr/bin/computer-use"\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("CODEX_HOME", str(home))
    command = _codex_command()
    command[-1:-1] = [
        "-c", 'mcp_servers.cua_repl={"enabled" = false, "command" = "/usr/bin/false"}',
        "-c", 'mcp_servers.brightdata={"enabled" = false, "command" = "/usr/bin/false"}',
    ]

    make_consumer_agent_command(command, task_workspace=str(tmp_path / "task-workspace"))

    assert 'mcp_servers.figma_local.default_tools_approval_mode="approve"' in command
    assert "mcp_servers.figma_local.enabled=false" not in command
    for disabled in ("cua_repl", "brightdata"):
        assert f"mcp_servers.{disabled}.enabled=false" not in command
        assert not any(
            o.startswith(f"mcp_servers.{disabled}.default_tools_approval_mode=")
            for o in command
        )
    assert any(o.startswith('mcp_servers.cua_repl={"enabled" = false') for o in command)
    # Desktop control is off even though the manifest does not name it.
    assert "mcp_servers.computer-use.enabled=false" in command
    assert not any(
        o.startswith("mcp_servers.computer-use.default_tools_approval_mode=")
        for o in command
    )
    # The fan-out and computer-use surfaces stay off for the Consumer too.
    for feature in ("multi_agent", "computer_use", "browser_use"):
        assert f"features.{feature}=false" in command


def test_audit_command_is_unchanged_by_the_consumer_tool_surface(
    tmp_path, monkeypatch
) -> None:
    home = tmp_path / "codex-home"
    home.mkdir()
    (home / "config.toml").write_text(
        '[mcp_servers.figma_local]\ncommand = "/usr/bin/figma"\n', encoding="utf-8",
    )
    monkeypatch.setenv("CODEX_HOME", str(home))
    audit = _codex_command()

    make_audit_agent_command(audit)

    assert "mcp_servers.figma_local.enabled=false" in audit
    for feature in ("apps", "plugins", "remote_plugin", "multi_agent", "computer_use"):
        assert f"features.{feature}=false" in audit
    assert 'mcp_servers.memory_connector.enabled_tools=["user_get", "memory_recall", "memory_get", "timeline_get"]' in audit
