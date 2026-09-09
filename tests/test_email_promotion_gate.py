from types import SimpleNamespace

import pytest

from app import email_classifier_runtime as runtime


def evidence():
    return {
        "model_id": "candidate",
        "compatibility": {"enabled_categories": ["work"], "description_version": "d1"},
        "metrics": {"categories": {"work": {"precision": .98, "f1": .97, "support": 25}}},
        "end_to_end_latency_ms": measured_latency(),
    }


def measured_latency():
    return {
        "protocol": "email-training-input-to-prediction-v2",
        "input_contract_verified": True,
        "boundary": "canonical_snapshot_message_to_prediction",
        "runtime_warm": True, "cache_hit": False,
        "stages": ["input_build", "cache_lookup", "queue", "http", "embedding", "head"],
        "p50": 100., "p95": 450., "p99": 480., "sample_count": 30,
    }


def assess(row, **kwargs):
    return runtime.assess_online_promotion_gate(
        evidence=row,
        config={"config_version": "v1", "macro_f1_min": .95,
                "category_precision_min": .95, "category_validation_samples_min": 20,
                "p95_latency_max_ms": 500.},
        enabled_category_keys=("work",),
        description_version="d1",
        readiness=SimpleNamespace(ready=True, passing_model_ids=("previous", "candidate"), reason="ready"),
        registry_issues=kwargs.get("registry_issues", ()),
        artifact_verified=True,
    )


def test_gate_accepts_complete_evidence():
    result = assess(evidence())
    assert result["promotion_eligible"] is True
    assert all(check["passed"] for check in result["checks"])


@pytest.mark.parametrize("field,value", [("precision", .94), ("support", 19), ("f1", .94), ("support", None)])
def test_gate_requires_each_class_metric(field, value):
    row = evidence()
    row["metrics"]["categories"]["work"][field] = value
    assert assess(row)["promotion_eligible"] is False


def test_gate_does_not_treat_head_latency_as_end_to_end():
    row = evidence()
    del row["end_to_end_latency_ms"]
    row["latency_ms"] = {"p95": 1.0}
    result = assess(row)
    assert result["promotion_eligible"] is False
    check = next(c for c in result["checks"] if c["key"] == "p95_latency")
    assert check["actual"] is None


def test_gate_rejects_changed_description_and_registry_error():
    row = evidence()
    row["compatibility"]["description_version"] = "old"
    assert assess(row)["promotion_eligible"] is False
    assert assess(evidence(), registry_issues=({"integrity_error": "corrupt"},))["promotion_eligible"] is False


def test_negative_latency_is_invalid_evidence():
    row = evidence()
    row["end_to_end_latency_ms"]["p95"] = -1
    assert assess(row)["promotion_eligible"] is False


@pytest.mark.parametrize("field,value", [
    ("protocol", None), ("protocol", "head-only"), ("boundary", "head"),
    ("protocol", "email-canonical-message-to-prediction-v1"),
    ("input_contract_verified", None), ("input_contract_verified", False),
    ("input_contract_verified", 1),
    ("runtime_warm", False), ("runtime_warm", 1), ("cache_hit", True),
    ("cache_hit", 0), ("stages", ["head"]), ("stages", "head"),
    ("p50", 460.), ("p99", 440.), ("p95", float("nan")),
    ("sample_count", True), ("sample_count", 0),
])
def test_gate_requires_warmed_remote_total_provenance(field, value):
    row = evidence()
    row["end_to_end_latency_ms"][field] = value
    result = assess(row)
    assert result["promotion_eligible"] is False
    assert next(c for c in result["checks"] if c["key"] == "p95_latency")["actual"] is None


@pytest.mark.parametrize("path", [(), ("metrics",), ("metrics", "categories"),
    ("metrics", "categories", "work"), ("compatibility",), ("end_to_end_latency_ms",)])
@pytest.mark.parametrize("value", [None, [], "invalid", 1])
def test_gate_malformed_containers_fail_without_crashing(path, value):
    row = evidence()
    if not path:
        row = value
    else:
        target = row
        for key in path[:-1]:
            target = target[key]
        target[path[-1]] = value
    assert assess(row)["promotion_eligible"] is False


@pytest.mark.parametrize("field", ["precision", "f1", "support", "p95"])
def test_gate_overflowing_json_numbers_are_unmeasured(field):
    row = evidence()
    target = row["end_to_end_latency_ms"] if field == "p95" else row["metrics"]["categories"]["work"]
    target[field] = 10 ** 400
    assert assess(row)["promotion_eligible"] is False
