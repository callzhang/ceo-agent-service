import asyncio
from pathlib import Path

import pytest

from app.agent_cli import build_role_server
from app.agent_runtime_contracts import CredentialMode, RuntimeKind, RuntimeRoute
from app.consumer_agent import system_action_contracts_text
from app.runtime_prompt_context import render_runtime_context
from app.wechat.codex_safety import make_role_agent_command


SERVICE_ROOT = Path(__file__).resolve().parent.parent
CONTRACT_PATH = SERVICE_ROOT / "docs" / "system-action-contracts.md"


def _route(kind: RuntimeKind = RuntimeKind.CODEX_CLI) -> RuntimeRoute:
    return RuntimeRoute(
        name="codex",
        runtime_kind=kind,
        credential_mode=CredentialMode.LOCAL_OAUTH,
        model="test-model",
    )


def _command(role: str) -> list[str]:
    command = ["codex", "exec", "task"]
    make_role_agent_command(
        command,
        role=role,
        task_workspace="/task/current" if role == "consumer" else None,
    )
    return command


def test_catalog_is_generated_from_every_canonical_capability_and_operation() -> None:
    from app.action_contract_catalog import action_contract_catalog, parse_action_contracts

    document = CONTRACT_PATH.read_text(encoding="utf-8").strip()
    entries = parse_action_contracts(document)
    catalog = action_contract_catalog(document)

    assert [(entry.capability, entry.operation) for entry in entries] == [
        ("dingtalk-chat", "reply_to_message"),
        ("dingtalk-chat", "send_message"),
        ("dingtalk-chat", "send_group_message"),
        ("dingtalk-chat", "send_direct_message"),
        ("dingtalk-chat", "add_message_emoji"),
        ("dingtalk-chat", "add_message_text_emotion"),
        ("dingtalk-chat", "create_message_text_emotion"),
        ("dingtalk-calendar", "respond_calendar_event"),
        ("dingtalk-oa", "approve"),
        ("dingtalk-oa", "reject"),
        ("dingtalk-oa", "revert_task"),
        ("dingtalk-oa", "redirect_task"),
        ("dingtalk-oa", "comment"),
        ("dingtalk-doc", "create_document"),
        ("dingtalk-doc", "create_doc_comment"),
    ]
    assert len(catalog) < len(document) // 2
    for entry in entries:
        assert f"`{entry.capability}`" in catalog
        assert f"`{entry.operation}`" in catalog
    assert "agent_cli.read_system_action_contract" in catalog
    assert "capability" in catalog and "operation" in catalog


def test_lookup_returns_the_complete_original_contract_for_an_exact_operation() -> None:
    from app.action_contract_catalog import read_system_action_contract

    original = CONTRACT_PATH.read_text(encoding="utf-8").strip()
    result = read_system_action_contract(
        capability="dingtalk-calendar",
        operation="respond_calendar_event",
    )

    assert result == {
        "capability": "dingtalk-calendar",
        "operation": "respond_calendar_event",
        "supported_operations": ["respond_calendar_event"],
        "contract": original,
    }
    assert "Consumer cannot directly dispatch" in result["contract"]
    assert "stable `action_identity`" in result["contract"]
    assert "Completion evidence" in result["contract"]
    assert "An unsupported operation fails explicitly." in result["contract"]


def test_lookup_lists_capability_operations_and_rejects_unknown_names() -> None:
    from app.action_contract_catalog import read_system_action_contract

    result = read_system_action_contract(capability="dingtalk-doc")
    assert result["operation"] is None
    assert result["supported_operations"] == [
        "create_document",
        "create_doc_comment",
    ]

    with pytest.raises(ValueError, match="unknown system action capability: unknown"):
        read_system_action_contract(capability="unknown")
    with pytest.raises(
        ValueError,
        match="unknown system action operation for dingtalk-doc: unknown",
    ):
        read_system_action_contract(capability="dingtalk-doc", operation="unknown")


@pytest.mark.parametrize("role", ["consumer", "audit"])
def test_exact_contract_lookup_is_declared_read_only_for_both_roles(role: str) -> None:
    server = build_role_server(role)
    tools = {tool.name: tool for tool in asyncio.run(server.list_tools())}
    lookup = tools["read_system_action_contract"]

    assert lookup.annotations.readOnlyHint is True
    assert lookup.annotations.destructiveHint is False
    assert lookup.annotations.idempotentHint is True
    assert lookup.inputSchema["required"] == ["capability"]
    assert set(lookup.inputSchema["properties"]) == {"capability", "operation"}
    result = server._tool_manager.get_tool("read_system_action_contract").fn(
        capability="dingtalk-oa",
        operation="approve",
    )
    assert result["operation"] == "approve"
    assert result["contract"] == CONTRACT_PATH.read_text(encoding="utf-8").strip()


@pytest.mark.parametrize("role", ["consumer", "audit"])
def test_contract_lookup_is_in_native_allowlist_and_runtime_context(role: str) -> None:
    command = _command(role)
    enabled = next(
        command[index + 1]
        for index, value in enumerate(command[:-1])
        if value == "-c"
        and command[index + 1].startswith("mcp_servers.agent_cli.enabled_tools=")
    )
    context = render_runtime_context(
        role=role,
        route=_route(),
        command=command,
        current_time="2026-10-07T12:00:00-07:00",
        preview=True,
    )

    assert '"read_system_action_contract"' in enabled
    assert "agent_cli.read_system_action_contract" in context


@pytest.mark.parametrize("role", ["consumer", "audit"])
@pytest.mark.parametrize("kind", [RuntimeKind.CODEX_CLI, RuntimeKind.CLAUDE_CLI])
@pytest.mark.parametrize("preview", [False, True])
def test_contract_lookup_is_visible_in_actual_and_preview_contexts(
    role: str,
    kind: RuntimeKind,
    preview: bool,
) -> None:
    command = _command(role) if kind is RuntimeKind.CODEX_CLI else ["claude"]
    context = render_runtime_context(
        role=role,
        route=_route(kind),
        command=command,
        current_time="2026-10-07T12:00:00-07:00",
        preview=preview,
    )

    assert "agent_cli.read_system_action_contract" in context


def test_consumer_prompt_uses_short_generated_catalog() -> None:
    original = CONTRACT_PATH.read_text(encoding="utf-8").strip()
    catalog = system_action_contracts_text()

    assert len(catalog) < len(original) // 2
    assert "agent_cli.read_system_action_contract" in catalog
    assert "| Capability | Operation |" not in catalog
