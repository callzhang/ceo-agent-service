"""Runtime loading boundary for the local email classifier."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from pickle import UnpicklingError
from types import MappingProxyType
import threading
from collections import deque
from collections.abc import Callable
from dataclasses import field
from enum import StrEnum
import math
import json
import os
import tempfile
import time
from hashlib import sha256
from collections.abc import Mapping

import numpy as np

from app.email_classifier_contracts import validate_email_category_key
from app.email_classifier_model import CpuTfidfLogisticClassifier
from app.email_classifier_scan import EmailScanConfig, EmailScanResult, scan_readonly_batch
from app.email_store import EmailStore
from app.email_model_registry import EmailModelRegistry, ModelRegistryError
from app.email_model_registry import assess_staged_candidate_readiness
from app.email_embedding_cache import EmbeddingCache, EmbeddingCacheKey
from app.email_embedding_classifier import DescriptionAwareEmailClassifier
from app.email_embedding_client import EmailEmbeddingClient, EmbeddingResult, EmbeddingTiming
from app.jieba_loader import jieba_lcut


class EmailClassifierUnavailable(RuntimeError):
    """No valid active or previous model is available for classification."""


class EmailClassifierRuntimeMode(StrEnum):
    AGENT_PRIMARY = "agent_primary"
    SHADOW_HISTORY = "shadow_history"
    MODEL_PRIMARY = "model_primary"


MAX_ONLINE_MICROBATCH_WAIT_SECONDS = 0.050
MAX_ONLINE_BATCH_SIZE = 8
ONLINE_EMBEDDING_TIMEOUT_SECONDS = 2.0
ONLINE_ACTIVATION_FILENAME = "online-active.json"


@dataclass(frozen=True)
class OnlineModelActivation:
    model_id: str
    artifact_sha256: str
    compatibility: Mapping[str, object]


@dataclass(frozen=True)
class OnlineModelInput:
    normalized_text: str
    input_schema_version: str

    def __post_init__(self) -> None:
        if type(self.normalized_text) is not str or not self.normalized_text:
            raise ValueError("online model input is incomplete")
        if (
            type(self.input_schema_version) is not str
            or not self.input_schema_version.strip()
        ):
            raise ValueError("online model input is incomplete")


@dataclass(frozen=True)
class OnlineBatchCompatibility:
    runtime_id: str
    model_id: str
    input_schema_version: str
    embedding_model_id: str
    embedding_revision: str

    def __post_init__(self) -> None:
        for field_name in (
            "runtime_id",
            "model_id",
            "input_schema_version",
            "embedding_model_id",
            "embedding_revision",
        ):
            value = getattr(self, field_name)
            if type(value) is not str or not value.strip():
                raise ValueError("online batch compatibility is incomplete")


class OnlineEmbeddingBatchError(RuntimeError):
    """A per-request remote batch failure with complete stage timing."""

    def __init__(self, reason: str, timing: EmbeddingTiming):
        super().__init__(reason)
        self.reason = reason
        self.timing = timing


class OnlineEmbeddingBatchTimeout(OnlineEmbeddingBatchError):
    """The hard remote embedding deadline elapsed."""


class OnlineEmbeddingBatchClosed(OnlineEmbeddingBatchError):
    """The runtime batcher closed before this request completed."""


@dataclass
class _OnlineEmbeddingRequest:
    normalized_text: str
    compatibility: OnlineBatchCompatibility
    queued_at: float
    event: threading.Event = field(default_factory=threading.Event)
    result: EmbeddingResult | None = None
    error: BaseException | None = None
    dispatched: bool = False


class OnlineEmbeddingMicrobatcher:
    """Thread-safe, bounded aggregator for compatible online cache misses."""

    def __init__(
        self,
        embedding_client: object,
        *,
        maximum_wait_seconds: float = MAX_ONLINE_MICROBATCH_WAIT_SECONDS,
        maximum_batch_size: int = MAX_ONLINE_BATCH_SIZE,
        remote_timeout_seconds: float = ONLINE_EMBEDDING_TIMEOUT_SECONDS,
        clock: Callable[[], float] = time.perf_counter,
    ) -> None:
        if not 0 < maximum_wait_seconds <= MAX_ONLINE_MICROBATCH_WAIT_SECONDS:
            raise ValueError("online microbatch wait must be in (0, 50ms]")
        if not 1 <= maximum_batch_size <= MAX_ONLINE_BATCH_SIZE:
            raise ValueError("online microbatch size must be between 1 and 8")
        if remote_timeout_seconds <= 0:
            raise ValueError("remote embedding timeout must be positive")
        self.embedding_client = embedding_client
        self.maximum_wait_seconds = float(maximum_wait_seconds)
        self.maximum_batch_size = int(maximum_batch_size)
        self.remote_timeout_seconds = float(remote_timeout_seconds)
        self._clock = clock
        self._condition = threading.Condition()
        self._pending: deque[_OnlineEmbeddingRequest] = deque()
        self._active: list[_OnlineEmbeddingRequest] = []
        self._closed = False
        self._retiring = False
        self._thread = threading.Thread(
            target=self._run,
            name="email-online-embedding-batcher",
            daemon=True,
        )
        self._thread.start()

    @property
    def pending_count(self) -> int:
        with self._condition:
            return sum(not item.event.is_set() for item in (*self._pending, *self._active))

    @property
    def is_alive(self) -> bool:
        return self._thread.is_alive()

    def embed_one(
        self, normalized_text: str, compatibility: OnlineBatchCompatibility
    ) -> EmbeddingResult:
        if type(normalized_text) is not str or not normalized_text:
            raise ValueError("online batch input is incomplete")
        if type(compatibility) is not OnlineBatchCompatibility:
            raise TypeError("online batch compatibility is invalid")
        request = _OnlineEmbeddingRequest(
            normalized_text=normalized_text,
            compatibility=compatibility,
            queued_at=float(self._clock()),
        )
        with self._condition:
            if self._closed or self._retiring:
                raise self._closed_error(request)
            self._pending.append(request)
            self._condition.notify_all()
        pending_deadline = (
            request.queued_at
            + self.maximum_wait_seconds
            + self.remote_timeout_seconds
        )
        remaining = max(0.0, pending_deadline - float(self._clock()))
        if not request.event.wait(remaining):
            with self._condition:
                if not request.dispatched and not request.event.is_set():
                    try:
                        self._pending.remove(request)
                    except ValueError:
                        pass
                    self._complete_locked(
                        request,
                        error=OnlineEmbeddingBatchTimeout(
                            "online embedding request expired before dispatch",
                            self._failure_timing(request, http_started=None),
                        ),
                    )
                self._condition.notify_all()
            if request.dispatched and not request.event.is_set():
                request.event.wait()
        if request.error is not None:
            raise request.error
        if request.result is None:
            raise RuntimeError("online embedding request completed without a result")
        return request.result

    def close(self) -> None:
        with self._condition:
            if self._closed:
                return
            self._closed = True
            outstanding = tuple(self._pending)
            self._pending.clear()
            for request in outstanding:
                self._complete_locked(request, error=self._closed_error(request))
            self._condition.notify_all()
        self._thread.join()

    def retire(self) -> None:
        """Reject new work while allowing already accepted requests to drain."""

        with self._condition:
            if self._closed:
                return
            self._retiring = True
            self._condition.notify_all()
        self._thread.join()

    def _run(self) -> None:
        while True:
            with self._condition:
                while not self._pending and not self._closed and not self._retiring:
                    self._condition.wait()
                if self._closed or (self._retiring and not self._pending):
                    self._closed = True
                    return
                first = self._pending.popleft()
                batch = [first]
                deadline = first.queued_at + self.maximum_wait_seconds
                while len(batch) < self.maximum_batch_size:
                    matching = next(
                        (
                            item
                            for item in self._pending
                            if item.compatibility == first.compatibility
                        ),
                        None,
                    )
                    if matching is not None:
                        self._pending.remove(matching)
                        batch.append(matching)
                        continue
                    remaining = deadline - float(self._clock())
                    if remaining <= 0 or self._closed:
                        break
                    self._condition.wait(remaining)
                self._active = batch
                for request in batch:
                    request.dispatched = True
            self._dispatch(batch)
            with self._condition:
                self._active = []
                if self._retiring and not self._pending:
                    self._closed = True
                    self._condition.notify_all()
                    return
                self._condition.notify_all()

    def _dispatch(self, batch: list[_OnlineEmbeddingRequest]) -> None:
        dispatched_at = float(self._clock())
        try:
            result = self.embedding_client.embed(
                tuple(item.normalized_text for item in batch),
                queued_at=batch[0].queued_at,
            )
        except BaseException as exc:  # preserve remote failure per waiter
            for request in batch:
                self._complete(
                    request,
                    error=OnlineEmbeddingBatchError(
                        f"online embedding request failed: {type(exc).__name__}",
                        self._failure_timing(request, http_started=dispatched_at),
                    ),
                )
            return
        if type(result) is not EmbeddingResult or len(result.vectors) != len(batch):
            for request in batch:
                self._complete(
                    request,
                    error=OnlineEmbeddingBatchError(
                        "online embedding response cannot align with requests",
                        self._failure_timing(request, http_started=dispatched_at),
                    ),
                )
            return
        finished_at = float(self._clock())
        for index, request in enumerate(batch):
            vector = np.asarray((result.vectors[index],), dtype=np.float32)
            vector.setflags(write=False)
            timing = EmbeddingTiming(
                queue_ms=max(0.0, (dispatched_at - request.queued_at) * 1000.0),
                http_ms=float(result.timing.http_ms),
                embedding_ms=float(result.timing.embedding_ms),
                head_ms=0.0,
                total_ms=max(0.0, (finished_at - request.queued_at) * 1000.0),
            )
            self._complete(request, result=EmbeddingResult(vector, timing))

    def _failure_timing(
        self, request: _OnlineEmbeddingRequest, *, http_started: float | None
    ) -> EmbeddingTiming:
        finished_at = float(self._clock())
        dispatched_at = finished_at if http_started is None else http_started
        elapsed_http_ms = max(0.0, (finished_at - dispatched_at) * 1000.0)
        return EmbeddingTiming(
            queue_ms=max(0.0, (dispatched_at - request.queued_at) * 1000.0),
            http_ms=elapsed_http_ms,
            embedding_ms=elapsed_http_ms,
            head_ms=0.0,
            total_ms=max(0.0, (finished_at - request.queued_at) * 1000.0),
        )

    def _closed_error(
        self, request: _OnlineEmbeddingRequest
    ) -> OnlineEmbeddingBatchClosed:
        return OnlineEmbeddingBatchClosed(
            "online embedding batcher is closed",
            self._failure_timing(request, http_started=None),
        )

    def _complete(
        self,
        request: _OnlineEmbeddingRequest,
        *,
        result: EmbeddingResult | None = None,
        error: BaseException | None = None,
    ) -> None:
        with self._condition:
            self._complete_locked(request, result=result, error=error)

    @staticmethod
    def _complete_locked(
        request: _OnlineEmbeddingRequest,
        *,
        result: EmbeddingResult | None = None,
        error: BaseException | None = None,
    ) -> None:
        if request.event.is_set():
            return
        request.result = result
        request.error = error
        request.event.set()


def activate_online_model(
    registry: object,
    model_id: str,
    *,
    classifier_loader: Callable[[Path], object] = DescriptionAwareEmailClassifier.load,
) -> OnlineModelActivation:
    """Atomically activate only the latest candidate in a verified two-pass pair."""

    evidence_rows = tuple(registry.list_staged_evidence())
    readiness = assess_staged_candidate_readiness(evidence_rows)
    if not readiness.ready or not readiness.passing_model_ids:
        raise EmailClassifierUnavailable(f"whole model is not ready: {readiness.reason}")
    if model_id != readiness.passing_model_ids[-1]:
        raise ValueError("only the latest whole-model-ready candidate may activate")
    evidence = registry.get_staged_evidence(model_id)
    hashes = evidence.get("hashes")
    compatibility = evidence.get("compatibility")
    if not isinstance(hashes, Mapping) or not isinstance(compatibility, Mapping):
        raise ValueError("online activation evidence is incomplete")
    expected_digest = str(hashes.get("artifact_sha256") or "")
    artifact = Path(registry.embedding_artifacts) / f"{model_id}.artifact"
    if not artifact.is_file() or sha256(artifact.read_bytes()).hexdigest() != expected_digest:
        raise ValueError("online activation artifact is not verified")
    classifier = classifier_loader(artifact)
    _verify_online_classifier_compatibility(classifier, compatibility)
    activation = OnlineModelActivation(
        model_id=model_id,
        artifact_sha256=expected_digest,
        compatibility=dict(compatibility),
    )
    payload = {
        "model_id": activation.model_id,
        "artifact_sha256": activation.artifact_sha256,
        "compatibility": dict(activation.compatibility),
    }
    root = Path(registry.root)
    root.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=root,
            prefix=".online-active.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary = Path(handle.name)
            json.dump(payload, handle, sort_keys=True, separators=(",", ":"))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, root / ONLINE_ACTIVATION_FILENAME)
        temporary = None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return activation


def derive_runtime_mode(
    registry: object, *, manual_historical: bool = False
) -> EmailClassifierRuntimeMode:
    """Derive runtime state from current verified registry files, failing closed."""

    if manual_historical:
        return EmailClassifierRuntimeMode.SHADOW_HISTORY
    try:
        payload = json.loads(
            (Path(registry.root) / ONLINE_ACTIVATION_FILENAME).read_text(
                encoding="utf-8"
            )
        )
        if not isinstance(payload, Mapping):
            raise ValueError("activation must be an object")
        model_id = str(payload["model_id"])
        artifact_digest = str(payload["artifact_sha256"])
        evidence_rows = tuple(registry.list_staged_evidence())
        readiness = assess_staged_candidate_readiness(evidence_rows)
        evidence = registry.get_staged_evidence(model_id)
        if (
            not readiness.ready
            or not readiness.passing_model_ids
            or readiness.passing_model_ids[-1] != model_id
            or evidence.get("compatibility") != payload.get("compatibility")
            or not isinstance(evidence.get("hashes"), Mapping)
            or evidence["hashes"].get("artifact_sha256") != artifact_digest
        ):
            raise ValueError("activation evidence no longer verifies")
        artifact = Path(registry.embedding_artifacts) / f"{model_id}.artifact"
        if sha256(artifact.read_bytes()).hexdigest() != artifact_digest:
            raise ValueError("activation artifact changed")
    except (KeyError, OSError, TypeError, ValueError, json.JSONDecodeError):
        return EmailClassifierRuntimeMode.AGENT_PRIMARY
    return EmailClassifierRuntimeMode.MODEL_PRIMARY


def _verify_online_classifier_compatibility(
    classifier: object, compatibility: Mapping[str, object]
) -> None:
    expected = {
        "enabled_categories": tuple(compatibility.get("enabled_categories", ())),
        "input_schema_version": compatibility.get("input_schema_version"),
        "embedding_model_id": compatibility.get("embedding_model_id"),
        "embedding_revision": compatibility.get("embedding_revision"),
    }
    observed = {
        "enabled_categories": tuple(getattr(classifier, "enabled_categories", ())),
        "input_schema_version": getattr(classifier, "input_schema_version", None),
        "embedding_model_id": getattr(classifier, "embedding_model_id", None),
        "embedding_revision": getattr(classifier, "embedding_revision", None),
    }
    if observed != expected:
        raise ValueError("online model compatibility mismatch")


class OnlineEmbeddingPredictor:
    """Run one exact-input cached embedding model prediction."""

    def __init__(
        self,
        *,
        model_id: str,
        classifier: object,
        cache: object,
        embedding_client: object,
        embedding_batcher: OnlineEmbeddingMicrobatcher | None = None,
        runtime_id: str | None = None,
        latency: "StageLatencyRecorder | None" = None,
        clock: Callable[[], float] = time.perf_counter,
    ) -> None:
        if not model_id.strip():
            raise ValueError("model_id must be nonblank")
        if isinstance(classifier, CpuTfidfLogisticClassifier):
            raise TypeError("TF-IDF cannot be used as an online model")
        if (
            getattr(classifier, "embedding_model_id", None)
            != getattr(embedding_client, "model_id", None)
            or getattr(classifier, "embedding_revision", None)
            != getattr(embedding_client, "embedding_revision", None)
        ):
            raise ValueError("online embedding version mismatch")
        self.model_id = model_id
        self.classifier = classifier
        self.cache = cache
        self.embedding_client = embedding_client
        self._owns_batcher = embedding_batcher is None
        self.embedding_batcher = embedding_batcher or OnlineEmbeddingMicrobatcher(
            embedding_client
        )
        self.runtime_id = runtime_id or f"online-runtime:{id(self)}"
        self.compatibility = OnlineBatchCompatibility(
            runtime_id=self.runtime_id,
            model_id=model_id,
            input_schema_version=str(classifier.input_schema_version),
            embedding_model_id=str(classifier.embedding_model_id),
            embedding_revision=str(classifier.embedding_revision),
        )
        self.latency = latency or StageLatencyRecorder()
        self._clock = clock
        self._warm_lock = threading.Lock()
        self._has_completed_request = False

    def __call__(self, value: OnlineModelInput) -> OnlineClassificationResult:
        started_at = float(self._clock())
        with self._warm_lock:
            runtime_warm = self._has_completed_request
        embedded: EmbeddingResult | None = None
        head_started_at: float | None = None
        cache_hit = False
        try:
            if type(value) is not OnlineModelInput:
                raise TypeError("online model input is invalid")
            if value.input_schema_version != self.classifier.input_schema_version:
                raise ValueError("online model input version mismatch")
            key = EmbeddingCacheKey.for_text(
                normalized_text=value.normalized_text,
                input_schema_version=value.input_schema_version,
                embedding_model_id=self.classifier.embedding_model_id,
                embedding_revision=self.classifier.embedding_revision,
            )
            vector = self.cache.get(key)
            if vector is None:
                embedded = self.embedding_batcher.embed_one(
                    value.normalized_text, self.compatibility
                )
                vector = embedded.vectors[0]
                self.cache.put(key, vector)
            else:
                cache_hit = True
                matrix = np.asarray((vector,), dtype=np.float32)
                matrix.setflags(write=False)
                embedded = EmbeddingResult(
                    vectors=matrix,
                    timing=EmbeddingTiming(0.0, 0.0, 0.0, 0.0, 0.0),
                )
            head_started_at = float(self._clock())
            timed = self.classifier.predict_result(embedded, index=0)
            timing = timed.timing
            finished_at = float(self._clock())
            self.latency.record(
                queue_ms=float(timing.queue_ms),
                http_ms=float(timing.http_ms),
                embedding_ms=float(timing.embedding_ms),
                head_ms=float(timing.head_ms),
                total_ms=max(0.0, (finished_at - started_at) * 1000.0),
                outcome=(
                    "success"
                    if timed.prediction.category_accepted
                    else "rejected"
                ),
                fallback_reason=(
                    "" if timed.prediction.category_accepted else "model_rejected"
                ),
                cache_hit=cache_hit,
                runtime_warm=runtime_warm,
            )
            if not timed.prediction.category_accepted:
                self.latency.record_fallback("model_rejected")
                return OnlineClassificationResult(
                    source="model", value=None, fallback_reason="model_rejected"
                )
            return OnlineClassificationResult(source="model", value=timed.prediction)
        except Exception as exc:
            if isinstance(exc, OnlineEmbeddingBatchError):
                failure_timing = exc.timing
            else:
                now = float(self._clock())
                prior = (
                    embedded.timing
                    if embedded is not None
                    else EmbeddingTiming(0.0, 0.0, 0.0, 0.0, 0.0)
                )
                head_ms = (
                    0.0
                    if head_started_at is None
                    else max(0.0, (now - head_started_at) * 1000.0)
                )
                failure_timing = EmbeddingTiming(
                    queue_ms=float(prior.queue_ms),
                    http_ms=float(prior.http_ms),
                    embedding_ms=float(prior.embedding_ms),
                    head_ms=head_ms,
                    total_ms=max(
                        float(prior.total_ms) + head_ms,
                        max(0.0, (now - started_at) * 1000.0),
                    ),
                )
            self.latency.record(
                queue_ms=float(failure_timing.queue_ms),
                http_ms=float(failure_timing.http_ms),
                embedding_ms=float(failure_timing.embedding_ms),
                head_ms=float(failure_timing.head_ms),
                total_ms=max(0.0, (float(self._clock()) - started_at) * 1000.0),
                outcome="failure",
                fallback_reason=type(exc).__name__,
                cache_hit=cache_hit,
                runtime_warm=runtime_warm,
            )
            self.latency.record_fallback(type(exc).__name__)
            raise
        finally:
            with self._warm_lock:
                self._has_completed_request = True

    def close(self) -> None:
        if self._owns_batcher:
            self.embedding_batcher.close()


@dataclass(frozen=True)
class RuntimeSnapshot:
    """One immutable routing decision for exactly one scanned message."""

    mode: EmailClassifierRuntimeMode
    predictor: object | None
    model_id: str | None
    input_schema_version: str | None
    compatibility: Mapping[str, object]

    @classmethod
    def agent_primary(cls):
        return cls(
            mode=EmailClassifierRuntimeMode.AGENT_PRIMARY,
            predictor=None,
            model_id=None,
            input_schema_version=None,
            compatibility=MappingProxyType({}),
        )

    @classmethod
    def model_primary(
        cls,
        *,
        predictor: object,
        model_id: str,
        input_schema_version: str,
        compatibility: Mapping[str, object],
    ):
        if not callable(predictor):
            raise TypeError("runtime snapshot predictor must be callable")
        if not model_id.strip() or not input_schema_version.strip():
            raise ValueError("runtime snapshot model identity is incomplete")
        return cls(
            mode=EmailClassifierRuntimeMode.MODEL_PRIMARY,
            predictor=predictor,
            model_id=model_id,
            input_schema_version=input_schema_version,
            compatibility=_freeze_runtime_mapping(compatibility),
        )


def _freeze_runtime_mapping(value: Mapping[str, object]) -> Mapping[str, object]:
    def freeze(item: object) -> object:
        if isinstance(item, Mapping):
            return MappingProxyType({str(key): freeze(child) for key, child in item.items()})
        if isinstance(item, list | tuple):
            return tuple(freeze(child) for child in item)
        return item

    return MappingProxyType({str(key): freeze(item) for key, item in value.items()})


@dataclass(frozen=True)
class _RuntimeGeneration:
    """Immutable resource ownership for one atomic runtime snapshot."""

    snapshot: RuntimeSnapshot
    predictor: object | None
    batcher: OnlineEmbeddingMicrobatcher
    client: object
    owns_client: bool
    _retire_lock: threading.Lock = field(
        default_factory=threading.Lock, init=False, repr=False, compare=False
    )
    _retired: threading.Event = field(
        default_factory=threading.Event, init=False, repr=False, compare=False
    )

    def retire(self) -> None:
        with self._retire_lock:
            if self._retired.is_set():
                return
            try:
                self.batcher.retire()
            finally:
                try:
                    if self.owns_client:
                        _close_embedding_client(self.client)
                finally:
                    self._retired.set()


class PromotedEmailClassifierRuntime:
    """Own the verified embedding-model mode used by the new-mail scan path."""

    def __init__(
        self,
        registry: object,
        *,
        learning_service: object | None = None,
        classifier_loader: Callable[[Path], object] = DescriptionAwareEmailClassifier.load,
        embedding_client_factory: Callable[[object], object] | None = None,
        embedding_client_owned: bool | None = None,
        cache_factory: Callable[[object], object] | None = None,
        latency: StageLatencyRecorder | None = None,
        observability_store: EmailStore | None = None,
    ) -> None:
        self.registry = registry
        self.learning_service = learning_service
        self._classifier_loader = classifier_loader
        self._embedding_client_factory = embedding_client_factory or _production_embedding_client
        self._embedding_client_owned = (
            embedding_client_factory is None
            if embedding_client_owned is None
            else bool(embedding_client_owned)
        )
        self._cache_factory = cache_factory or (
            lambda classifier: EmbeddingCache(
                registry.root, dimension=int(classifier.dimension)
            )
        )
        self.latency = latency or StageLatencyRecorder()
        self._observability_store = observability_store
        self._lock = threading.RLock()
        self._snapshot = RuntimeSnapshot.agent_primary()
        self._generation: _RuntimeGeneration | None = None
        self._closed = False
        self._refresh_epoch = 0
        self.refresh()

    def snapshot(self) -> RuntimeSnapshot:
        with self._lock:
            return self._snapshot

    @property
    def mode(self) -> EmailClassifierRuntimeMode:
        return self.snapshot().mode

    @property
    def model_id(self) -> str | None:
        return self.snapshot().model_id

    @property
    def model_predict(self):
        return self.snapshot().predictor

    @property
    def input_schema_version(self) -> str | None:
        return self.snapshot().input_schema_version

    @property
    def embedding_batcher(self) -> OnlineEmbeddingMicrobatcher | None:
        with self._lock:
            return None if self._generation is None else self._generation.batcher

    def tick(self, *, now: datetime | None = None):
        with self._lock:
            if self._closed:
                return None
        result = None
        if self.learning_service is not None:
            poll = getattr(self.learning_service, "poll_retrain")
            result = poll(now=now)
        self.refresh()
        return result

    def refresh(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._refresh_epoch += 1
            refresh_token = self._refresh_epoch
        mode = derive_runtime_mode(self.registry)
        if mode is not EmailClassifierRuntimeMode.MODEL_PRIMARY:
            self._deactivate_if_current(refresh_token)
            return
        client = None
        batcher = None
        try:
            payload = json.loads(
                (Path(self.registry.root) / ONLINE_ACTIVATION_FILENAME).read_text(
                    encoding="utf-8"
                )
            )
            model_id = str(payload["model_id"])
            compatibility = payload.get("compatibility")
            if not isinstance(compatibility, Mapping):
                raise ValueError("activation compatibility is invalid")
            compatibility_snapshot = {
                **dict(compatibility),
                "artifact_sha256": str(payload.get("artifact_sha256") or ""),
            }
            frozen_compatibility = _freeze_runtime_mapping(compatibility_snapshot)
            current = self.snapshot()
            if (
                current.mode is EmailClassifierRuntimeMode.MODEL_PRIMARY
                and current.model_id == model_id
                and dict(current.compatibility) == dict(frozen_compatibility)
            ):
                return
            artifact = (
                Path(self.registry.embedding_artifacts) / f"{model_id}.artifact"
            )
            classifier = self._classifier_loader(artifact)
            _verify_online_classifier_compatibility(classifier, compatibility)
            client = self._embedding_client_factory(classifier)
            cache = self._cache_factory(classifier)
            batcher = OnlineEmbeddingMicrobatcher(client)
            generation_latency = (
                self.latency
                if self._observability_store is None
                else StageLatencyRecorder(
                    sample_sink=lambda sample: (
                        self._observability_store.record_classifier_runtime_sample(
                            model_id=model_id,
                            outcome=str(sample["outcome"]),
                            fallback_code=str(sample["fallback_reason"]),
                            cache_hit=bool(sample["cache_hit"]),
                            runtime_warm=bool(sample["runtime_warm"]),
                            queue_ms=float(sample["queue_ms"]),
                            http_ms=float(sample["http_ms"]),
                            embedding_ms=float(sample["embedding_ms"]),
                            head_ms=float(sample["head_ms"]),
                            total_ms=float(sample["total_ms"]),
                        )
                    )
                )
            )
            predictor = OnlineEmbeddingPredictor(
                model_id=model_id,
                classifier=classifier,
                cache=cache,
                embedding_client=client,
                embedding_batcher=batcher,
                runtime_id=(
                    f"{model_id}:{payload.get('artifact_sha256', '')}:"
                    f"{classifier.input_schema_version}:"
                    f"{classifier.embedding_revision}"
                ),
                latency=generation_latency,
            )
        except Exception:
            if batcher is not None:
                batcher.retire()
            if client is not None and self._embedding_client_owned:
                _close_embedding_client(client)
            self._deactivate_if_current(refresh_token)
            return
        promoted = RuntimeSnapshot.model_primary(
            predictor=predictor,
            model_id=model_id,
            input_schema_version=str(classifier.input_schema_version),
            compatibility=frozen_compatibility,
        )
        generation = _RuntimeGeneration(
            snapshot=promoted,
            predictor=predictor,
            batcher=batcher,
            client=client,
            owns_client=self._embedding_client_owned,
        )
        with self._lock:
            publish = (
                not self._closed and refresh_token == self._refresh_epoch
            )
            previous_generation = self._generation if publish else None
            if publish:
                self._snapshot = promoted
                self._generation = generation
                self.latency = generation_latency
        if not publish:
            generation.retire()
            return
        if previous_generation is not None:
            previous_generation.retire()

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            self._refresh_epoch += 1
            generation = self._generation
            self._snapshot = RuntimeSnapshot.agent_primary()
            self._generation = None
        if generation is not None:
            generation.retire()

    def _deactivate_if_current(self, refresh_token: int) -> None:
        with self._lock:
            if self._closed or refresh_token != self._refresh_epoch:
                return
            generation = self._generation
            self._snapshot = RuntimeSnapshot.agent_primary()
            self._generation = None
        if generation is not None:
            generation.retire()


def _close_embedding_client(client: object) -> None:
    close = getattr(client, "close", None)
    if callable(close):
        close()


def _production_embedding_client(classifier: object) -> EmailEmbeddingClient:
    return EmailEmbeddingClient.from_environment(
        embedding_revision=str(classifier.embedding_revision),
        dimension=int(classifier.dimension),
    )


@dataclass(frozen=True)
class OnlineClassificationResult:
    source: str
    value: object
    fallback_reason: str = ""
    accept_outcome: "OnlineModelAcceptOutcome | None" = None

    def __post_init__(self) -> None:
        if self.source not in {"model", "agent"}:
            raise ValueError("online classification source is invalid")


class OnlineModelAcceptStage(StrEnum):
    BEFORE_DURABLE_COMMIT = "before_durable_commit"
    AFTER_DURABLE_COMMIT = "after_durable_commit"


class OnlineModelAcceptError(RuntimeError):
    """Classify acceptance failures by whether durable model state exists."""

    def __init__(self, stage: OnlineModelAcceptStage, message: str) -> None:
        super().__init__(message)
        self.stage = OnlineModelAcceptStage(stage)


class OnlineModelDurableConflict(OnlineModelAcceptError):
    """A stable identity is durably bound to a different model decision."""

    def __init__(self, message: str) -> None:
        super().__init__(OnlineModelAcceptStage.AFTER_DURABLE_COMMIT, message)


@dataclass(frozen=True)
class OnlineModelAcceptOutcome:
    status: str
    persisted: Mapping[str, object]

    def __post_init__(self) -> None:
        if self.status not in {"accepted", "already_committed"}:
            raise ValueError("online model accept status is invalid")

    @classmethod
    def accepted(cls, persisted: Mapping[str, object]):
        return cls("accepted", persisted)

    @classmethod
    def already_committed(cls, persisted: Mapping[str, object]):
        return cls("already_committed", persisted)


class SequentialOnlineClassifier:
    """Call exactly one primary and only then an Agent fallback when required."""

    def __init__(self, *, mode, model_predict, agent_classify) -> None:
        self.mode = EmailClassifierRuntimeMode(mode)
        if self.mode is EmailClassifierRuntimeMode.SHADOW_HISTORY:
            raise ValueError("shadow_history is only valid for manual historical jobs")
        if self.mode is EmailClassifierRuntimeMode.MODEL_PRIMARY and isinstance(
            model_predict, CpuTfidfLogisticClassifier
        ):
            raise TypeError("TF-IDF cannot be used as an online model")
        if not callable(model_predict) or not callable(agent_classify):
            raise TypeError("online classifier dependencies must be callable")
        self._model_predict = model_predict
        self._agent_classify = agent_classify

    def classify(self, current_input: object) -> OnlineClassificationResult:
        if self.mode is EmailClassifierRuntimeMode.AGENT_PRIMARY:
            return OnlineClassificationResult(
                source="agent", value=self._agent_classify(current_input)
            )
        try:
            model_result = self._model_predict(current_input)
            if (
                type(model_result) is OnlineClassificationResult
                and model_result.source == "model"
                and model_result.value is not None
                and not model_result.fallback_reason
            ):
                return model_result
            reason = (
                model_result.fallback_reason
                if type(model_result) is OnlineClassificationResult
                and model_result.fallback_reason
                else "model_rejected"
            )
        except Exception as exc:  # model failure is the defined Agent fallback boundary
            reason = f"model_failure:{type(exc).__name__}"
        return OnlineClassificationResult(
            source="agent",
            value=self._agent_classify(current_input),
            fallback_reason=reason,
        )


@dataclass
class StageLatencyRecorder:
    """Keep every in-process stage sample and expose runtime percentiles."""

    _values: dict[str, list[float]] = field(
        default_factory=lambda: {
            "queue": [],
            "http": [],
            "embedding": [],
            "head": [],
            "total": [],
        }
    )
    _raw: list[dict[str, object]] = field(default_factory=list)
    _fallback_reasons: dict[str, int] = field(default_factory=dict)
    sample_sink: Callable[[Mapping[str, object]], None] | None = field(
        default=None, repr=False
    )
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def record(
        self,
        *,
        outcome: str = "success",
        fallback_reason: str = "",
        cache_hit: bool,
        runtime_warm: bool,
        **values: float,
    ) -> None:
        expected = {f"{stage}_ms" for stage in self._values}
        if set(values) != expected:
            raise ValueError("all latency stages must be recorded together")
        if outcome not in {"success", "rejected", "failure"}:
            raise ValueError("latency outcome is invalid")
        if type(cache_hit) is not bool or type(runtime_warm) is not bool:
            raise TypeError("latency sample flags must be strict bools")
        validated: dict[str, float] = {}
        for stage in self._values:
            value = values[f"{stage}_ms"]
            if not isinstance(value, float) or not math.isfinite(value) or value < 0:
                raise ValueError("latency samples must be finite non-negative floats")
            validated[f"{stage}_ms"] = value
        sample = {
            **validated,
            "outcome": outcome,
            "fallback_reason": fallback_reason,
            "cache_hit": cache_hit,
            "runtime_warm": runtime_warm,
        }
        with self._lock:
            for stage in self._values:
                self._values[stage].append(validated[f"{stage}_ms"])
            self._raw.append(sample)
        if self.sample_sink is not None:
            try:
                self.sample_sink(MappingProxyType(dict(sample)))
            except Exception:
                # Observability is best effort and must never alter routing.
                pass

    def record_fallback(self, reason: str) -> None:
        normalized = str(reason or "unknown")
        with self._lock:
            self._fallback_reasons[normalized] = (
                self._fallback_reasons.get(normalized, 0) + 1
            )

    @property
    def fallback_count(self) -> int:
        with self._lock:
            return sum(self._fallback_reasons.values())

    def fallback_counts(self) -> dict[str, int]:
        with self._lock:
            return dict(self._fallback_reasons)

    def raw_samples(self) -> tuple[dict[str, object], ...]:
        with self._lock:
            return tuple(dict(item) for item in self._raw)

    def summary(self) -> dict[str, object]:
        with self._lock:
            raw = tuple(dict(sample) for sample in self._raw)
        all_samples = raw
        warm_success = tuple(
            sample
            for sample in raw
            if sample["runtime_warm"] is True and sample["outcome"] == "success"
        )
        return {
            "all": _latency_segment(all_samples),
            "warm_success": _latency_segment(warm_success),
            "warm_success_cache": _latency_segment(
                tuple(sample for sample in warm_success if sample["cache_hit"] is True)
            ),
            "warm_success_remote": _latency_segment(
                tuple(sample for sample in warm_success if sample["cache_hit"] is False)
            ),
            "slo_status": self.slo_status,
        }

    @property
    def slo_status(self) -> str:
        with self._lock:
            totals = [
                float(sample["total_ms"])
                for sample in self._raw
                if sample["runtime_warm"] is True
                and sample["outcome"] == "success"
            ]
        if not totals:
            return "not_enough_data"
        return "compliant" if _percentile(totals, 0.95) < 500.0 else "non_compliant"

    @property
    def slo_compliant(self) -> bool | None:
        if self.slo_status == "not_enough_data":
            return None
        return self.slo_status == "compliant"


def _latency_segment(samples: tuple[dict[str, object], ...]) -> dict[str, object]:
    return {
        "sample_count": len(samples),
        "stages": {
            stage: {
                "p50": _percentile(
                    [float(sample[f"{stage}_ms"]) for sample in samples], 0.50
                ),
                "p95": _percentile(
                    [float(sample[f"{stage}_ms"]) for sample in samples], 0.95
                ),
                "p99": _percentile(
                    [float(sample[f"{stage}_ms"]) for sample in samples], 0.99
                ),
            }
            for stage in ("queue", "http", "embedding", "head", "total")
        },
    }


def _percentile(values: list[float], quantile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    position = (len(ordered) - 1) * quantile
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    weight = position - lower
    return ordered[lower] * (1.0 - weight) + ordered[upper] * weight


@dataclass(frozen=True)
class LoadedEmailClassifier:
    classifier: object
    path: Path
    used_previous: bool
    model_id: str


@dataclass(frozen=True)
class ReadonlyScanWithModelResult:
    loaded: LoadedEmailClassifier
    scan: EmailScanResult


class RegistryPredictionClassifier:
    """Count consecutive runtime failures and atomically fall back at threshold."""

    def __init__(
        self,
        registry: EmailModelRegistry,
        classifier: CpuTfidfLogisticClassifier,
        model_id: str,
        *,
        failure_threshold: int = 3,
    ) -> None:
        if failure_threshold <= 0:
            raise ValueError("failure_threshold must be positive")
        self.registry = registry
        self.classifier = classifier
        self.model_id = model_id
        self.failure_threshold = failure_threshold
        self.consecutive_failures = 0

    def predict(self, text: str):
        return self._predict("predict", text)

    def predict_message(self, message: object):
        return self._predict("predict_message", message)

    def _predict(self, method: str, value: object):
        try:
            result = getattr(self.classifier, method)(value)
            self.consecutive_failures = 0
            return result
        except Exception:
            self.consecutive_failures += 1
            if self.consecutive_failures < self.failure_threshold:
                raise
            restored = self.registry.fallback_to_previous(
                reason="active_prediction_failed_repeatedly",
                failed_model_id=self.model_id,
            )
            self.classifier = self.registry.load_classifier(restored.model_id)
            self.model_id = restored.model_id
            self.consecutive_failures = 0
            return getattr(self.classifier, method)(value)


class EmailClassifierRuntime:
    """Own one loaded classifier and learning tick across mailbox scan cycles."""

    def __init__(self, registry: EmailModelRegistry, *, learning_service=None) -> None:
        self.registry = registry
        self.learning_service = learning_service
        self._lock = threading.RLock()
        self.loaded = _load_registry_classifier(registry)

    def tick(self, *, now: datetime | None = None):
        result = (
            None
            if self.learning_service is None
            else self.learning_service.poll_retrain(now=now)
        )
        self._adopt_active_model()
        return result

    def _adopt_active_model(self) -> None:
        with self._lock:
            manifest = self.registry.active_manifest()
            if manifest is None:
                raise EmailClassifierUnavailable(
                    "no active email classifier manifest"
                )
            if self.loaded.model_id == manifest.model_id:
                return
            self.loaded = _load_registry_classifier(self.registry)

    def scan(
        self,
        source: object,
        store: EmailStore,
        config: EmailScanConfig,
        *,
        mailbox: str = "INBOX",
        limit: int = 50,
        now: datetime | None = None,
    ) -> ReadonlyScanWithModelResult:
        self.tick(now=now)
        with self._lock:
            loaded = self.loaded
        result = scan_readonly_batch(
            source,
            loaded.classifier,
            store,
            config,
            mailbox=mailbox,
            limit=limit,
        )
        if loaded.classifier.model_id != loaded.model_id:
            self._adopt_active_model()
            with self._lock:
                loaded = self.loaded
        return ReadonlyScanWithModelResult(loaded=loaded, scan=result)


class EmailClassifierRuntimeFactory:
    """Task8-owned singleton boundary; this module does not start a worker."""

    def __init__(self, builder: Callable[[], EmailClassifierRuntime]) -> None:
        self._builder = builder
        self._runtime: EmailClassifierRuntime | None = None
        self._lock = threading.Lock()

    def get(self) -> EmailClassifierRuntime:
        with self._lock:
            if self._runtime is None:
                self._runtime = self._builder()
            return self._runtime


def _load_registry_classifier(registry) -> LoadedEmailClassifier:
    jieba_lcut("email classifier warmup")
    failed_model_id = registry.active_model_id_unverified()
    if failed_model_id is None:
        raise EmailClassifierUnavailable("no active email classifier manifest")
    try:
        manifest = registry.active_manifest()
        assert manifest is not None
        classifier = registry.load_classifier(manifest.model_id)
        return LoadedEmailClassifier(
            classifier=RegistryPredictionClassifier(
                registry, classifier, manifest.model_id
            ),
            path=registry.get_model(manifest.model_id).artifact_path,
            used_previous=False,
            model_id=manifest.model_id,
        )
    except ModelRegistryError:
        try:
            restored = registry.fallback_to_previous(
                reason="active_model_load_failed",
                failed_model_id=failed_model_id,
            )
            classifier = registry.load_classifier(restored.model_id)
        except ModelRegistryError as fallback_exc:
            raise EmailClassifierUnavailable(
                "no valid email classifier model after active load failure"
            ) from fallback_exc
        return LoadedEmailClassifier(
            classifier=RegistryPredictionClassifier(
                registry, classifier, restored.model_id
            ),
            path=registry.get_model(restored.model_id).artifact_path,
            used_previous=True,
            model_id=restored.model_id,
        )


def load_active_classifier(
    active_path: str | Path | EmailModelRegistry,
    previous_path: str | Path | None = None,
) -> LoadedEmailClassifier:
    """Load active first, then previous, without modifying either file."""
    if isinstance(active_path, EmailModelRegistry):
        return _load_registry_classifier(active_path)
    if previous_path is None:
        raise ValueError("previous_path is required for path-based model loading")
    candidates = ((Path(active_path), False), (Path(previous_path), True))
    errors: list[str] = []
    for path, used_previous in candidates:
        try:
            classifier = CpuTfidfLogisticClassifier.load(path)
            for label in classifier.class_labels():
                validate_email_category_key(label)
        except (OSError, KeyError, TypeError, ValueError, UnpicklingError) as exc:
            errors.append(f"{path}: {type(exc).__name__}")
            continue
        return LoadedEmailClassifier(
            classifier=classifier,
            path=path,
            used_previous=used_previous,
            model_id=classifier.model_version,
        )
    detail = "; ".join(errors) if errors else "no model paths configured"
    raise EmailClassifierUnavailable(f"no valid email classifier model: {detail}")


def scan_with_active_model(
    source: object,
    store: EmailStore,
    config: EmailScanConfig,
    *,
    runtime: EmailClassifierRuntime,
    mailbox: str = "INBOX",
    limit: int = 50,
    now: datetime | None = None,
) -> ReadonlyScanWithModelResult:
    """Load a local model and run one provider-readonly classification batch."""
    return runtime.scan(
        source,
        store,
        config,
        mailbox=mailbox,
        limit=limit,
        now=now,
    )
