import asyncio

import pytest

from app.agent_cli import build_role_server
from app.dws_client import DwsClient
from app.wechat.codex_safety import AGENT_CLI_READ_TOOLS


@pytest.mark.parametrize("role", ["consumer", "audit"])
def test_audience_reads_available_in_actual_role_catalogue(role):
    tools = {tool.name: tool for tool in asyncio.run(build_role_server(role).list_tools())}
    for name in ("read_dingtalk_group_members", "read_dingtalk_user_profiles"):
        assert name in tools
        assert tools[name].annotations.readOnlyHint is True
        assert name in AGENT_CLI_READ_TOOLS


@pytest.mark.parametrize("has_more", [True, False])
def test_group_members_use_native_complete_pagination_and_preserve_partial(monkeypatch, has_more):
    client = DwsClient()
    payload = {"conversationId": "cid", "complete": False, "hasMore": has_more,
               "users": [{"openDingtalkId": "open"}], "bots": [],
               "buckets": {"users": {"pagesFetched": 50, "complete": False}},
               "failures": [{"code": "quota"}]}
    commands = []
    monkeypatch.setattr(client, "run_json", lambda args: commands.append(args) or payload)
    assert client.read_group_members("cid") is payload
    assert commands == [[client.dws_bin, "chat", "+chat-members-list",
                         "--conversation-id", "cid", "--format", "json"]]


def test_profiles_read_exact_user_ids_without_name_search(monkeypatch):
    client = DwsClient()
    payload = {"success": True, "result": [{"orgEmployeeModel": {"orgUserId": "staff", "depts": [], "labels": []}}]}
    commands = []
    monkeypatch.setattr(client, "run_json", lambda args: commands.append(args) or payload)
    assert client.read_user_profiles(["staff", "other"]) is payload
    assert commands == [[client.dws_bin, "contact", "user", "get", "--ids",
                         "staff,other", "--format", "json"]]


@pytest.mark.parametrize("ids", [[], [""], ["a,b"], [" staff "], [1]])
def test_profile_read_rejects_invalid_id_batch(ids):
    with pytest.raises(ValueError):
        DwsClient().read_user_profiles(ids)


def test_empty_provider_profile_remains_a_gap(monkeypatch):
    client = DwsClient()
    payload = {"success": True, "result": [{"orgEmployeeModel": {"positions": None}}]}
    monkeypatch.setattr(client, "run_json", lambda args: payload)
    assert client.read_user_profiles(["staff"]) is payload


@pytest.mark.parametrize("role", ["consumer", "audit"])
def test_role_tools_call_the_native_read_client(role, monkeypatch):
    members = {"complete": True, "hasMore": False, "users": [], "bots": []}
    profiles = {"success": True, "result": [{"orgEmployeeModel": {"orgUserId": "staff", "depts": [], "labels": []}}]}
    calls = []
    monkeypatch.setattr(DwsClient, "read_group_members", lambda self, ref: calls.append(ref) or members)
    monkeypatch.setattr(DwsClient, "read_user_profiles", lambda self, ids: calls.append(ids) or profiles)
    server = build_role_server(role)
    async def read():
        _, member_result = await server.call_tool("read_dingtalk_group_members", {"conversation_id": "cid"})
        _, profile_result = await server.call_tool("read_dingtalk_user_profiles", {"user_ids": ["staff"]})
        return member_result, profile_result
    assert asyncio.run(read()) == (members, profiles)
    assert calls == ["cid", ["staff"]]

