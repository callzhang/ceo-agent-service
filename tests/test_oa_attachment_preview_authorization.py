import asyncio
import json

import pytest

from app import agent_cli
from app.dws_client import DwsClient, DwsError


class _RecordingClient(DwsClient):
    def __init__(self, response):
        super().__init__(dws_bin="dws")
        self.response = response
        self.commands = []

    def run_json(self, command, **_kwargs):
        self.commands.append(command)
        return self.response


def test_preview_authorization_runs_the_comment_attachment_command_and_returns_space():
    client = _RecordingClient({"ok": True, "data": {"agentId": 7, "spaceId": 671896910}})

    space_id = client.authorize_oa_attachment_preview("proc-1", "file-1")

    assert space_id == "671896910"
    assert client.commands == [[
        "dws", "oa", "approval", "attachment", "authorize-preview",
        "--instance-id", "proc-1", "--file-ids", "file-1",
        "--with-comment-attachment", "--format", "json",
    ]]


def test_preview_authorization_without_a_space_is_a_technical_failure():
    client = _RecordingClient({"ok": True, "data": {}})

    with pytest.raises(DwsError, match="spaceId"):
        client.authorize_oa_attachment_preview("proc-1", "file-1")


@pytest.mark.parametrize("role", ["consumer", "audit"])
def test_both_roles_can_open_preview_access_for_one_oa_attachment(role, monkeypatch):
    calls = []

    class FakeDws:
        def authorize_oa_attachment_preview(self, process_instance_id, file_id):
            calls.append((process_instance_id, file_id))
            return "671896910"

    monkeypatch.setattr("app.dws_client.DwsClient", lambda: FakeDws())
    tool = agent_cli.build_role_server(role)._tool_manager.get_tool(
        "authorize_dingtalk_oa_attachment_preview"
    )

    assert tool.annotations.readOnlyHint is False
    assert tool.annotations.idempotentHint is True
    result = asyncio.run(tool.run(
        {"process_instance_id": "proc-1", "file_id": "file-1"}, convert_result=True,
    ))

    assert json.loads(result[0][0].text) == {
        "process_instance_id": "proc-1", "file_id": "file-1", "space_id": "671896910",
    }
    assert calls == [("proc-1", "file-1")]


def test_preview_authorization_rejects_blank_identity_before_provider_access(monkeypatch):
    class FakeDws:
        def authorize_oa_attachment_preview(self, *_args):
            pytest.fail("blank identity must not reach the provider")

    monkeypatch.setattr("app.dws_client.DwsClient", lambda: FakeDws())
    tool = agent_cli.build_role_server("consumer")._tool_manager.get_tool(
        "authorize_dingtalk_oa_attachment_preview"
    )

    with pytest.raises(Exception, match="oa_attachment_preview_identity_required"):
        asyncio.run(tool.run(
            {"process_instance_id": " ", "file_id": "file-1"}, convert_result=True,
        ))
