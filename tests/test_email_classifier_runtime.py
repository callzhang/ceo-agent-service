from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import pickle
from threading import Barrier, Event, Lock, Thread
import time
from types import SimpleNamespace
from hashlib import sha256

import numpy as np

import pytest
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression

from app.email_classifier_model import (
    CpuTfidfLogisticClassifier,
    EmailModelPrediction,
)
from app.email_classifier_contracts import (
    EmailAction,
    EmailCategory,
    EmailClassificationStatus,
    INITIAL_EMAIL_CATEGORY_KEYS,
)
from app.email_classifier_scan import EmailScanConfig, scan_readonly_batch
from app.email_classifier_training import CategoryEligibility, EmailActionEligibility
from app.email_store import EmailStore
from app.email_classifier_runtime import (
    EmailClassifierRuntime,
    EmailClassifierRuntimeFactory,
    EmailClassifierRuntimeMode,
    EmailClassifierUnavailable,
    OnlineClassificationResult,
    OnlineModelInput,
    OnlineEmbeddingPredictor,
    PromotedEmailClassifierRuntime,
    RuntimeSnapshot,
    SequentialOnlineClassifier,
    StageLatencyRecorder,
    activate_online_model,
    derive_runtime_mode,
    load_active_classifier,
    scan_with_active_model,
    RegistryPredictionClassifier,
)
from app.email_classifier_retrain import TrainingSubprocessController
from app.email_model_registry import EmailModelRegistry
from app.email_embedding_client import (
    EmailEmbeddingClient,
    EmbeddingResult,
    EmbeddingTiming,
)
from app.email_embedding_classifier import (
    EmbeddingModelPrediction,
    TimedEmbeddingModelPrediction,
)


def test_agent_primary_never_calls_model_and_calls_agent_once():
    calls = []
    router = SequentialOnlineClassifier(
        mode=EmailClassifierRuntimeMode.AGENT_PRIMARY,
        model_predict=lambda _value: calls.append("model"),
        agent_classify=lambda value: calls.append(("agent", value)) or "agent-result",
    )

    result = router.classify("current-input")

    assert result == OnlineClassificationResult(
        source="agent", value="agent-result", fallback_reason=""
    )
    assert calls == [("agent", "current-input")]


@pytest.mark.parametrize(
    "failure",
    (
        "rejected",
        "timeout",
        "embedding_failure",
        "model_version_mismatch",
        "incomplete_input",
    ),
)
def test_model_primary_falls_back_to_agent_once_and_sequentially(failure):
    calls = []

    def model_predict(value):
        calls.append(("model-start", value))
        if failure == "rejected":
            calls.append("model-end")
            return OnlineClassificationResult(
                source="model", value=None, fallback_reason="model_rejected"
            )
        calls.append("model-end")
        raise RuntimeError(failure)

    router = SequentialOnlineClassifier(
        mode=EmailClassifierRuntimeMode.MODEL_PRIMARY,
        model_predict=model_predict,
        agent_classify=lambda value: calls.append(("agent", value)) or "agent-result",
    )

    result = router.classify("current-input")

    assert result.source == "agent"
    assert result.value == "agent-result"
    assert result.fallback_reason
    assert calls == [
        ("model-start", "current-input"),
        "model-end",
        ("agent", "current-input"),
    ]


def test_model_primary_accepted_result_bypasses_agent():
    calls = []
    accepted = OnlineClassificationResult(source="model", value="legal")
    router = SequentialOnlineClassifier(
        mode=EmailClassifierRuntimeMode.MODEL_PRIMARY,
        model_predict=lambda value: calls.append(("model", value)) or accepted,
        agent_classify=lambda _value: calls.append("agent"),
    )

    assert router.classify("current-input") == accepted
    assert calls == [("model", "current-input")]


def test_shadow_history_is_not_a_realtime_model_mode():
    with pytest.raises(ValueError, match="manual.*historical"):
        SequentialOnlineClassifier(
            mode=EmailClassifierRuntimeMode.SHADOW_HISTORY,
            model_predict=lambda _value: None,
            agent_classify=lambda _value: None,
        )


def test_runtime_rejects_tfidf_as_online_model():
    with pytest.raises(TypeError, match="TF-IDF.*online"):
        SequentialOnlineClassifier(
            mode=EmailClassifierRuntimeMode.MODEL_PRIMARY,
            model_predict=CpuTfidfLogisticClassifier(),
            agent_classify=lambda _value: None,
        )


def test_stage_latency_records_p50_p95_p99_and_500ms_slo():
    recorder = StageLatencyRecorder()
    for total in (100.0, 200.0, 300.0, 400.0, 490.0):
        recorder.record(
            queue_ms=10.0,
            http_ms=20.0,
            embedding_ms=30.0,
            head_ms=5.0,
            total_ms=total,
            cache_hit=False,
            runtime_warm=True,
        )

    summary = recorder.summary()

    assert set(summary) == {
        "all",
        "warm_success",
        "warm_success_cache",
        "warm_success_remote",
        "slo_status",
    }
    assert set(summary["warm_success"]["stages"]["total"]) == {
        "p50",
        "p95",
        "p99",
    }
    assert summary["warm_success"]["stages"]["total"]["p95"] < 500.0
    assert recorder.slo_compliant

    recorder.record(
        queue_ms=0.0,
        http_ms=0.0,
        embedding_ms=0.0,
        head_ms=0.0,
        total_ms=1000.0,
        cache_hit=False,
        runtime_warm=True,
    )
    assert not recorder.slo_compliant


def test_latency_slo_excludes_cold_and_failures_but_keeps_them_in_all_samples():
    recorder = StageLatencyRecorder()
    recorder.record(
        queue_ms=50.0,
        http_ms=2000.0,
        embedding_ms=2000.0,
        head_ms=0.0,
        total_ms=2050.0,
        outcome="failure",
        fallback_reason="OnlineEmbeddingBatchTimeout",
        cache_hit=False,
        runtime_warm=False,
    )
    recorder.record_fallback("OnlineEmbeddingBatchTimeout")
    recorder.record(
        queue_ms=0.0,
        http_ms=0.0,
        embedding_ms=0.0,
        head_ms=2.0,
        total_ms=2.0,
        cache_hit=True,
        runtime_warm=True,
    )

    summary = recorder.summary()

    assert summary["all"]["sample_count"] == 2
    assert summary["warm_success"]["sample_count"] == 1
    assert summary["warm_success_cache"]["sample_count"] == 1
    assert summary["warm_success_remote"]["sample_count"] == 0
    assert summary["slo_status"] == "compliant"
    assert recorder.fallback_count == 1
    assert recorder.raw_samples()[0]["outcome"] == "failure"


def test_latency_slo_is_not_enough_without_warm_success_samples():
    recorder = StageLatencyRecorder()
    recorder.record(
        queue_ms=0.0,
        http_ms=10.0,
        embedding_ms=10.0,
        head_ms=1.0,
        total_ms=11.0,
        cache_hit=False,
        runtime_warm=False,
    )

    assert recorder.slo_status == "not_enough_data"
    assert recorder.slo_compliant is None
    assert recorder.summary()["slo_status"] == "not_enough_data"


def _mature_evidence(model_id, artifact_sha, *, parent="same-parent"):
    metrics = {
        "accepted_precision": 0.99,
        "accepted_hits": 25,
        "independent_groups": 12,
    }
    return {
        "model_id": model_id,
        "trained_at": "2026-09-07T00:00:00+00:00",
        "compatibility": {
            "enabled_categories": ["work", "legal"],
            "description_version": "descriptions-v4",
            "input_schema_version": "input-v3",
            "embedding_model_id": "jina",
            "embedding_revision": "r17",
            "head_format": "description-mlp-v1",
            "parent_model_id": parent,
        },
        "metrics": {
            "categories": {"work": metrics, "legal": metrics},
            "important": metrics,
        },
        "hashes": {"artifact_sha256": artifact_sha},
        "unresolved_historical_systematic_error": False,
    }


class _ActivationRegistry:
    def __init__(self, root, evidence):
        self.root = root
        self.embedding_artifacts = root / "embedding-artifacts"
        self.embedding_artifacts.mkdir(parents=True)
        self._evidence = evidence

    def list_staged_evidence(self):
        return list(self._evidence)

    def get_staged_evidence(self, model_id):
        return next(item for item in self._evidence if item["model_id"] == model_id)


class _LoadedOnlineModel:
    enabled_categories = ("work", "legal")
    input_schema_version = "input-v3"
    embedding_model_id = "jina"
    embedding_revision = "r17"


