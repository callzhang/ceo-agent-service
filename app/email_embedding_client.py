"""Private, bounded client for the configured email embedding endpoint."""

from __future__ import annotations

import os
import json
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

import httpx
import numpy as np


DEFAULT_EMBEDDING_MODEL_ID = "jinaai/jina-embeddings-v5-text-small"
MAX_EMBEDDING_BATCH_SIZE = 8
EMBEDDING_TIMEOUT_SECONDS = 2.0
MAX_EMBEDDING_RESPONSE_BYTES = 2 * 1024 * 1024


class EmbeddingProtocolError(RuntimeError):
    """The endpoint returned a response that cannot be safely aligned."""


class EmbeddingDeadlineExceeded(EmbeddingProtocolError):
    """The absolute remote request wall-clock deadline elapsed."""


class EmbeddingTransport(Protocol):
    def stream(self, method: str, url: str, **kwargs: object) -> Any: ...


@dataclass(frozen=True)
class EmbeddingTiming:
    queue_ms: float
    http_ms: float
    embedding_ms: float
    head_ms: float
    total_ms: float

    def to_dict(self) -> dict[str, float]:
        return {
            "queue_ms": self.queue_ms,
            "http_ms": self.http_ms,
            "embedding_ms": self.embedding_ms,
            "head_ms": self.head_ms,
            "total_ms": self.total_ms,
        }


@dataclass(frozen=True)
class EmbeddingResult:
    vectors: np.ndarray
    timing: EmbeddingTiming


