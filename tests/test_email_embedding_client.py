from __future__ import annotations

import json
import os
from dataclasses import dataclass

import numpy as np
import pytest

from app.email_embedding_client import (
    DEFAULT_EMBEDDING_MODEL_ID,
    EmailEmbeddingClient,
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


class RecordingTransport:
    def __init__(self, *, reverse: bool = False, dimension: int = 3) -> None:
        self.calls: list[dict[str, object]] = []
        self.reverse = reverse
        self.dimension = dimension

    def post(self, url: str, **kwargs: object) -> FakeResponse:
        self.calls.append({"url": url, **kwargs})
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
    assert all(call["timeout"] == 2.0 for call in transport.calls)
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
        def post(self, *_args: object, **_kwargs: object) -> FakeResponse:
            return FakeResponse(payload)

    client = EmailEmbeddingClient(
        url="https://gpu4.example/embed",
        embedding_revision="rev",
        dimension=3,
        transport=InvalidTransport(),
    )

    with pytest.raises(EmbeddingProtocolError):
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