def test_atomic_activation_requires_latest_whole_model_ready_candidate(tmp_path):
    root = tmp_path / "registry"
    first_id = "email-embedding-mlp-first"
    second_id = "email-embedding-mlp-second"
    first_bytes = b"first"
    second_bytes = b"second"
    registry = _ActivationRegistry(
        root,
        (
            _mature_evidence(first_id, sha256(first_bytes).hexdigest()),
            _mature_evidence(second_id, sha256(second_bytes).hexdigest()),
        ),
    )
    (registry.embedding_artifacts / f"{first_id}.artifact").write_bytes(first_bytes)
    artifact = registry.embedding_artifacts / f"{second_id}.artifact"
    artifact.write_bytes(second_bytes)

    activation = activate_online_model(
        registry,
        second_id,
        classifier_loader=lambda path: _LoadedOnlineModel()
        if path == artifact
        else None,
    )

    assert activation.model_id == second_id
    assert derive_runtime_mode(registry) is EmailClassifierRuntimeMode.MODEL_PRIMARY
    assert not list(root.glob(".online-active.*.tmp"))


def test_runtime_mode_fails_closed_for_missing_or_tampered_activation(tmp_path):
    registry = _ActivationRegistry(tmp_path / "registry", ())
    assert derive_runtime_mode(registry) is EmailClassifierRuntimeMode.AGENT_PRIMARY
    assert (
        derive_runtime_mode(registry, manual_historical=True)
        is EmailClassifierRuntimeMode.SHADOW_HISTORY
    )
    (registry.root / "online-active.json").write_text("{}", encoding="utf-8")
    assert derive_runtime_mode(registry) is EmailClassifierRuntimeMode.AGENT_PRIMARY


class _ExactCache:
    def __init__(self, vector=None):
        self.vector = vector
        self.get_keys = []
        self.put_keys = []

    def get(self, key):
        self.get_keys.append(key)
        return self.vector

    def put(self, key, vector):
        self.put_keys.append(key)
        self.vector = vector


class _EmbeddingClient:
    model_id = "jina"
    embedding_revision = "r17"

    def __init__(self):
        self.calls = []

    def embed(self, texts, *, queued_at=None):
        self.calls.append((tuple(texts), queued_at))
        return EmbeddingResult(
            vectors=np.array([[1.0, 0.0]], dtype=np.float32),
            timing=EmbeddingTiming(1.0, 2.0, 3.0, 0.0, 6.0),
        )


class _OnlineHead:
    input_schema_version = "input-v3"
    embedding_model_id = "jina"
    embedding_revision = "r17"

    def predict_result(self, result, *, index=0):
        assert index == 0
        assert result.vectors.shape == (1, 2)
        prediction = EmbeddingModelPrediction(
            category="legal",
            category_probability=0.97,
            category_probabilities={"work": 0.03, "legal": 0.97},
            category_accepted=True,
            important=True,
            important_probability=0.95,
            head_ms=4.0,
        )
        return TimedEmbeddingModelPrediction(
            prediction=prediction,
            timing=EmbeddingTiming(1.0, 2.0, 3.0, 4.0, 10.0),
        )


def test_online_predictor_reuses_only_exact_current_input_cache_key():
    client = _EmbeddingClient()
    cache = _ExactCache()
    latency = StageLatencyRecorder()
    clock_values = iter((100.000, 100.001, 100.002, 100.003, 100.004, 100.005))
    predictor = OnlineEmbeddingPredictor(
        model_id="email-embedding-mlp-second",
        classifier=_OnlineHead(),
        cache=cache,
        embedding_client=client,
        latency=latency,
        clock=lambda: next(clock_values),
    )
    current = OnlineModelInput("exact current model text", "input-v3")

    first = predictor(current)
    second = predictor(current)

    assert first.source == second.source == "model"
    assert first.value.category == "legal"
    assert len(client.calls) == 1
    assert cache.get_keys[0] == cache.put_keys[0] == cache.get_keys[1]
    assert cache.get_keys[0].normalized_input_hash == sha256(
        current.normalized_text.encode()
    ).hexdigest()
    assert latency.summary()["all"]["stages"]["total"]["p95"] > 0


def test_online_microbatcher_combines_eight_compatible_concurrent_requests():
    runtime_module = __import__(
        "app.email_classifier_runtime", fromlist=["OnlineEmbeddingMicrobatcher"]
    )
    calls = []
    call_lock = Lock()

    class Client:
        def embed(self, texts, *, queued_at=None):
            with call_lock:
                calls.append((tuple(texts), queued_at))
            return EmbeddingResult(
                vectors=np.array(
                    [[float(text.rsplit("-", 1)[1]), 1.0] for text in texts],
                    dtype=np.float32,
                ),
                timing=EmbeddingTiming(0.0, 2.0, 3.0, 0.0, 4.0),
            )

    compatibility = runtime_module.OnlineBatchCompatibility(
        runtime_id="runtime-1",
        model_id="email-embedding-mlp-ready",
        input_schema_version="input-v3",
        embedding_model_id="jina",
        embedding_revision="r17",
    )
    batcher = runtime_module.OnlineEmbeddingMicrobatcher(Client())
    assert batcher.remote_timeout_seconds == 2.0
    barrier = Barrier(9)

    def submit(index):
        barrier.wait()
        return batcher.embed_one(f"mail-{index}", compatibility)

    try:
        with ThreadPoolExecutor(max_workers=8) as pool:
            futures = [pool.submit(submit, index) for index in range(8)]
            barrier.wait()
            results = [future.result(timeout=1.0) for future in futures]
    finally:
        batcher.close()

    assert len(calls) == 1
    assert set(calls[0][0]) == {f"mail-{index}" for index in range(8)}
    assert sorted(float(result.vectors[0][0]) for result in results) == list(
        map(float, range(8))
    )


def _batch_compatibility(runtime_id="runtime-1"):
    runtime_module = __import__(
        "app.email_classifier_runtime", fromlist=["OnlineBatchCompatibility"]
    )
    return runtime_module.OnlineBatchCompatibility(
        runtime_id=runtime_id,
        model_id="email-embedding-mlp-ready",
        input_schema_version="input-v3",
        embedding_model_id="jina",
        embedding_revision="r17",
    )


def test_online_microbatcher_splits_more_than_eight_requests():
    runtime_module = __import__(
        "app.email_classifier_runtime", fromlist=["OnlineEmbeddingMicrobatcher"]
    )
    batch_sizes = []
    lock = Lock()

    class Client:
        def embed(self, texts, *, queued_at=None):
            del queued_at
            with lock:
                batch_sizes.append(len(texts))
            return EmbeddingResult(
                vectors=np.ones((len(texts), 2), dtype=np.float32),
                timing=EmbeddingTiming(0.0, 1.0, 1.0, 0.0, 2.0),
            )

    batcher = runtime_module.OnlineEmbeddingMicrobatcher(Client())
    barrier = Barrier(11)

    def submit(index):
        barrier.wait()
        return batcher.embed_one(f"mail-{index}", _batch_compatibility())

    try:
        with ThreadPoolExecutor(max_workers=10) as pool:
            futures = [pool.submit(submit, index) for index in range(10)]
            barrier.wait()
            assert all(future.result(timeout=1.0) is not None for future in futures)
    finally:
        batcher.close()

    assert sorted(batch_sizes) == [2, 8]


def test_online_microbatch_wait_is_bounded_from_first_request():
    runtime_module = __import__(
        "app.email_classifier_runtime", fromlist=["OnlineEmbeddingMicrobatcher"]
    )
    called_at = []

    class Client:
        def embed(self, texts, *, queued_at=None):
            del queued_at
            called_at.append(time.perf_counter())
            return EmbeddingResult(
                vectors=np.ones((len(texts), 2), dtype=np.float32),
                timing=EmbeddingTiming(0.0, 1.0, 1.0, 0.0, 2.0),
            )

    batcher = runtime_module.OnlineEmbeddingMicrobatcher(Client())
    started = time.perf_counter()
    try:
        batcher.embed_one("single", _batch_compatibility())
    finally:
        batcher.close()

    queue_seconds = called_at[0] - started
    assert 0.035 <= queue_seconds <= 0.10


