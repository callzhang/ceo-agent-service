import importlib
import json
from hashlib import sha256
from types import SimpleNamespace

import numpy as np
import pytest

from app.email_embedding_client import EmbeddingResult, EmbeddingTiming
from app.email_training_snapshot import _model_input


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


class Client:
    model_id = "jinaai/jina-embeddings-v5-text-small"
    embedding_revision = "r1"

    def __init__(self, clock, fail=False):
        self.clock, self.fail = clock, fail
        self.texts = []
        self.closed = False

    def embed(self, texts, **kwargs):
        self.texts.extend(texts)
        self.clock.now += 0.4
        if self.fail:
            raise RuntimeError("private endpoint detail must not escape")
        return EmbeddingResult(
            np.array([[1.0, 0.0]], dtype=np.float32),
            EmbeddingTiming(0.0, 400.0, 400.0, 0.0, 400.0),
        )

    def close(self):
        self.closed = True


class Classifier:
    model_id = Client.model_id
    embedding_model_id = Client.model_id
    embedding_revision = "r1"
    input_schema_version = "email-folder-model-input-v2"
    dimension = 2

    def __init__(self, clock):
        self.clock = clock

    def predict_result(self, embedded, index=0):
        self.clock.now += 0.01
        # A declined classification is still a completed timing measurement.
        return SimpleNamespace(
            prediction=SimpleNamespace(category_accepted=False),
            timing=EmbeddingTiming(
                0.0,
                embedded.timing.http_ms,
                embedded.timing.embedding_ms,
                10.0,
                embedded.timing.total_ms + 10.0,
            ),
        )


def row():
    payload = {
        "input_schema_version": Classifier.input_schema_version,
        "sender": {"name": "Sender", "email": "sender@example.test"},
        "to_recipients": [{"email": "to@example.test"}],
        "cc_recipients": [],
        "subject": "Example",
        "body": "Example body",
    }
    raw = _model_input(payload)[0]
    return {
        "normalized_model_input": raw,
        "normalized_model_input_hash": sha256(raw.encode()).hexdigest(),
    }


def test_benchmark_measures_verified_canonical_input_remote_and_cache(
    monkeypatch,
):
    module = importlib.import_module("app.email_candidate_benchmark")
    clock = Clock()
    client = Client(clock)
    original = module._online_input

    def timed_builder(row, schema):
        result = original(row, schema)
        clock.now += 0.02
        return result

    monkeypatch.setattr(module, "_online_input", timed_builder)
    result = module.benchmark_candidate(
        Classifier(clock),
        [row()] * 5,
        client_factory=lambda **kwargs: client,
        clock=clock,
        max_samples=2,
    )
    assert result["status"] == "measured"
    remote = result["end_to_end_latency_ms"]
    assert remote["protocol"] == "email-training-input-to-prediction-v2"
    assert remote["input_contract_verified"] is True
    assert remote["boundary"] == "canonical_snapshot_message_to_prediction"
    assert remote["runtime_warm"] is True
    assert remote["cache_hit"] is False
    assert remote["stages"] == [
        "input_build",
        "cache_lookup",
        "queue",
        "http",
        "embedding",
        "head",
    ]
    assert remote["sample_count"] == 2
    assert remote["p95"] == pytest.approx(430.0)
    assert result["cache_latency_ms"]["p95"] == pytest.approx(30.0)
    assert result["cache_latency_ms"]["cache_hit"] is True
    assert len(result["raw_timings"]) == 4
    assert all(
        sample["runtime_warm"] and sample["input_contract_verified"]
        for sample in result["raw_timings"]
    )
    assert [sample["cache_hit"] for sample in result["raw_timings"]] == [
        False,
        True,
    ] * 2
    assert len(client.texts) == 3
    assert set(client.texts) == {row()["normalized_model_input"]}
    assert client.closed
    assert "sender@example.test" not in json.dumps(result)


@pytest.mark.parametrize(
    "case", ["missing_config", "remote_failure", "bad_input", "empty", "input_mismatch"]
)
def test_benchmark_failure_is_unmeasured_and_releases_client(monkeypatch, case):
    module = importlib.import_module("app.email_candidate_benchmark")
    clock = Clock()
    client = Client(clock, fail=case == "remote_failure")
    monkeypatch.delenv("CEO_EMAIL_EMBEDDING_URL", raising=False)
    kwargs = {} if case == "missing_config" else {"client_factory": lambda **kw: client}
    rows = [] if case == "empty" else [row()]
    if case == "bad_input":
        rows[0]["normalized_model_input"] = "{}"
    if case == "input_mismatch":
        rows[0]["normalized_model_input"] += " "
        rows[0]["normalized_model_input_hash"] = sha256(
            rows[0]["normalized_model_input"].encode()
        ).hexdigest()
    result = module.benchmark_candidate(Classifier(clock), rows, clock=clock, **kwargs)
    assert result["status"] == "unmeasured"
    assert result["reason"]
    assert "end_to_end_latency_ms" not in result
    assert "private endpoint" not in json.dumps(result)
    if case == "input_mismatch":
        assert result["reason"] == "input_contract_mismatch"
        assert not client.texts
        assert not client.closed
    if case == "remote_failure":
        assert client.closed
    if case == "missing_config":
        assert result["reason"] == "embedding_configuration_unavailable"