class EmailEmbeddingClient:
    """Synchronous OpenAI-compatible embedding client with injectable I/O."""

    def __init__(
        self,
        *,
        url: str,
        embedding_revision: str,
        dimension: int,
        api_key: str | None = None,
        model_id: str = DEFAULT_EMBEDDING_MODEL_ID,
        transport: EmbeddingTransport | None = None,
        clock: Callable[[], float] = time.perf_counter,
    ) -> None:
        self.url = _required_text(url, "url")
        self.model_id = _required_text(model_id, "model_id")
        self.embedding_revision = _required_text(
            embedding_revision, "embedding_revision"
        )
        if (
            isinstance(dimension, bool)
            or not isinstance(dimension, int)
            or dimension < 1
        ):
            raise ValueError("dimension must be a positive integer")
        self.dimension = dimension
        self._api_key = api_key.strip() if api_key and api_key.strip() else None
        self._transport = transport or httpx.Client(trust_env=False)
        self._owns_transport = transport is None
        self._clock = clock

    def __repr__(self) -> str:
        return (
            f"EmailEmbeddingClient(url=<configured>, model_id={self.model_id!r}, "
            f"embedding_revision={self.embedding_revision!r}, "
            f"dimension={self.dimension!r}, api_key=<redacted>)"
        )

    @classmethod
    def from_environment(
        cls,
        *,
        embedding_revision: str,
        dimension: int,
        transport: EmbeddingTransport | None = None,
        clock: Callable[[], float] = time.perf_counter,
        environ: Mapping[str, str] | None = None,
    ) -> "EmailEmbeddingClient":
        values = os.environ if environ is None else environ
        return cls(
            url=_required_text(
                values.get("CEO_EMAIL_EMBEDDING_URL"), "CEO_EMAIL_EMBEDDING_URL"
            ),
            api_key=values.get("CEO_EMAIL_EMBEDDING_API_KEY"),
            embedding_revision=embedding_revision,
            dimension=dimension,
            transport=transport,
            clock=clock,
        )

    def embed(
        self, normalized_texts: Sequence[str], *, queued_at: float | None = None
    ) -> EmbeddingResult:
        texts = tuple(normalized_texts)
        if not texts or any(type(item) is not str or not item for item in texts):
            raise ValueError("normalized_texts must contain non-empty strings")
        entered = self._clock()
        lifecycle_started = entered if queued_at is None else queued_at
        if not isinstance(lifecycle_started, float) or lifecycle_started > entered:
            raise ValueError(
                "queued_at must be a monotonic timestamp not in the future"
            )
        headers = {"Authorization": f"Bearer {self._api_key}"} if self._api_key else {}
        vectors: list[np.ndarray] = []
        http_seconds = 0.0
        embedding_started = self._clock()
        for offset in range(0, len(texts), MAX_EMBEDDING_BATCH_SIZE):
            batch = texts[offset : offset + MAX_EMBEDDING_BATCH_SIZE]
            http_started = self._clock()
            deadline = http_started + EMBEDDING_TIMEOUT_SECONDS
            try:
                payload = self._stream_json_response(
                    batch=batch,
                    headers=headers,
                    deadline=deadline,
                )
            except EmbeddingDeadlineExceeded:
                raise
            except EmbeddingProtocolError:
                raise
            except Exception as exc:
                raise EmbeddingProtocolError(
                    "embedding endpoint request failed"
                ) from exc
            http_seconds += self._clock() - http_started
            vectors.extend(self._validated_batch(payload, expected_count=len(batch)))
        embedding_finished = self._clock()
        finished = self._clock()
        matrix = np.asarray(vectors, dtype=np.float32)
        matrix.setflags(write=False)
        return EmbeddingResult(
            vectors=matrix,
            timing=EmbeddingTiming(
                queue_ms=max(0.0, (entered - lifecycle_started) * 1000.0),
                http_ms=max(0.0, http_seconds * 1000.0),
                embedding_ms=max(
                    0.0, (embedding_finished - embedding_started) * 1000.0
                ),
                head_ms=0.0,
                total_ms=max(0.0, (finished - lifecycle_started) * 1000.0),
            ),
        )

    def _stream_json_response(
        self,
        *,
        batch: Sequence[str],
        headers: Mapping[str, str],
        deadline: float,
    ) -> object:
        remaining = self._remaining(deadline)
        timeout = httpx.Timeout(
            remaining,
            connect=remaining,
            read=remaining,
            write=remaining,
            pool=remaining,
        )
        try:
            with self._transport.stream(
                "POST",
                self.url,
                json={"model": self.model_id, "input": list(batch)},
                headers=headers,
                timeout=timeout,
            ) as response:
                self._check_deadline(deadline)
                response.raise_for_status()
                body = bytearray()
                restore_network_read = self._install_deadline_read(
                    response, deadline=deadline
                )
                try:
                    for chunk in response.iter_bytes():
                        self._check_deadline(deadline)
                        if len(body) + len(chunk) > MAX_EMBEDDING_RESPONSE_BYTES:
                            raise EmbeddingProtocolError(
                                "embedding response body is too large"
                            )
                        body.extend(chunk)
                        self._check_deadline(deadline)
                finally:
                    restore_network_read()
                self._check_deadline(deadline)
                payload = json.loads(body)
                self._check_deadline(deadline)
                return payload
        except httpx.TimeoutException as exc:
            raise EmbeddingDeadlineExceeded(
                "embedding request exceeded absolute wall-clock deadline"
            ) from exc

    def _remaining(self, deadline: float) -> float:
        remaining = deadline - self._clock()
        if remaining <= 0:
            raise EmbeddingDeadlineExceeded(
                "embedding request exceeded absolute wall-clock deadline"
            )
        return remaining

    def _check_deadline(self, deadline: float) -> None:
        self._remaining(deadline)

    def _install_deadline_read(
        self, response: object, *, deadline: float
    ) -> Callable[[], None]:
        """Clamp each same-thread socket read to the remaining absolute budget."""

        extensions = getattr(response, "extensions", None)
        network_stream = (
            extensions.get("network_stream")
            if isinstance(extensions, Mapping)
            else None
        )
        original_read = getattr(network_stream, "read", None)
        if not callable(original_read):
            return lambda: None

        def deadline_read(max_bytes: int, timeout: float | None = None) -> bytes:
            remaining = self._remaining(deadline)
            bounded_timeout = (
                remaining if timeout is None else min(float(timeout), remaining)
            )
            return original_read(max_bytes, timeout=bounded_timeout)

        setattr(network_stream, "read", deadline_read)

        def restore() -> None:
            setattr(network_stream, "read", original_read)

        return restore

    def close(self) -> None:
        if self._owns_transport:
            close = getattr(self._transport, "close", None)
            if callable(close):
                close()

    def _validated_batch(
        self, payload: object, *, expected_count: int
    ) -> list[np.ndarray]:
        if not isinstance(payload, Mapping):
            raise EmbeddingProtocolError("embedding response must be an object")
        data = payload.get("data")
        if not isinstance(data, list) or len(data) != expected_count:
            raise EmbeddingProtocolError("embedding response count mismatch")
        result: list[np.ndarray] = []
        for expected_index, item in enumerate(data):
            if (
                not isinstance(item, Mapping)
                or type(item.get("index")) is not int
                or item.get("index") != expected_index
            ):
                raise EmbeddingProtocolError("embedding response ordering mismatch")
            raw = item.get("embedding")
            if (
                not isinstance(raw, list)
                or len(raw) != self.dimension
                or any(isinstance(value, bool) for value in raw)
            ):
                raise EmbeddingProtocolError("embedding vector dimension mismatch")
            try:
                vector = np.asarray(raw, dtype=np.float32)
            except (TypeError, ValueError, OverflowError) as exc:
                raise EmbeddingProtocolError("embedding vector is invalid") from exc
            if vector.shape != (self.dimension,) or not np.isfinite(vector).all():
                raise EmbeddingProtocolError("embedding vector is invalid")
            result.append(vector)
        return result


def _required_text(value: object, field: str) -> str:
    if type(value) is not str or not value.strip():
        raise ValueError(f"{field} must be non-empty")
    return value.strip()