def test_online_microbatcher_never_combines_incompatible_runtime_requests():
    runtime_module = __import__(
        "app.email_classifier_runtime", fromlist=["OnlineEmbeddingMicrobatcher"]
    )
    calls = []

    class Client:
        def embed(self, texts, *, queued_at=None):
            del queued_at
            calls.append(tuple(texts))
            return EmbeddingResult(
                vectors=np.ones((len(texts), 2), dtype=np.float32),
                timing=EmbeddingTiming(0.0, 1.0, 1.0, 0.0, 2.0),
            )

    batcher = runtime_module.OnlineEmbeddingMicrobatcher(Client())
    barrier = Barrier(3)

    def submit(text, runtime_id):
        barrier.wait()
        return batcher.embed_one(text, _batch_compatibility(runtime_id))

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(submit, "first", "runtime-1")
            second = pool.submit(submit, "second", "runtime-2")
            barrier.wait()
            first.result(timeout=1.0)
            second.result(timeout=1.0)
    finally:
        batcher.close()

    assert sorted(calls) == [("first",), ("second",)]


def test_online_microbatcher_dispatched_request_waits_for_remote_before_returning():
    runtime_module = __import__(
        "app.email_classifier_runtime",
        fromlist=["OnlineEmbeddingMicrobatcher", "OnlineEmbeddingBatchTimeout"],
    )
    release = Event()

    class Client:
        def embed(self, texts, *, queued_at=None):
            del texts, queued_at
            release.wait(1.0)
            return EmbeddingResult(
                vectors=np.ones((1, 2), dtype=np.float32),
                timing=EmbeddingTiming(0.0, 1.0, 1.0, 0.0, 2.0),
            )

    batcher = runtime_module.OnlineEmbeddingMicrobatcher(
        Client(), maximum_wait_seconds=0.005, remote_timeout_seconds=0.03
    )
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(
                batcher.embed_one, "timeout", _batch_compatibility()
            )
            time.sleep(0.08)
            assert not future.done()
            assert batcher.pending_count == 1
            release.set()
            assert future.result(timeout=1.0).vectors.shape == (1, 2)
    finally:
        release.set()
        batcher.close()


def test_online_microbatcher_blocking_client_has_only_managed_worker_thread():
    from threading import enumerate as enumerate_threads

    runtime_module = __import__(
        "app.email_classifier_runtime", fromlist=["OnlineEmbeddingMicrobatcher"]
    )
    release = Event()
    calls = []

    class Client:
        def embed(self, texts, *, queued_at=None):
            del queued_at
            calls.append(tuple(texts))
            release.wait(1.0)
            return EmbeddingResult(
                vectors=np.ones((len(texts), 2), dtype=np.float32),
                timing=EmbeddingTiming(0.0, 1.0, 1.0, 0.0, 2.0),
            )

    baseline = {
        thread.ident
        for thread in enumerate_threads()
        if thread.name == "email-online-embedding-request"
    }
    batcher = runtime_module.OnlineEmbeddingMicrobatcher(
        Client(), maximum_wait_seconds=0.005, remote_timeout_seconds=0.03
    )
    try:
        with ThreadPoolExecutor(max_workers=3) as pool:
            futures = [
                pool.submit(
                    batcher.embed_one, f"timeout-{index}", _batch_compatibility()
                )
                for index in range(3)
            ]
            time.sleep(0.08)
            assert all(not future.done() for future in futures)
            release.set()
            assert all(future.result(timeout=1.0) for future in futures)
        untracked = {
            thread.ident
            for thread in enumerate_threads()
            if thread.name == "email-online-embedding-request"
            and thread.ident not in baseline
        }
        assert untracked == set()
        assert batcher.is_alive
    finally:
        release.set()
        batcher.close()

    assert len(calls) >= 1

def test_predictor_never_starts_agent_while_dispatched_remote_is_active():
    runtime_module = __import__(
        "app.email_classifier_runtime",
        fromlist=["OnlineEmbeddingMicrobatcher"],
    )
    release = Event()
    remote_active = Event()

    class Client(_EmbeddingClient):
        def embed(self, texts, *, queued_at=None):
            del texts, queued_at
            remote_active.set()
            release.wait(1.0)
            remote_active.clear()
            return EmbeddingResult(
                vectors=np.ones((1, 2), dtype=np.float32),
                timing=EmbeddingTiming(0.0, 1.0, 1.0, 0.0, 2.0),
            )

    client = Client()
    latency = StageLatencyRecorder()
    batcher = runtime_module.OnlineEmbeddingMicrobatcher(
        client, maximum_wait_seconds=0.005, remote_timeout_seconds=0.03
    )
    predictor = OnlineEmbeddingPredictor(
        model_id="email-embedding-mlp-timeout",
        classifier=_OnlineHead(),
        cache=_ExactCache(),
        embedding_client=client,
        embedding_batcher=batcher,
        latency=latency,
    )
    agent_remote_states = []
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(
                SequentialOnlineClassifier(
                    mode=EmailClassifierRuntimeMode.MODEL_PRIMARY,
                    model_predict=predictor,
                    agent_classify=lambda value: agent_remote_states.append(
                        remote_active.is_set()
                    )
                    or "agent-result",
                ).classify,
                OnlineModelInput("timeout", "input-v3"),
            )
            assert remote_active.wait(0.5)
            time.sleep(0.08)
            assert not future.done()
            assert agent_remote_states == []
            release.set()
            result = future.result(timeout=1.0)
    finally:
        release.set()
        batcher.close()

    summary = latency.summary()
    assert result.source == "model"
    assert agent_remote_states == []
    assert summary["all"]["sample_count"] == 1
    assert latency.fallback_count == 0


def test_remote_failure_starts_agent_only_after_managed_embed_returns():
    runtime_module = __import__(
        "app.email_classifier_runtime", fromlist=["OnlineEmbeddingMicrobatcher"]
    )
    release = Event()
    remote_active = Event()
    agent_remote_states = []

    class Client(_EmbeddingClient):
        def embed(self, texts, *, queued_at=None):
            del texts, queued_at
            remote_active.set()
            release.wait(1.0)
            remote_active.clear()
            raise RuntimeError("simulated transport timeout")

    client = Client()
    batcher = runtime_module.OnlineEmbeddingMicrobatcher(
        client, maximum_wait_seconds=0.005, remote_timeout_seconds=0.03
    )
    predictor = OnlineEmbeddingPredictor(
        model_id="email-embedding-mlp-failure",
        classifier=_OnlineHead(),
        cache=_ExactCache(),
        embedding_client=client,
        embedding_batcher=batcher,
    )
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(
                SequentialOnlineClassifier(
                    mode=EmailClassifierRuntimeMode.MODEL_PRIMARY,
                    model_predict=predictor,
                    agent_classify=lambda value: agent_remote_states.append(
                        remote_active.is_set()
                    )
                    or "agent-result",
                ).classify,
                OnlineModelInput("timeout", "input-v3"),
            )
            assert remote_active.wait(0.5)
            time.sleep(0.08)
            assert agent_remote_states == []
            release.set()
            result = future.result(timeout=1.0)
    finally:
        release.set()
        batcher.close()

    assert result.source == "agent"
    assert agent_remote_states == [False]


def test_slow_drip_deadline_finishes_remote_before_agent_fallback():
    runtime_module = __import__(
        "app.email_classifier_runtime", fromlist=["OnlineEmbeddingMicrobatcher"]
    )

    class SlowDripHandler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802 - stdlib callback name
            body = b'{"data":[{"index":0,"embedding":[1,2]}]}'
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
                return

        def log_message(self, *_args: object) -> None:
            return None

    remote_active = Event()

    class TrackingClient(EmailEmbeddingClient):
        def embed(self, texts, *, queued_at=None):
            remote_active.set()
            try:
                return super().embed(texts, queued_at=queued_at)
            finally:
                remote_active.clear()

    server = ThreadingHTTPServer(("127.0.0.1", 0), SlowDripHandler)
    server_thread = Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    client = TrackingClient(
        url=f"http://127.0.0.1:{server.server_port}/embeddings",
        embedding_revision="r17",
        dimension=2,
        model_id="jina",
    )
    batcher = runtime_module.OnlineEmbeddingMicrobatcher(
        client, maximum_wait_seconds=0.005
    )
    predictor = OnlineEmbeddingPredictor(
        model_id="email-embedding-mlp-slow-drip",
        classifier=_OnlineHead(),
        cache=_ExactCache(),
        embedding_client=client,
        embedding_batcher=batcher,
    )
    agent_remote_states = []
    started = time.perf_counter()
    try:
        result = SequentialOnlineClassifier(
            mode=EmailClassifierRuntimeMode.MODEL_PRIMARY,
            model_predict=predictor,
            agent_classify=lambda _value: agent_remote_states.append(
                remote_active.is_set()
            )
            or "agent-result",
        ).classify(OnlineModelInput("slow drip", "input-v3"))
        elapsed = time.perf_counter() - started
    finally:
        batcher.close()
        client.close()
        server.shutdown()
        server.server_close()
        server_thread.join(timeout=1.0)

    assert result.source == "agent"
    assert agent_remote_states == [False]
    assert elapsed <= 2.6
    assert not batcher.is_alive


