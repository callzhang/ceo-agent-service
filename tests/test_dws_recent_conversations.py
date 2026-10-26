import pytest

from app import agent_cli
from app.dws_client import DwsClient, DwsError

START = "2026-10-17T00:00:00Z"
END = "2026-10-17T02:00:00Z"


def complete_payload():
    return {
        "ok": True, "outcome": "success",
        "data": {
            "start": START, "end": END, "rangeSemantics": "[start,end)",
            "count": 1, "complete": True, "pagesFetched": 2,
            "pageSize": 100, "unknownTypeCount": 0,
            "conversations": [{"conversationId": "group-1", "type": "group"}],
        },
        "meta": {"pagination": {"endpoint_exhausted": True, "next_token": ""}},
    }


class RecordingClient(DwsClient):
    def __init__(self, payload):
        super().__init__(dws_bin="dws")
        self.payload = payload
        self.commands = []

    def run_json(self, command):
        self.commands.append(command)
        return self.payload


def test_complete_recent_conversations_preserves_provider_result():
    payload = complete_payload()
    client = RecordingClient(payload)
    assert client.read_complete_recent_conversations(START, END) is payload
    assert client.commands == [[
        "dws", "chat", "+recent-conversations", "--start", START,
        "--end", END, "--page-limit", "50", "--format", "json",
    ]]


@pytest.mark.parametrize("section,key,value", [
    ("data", "complete", False),
    ("pagination", "endpoint_exhausted", False),
    ("pagination", "next_token", "remaining-page"),
    ("data", "count", 3),
    ("data", "count", True),
    ("data", "conversations", None),
    ("data", "start", "2026-10-16T00:00:00Z"),
    ("data", "end", "2026-10-17T03:00:00Z"),
    ("data", "rangeSemantics", "[start,end]"),
    ("data", "pagesFetched", 0),
    ("data", "pagesFetched", True),
    ("data", "pageSize", 101),
    ("data", "unknownTypeCount", 2),
    ("root", "ok", False),
    ("root", "outcome", "partial_success"),
])
def test_incomplete_discovery_is_not_reported_as_complete(section, key, value):
    payload = complete_payload()
    target = payload if section == "root" else (
        payload["meta"]["pagination"] if section == "pagination" else payload["data"]
    )
    target[key] = value
    client = RecordingClient(payload)
    with pytest.raises(DwsError, match="incomplete or inconsistent"):
        client.read_complete_recent_conversations(START, END)
    assert len(client.commands) == 1


@pytest.mark.parametrize("payload", [None, [], {}, {"data": None}])
def test_missing_discovery_ledger_is_not_an_empty_success(payload):
    with pytest.raises(DwsError, match="structured result"):
        RecordingClient(payload).read_complete_recent_conversations(START, END)


def test_empty_complete_discovery_and_equivalent_timezone_window():
    payload = complete_payload()
    payload["data"].update(conversations=[], count=0, start="2026-10-17T08:00:00+08:00")
    assert RecordingClient(payload).read_complete_recent_conversations(START, END) is payload


@pytest.mark.parametrize("role", ["consumer", "audit"])
def test_role_tool_uses_complete_discovery_reader(monkeypatch, role):
    payload = complete_payload()
    payload["data"]["complete"] = False
    client = RecordingClient(payload)
    monkeypatch.setattr("app.dws_client.DwsClient", lambda: client)
    tool = agent_cli.build_role_server(role)._tool_manager.get_tool(
        "recent_dingtalk_conversations"
    ).fn
    with pytest.raises(DwsError, match="incomplete or inconsistent"):
        tool(START, END)
    assert len(client.commands) == 1
