from __future__ import annotations

import json
import os
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Event, Thread
from dataclasses import dataclass

import numpy as np
import pytest

from app.email_embedding_client import (
    DEFAULT_EMBEDDING_MODEL_ID,
    EmailEmbeddingClient,
    EmbeddingDeadlineExceeded,
    EmbeddingProtocolError,
)


class FakeClock:
    def __init__(self) -> None:
        self.value = 0.0

    def __call__(self) -> float:
        self.value += 0.001
        return self.value


@dataclass
class FakeResponse:
    payload: dict[str, object]
    status_code: int = 200

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError("http failure")

    def json(self) -> dict[str, object]:
        return self.payload

    def iter_bytes(self):
        yield json.dumps(self.payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *_args: object) -> None:
        return None


class RecordingTransport:
    def __init__(self, *, reverse: bool = False, dimension: int = 3) -> None:
        self.calls: list[dict[str, object]] = []
        self.reverse = reverse
        self.dimension = dimension

    def stream(self, method: str, url: str, **kwargs: object) -> FakeResponse:
        self.calls.append({"method": method, "url": url, **kwargs})
        inputs = kwargs["json"]["input"]  # type: ignore[index]
        data = [
            {"index": index, "embedding": [float(index + 1)] * self.dimension}
            for index in range(len(inputs))
        ]
        if self.reverse:
            data.reverse()
        return FakeResponse({"data": data})


def test_client_batches_eight_uses_timeout_and_never_serializes_secret_elsewhere() -> (
    None
):
    transport = RecordingTransport()
    client = EmailEmbeddingClient(
        url="https://gpu4.example/v1/embeddings",
        api_key="private-secret",
        embedding_revision="gpu4-r17",
        dimension=3,
        transport=transport,
        clock=FakeClock(),
    )

    result = client.embed([f"mail-{index}" for index in range(17)], queued_at=0.0)

    assert result.vectors.shape == (17, 3)
    assert result.vectors.dtype == np.float32
    assert [len(call["json"]["input"]) for call in transport.calls] == [8, 8, 1]  # type: ignore[index]
    assert all(call["method"] == "POST" for call in transport.calls)
    assert all(
        0 < call["timeout"].connect <= 2.0  # type: ignore[union-attr]
        and 0 < call["timeout"].read <= 2.0  # type: ignore[union-attr]
        and 0 < call["timeout"].write <= 2.0  # type: ignore[union-attr]
        and 0 < call["timeout"].pool <= 2.0  # type: ignore[union-attr]
        for call in transport.calls
    )
    assert all(
        call["json"]["model"] == DEFAULT_EMBEDDING_MODEL_ID for call in transport.calls
    )  # type: ignore[index]
    assert all(
        call["headers"] == {"Authorization": "Bearer private-secret"}
        for call in transport.calls
    )
    assert result.timing.queue_ms == pytest.approx(1.0)
    assert result.timing.http_ms >= 0
    assert result.timing.embedding_ms >= result.timing.http_ms
    assert result.timing.total_ms >= result.timing.embedding_ms
    assert "private-secret" not in repr(client)


def test_client_reads_optional_secret_from_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CEO_EMAIL_EMBEDDING_URL", "https://gpu4.example/embed")
    monkeypatch.setenv("CEO_EMAIL_EMBEDDING_API_KEY", "environment-secret")
    transport = RecordingTransport()

    client = EmailEmbeddingClient.from_environment(
        embedding_revision="rev-1", dimension=3, transport=transport
    )
    client.embed(["hello"])

    assert transport.calls[0]["headers"] == {
        "Authorization": "Bearer environment-secret"
    }