def test_predictor_total_latency_includes_slow_exact_cache_get_and_put():
    class SlowCache:
        def __init__(self):
            self.value = None

        def get(self, _key):
            time.sleep(0.02)
            return self.value

        def put(self, _key, value):
            time.sleep(0.02)
            self.value = value

    latency = StageLatencyRecorder()
    predictor = OnlineEmbeddingPredictor(
        model_id="email-embedding-mlp-latency",
        classifier=_OnlineHead(),
        cache=SlowCache(),
        embedding_client=_EmbeddingClient(),
        latency=latency,
    )
    try:
        predictor(OnlineModelInput("first", "input-v3"))
        predictor(OnlineModelInput("first", "input-v3"))
    finally:
        predictor.close()

    first, second = latency.raw_samples()
    assert first["total_ms"] >= 40.0
    assert second["total_ms"] >= 20.0
    assert latency.summary()["warm_success"]["stages"]["total"]["p50"] >= 20.0

def test_online_microbatcher_shutdown_drains_dispatched_waiter_and_stops_worker():
    runtime_module = __import__(
        "app.email_classifier_runtime",
        fromlist=["OnlineEmbeddingMicrobatcher", "OnlineEmbeddingBatchClosed"],
    )

    started = Event()

    class Client:
        def embed(self, texts, *, queued_at=None):
            del queued_at
            started.set()
            time.sleep(0.1)
            return EmbeddingResult(
                vectors=np.ones((len(texts), 2), dtype=np.float32),
                timing=EmbeddingTiming(0.0, 1.0, 1.0, 0.0, 2.0),
            )

    batcher = runtime_module.OnlineEmbeddingMicrobatcher(Client())
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(
            batcher.embed_one, "shutdown", _batch_compatibility()
        )
        assert started.wait(0.5)
        batcher.close()
        assert future.result(timeout=1.0).vectors.shape == (1, 2)

    assert batcher.pending_count == 0
    assert not batcher.is_alive


def test_online_microbatcher_retire_drains_active_request_before_close():
    runtime_module = __import__(
        "app.email_classifier_runtime",
        fromlist=["OnlineEmbeddingMicrobatcher", "OnlineEmbeddingBatchClosed"],
    )
    started = Event()
    release = Event()

    class Client:
        def embed(self, texts, *, queued_at=None):
            del queued_at
            started.set()
            release.wait(1.0)
            return EmbeddingResult(
                vectors=np.ones((len(texts), 2), dtype=np.float32),
                timing=EmbeddingTiming(0.0, 1.0, 1.0, 0.0, 2.0),
            )

    batcher = runtime_module.OnlineEmbeddingMicrobatcher(
        Client(), maximum_wait_seconds=0.005
    )
    with ThreadPoolExecutor(max_workers=2) as pool:
        request = pool.submit(batcher.embed_one, "drain", _batch_compatibility())
        assert started.wait(0.5)
        retiring = pool.submit(batcher.retire)
        release.set()
        assert request.result(timeout=1.0).vectors.shape == (1, 2)
        retiring.result(timeout=1.0)

    assert not batcher.is_alive
    with pytest.raises(runtime_module.OnlineEmbeddingBatchClosed):
        batcher.embed_one("late", _batch_compatibility())


def test_online_predictor_exact_cache_hit_bypasses_microbatch_wait():
    runtime_module = __import__(
        "app.email_classifier_runtime", fromlist=["OnlineEmbeddingMicrobatcher"]
    )
    client = _EmbeddingClient()
    batcher = runtime_module.OnlineEmbeddingMicrobatcher(client)
    predictor = OnlineEmbeddingPredictor(
        model_id="email-embedding-mlp-second",
        classifier=_OnlineHead(),
        cache=_ExactCache(np.array([1.0, 0.0], dtype=np.float32)),
        embedding_client=client,
        embedding_batcher=batcher,
        runtime_id="runtime-cache-hit",
    )
    started = time.perf_counter()
    try:
        result = predictor(OnlineModelInput("exact current", "input-v3"))
    finally:
        batcher.close()

    assert result.source == "model"
    assert client.calls == []
    assert time.perf_counter() - started < 0.03


def test_online_predictor_mixed_head_failure_isolated_and_agent_fallback_sequential():
    runtime_module = __import__(
        "app.email_classifier_runtime", fromlist=["OnlineEmbeddingMicrobatcher"]
    )
    remote_active = Event()
    remote_calls = []
    agent_calls = []

    class Client:
        model_id = "jina"
        embedding_revision = "r17"

        def embed(self, texts, *, queued_at=None):
            del queued_at
            remote_active.set()
            remote_calls.append(tuple(texts))
            vectors = np.array(
                [[-1.0, 0.0] if text == "bad" else [1.0, 0.0] for text in texts],
                dtype=np.float32,
            )
            remote_active.clear()
            return EmbeddingResult(
                vectors=vectors,
                timing=EmbeddingTiming(0.0, 20.0, 25.0, 0.0, 30.0),
            )

    class Head(_OnlineHead):
        def predict_result(self, result, *, index=0):
            if float(result.vectors[index][0]) < 0:
                raise RuntimeError("one head failed")
            return super().predict_result(result, index=index)

    class Cache:
        def __init__(self):
            self.values = {}
            self.lock = Lock()

        def get(self, key):
            with self.lock:
                return self.values.get(key)

        def put(self, key, vector):
            with self.lock:
                self.values[key] = vector

    client = Client()
    latency = StageLatencyRecorder()
    batcher = runtime_module.OnlineEmbeddingMicrobatcher(client)
    predictor = OnlineEmbeddingPredictor(
        model_id="email-embedding-mlp-second",
        classifier=Head(),
        cache=Cache(),
        embedding_client=client,
        embedding_batcher=batcher,
        latency=latency,
        runtime_id="runtime-mixed",
    )
    barrier = Barrier(3)

    def classify(text):
        barrier.wait()
        return SequentialOnlineClassifier(
            mode=EmailClassifierRuntimeMode.MODEL_PRIMARY,
            model_predict=predictor,
            agent_classify=lambda value: (
                pytest.fail("Agent ran in parallel with remote embedding")
                if remote_active.is_set()
                else agent_calls.append(value.normalized_text) or "agent-result"
            ),
        ).classify(OnlineModelInput(text, "input-v3"))

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            good = pool.submit(classify, "good")
            bad = pool.submit(classify, "bad")
            barrier.wait()
            results = (good.result(timeout=1.0), bad.result(timeout=1.0))
    finally:
        batcher.close()

    assert len(remote_calls) == 1
    assert set(remote_calls[0]) == {"good", "bad"}
    assert sorted(item.source for item in results) == ["agent", "model"]
    assert agent_calls == ["bad"]
    assert latency.fallback_count == 1
    assert len(latency.raw_samples()) == 2
    assert {item["outcome"] for item in latency.raw_samples()} == {
        "success",
        "failure",
    }
    assert all(
        set(item)
        >= {
            "queue_ms",
            "http_ms",
            "embedding_ms",
            "head_ms",
            "total_ms",
            "cache_hit",
            "runtime_warm",
            "outcome",
        }
        for item in latency.raw_samples()
    )


@pytest.mark.parametrize(
    "current_input",
    (OnlineModelInput("current", "input-v2"), "not-an-online-input"),
)
def test_actual_online_input_failures_fallback_agent_once_and_record_timing(current_input):
    latency = StageLatencyRecorder()
    predictor = OnlineEmbeddingPredictor(
        model_id="email-embedding-mlp-second",
        classifier=_OnlineHead(),
        cache=_ExactCache(),
        embedding_client=_EmbeddingClient(),
        latency=latency,
    )
    calls = []
    router = SequentialOnlineClassifier(
        mode=EmailClassifierRuntimeMode.MODEL_PRIMARY,
        model_predict=predictor,
        agent_classify=lambda value: calls.append(value) or "agent-result",
    )
    try:
        result = router.classify(current_input)
    finally:
        predictor.close()

    assert result.source == "agent"
    assert calls == [current_input]
    assert latency.fallback_count == 1
    assert len(latency.raw_samples()) == 1
    assert latency.raw_samples()[0]["outcome"] == "failure"


