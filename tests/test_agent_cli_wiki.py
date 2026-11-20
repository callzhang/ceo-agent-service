"""Exact Wiki evidence reads needed to verify company document placement."""
import asyncio

import pytest

from app import agent_cli
from app.dws_client import DwsError
from app.wechat.codex_safety import AGENT_CLI_READ_TOOLS


@pytest.mark.parametrize("role", ["consumer", "audit"])
def test_wiki_evidence_readers_available_to_both_roles(role):
    catalog = {t.name: t for t in asyncio.run(agent_cli.build_role_server(role).list_tools())}
    for name in ("read_dingtalk_wiki_node", "read_dingtalk_wiki_space"):
        assert name in catalog
        assert name in AGENT_CLI_READ_TOOLS
        assert catalog[name].annotations.readOnlyHint is True


@pytest.mark.parametrize("role", ["consumer", "audit"])
@pytest.mark.parametrize(("name", "argument", "leaf", "flag"), [
    ("read_dingtalk_wiki_node", "source-node", "+node-get", "--node"),
    ("read_dingtalk_wiki_space", "source-workspace", "+space-get", "--workspace"),
])
def test_exact_wiki_reads_preserve_authoritative_metadata(monkeypatch, role, name, argument, leaf, flag):
    calls = []
    payload = {"success": True, "result": {"workspaceId": "source-workspace", "spaceType": "myWikiSpace", "parentId": "parent-node", "visibility": "PRIVATE"}}
    class Dws:
        dws_bin = "dws"
        def run_json(self, command):
            calls.append(command)
            return payload
    monkeypatch.setattr("app.dws_client.DwsClient", Dws)
    tool = agent_cli.build_role_server(role)._tool_manager.get_tool(name)
    assert tool is not None
    assert tool.fn(argument) == payload
    assert calls == [["dws", "wiki", leaf, flag, argument, "--format", "json"]]


@pytest.mark.parametrize("name", ["read_dingtalk_wiki_node", "read_dingtalk_wiki_space"])
@pytest.mark.parametrize("identifier", ["", "   "])
def test_wiki_reads_refuse_missing_identifiers_before_provider(monkeypatch, name, identifier):
    def unexpected_provider():
        pytest.fail("empty identifier must not call DWS")
    monkeypatch.setattr("app.dws_client.DwsClient", unexpected_provider)
    tool = agent_cli.build_role_server("consumer")._tool_manager.get_tool(name)
    assert tool is not None
    with pytest.raises(ValueError, match="required"):
        tool.fn(identifier)


@pytest.mark.parametrize("name", ["read_dingtalk_wiki_node", "read_dingtalk_wiki_space"])
def test_wiki_permission_errors_remain_errors(monkeypatch, name):
    class Dws:
        dws_bin = "dws"
        def run_json(self, command):
            raise DwsError("permission denied")
    monkeypatch.setattr("app.dws_client.DwsClient", Dws)
    tool = agent_cli.build_role_server("audit")._tool_manager.get_tool(name)
    assert tool is not None
    with pytest.raises(DwsError, match="permission denied"):
        tool.fn("source-id")