@pytest.mark.parametrize(
    "payload",
    [
        {"data": [{"index": 1, "embedding": [1.0, 2.0, 3.0]}]},
        {"data": [{"index": 0.0, "embedding": [1.0, 2.0, 3.0]}]},
        {"data": [{"index": 0, "embedding": [1.0, 2.0]}]},
        {"data": [{"index": 0, "embedding": [1.0, float("nan"), 3.0]}]},
    ],
)
def test_client_rejects_order_dimension_and_non_finite_vectors(
    payload: dict[str, object],
) -> None:
    class InvalidTransport:
        def stream(self, *_args: object, **_kwargs: object) -> FakeResponse:
            return FakeResponse(payload)

    client = EmailEmbeddingClient(
        url="https://gpu4.example/embed",
        embedding_revision="rev",
        dimension=3,
        transport=InvalidTransport(),
    )

    with pytest.raises(EmbeddingProtocolError):
        client.embed(["mail"])


def test_real_httpx_client_enforces_two_second_transport_timeout() -> None:
    class BlockingHandler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802 - stdlib callback name
            time.sleep(3.0)

        def log_message(self, *_args: object) -> None:
            return None

    server = ThreadingHTTPServer(("127.0.0.1", 0), BlockingHandler)
    server_thread = Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    client = EmailEmbeddingClient(
        url=f"http://127.0.0.1:{server.server_port}/embeddings",
        embedding_revision="timeout-test",
        dimension=3,
    )
    started = time.perf_counter()
    try:
        with pytest.raises(EmbeddingProtocolError):
            client.embed(["mail"])
    finally:
        elapsed = time.perf_counter() - started
        client.close()
        server.shutdown()
        server.server_close()
        server_thread.join(timeout=1.0)

    assert 1.5 <= elapsed <= 2.6


def test_real_httpx_client_stops_slow_drip_at_absolute_deadline() -> None:
    connection_closed = Event()

    class SlowDripHandler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802 - stdlib callback name
            body = b'{"data":[{"index":0,"embedding":[1,2,3]}]}'
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            try:
                for byte in body:
                    self.wfile.write(bytes((byte,)))
                    self.wfile.flush()
                    time.sleep(0.8)
            except (BrokenPipeError, ConnectionResetError):
                connection_closed.set()

        def log_message(self, *_args: object) -> None:
            return None

    server = ThreadingHTTPServer(("127.0.0.1", 0), SlowDripHandler)
    server_thread = Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    client = EmailEmbeddingClient(
        url=f"http://127.0.0.1:{server.server_port}/embeddings",
        embedding_revision="slow-drip-test",
        dimension=3,
    )
    started = time.perf_counter()
    try:
        with pytest.raises(EmbeddingDeadlineExceeded):
            client.embed(["mail"])
        elapsed = time.perf_counter() - started
        assert elapsed <= 2.6
        assert connection_closed.wait(2.0)
    finally:
        client.close()
        server.shutdown()
        server.server_close()
        server_thread.join(timeout=1.0)


def test_client_rejects_response_body_over_limit() -> None:
    class OversizedResponse(FakeResponse):
        def iter_bytes(self):
            yield b"x" * (2 * 1024 * 1024 + 1)

    class OversizedTransport:
        def stream(self, *_args: object, **_kwargs: object) -> FakeResponse:
            return OversizedResponse({})

    client = EmailEmbeddingClient(
        url="https://gpu4.example/embed",
        embedding_revision="oversized",
        dimension=3,
        transport=OversizedTransport(),
    )

    with pytest.raises(EmbeddingProtocolError, match="response body is too large"):
        client.embed(["mail"])


@pytest.mark.live
def test_live_gpu4_embedding_endpoint_is_opt_in(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    if os.getenv("CEO_LIVE_EMAIL_EMBEDDING_E2E") != "1":
        pytest.skip("set CEO_LIVE_EMAIL_EMBEDDING_E2E=1 for GPU4")
    client = EmailEmbeddingClient.from_environment(
        embedding_revision=os.getenv("CEO_EMAIL_EMBEDDING_REVISION") or "live",
        dimension=int(os.getenv("CEO_EMAIL_EMBEDDING_DIMENSION") or "1024"),
    )
    client.embed(["邮件分类端点预热测试"] * 8)
    results = [client.embed(["邮件分类端点延迟测试"] * 8) for _ in range(10)]
    assert all(result.vectors.shape[0] == 8 for result in results)
    assert float(np.percentile([item.timing.total_ms for item in results], 95)) < 500.0
    json.dumps([item.timing.to_dict() for item in results])