def test_online_predictor_rejects_incomplete_or_version_mismatched_input():
    predictor = OnlineEmbeddingPredictor(
        model_id="email-embedding-mlp-second",
        classifier=_OnlineHead(),
        cache=_ExactCache(),
        embedding_client=_EmbeddingClient(),
    )

    with pytest.raises(ValueError, match="incomplete"):
        predictor(OnlineModelInput("", "input-v3"))
    with pytest.raises(ValueError, match="version mismatch"):
        predictor(OnlineModelInput("current", "input-v2"))


def test_promoted_runtime_is_agent_primary_without_verified_activation(tmp_path):
    registry = _ActivationRegistry(tmp_path / "registry", ())
    client_calls = []

    runtime = PromotedEmailClassifierRuntime(
        registry,
        embedding_client_factory=lambda _classifier: client_calls.append(True),
    )

    assert runtime.mode is EmailClassifierRuntimeMode.AGENT_PRIMARY
    assert runtime.model_id is None
    assert runtime.model_predict is None
    assert client_calls == []


def test_promoted_runtime_owns_one_resident_batcher_and_closes_it(tmp_path):
    first_id = "email-embedding-mlp-first"
    second_id = "email-embedding-mlp-second"
    first_bytes = b"first"
    second_bytes = b"second"
    registry = _ActivationRegistry(
        tmp_path / "registry",
        (
            _mature_evidence(first_id, sha256(first_bytes).hexdigest()),
            _mature_evidence(second_id, sha256(second_bytes).hexdigest()),
        ),
    )
    (registry.embedding_artifacts / f"{first_id}.artifact").write_bytes(first_bytes)
    artifact = registry.embedding_artifacts / f"{second_id}.artifact"
    artifact.write_bytes(second_bytes)
    activate_online_model(
        registry,
        second_id,
        classifier_loader=lambda _path: _LoadedOnlineModel(),
    )

    class Model(_LoadedOnlineModel):
        dimension = 2

    runtime = PromotedEmailClassifierRuntime(
        registry,
        classifier_loader=lambda path: Model() if path == artifact else None,
        embedding_client_factory=lambda _model: _EmbeddingClient(),
        cache_factory=lambda _model: _ExactCache(),
    )
    batcher = runtime.embedding_batcher

    runtime.refresh()

    assert batcher is not None
    assert runtime.embedding_batcher is batcher
    assert runtime.model_predict.embedding_batcher is batcher
    assert batcher.is_alive

    runtime.close()

    assert not batcher.is_alive
    assert runtime.embedding_batcher is None
    assert runtime.model_predict is None


def test_promoted_runtime_owned_generation_closes_client_once_on_revoke(tmp_path):
    first_id = "email-embedding-mlp-owned-first"
    model_id = "email-embedding-mlp-owned"
    first_bytes = b"owned-first"
    artifact_bytes = b"owned"
    registry = _ActivationRegistry(
        tmp_path / "registry",
        (
            _mature_evidence(first_id, sha256(first_bytes).hexdigest()),
            _mature_evidence(model_id, sha256(artifact_bytes).hexdigest()),
        ),
    )
    (registry.embedding_artifacts / f"{first_id}.artifact").write_bytes(first_bytes)
    artifact = registry.embedding_artifacts / f"{model_id}.artifact"
    artifact.write_bytes(artifact_bytes)

    class Model(_LoadedOnlineModel):
        dimension = 2

    class Client(_EmbeddingClient):
        def __init__(self):
            super().__init__()
            self.close_calls = 0

        def close(self):
            self.close_calls += 1

    client = Client()
    activate_online_model(registry, model_id, classifier_loader=lambda _path: Model())
    runtime = PromotedEmailClassifierRuntime(
        registry,
        classifier_loader=lambda _path: Model(),
        embedding_client_factory=lambda _model: client,
        embedding_client_owned=True,
        cache_factory=lambda _model: _ExactCache(),
    )

    (registry.root / "online-active.json").unlink()
    runtime.refresh()
    runtime.close()

    assert client.close_calls == 1


def test_promoted_runtime_closes_owned_client_when_generation_build_fails(tmp_path):
    first_id = "email-embedding-mlp-build-failure-first"
    model_id = "email-embedding-mlp-build-failure"
    first_bytes = b"build-failure-first"
    artifact_bytes = b"build-failure"
    registry = _ActivationRegistry(
        tmp_path / "registry",
        (
            _mature_evidence(first_id, sha256(first_bytes).hexdigest()),
            _mature_evidence(model_id, sha256(artifact_bytes).hexdigest()),
        ),
    )
    (registry.embedding_artifacts / f"{first_id}.artifact").write_bytes(first_bytes)
    (registry.embedding_artifacts / f"{model_id}.artifact").write_bytes(artifact_bytes)

    class Model(_LoadedOnlineModel):
        dimension = 2

    class Client(_EmbeddingClient):
        embedding_revision = "wrong-revision"
        close_calls = 0

        def close(self):
            self.close_calls += 1

    client = Client()
    activate_online_model(registry, model_id, classifier_loader=lambda _path: Model())

    runtime = PromotedEmailClassifierRuntime(
        registry,
        classifier_loader=lambda _path: Model(),
        embedding_client_factory=lambda _model: client,
        embedding_client_owned=True,
        cache_factory=lambda _model: _ExactCache(),
    )

    assert runtime.mode is EmailClassifierRuntimeMode.AGENT_PRIMARY
    assert client.close_calls == 1


def test_promoted_runtime_does_not_close_shared_factory_client_by_default(tmp_path):
    first_id = "email-embedding-mlp-shared-first"
    model_id = "email-embedding-mlp-shared"
    first_bytes = b"shared-first"
    artifact_bytes = b"shared"
    registry = _ActivationRegistry(
        tmp_path / "registry",
        (
            _mature_evidence(first_id, sha256(first_bytes).hexdigest()),
            _mature_evidence(model_id, sha256(artifact_bytes).hexdigest()),
        ),
    )
    (registry.embedding_artifacts / f"{first_id}.artifact").write_bytes(first_bytes)
    (registry.embedding_artifacts / f"{model_id}.artifact").write_bytes(artifact_bytes)

    class Model(_LoadedOnlineModel):
        dimension = 2

    class Client(_EmbeddingClient):
        close_calls = 0

        def close(self):
            self.close_calls += 1

    client = Client()
    activate_online_model(registry, model_id, classifier_loader=lambda _path: Model())
    runtime = PromotedEmailClassifierRuntime(
        registry,
        classifier_loader=lambda _path: Model(),
        embedding_client_factory=lambda _model: client,
        cache_factory=lambda _model: _ExactCache(),
    )

    runtime.close()
    runtime.close()

    assert client.close_calls == 0


def test_runtime_generation_drains_inflight_before_closing_owned_client():
    runtime_module = __import__(
        "app.email_classifier_runtime", fromlist=["_RuntimeGeneration"]
    )
    entered = Event()
    release = Event()

    class Client(_EmbeddingClient):
        def __init__(self):
            super().__init__()
            self.close_calls = 0

        def embed(self, texts, *, queued_at=None):
            entered.set()
            release.wait(1.0)
            return super().embed(texts, queued_at=queued_at)

        def close(self):
            self.close_calls += 1

    client = Client()
    batcher = runtime_module.OnlineEmbeddingMicrobatcher(
        client, maximum_wait_seconds=0.005
    )
    snapshot = RuntimeSnapshot.model_primary(
        predictor=lambda _value: None,
        model_id="owned-generation",
        input_schema_version="input-v3",
        compatibility={"embedding_revision": "r17"},
    )
    generation = runtime_module._RuntimeGeneration(
        snapshot=snapshot,
        predictor=snapshot.predictor,
        batcher=batcher,
        client=client,
        owns_client=True,
    )
    with ThreadPoolExecutor(max_workers=2) as pool:
        request = pool.submit(
            batcher.embed_one,
            "inflight",
            _batch_compatibility("owned-generation"),
        )
        assert entered.wait(0.5)
        retiring = pool.submit(generation.retire)
        time.sleep(0.02)
        assert client.close_calls == 0
        release.set()
        request.result(timeout=1.0)
        retiring.result(timeout=1.0)

    generation.retire()
    assert client.close_calls == 1
    assert not batcher.is_alive


