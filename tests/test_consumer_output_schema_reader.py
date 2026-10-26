"""Native schema delivery replaces the retired full-schema query tool."""

import asyncio

import pytest

from app.agent_cli import build_role_server
from app.agent_turn_runner import _native_output_schema_for_route
from app.agent_runtime_contracts import RuntimeKind
from app.codex_runner import CodexRunner
from app.store import AgentRole
from app.wechat.codex_safety import AGENT_CLI_CONSUMER_TOOLS, AGENT_CLI_READ_TOOLS


@pytest.mark.parametrize("role", ["consumer", "audit"])
def test_role_tools_do_not_expose_duplicate_schema_reader(role):
    server = build_role_server(role)
    assert server._tool_manager.get_tool("read_consumer_output_schema") is None
    assert "read_consumer_output_schema" not in {tool.name for tool in asyncio.run(server.list_tools())}
    assert "read_consumer_output_schema" not in AGENT_CLI_CONSUMER_TOOLS
    assert "read_consumer_output_schema" not in AGENT_CLI_READ_TOOLS


@pytest.mark.parametrize("purpose", ["general", "oa"])
@pytest.mark.parametrize("session_id", [None, "existing-session"])
def test_consumer_native_schema_is_submitted_on_cold_and_resume(tmp_path, purpose, session_id):
    schema = _native_output_schema_for_route(RuntimeKind.CODEX_CLI, AgentRole.CONSUMER, purpose=purpose)
    assert schema is not None and schema.is_file()
    command = CodexRunner(workspace=tmp_path, codex_bin="codex").build_command(
        prompt="Current task facts", session_id=session_id,
        output_schema_path=schema, use_output_schema=True,
    )
    assert command.count("--output-schema") == 1
    assert command[command.index("--output-schema") + 1] == str(schema)
    assert ("resume" in command) is (session_id is not None)
