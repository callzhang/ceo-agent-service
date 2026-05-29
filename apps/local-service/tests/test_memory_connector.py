import json

import pytest

from ceo_agent_service.memory_connector import (
    MemoryConnectorClient,
    MemoryConnectorError,
    extract_memory_episode_id,
)


def test_extract_memory_episode_id_supports_common_shapes():
    assert extract_memory_episode_id({"episode_uuid": "ep-1"}) == "ep-1"
    assert extract_memory_episode_id({"episode_id": "ep-2"}) == "ep-2"
    assert extract_memory_episode_id({"source_episode_uuid": "ep-3"}) == "ep-3"
    assert (
        extract_memory_episode_id({"result": {"episode_uuids": ["ep-4", "ep-5"]}})
        == "ep-4"
    )
    assert extract_memory_episode_id({"result": {"episode_uuids": []}}) == ""
    assert extract_memory_episode_id({"result": {}}) == ""


def test_memory_connector_config_loads_installed_env_file(tmp_path, monkeypatch):
    codex_home = tmp_path / ".codex"
    codex_home.mkdir()
    (codex_home / "memory_connector.env").write_text(
        "\n".join(
            [
                "export CONNECTOR_API_KEY='file-token'",
                "export MEMORY_CONNECTOR_URL='https://memory.example/mcp/'",
                "export MEMORY_CONNECTOR_USER_ID='derek-file'",
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    monkeypatch.delenv("CONNECTOR_API_KEY", raising=False)
    monkeypatch.delenv("MEMORY_CONNECTOR_URL", raising=False)
    monkeypatch.delenv("MEMORY_CONNECTOR_USER_ID", raising=False)

    client = MemoryConnectorClient()

    assert client.url == "https://memory.example/mcp/"
    assert client.token == "file-token"
    assert client.user_id == "derek-file"


def test_memory_write_posts_json_rpc_and_parses_json_response(monkeypatch):
    requests = []

    class FakeResponse:
        def __init__(self, payload, headers=None):
            self.payload = payload
            self.headers = headers or {}

        status = 200

        def read(self):
            return json.dumps(self.payload).encode()

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    def fake_urlopen(request, timeout):
        requests.append((request, timeout))
        call_index = len(requests)
        if call_index == 1:
            return FakeResponse(
                {"result": {"protocolVersion": "2025-03-26"}},
                {"mcp-session-id": "session-1"},
            )
        if call_index == 2:
            return FakeResponse({})
        return FakeResponse({"result": {"episode_uuid": "ep-json"}})

    monkeypatch.setattr("ceo_agent_service.memory_connector.request.urlopen", fake_urlopen)

    client = MemoryConnectorClient(
        url="http://memory.local/mcp", token="secret", user_id="derek"
    )
    payload = client.memory_write(
        data='{"n":1}',
        type="json",
        created_at="2026-05-29T10:00:00",
        user_id="derek",
        source_description="ceo-agent-service:reply_sent:12",
        source_metadata={"outbox_event_id": 7},
        provenance_metadata={"source": "ceo-agent-service"},
    )

    assert payload == {"episode_uuid": "ep-json"}
    assert len(requests) == 3
    initialize_request, timeout = requests[0]
    assert timeout == 30
    assert initialize_request.full_url == "http://memory.local/mcp"
    assert initialize_request.headers["Authorization"] == "Bearer secret"
    assert initialize_request.headers["X-memory-user-id"] == "derek"
    initialize_body = json.loads(initialize_request.data.decode())
    assert initialize_body["method"] == "initialize"
    assert initialize_body["params"]["protocolVersion"] == "2025-03-26"

    initialized_request, _ = requests[1]
    initialized_body = json.loads(initialized_request.data.decode())
    assert initialized_body["method"] == "notifications/initialized"
    assert initialized_request.headers["Mcp-session-id"] == "session-1"

    call_request, _ = requests[2]
    assert call_request.headers["Mcp-session-id"] == "session-1"
    body = json.loads(call_request.data.decode())
    assert body["method"] == "tools/call"
    assert body["params"]["name"] == "memory_write"
    assert body["params"]["arguments"]["wait_for_processing"] is False
    assert body["params"]["arguments"]["data"] == '{"n":1}'


def test_memory_write_parses_text_event_stream_response(monkeypatch):
    class FakeResponse:
        def __init__(self, payload):
            self.payload = payload

        status = 200
        headers = {"Content-Type": "text/event-stream"}

        def read(self):
            return self.payload

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    monkeypatch.setattr(
        "ceo_agent_service.memory_connector.request.urlopen",
        lambda request, timeout: FakeResponse(
            (
                b'event: message\n'
                b'data: {"result":{"episode_uuid":"ep-sse"}}\n\n'
                b'data: [DONE]\n\n'
            )
            if json.loads(request.data.decode()).get("method") == "tools/call"
            else b"{}\n"
        ),
    )

    payload = MemoryConnectorClient(
        url="http://memory.local/mcp", token="secret"
    ).memory_write(
        data="{}",
        type="json",
        created_at="2026-05-29T10:00:00",
        user_id="derek",
        source_description="source",
        source_metadata={},
        provenance_metadata={},
    )

    assert payload == {"episode_uuid": "ep-sse"}


def test_memory_write_raises_on_tool_error(monkeypatch):
    class FakeResponse:
        status = 200
        headers = {"Content-Type": "application/json"}

        def read(self):
            return json.dumps({"error": {"message": "tool failed"}}).encode()

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    monkeypatch.setattr(
        "ceo_agent_service.memory_connector.request.urlopen",
        lambda request, timeout: FakeResponse(),
    )

    with pytest.raises(MemoryConnectorError, match="tool failed"):
        MemoryConnectorClient(url="http://memory.local/mcp", token="secret").memory_write(
            data="{}",
            type="json",
            created_at="2026-05-29T10:00:00",
            user_id="derek",
            source_description="source",
            source_metadata={},
            provenance_metadata={},
        )
