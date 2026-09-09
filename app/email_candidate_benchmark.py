"""Bounded resident-candidate timing without mailbox reads or classification actions.

Measurement requires byte-identical training and serving inputs. The online scan
and frozen snapshots share the canonical model-input builder. Rebuild frozen
fields with that same builder and prove exact equality before client creation.
Original provider fetch/MIME decoding and actions are outside this boundary.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from contextlib import ExitStack
from hashlib import sha256
import json
from pathlib import Path
import tempfile
import time

import numpy as np

from app.email_classifier_runtime import (
    OnlineEmbeddingPredictor,
    OnlineModelInput,
    StageLatencyRecorder,
)
from app.email_embedding_cache import EmbeddingCache
from app.email_embedding_client import EmailEmbeddingClient
from app.email_training_snapshot import canonical_model_input


BENCHMARK_PROTOCOL = "email-training-input-to-prediction-v2"
BENCHMARK_BOUNDARY = "canonical_snapshot_message_to_prediction"
BENCHMARK_STAGES = ("input_build", "cache_lookup", "queue", "http", "embedding", "head")
MAX_BENCHMARK_SAMPLES = 32


class InputContractMismatch(ValueError):
    """The online builder does not reproduce the candidate's training input."""


def _online_input(row: Mapping[str, object], schema: str) -> OnlineModelInput:
    raw = row["normalized_model_input"]
    if (
        type(raw) is not str
        or sha256(raw.encode()).hexdigest() != row["normalized_model_input_hash"]
    ):
        raise ValueError("snapshot input digest mismatch")
    payload = json.loads(raw)
    if not isinstance(payload, dict) or payload.get("input_schema_version") != schema:
        raise ValueError("snapshot input schema mismatch")
    # Snapshot JSON stores the already-derived unsubscribe features under
    # "unsubscribe"; feed them through the builder's existing validated input.
    online_text = canonical_model_input(
        {
            **payload,
            "unsubscribe_features": payload["unsubscribe"],
        }
    )
    if online_text != raw:
        raise InputContractMismatch("training and serving input representations differ")
    return OnlineModelInput(online_text, schema)


def _summary(samples: Sequence[Mapping[str, object]]) -> dict[str, object]:
    totals = [float(sample["total_ms"]) for sample in samples]
    return {
        "protocol": BENCHMARK_PROTOCOL,
        "boundary": BENCHMARK_BOUNDARY,
        "runtime_warm": all(sample["runtime_warm"] is True for sample in samples),
        "input_contract_verified": all(
            sample["input_contract_verified"] is True for sample in samples
        ),
        "cache_hit": all(sample["cache_hit"] is True for sample in samples),
        "stages": list(BENCHMARK_STAGES),
        "sample_count": len(totals),
        **{
            key: float(np.percentile(totals, percentile))
            for key, percentile in (("p50", 50), ("p95", 95), ("p99", 99))
        },
    }


def benchmark_candidate(
    classifier: object,
    test_rows: Sequence[Mapping[str, object]],
    *,
    client_factory: Callable[..., object] = EmailEmbeddingClient.from_environment,
    clock: Callable[[], float] = time.perf_counter,
    max_samples: int = MAX_BENCHMARK_SAMPLES,
) -> dict[str, object]:
    """Time warm forced-miss and cache-hit paths; only remote totals feed the gate.

    A private temporary disk cache uses production cache code. Every measured
    pair starts with a new cache, even for duplicate text; no training/online
    cache is cleared. Failed measurements yield no end-to-end percentile.
    """
    reason = "benchmark_input_invalid"
    try:
        if (
            type(max_samples) is not int
            or not 1 <= max_samples <= MAX_BENCHMARK_SAMPLES
        ):
            raise ValueError("benchmark sample bound is invalid")
        if not test_rows:
            return {"status": "unmeasured", "reason": "no_test_rows"}
        indices = np.linspace(
            0, len(test_rows) - 1, min(len(test_rows), max_samples), dtype=int
        )
        selected = [test_rows[int(index)] for index in indices]
        schema = str(classifier.input_schema_version)
        warm_input = _online_input(selected[0], schema)
        reason = "embedding_configuration_unavailable"
        with ExitStack() as resources:
            client = client_factory(
                embedding_revision=classifier.embedding_revision,
                dimension=classifier.dimension,
            )
            resources.callback(client.close)
            root = Path(
                resources.enter_context(
                    tempfile.TemporaryDirectory(prefix="email-candidate-benchmark-")
                )
            )
            recorder = StageLatencyRecorder()
            predictor = OnlineEmbeddingPredictor(
                model_id="candidate-benchmark",
                classifier=classifier,
                cache=EmbeddingCache(root / "warmup", dimension=classifier.dimension),
                embedding_client=client,
                latency=recorder,
                clock=clock,
            )
            resources.callback(predictor.close)
            reason = "candidate_benchmark_failed"
            predictor(warm_input)  # warm HTTP client, batcher, cache code and head
            samples = []
            for index, row in enumerate(selected):
                predictor.cache = EmbeddingCache(
                    root / str(index), dimension=classifier.dimension
                )
                for path in ("remote", "cache"):
                    started = clock()
                    value = _online_input(row, schema)
                    input_finished = clock()
                    predictor(value)
                    finished = clock()
                    runtime = recorder.raw_samples()[-1]
                    if (
                        runtime["cache_hit"] != (path == "cache")
                        or not runtime["runtime_warm"]
                    ):
                        raise ValueError("benchmark path did not match measurement")
                    samples.append(
                        {
                            **runtime,
                            "path": path,
                            "input_contract_verified": True,
                            "sample_index": index,
                            "input_build_ms": (input_finished - started) * 1000.0,
                            "prediction_total_ms": runtime["total_ms"],
                            "total_ms": (finished - started) * 1000.0,
                        }
                    )
            return {
                "status": "measured",
                "boundary": BENCHMARK_BOUNDARY,
                "warmup_sample_count": 1,
                "end_to_end_latency_ms": _summary(
                    [row for row in samples if row["path"] == "remote"]
                ),
                "cache_latency_ms": _summary(
                    [row for row in samples if row["path"] == "cache"]
                ),
                "raw_timings": samples,
            }
    except Exception as exc:
        # Benchmark availability must not discard a successfully trained artifact.
        # Exception messages can contain private request/endpoint data.
        return {
            "status": "unmeasured",
            "reason": "input_contract_mismatch"
            if isinstance(exc, InputContractMismatch)
            else reason,
            "error_type": type(exc).__name__,
        }