def test_runtime_generation_never_closes_shared_injected_client():
    runtime_module = __import__(
        "app.email_classifier_runtime", fromlist=["_RuntimeGeneration"]
    )

    class Client(_EmbeddingClient):
        close_calls = 0

        def close(self):
            self.close_calls += 1

    client = Client()
    batcher = runtime_module.OnlineEmbeddingMicrobatcher(client)
    snapshot = RuntimeSnapshot.agent_primary()
    generation = runtime_module._RuntimeGeneration(
        snapshot=snapshot,
        predictor=None,
        batcher=batcher,
        client=client,
        owns_client=False,
    )

    generation.retire()
    generation.retire()

    assert client.close_calls == 0
    assert not batcher.is_alive


def test_promoted_runtime_continuous_generations_close_each_old_resource_once(tmp_path):
    model_ids = tuple(f"email-embedding-mlp-generation-{name}" for name in "abc")
    artifacts = {model_id: model_id.encode() for model_id in model_ids}
    predecessor = "email-embedding-mlp-generation-pre"
    predecessor_bytes = b"predecessor"
    registry = _ActivationRegistry(tmp_path / "registry", ())
    (registry.embedding_artifacts / f"{predecessor}.artifact").write_bytes(
        predecessor_bytes
    )
    for model_id, payload in artifacts.items():
        (registry.embedding_artifacts / f"{model_id}.artifact").write_bytes(payload)

    class Model(_LoadedOnlineModel):
        dimension = 2

    class Client(_EmbeddingClient):
        def __init__(self, model_id):
            super().__init__()
            self.generation_model_id = model_id
            self.close_calls = 0

        def close(self):
            self.close_calls += 1

    loaded_model_id = [""]
    clients = []

    def loader(path):
        loaded_model_id[0] = path.stem
        return Model()

    def client_factory(_model):
        client = Client(loaded_model_id[0])
        clients.append(client)
        return client

    previous_id = predecessor
    previous_bytes = predecessor_bytes
    runtime = None
    for model_id in model_ids:
        registry._evidence = (
            _mature_evidence(previous_id, sha256(previous_bytes).hexdigest()),
            _mature_evidence(model_id, sha256(artifacts[model_id]).hexdigest()),
        )
        activate_online_model(registry, model_id, classifier_loader=loader)
        if runtime is None:
            runtime = PromotedEmailClassifierRuntime(
                registry,
                classifier_loader=loader,
                embedding_client_factory=client_factory,
                embedding_client_owned=True,
                cache_factory=lambda _model: _ExactCache(),
            )
        else:
            runtime.refresh()
        previous_id = model_id
        previous_bytes = artifacts[model_id]

    assert runtime is not None
    assert runtime.snapshot().model_id == model_ids[-1]
    assert [client.close_calls for client in clients] == [1, 1, 0]
    runtime.close()
    runtime.close()
    assert [client.close_calls for client in clients] == [1, 1, 1]


def test_runtime_close_rejects_late_refresh_generation_and_closes_it_once(
    tmp_path, monkeypatch
):
    runtime_module = __import__(
        "app.email_classifier_runtime", fromlist=["OnlineEmbeddingPredictor"]
    )
    first_id = "email-embedding-mlp-close-race-first"
    second_id = "email-embedding-mlp-close-race-second"
    predecessor = "email-embedding-mlp-close-race-pre"
    payloads = {
        predecessor: b"pre",
        first_id: b"first",
        second_id: b"second",
    }
    registry = _ActivationRegistry(tmp_path / "registry", ())
    for model_id, payload in payloads.items():
        (registry.embedding_artifacts / f"{model_id}.artifact").write_bytes(payload)

    class Model(_LoadedOnlineModel):
        dimension = 2

    class Client(_EmbeddingClient):
        def __init__(self):
            super().__init__()
            self.close_calls = 0

        def close(self):
            self.close_calls += 1

    clients = []

    def client_factory(_model):
        client = Client()
        clients.append(client)
        return client

    registry._evidence = (
        _mature_evidence(predecessor, sha256(payloads[predecessor]).hexdigest()),
        _mature_evidence(first_id, sha256(payloads[first_id]).hexdigest()),
    )
    activate_online_model(registry, first_id, classifier_loader=lambda _path: Model())
    runtime = PromotedEmailClassifierRuntime(
        registry,
        classifier_loader=lambda _path: Model(),
        embedding_client_factory=client_factory,
        embedding_client_owned=True,
        cache_factory=lambda _model: _ExactCache(),
    )
    first_batcher = runtime.embedding_batcher

    registry._evidence = (
        _mature_evidence(first_id, sha256(payloads[first_id]).hexdigest()),
        _mature_evidence(second_id, sha256(payloads[second_id]).hexdigest()),
    )
    activate_online_model(registry, second_id, classifier_loader=lambda _path: Model())
    built = Event()
    release = Event()
    late_batchers = []
    real_predictor = runtime_module.OnlineEmbeddingPredictor

    def blocking_predictor(**kwargs):
        predictor = real_predictor(**kwargs)
        late_batchers.append(predictor.embedding_batcher)
        built.set()
        release.wait(1.0)
        return predictor

    monkeypatch.setattr(runtime_module, "OnlineEmbeddingPredictor", blocking_predictor)
    with ThreadPoolExecutor(max_workers=1) as pool:
        refreshing = pool.submit(runtime.refresh)
        assert built.wait(0.5)
        runtime.close()
        assert runtime.snapshot().mode is EmailClassifierRuntimeMode.AGENT_PRIMARY
        release.set()
        refreshing.result(timeout=1.0)

    runtime.close()
    runtime.refresh()
    runtime.tick()

    assert runtime.snapshot().mode is EmailClassifierRuntimeMode.AGENT_PRIMARY
    assert runtime.embedding_batcher is None
    assert first_batcher is not None and not first_batcher.is_alive
    assert len(late_batchers) == 1 and not late_batchers[0].is_alive
    assert [client.close_calls for client in clients] == [1, 1]


def test_promoted_runtime_tick_adopts_and_revokes_atomic_activation_without_restart(
    tmp_path,
):
    first_id = "email-embedding-mlp-first"
    model_id = "email-embedding-mlp-live"
    first_bytes = b"first-model"
    artifact_bytes = b"live-model"
    registry = _ActivationRegistry(
        tmp_path / "registry",
        (
            _mature_evidence(first_id, sha256(first_bytes).hexdigest()),
            _mature_evidence(model_id, sha256(artifact_bytes).hexdigest()),
        ),
    )
    (registry.embedding_artifacts / f"{first_id}.artifact").write_bytes(first_bytes)
    artifact = registry.embedding_artifacts / f"{model_id}.artifact"
    artifact.write_bytes(artifact_bytes)
    polls = []

    class Model(_LoadedOnlineModel):
        dimension = 2

    runtime = PromotedEmailClassifierRuntime(
        registry,
        learning_service=SimpleNamespace(
            poll_retrain=lambda **kwargs: polls.append(kwargs) or "polled"
        ),
        classifier_loader=lambda path: Model() if path == artifact else None,
        embedding_client_factory=lambda _model: _EmbeddingClient(),
        cache_factory=lambda _model: _ExactCache(),
    )
    assert runtime.snapshot().mode is EmailClassifierRuntimeMode.AGENT_PRIMARY

    activate_online_model(registry, model_id, classifier_loader=lambda _path: Model())
    assert runtime.tick() == "polled"
    promoted = runtime.snapshot()
    assert isinstance(promoted, RuntimeSnapshot)
    assert promoted.mode is EmailClassifierRuntimeMode.MODEL_PRIMARY
    assert promoted.model_id == model_id
    old_batcher = promoted.predictor.embedding_batcher

    (registry.root / "online-active.json").unlink()
    runtime.tick()
    assert runtime.snapshot().mode is EmailClassifierRuntimeMode.AGENT_PRIMARY
    assert not old_batcher.is_alive

    activate_online_model(registry, model_id, classifier_loader=lambda _path: Model())
    runtime.tick()
    artifact.write_bytes(b"tampered")
    runtime.tick()
    assert runtime.snapshot().mode is EmailClassifierRuntimeMode.AGENT_PRIMARY
    assert len(polls) == 4


def test_runtime_snapshot_is_immutable():
    snapshot = RuntimeSnapshot.model_primary(
        predictor=lambda _value: None,
        model_id="immutable-model",
        input_schema_version="input-v3",
        compatibility={"enabled_categories": ["work"]},
    )

    with pytest.raises(AttributeError):
        snapshot.mode = EmailClassifierRuntimeMode.MODEL_PRIMARY
    with pytest.raises(TypeError):
        snapshot.compatibility["revision"] = "changed"
    with pytest.raises(AttributeError):
        snapshot.compatibility["enabled_categories"].append("legal")


