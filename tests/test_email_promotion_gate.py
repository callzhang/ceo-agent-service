from types import SimpleNamespace

import pytest

from app import email_classifier_runtime as runtime


def evidence():
    return {
        "model_id": "candidate",
        "compatibility": {"enabled_categories": ["work"], "description_version": "d1"},
        "metrics": {"categories": {"work": {"precision": .98, "recall": .97, "f1": .97, "support": 25}}},
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
        config={"config_version": "v1", "micro_f1_min": .95,
                "category_precision_min": .95, "category_validation_samples_min": 20,
                "p95_latency_max_ms": 500.},
        enabled_category_keys=kwargs.get("enabled_category_keys", ("work",)),
        description_version="d1",
        readiness=SimpleNamespace(
            ready=True, passing_model_ids=("previous", "candidate"), reason="ready",
            promoted_categories=kwargs.get(
                "proven", tuple(kwargs.get("enabled_category_keys", ("work",)))
            ),
            important_promoted=True,
        ),
        registry_issues=kwargs.get("registry_issues", ()),
        artifact_verified=True,
    )


def test_gate_accepts_complete_evidence():
    result = assess(evidence())
    assert result["promotion_eligible"] is True
    assert all(check["passed"] for check in result["checks"])


@pytest.mark.parametrize("field,value", [("precision", .94), ("support", 19), ("recall", .94), ("support", None)])
def test_gate_requires_each_class_metric(field, value):
    row = evidence()
    row["metrics"]["categories"]["work"][field] = value
    assert assess(row)["promotion_eligible"] is False


def micro_check(result):
    return next(check for check in result["checks"] if check["key"] == "micro_f1")


def test_micro_f1_weighs_each_class_by_its_evaluated_messages():
    """A class too thin to promote stays with the Agent and is not scored."""

    row = evidence()
    row["compatibility"]["enabled_categories"] = ["work", "legal"]
    row["metrics"]["categories"]["work"]["recall"] = 1.
    row["metrics"]["categories"]["legal"] = {
        "precision": 1., "recall": .0, "f1": .0, "support": 1,
    }
    result = assess(row, enabled_category_keys=("work", "legal"))
    check = micro_check(result)
    assert result["promoted_categories"] == ["work"]
    assert check["actual"] == pytest.approx(1.0)
    assert check["passed"] is True
    # The unweighted mean of the two classes would have been .5 and failed.
    assert (1. + .0) / 2 < .95


def test_micro_f1_scores_the_classes_the_evaluation_measured():
    """A class with no evaluated mail fails its own checks rather than making
    the overall score unmeasurable."""

    row = evidence()
    row["compatibility"]["enabled_categories"] = ["work", "legal"]
    result = assess(row, enabled_category_keys=("work", "legal"))
    check = micro_check(result)
    assert check["actual"] == pytest.approx(.97)
    assert check["passed"] is True
    coverage = next(
        item for item in result["checks"]
        if item["key"] == "category_validation_samples:legal"
    )
    assert coverage["actual"] is None
    # legal fails its own checks, which no longer holds work back.
    assert result["promoted_categories"] == ["work"]
    assert result["promotion_eligible"] is True


def test_micro_f1_is_unmeasured_without_recall_for_every_class():
    row = evidence()
    del row["metrics"]["categories"]["work"]["recall"]
    check = micro_check(assess(row))
    assert check["actual"] is None
    assert check["reason"] == "not_measured"
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


@pytest.mark.parametrize("field", ["precision", "recall", "support", "p95"])
def test_gate_overflowing_json_numbers_are_unmeasured(field):
    row = evidence()
    target = row["end_to_end_latency_ms"] if field == "p95" else row["metrics"]["categories"]["work"]
    target[field] = 10 ** 400
    assert assess(row)["promotion_eligible"] is False


def test_others_counts_towards_micro_f1_and_keeps_the_candidate_compatible():
    """others is scored but is not a configured category, so it must not make
    the candidate look incompatible either."""

    row = evidence()
    row["compatibility"]["enabled_categories"] = ["work", "others"]
    row["metrics"]["categories"]["others"] = {
        "precision": .2, "recall": .2, "f1": .2, "support": 25,
    }
    result = assess(row)
    check = micro_check(result)
    assert check["actual"] == pytest.approx((.97 * 25 + .2 * 25) / 50)
    assert check["passed"] is False
    integrity = next(
        item for item in result["checks"] if item["key"] == "system_integrity"
    )
    assert integrity["passed"] is True


def test_others_is_not_held_to_the_per_category_checks():
    row = evidence()
    row["compatibility"]["enabled_categories"] = ["work", "others"]
    row["metrics"]["categories"]["others"] = {
        "precision": .2, "recall": 1., "f1": .3, "support": 1,
    }
    keys = {check["key"] for check in assess(row)["checks"]}
    assert "category_precision:others" not in keys
    assert "category_validation_samples:others" not in keys


def test_each_category_is_promoted_on_its_own_evidence():
    """A proven category goes live while a weak one stays with the Agent."""

    row = evidence()
    row["compatibility"]["enabled_categories"] = ["work", "legal"]
    row["metrics"]["categories"]["work"]["recall"] = 1.
    row["metrics"]["categories"]["legal"] = {
        "precision": .5, "recall": .9, "f1": .6, "support": 40,
    }

    result = assess(row, enabled_category_keys=("work", "legal"))

    assert result["promotion_eligible"] is True
    assert result["promoted_categories"] == ["work"]
    legal = next(c for c in result["checks"] if c["key"] == "category_precision:legal")
    assert legal["passed"] is False


def test_a_category_the_registry_has_not_proven_twice_is_not_promoted():
    row = evidence()
    row["compatibility"]["enabled_categories"] = ["work", "legal"]
    row["metrics"]["categories"]["legal"] = {
        "precision": .99, "recall": .99, "f1": .99, "support": 40,
    }

    result = assess(row, enabled_category_keys=("work", "legal"), proven=("work",))

    assert result["promoted_categories"] == ["work"]


def test_nothing_is_promoted_when_no_category_qualifies():
    row = evidence()
    row["metrics"]["categories"]["work"]["precision"] = .5

    result = assess(row)

    assert result["promoted_categories"] == []
    assert result["promotion_eligible"] is False
    assert micro_check(result)["reason"] == "not_measured"
