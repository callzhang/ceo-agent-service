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


def test_memory_write_posts_json_rpc_and_parses_json_response(monkeypatch):
    requests = []

    class FakeResponse:
        status = 200
        headers = {"Content-Type": "application/json"}

        def read(self):
            return json.dumps({"result": {"episode_uuid": "ep-json"}}).encode()

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    def fake_urlopen(request, timeout):
        requests.append((request, timeout))
        return FakeResponse()

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
    request_obj, timeout = requests[0]
    assert timeout == 30
    assert request_obj.full_url == "http://memory.local/mcp"
    assert request_obj.headers["Authorization"] == "Bearer secret"
    body = json.loads(request_obj.data.decode())
    assert body["method"] == "tools/call"
    assert body["params"]["name"] == "memory_write"
    assert body["params"]["arguments"]["wait_for_processing"] is False
    assert body["params"]["arguments"]["data"] == '{"n":1}'


def test_memory_write_parses_text_event_stream_response(monkeypatch):
    class FakeResponse:
        status = 200
        headers = {"Content-Type": "text/event-stream"}

        def read(self):
            return (
                b'event: message\n'
                b'data: {"result":{"episode_uuid":"ep-sse"}}\n\n'
                b'data: [DONE]\n\n'
            )

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    monkeypatch.setattr(
        "ceo_agent_service.memory_connector.request.urlopen",
        lambda request, timeout: FakeResponse(),
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