def _model() -> CpuTfidfLogisticClassifier:
    return CpuTfidfLogisticClassifier(model_version="runtime-test").fit(
        ["work project", "work meeting", "junk promotion", "junk offer"],
        ["work", "work", "junk", "junk"],
        enabled_category_keys=("junk", "work"),
    )


def _legacy_reserved_model(path: Path) -> None:
    texts = ["urgent approval", "urgent contract", "project plan", "team meeting"]
    labels = ["important", "important", "work", "work"]
    vectorizer = TfidfVectorizer(token_pattern=r"\S+")
    classifier = LogisticRegression(random_state=42).fit(
        vectorizer.fit_transform(texts), labels
    )
    path.write_bytes(
        pickle.dumps(
            {
                "format_version": CpuTfidfLogisticClassifier.FORMAT_VERSION,
                "feature_version": CpuTfidfLogisticClassifier.FEATURE_VERSION,
                "c": 0.25,
                "model_version": "legacy-reserved-v1",
                "vectorizer": vectorizer,
                "classifier": classifier,
            }
        )
    )


def test_runtime_rejects_reserved_legacy_artifact_before_scan_adoption(tmp_path: Path):
    active = tmp_path / "legacy-reserved.pkl"
    _legacy_reserved_model(active)

    with pytest.raises(EmailClassifierUnavailable, match="no valid"):
        load_active_classifier(active, tmp_path / "missing-previous.pkl")


def test_runtime_prefers_active_model(tmp_path: Path):
    active = tmp_path / "model.active.pkl"
    previous = tmp_path / "model.previous.pkl"
    _model().save(active)
    _model().save(previous)

    loaded = load_active_classifier(active, previous)

    assert loaded.path == active
    assert loaded.used_previous is False
    assert loaded.classifier.model_version == "runtime-test"


def test_runtime_falls_back_to_previous_when_active_is_invalid(tmp_path: Path):
    active = tmp_path / "model.active.pkl"
    previous = tmp_path / "model.previous.pkl"
    active.write_bytes(b"not a model")
    _model().save(previous)

    loaded = load_active_classifier(active, previous)

    assert loaded.path == previous
    assert loaded.used_previous is True


def test_runtime_fails_explicitly_when_no_model_is_valid(tmp_path: Path):
    with pytest.raises(EmailClassifierUnavailable, match="no valid"):
        load_active_classifier(
            tmp_path / "model.active.pkl", tmp_path / "model.previous.pkl"
        )


class FakeReadonlySource:
    def fetch_recent(self, mailbox: str = "INBOX", *, limit: int = 50):
        return [
            {
                "messageId": "message-1",
                "accountId": "test-account",
                "folder": mailbox,
                "uidValidity": 1,
                "uid": 1,
                "from": {"email": "team@example.com"},
                "subject": "work project",
                "textBody": "work meeting",
            }
        ]


def test_runtime_loads_model_and_runs_only_readonly_scan(tmp_path: Path):
    active = tmp_path / "registry-model.pkl"
    model = _model()
    model.save(active)

    class FakeRegistry(EmailModelRegistry):
        def __init__(self):
            pass

        def active_model_id_unverified(self):
            return "runtime-test"

        def active_manifest(self):
            return SimpleNamespace(model_id="runtime-test")

        def load_classifier(self, model_id):
            return model

        def get_model(self, model_id):
            return SimpleNamespace(artifact_path=active)

    registry = FakeRegistry()
    config = EmailScanConfig(
        config_version="runtime-scan-v1",
        thresholds={category: 0.0 for category in INITIAL_EMAIL_CATEGORY_KEYS},
        actions={},
        category_eligibility={
            category: CategoryEligibility(
                category=category,
                configured_threshold=0.0,
                validated_precision=1.0,
                validation_sample_count=30,
                auto_action_eligible=True,
                reason="precision_and_sample_gate_met",
            )
            for category in INITIAL_EMAIL_CATEGORY_KEYS
        },
    )

    runtime = EmailClassifierRuntime(registry)
    result = scan_with_active_model(
        FakeReadonlySource(),
        EmailStore(tmp_path / "email.sqlite3"),
        config,
        runtime=runtime,
        limit=1,
    )

    assert result.loaded.path == active
    assert result.scan.persisted_count == 1


def test_runtime_zero_mail_scan_still_polls_learning_with_cycle_clock(tmp_path: Path):
    model = _model()
    now = datetime(2026, 8, 29, 22, 0, tzinfo=timezone.utc)

    class Registry:
        def active_model_id_unverified(self):
            return "active"

        def active_manifest(self):
            return SimpleNamespace(model_id="active")

        def load_classifier(self, _model_id):
            return model

        def get_model(self, _model_id):
            return SimpleNamespace(artifact_path=tmp_path / "active.pkl")

    class Learning:
        def __init__(self):
            self.polls = []

        def poll_retrain(self, *, now=None):
            self.polls.append(now)

    class EmptySource:
        def fetch_recent(self, mailbox="INBOX", *, limit=50):
            return []

    learning = Learning()
    runtime = EmailClassifierRuntime(Registry(), learning_service=learning)

    result = scan_with_active_model(
        EmptySource(),
        EmailStore(tmp_path / "empty.sqlite3"),
        EmailScanConfig.cold_start(),
        runtime=runtime,
        now=now,
    )

    assert result.scan.persisted_count == 0
    assert learning.polls == [now]


def test_runtime_adopts_promoted_active_model_on_next_tick(tmp_path: Path):
    models = {
        "email-tfidf-lr-20260829T220000Z-11111111": _model(),
        "email-tfidf-lr-20260829T220100Z-22222222": _model(),
    }

    class Registry:
        def __init__(self):
            self.active_id = "email-tfidf-lr-20260829T220000Z-11111111"
            self.loads = []

        def active_model_id_unverified(self):
            return self.active_id

        def active_manifest(self):
            return SimpleNamespace(model_id=self.active_id)

        def load_classifier(self, model_id):
            self.loads.append(model_id)
            return models[model_id]

        def get_model(self, model_id):
            return SimpleNamespace(artifact_path=tmp_path / f"{model_id}.pkl")

    class Learning:
        def __init__(self, registry):
            self.registry = registry

        def poll_retrain(self, *, now=None):
            self.registry.active_id = "email-tfidf-lr-20260829T220100Z-22222222"
            return SimpleNamespace(training_run=SimpleNamespace(status="succeeded"))

    registry = Registry()
    runtime = EmailClassifierRuntime(registry, learning_service=Learning(registry))

    runtime.tick(now=datetime(2026, 8, 29, 22, 1, tzinfo=timezone.utc))

    assert runtime.loaded.classifier.model_id == (
        "email-tfidf-lr-20260829T220100Z-22222222"
    )
    assert runtime.loaded.path.name == ("email-tfidf-lr-20260829T220100Z-22222222.pkl")
    assert registry.loads == [
        "email-tfidf-lr-20260829T220000Z-11111111",
        "email-tfidf-lr-20260829T220100Z-22222222",
    ]


@pytest.mark.parametrize("terminal_status", ["rejected", "failed"])
def test_runtime_keeps_loaded_model_when_training_does_not_promote(
    tmp_path: Path, terminal_status: str
):
    model_id = "email-tfidf-lr-20260829T220000Z-11111111"
    model = _model()

    class Registry:
        def __init__(self):
            self.loads = []

        def active_model_id_unverified(self):
            return model_id

        def active_manifest(self):
            return SimpleNamespace(model_id=model_id)

        def load_classifier(self, requested_model_id):
            self.loads.append(requested_model_id)
            return model

        def get_model(self, requested_model_id):
            return SimpleNamespace(artifact_path=tmp_path / f"{requested_model_id}.pkl")

    class Learning:
        def poll_retrain(self, *, now=None):
            return SimpleNamespace(training_run=SimpleNamespace(status=terminal_status))

    registry = Registry()
    runtime = EmailClassifierRuntime(registry, learning_service=Learning())
    original = runtime.loaded

    runtime.tick(now=datetime(2026, 8, 29, 22, 1, tzinfo=timezone.utc))

    assert runtime.loaded is original
    assert registry.loads == [model_id]


