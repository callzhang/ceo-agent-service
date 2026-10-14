"""Role readers preserve native mail sources without enabling Audit commands."""
import pytest

from app.agent_cli import build_role_server
from app.wechat.codex_safety import AGENT_CLI_READ_TOOLS


@pytest.mark.parametrize("role", ["consumer", "audit"])
@pytest.mark.parametrize("name,arguments,leaf", [
    ("list_dingtalk_mailboxes", {}, ["mail", "mailbox", "list"]),
    ("read_dingtalk_mail_thread", {"email": "principal@example.test", "conversation_id": "thread/原文"},
     ["mail", "+thread", "--email", "principal@example.test", "--id", "thread/原文"]),
    ("read_dingtalk_mail_message", {"email": "principal@example.test", "message_id": "message/正文"},
     ["mail", "+message", "--email", "principal@example.test", "--id", "message/正文"]),
    ("read_dingtalk_mail_sent_status", {"email": "principal@example.test", "internet_message_id": "<receipt@example.test>"},
     ["mail", "message", "verify", "--email", "principal@example.test", "--internet-message-id", "<receipt@example.test>"]),
])
def test_role_mail_reader_preserves_native_result_and_exact_identity(monkeypatch, role, name, arguments, leaf):
    # Stub only the external provider boundary, never the role's registered tool.
    received = []
    source = {"success": True, "data": {"value": {"body": "原文\n<quoted>", "attachments": [{"id": "a"}]}},
              "complete": False, "hasMore": True}

    class NativeClient:
        dws_bin = "/native/dws"

        def run_json(self, command):
            received.append(command)
            return source

    monkeypatch.setattr("app.dws_client.DwsClient", NativeClient)
    server = build_role_server(role)
    assert name in {t.name for t in server._tool_manager.list_tools()}
    tool = server._tool_manager.get_tool(name)
    assert tool.fn(**arguments) is source
    assert received == [["/native/dws", *leaf, "--format", "json"]]
    assert tool.annotations.readOnlyHint is True
    assert name in AGENT_CLI_READ_TOOLS


def test_mail_source_failure_propagates_without_replacement(monkeypatch):
    class NativeClient:
        dws_bin = "/native/dws"

        def run_json(self, command):
            raise RuntimeError("provider unavailable")

    monkeypatch.setattr("app.dws_client.DwsClient", NativeClient)
    tool = build_role_server("audit")._tool_manager.get_tool("read_dingtalk_mail_thread")
    assert tool is not None
    with pytest.raises(RuntimeError, match="provider unavailable"):
        tool.fn(email="principal@example.test", conversation_id="thread-1")
