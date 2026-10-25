import asyncio
import json

import pytest
from mcp.server.fastmcp.exceptions import ToolError

from app.agent_cli import build_role_server
from app.native_agent_output import native_output_schema_path
from app.wechat.codex_safety import AGENT_CLI_CONSUMER_TOOLS, AGENT_CLI_READ_TOOLS


@pytest.mark.parametrize("purpose", ["general", "oa"])
def test_consumer_queries_complete_native_contract_without_duplicate_structured_output(purpose):
    server = build_role_server("consumer")
    tool = server._tool_manager.get_tool("read_consumer_output_schema")
    assert tool is not None
    content = asyncio.run(server.call_tool("read_consumer_output_schema", {"purpose": purpose}))
    assert isinstance(content, list) and len(content) == 1
    assert json.loads(content[0].text) == json.loads(native_output_schema_path(purpose=purpose).read_text())
    spec = next(t for t in asyncio.run(server.list_tools()) if t.name == "read_consumer_output_schema")
    assert spec.outputSchema is None
    assert spec.annotations.readOnlyHint is True
    assert spec.annotations.destructiveHint is False


def test_schema_query_adds_no_audit_or_arbitrary_file_access():
    assert "read_consumer_output_schema" in AGENT_CLI_CONSUMER_TOOLS
    assert "read_consumer_output_schema" not in AGENT_CLI_READ_TOOLS
    assert build_role_server("audit")._tool_manager.get_tool("read_consumer_output_schema") is None
    server = build_role_server("consumer")
    with pytest.raises(ToolError):
        asyncio.run(server.call_tool("read_consumer_output_schema", {"purpose": "../../.env"}))