def test_runtime_reuses_failure_counter_across_three_scan_calls(tmp_path: Path):
    model = _model()

    class Broken:
        def predict_message(self, message):
            raise RuntimeError("broken")

    class Registry:
        def __init__(self):
            self.fallbacks = 0
            self.active_id = "active"

        def active_model_id_unverified(self):
            return self.active_id

        def active_manifest(self):
            return SimpleNamespace(model_id=self.active_id)

        def load_classifier(self, model_id):
            return Broken() if model_id == "active" else model

        def get_model(self, model_id):
            return SimpleNamespace(artifact_path=tmp_path / f"{model_id}.pkl")

        def fallback_to_previous(self, **_values):
            self.fallbacks += 1
            self.active_id = "previous"
            return SimpleNamespace(model_id="previous")

    registry = Registry()
    runtime = EmailClassifierRuntime(registry)
    store = EmailStore(tmp_path / "persistent.sqlite3")
    config = EmailScanConfig.cold_start()

    for _ in range(2):
        with pytest.raises(RuntimeError, match="broken"):
            scan_with_active_model(
                FakeReadonlySource(), store, config, runtime=runtime, limit=1
            )
    result = scan_with_active_model(
        FakeReadonlySource(), store, config, runtime=runtime, limit=1
    )

    assert registry.fallbacks == 1
    assert result.scan.persisted_count == 1
    assert result.loaded.model_id == "previous"
    assert result.loaded.path == tmp_path / "previous.pkl"


def test_task8_runtime_factory_returns_one_runtime_instance():
    created = []
    factory = EmailClassifierRuntimeFactory(
        lambda: created.append(object()) or created[-1]
    )

    assert factory.get() is factory.get()
    assert len(created) == 1


def test_consecutive_prediction_failures_fallback_only_at_threshold():
    class Broken:
        def predict(self, text):
            raise RuntimeError("broken")

    class Previous:
        def predict(self, text):
            return "previous:" + text

    class Registry:
        def __init__(self):
            self.fallbacks = []

        def fallback_to_previous(self, **values):
            self.fallbacks.append(values)
            return SimpleNamespace(model_id="previous-model")

        def load_classifier(self, model_id):
            return Previous()

    registry = Registry()
    classifier = RegistryPredictionClassifier(
        registry, Broken(), "active-model", failure_threshold=3
    )

    with pytest.raises(RuntimeError):
        classifier.predict("one")
    with pytest.raises(RuntimeError):
        classifier.predict("two")
    assert registry.fallbacks == []
    assert classifier.predict("three") == "previous:three"
    assert registry.fallbacks == [
        {
            "reason": "active_prediction_failed_repeatedly",
            "failed_model_id": "active-model",
        }
    ]


def test_runtime_fallback_prediction_cannot_reuse_previous_source_eligibility(
    tmp_path: Path,
):
    model_a = "email-model:active-a"
    model_b = "email-model:fallback-b"

    class BrokenActive:
        def predict_message(self, _message):
            raise RuntimeError("active model failed")

    class FallbackModel:
        def predict_message(self, _message):
            return EmailModelPrediction(
                label=EmailCategory.WORK.value,
                probability=0.999,
                margin=0.99,
                probabilities={EmailCategory.WORK.value: 0.999},
                model_version=model_b,
            )

    class Registry:
        def fallback_to_previous(self, **_values):
            return SimpleNamespace(model_id=model_b)

        def load_classifier(self, model_id):
            assert model_id == model_b
            return FallbackModel()

    eligibility = {
        category: CategoryEligibility(
            category=category,
            configured_threshold=0.95,
            validated_precision=0.99 if category == EmailCategory.WORK.value else None,
            validation_sample_count=30 if category == EmailCategory.WORK.value else 0,
            auto_action_eligible=category == EmailCategory.WORK.value,
            reason=(
                "precision_and_sample_gate_met"
                if category == EmailCategory.WORK.value
                else "model_eligibility_missing"
            ),
            source_model_id=model_a,
            action_eligibility=(
                {
                    EmailAction.LABEL: EmailActionEligibility(
                        action=EmailAction.LABEL,
                        auto_action_eligible=True,
                        reason="action_precision_and_support_gate_met",
                        source_model_id=model_a,
                        evidence_reference="email-model-eligibility:model-a:label",
                    )
                }
                if category == EmailCategory.WORK.value
                else {}
            ),
        )
        for category in INITIAL_EMAIL_CATEGORY_KEYS
    }
    config = EmailScanConfig(
        config_version="email-config:fallback-boundary-v1",
        thresholds={category: 0.95 for category in INITIAL_EMAIL_CATEGORY_KEYS},
        actions={EmailCategory.WORK: (EmailAction.LABEL,)},
        category_eligibility=eligibility,
        action_parameters={
            EmailCategory.WORK: {EmailAction.LABEL: {"labels": ["Work"]}}
        },
    )
    classifier = RegistryPredictionClassifier(
        Registry(),
        BrokenActive(),
        model_a,
        failure_threshold=1,
    )
    store = EmailStore(tmp_path / "fallback-boundary.sqlite3")

    result = scan_readonly_batch(
        FakeReadonlySource(),
        classifier,
        store,
        config,
        limit=1,
    )

    assert result.processed_count == 0
    assert result.pending_feedback_count == 1
    rows, total = store.list_classifications(
        status=EmailClassificationStatus.PENDING_FEEDBACK,
        limit=10,
        offset=0,
    )
    assert total == 1
    assert rows[0]["model_id"] == model_b
    assert rows[0]["action_plan"] is None


def test_training_subprocess_is_nonblocking_and_durably_polled(tmp_path: Path):
    class FakeProcess:
        pid = 4321

        def __init__(self):
            self.exit_code = None

        def poll(self):
            return self.exit_code

    process = FakeProcess()
    controller = TrainingSubprocessController(
        EmailModelRegistry(tmp_path / "registry"),
        launcher=lambda command: process,
    )
    now = datetime(2026, 8, 29, 21, 0, tzinfo=timezone.utc)

    run = controller.start(["python", "-m", "trainer"], now=now)
    assert run.status == "running"
    assert controller.poll(run.run_id, now=now).status == "running"
    process.exit_code = 0
    terminal = controller.poll(run.run_id, now=now)
    assert terminal.status == "failed"
    assert terminal.reason == "training_subprocess_exited_without_result:0"


def test_training_subprocess_launcher_failure_is_durable_and_retryable(tmp_path: Path):
    registry = EmailModelRegistry(tmp_path / "registry")
    controller = TrainingSubprocessController(
        registry,
        launcher=lambda _command: (_ for _ in ()).throw(OSError("no launcher")),
    )
    now = datetime(2026, 8, 29, 21, 0, tzinfo=timezone.utc)

    with pytest.raises(OSError, match="no launcher"):
        controller.start(["python", "-m", "trainer"], now=now)

    run_paths = list(registry.runs.glob("*.json"))
    assert len(run_paths) == 1
    run = controller._load_run(run_paths[0].stem)
    assert run.status == "failed"
    assert run.finished_at == now.isoformat()
    assert run.reason == "training_subprocess_launch_failed:OSError"


def test_controller_restart_fails_orphaned_running_record_and_allows_learning_retry(
    tmp_path: Path,
):
    now = datetime(2026, 8, 29, 21, 0, tzinfo=timezone.utc)
    registry = EmailModelRegistry(tmp_path / "registry")
    first = TrainingSubprocessController(
        registry,
        launcher=lambda _command: SimpleNamespace(pid=4321, poll=lambda: None),
    )
    run = first.start(["python", "-m", "trainer"], now=now)
    restarted = TrainingSubprocessController(
        registry,
        pid_is_alive=lambda pid: False,
    )

    failed = restarted.poll(run.run_id, now=now)

    assert failed.status == "failed"
    assert failed.reason == "training_subprocess_orphaned"


def test_controller_restart_keeps_known_live_pid_running_past_stale_timeout(
    tmp_path: Path,
):
    now = datetime(2026, 8, 29, 21, 0, tzinfo=timezone.utc)
    registry = EmailModelRegistry(tmp_path / "registry")
    first = TrainingSubprocessController(
        registry,
        launcher=lambda _command: SimpleNamespace(pid=4321, poll=lambda: None),
    )
    run = first.start(["python", "-m", "trainer"], now=now)
    restarted = TrainingSubprocessController(
        registry,
        pid_is_alive=lambda pid: True,
        stale_after_seconds=1,
    )

    observed = restarted.poll(run.run_id, now=now.replace(hour=22))

    assert observed.status == "running"
    assert observed.run_id == run.run_id
