import pytest

from app.decision_quality import (
    DecisionQuality,
    DecisionQualityResult,
    StoredNeedsHumanProjection,
    classify_decision_quality,
    classify_stored_needs_human_projection,
)


@pytest.mark.parametrize(
    ("information_completeness", "expected"),
    [
        (0.49, DecisionQuality.ASK_BACK),
        (0.5, DecisionQuality.AUTONOMOUS),
        (0.51, DecisionQuality.AUTONOMOUS),
    ],
)
def test_information_completeness_threshold_is_inclusive_at_half(
    information_completeness, expected
):
    result = classify_decision_quality(
        risk="low",
        confidence=0.1,
        rule_coverage=1.0,
        information_completeness=information_completeness,
    )

    assert isinstance(result, DecisionQualityResult)
    assert result.classification is expected


def test_incomplete_information_takes_precedence_over_low_rule_coverage():
    result = classify_decision_quality(
        risk="high",
        confidence=0.1,
        rule_coverage=0.1,
        information_completeness=0.49,
    )

    assert result.classification is DecisionQuality.ASK_BACK


def test_high_risk_with_low_confidence_needs_human():
    result = classify_decision_quality(
        risk="high",
        confidence=0.49,
        rule_coverage=1.0,
        information_completeness=0.5,
    )

    assert result.classification is DecisionQuality.NEEDS_HUMAN


@pytest.mark.parametrize("risk", ["low", "medium"])
def test_low_confidence_is_allowed_for_lower_risk(risk):
    result = classify_decision_quality(
        risk=risk,
        confidence=0.0,
        rule_coverage=1.0,
        information_completeness=1.0,
    )

    assert result.classification is DecisionQuality.AUTONOMOUS


def test_low_rule_coverage_needs_human():
    result = classify_decision_quality(
        risk="low",
        confidence=1.0,
        rule_coverage=0.49,
        information_completeness=0.5,
    )

    assert result.classification is DecisionQuality.NEEDS_HUMAN


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("risk", "critical"),
        ("risk", []),
        ("risk", {}),
        ("confidence", -0.01),
        ("confidence", 1.01),
        ("rule_coverage", -0.01),
        ("information_completeness", 1.01),
    ],
)
def test_invalid_inputs_are_rejected(field, value):
    values = {
        "risk": "low",
        "confidence": 0.5,
        "rule_coverage": 0.5,
        "information_completeness": 0.5,
    }
    values[field] = value

    with pytest.raises(ValueError):
        classify_decision_quality(**values)


def test_classification_enum_has_stable_string_values():
    assert [member.value for member in DecisionQuality] == [
        "ask_back",
        "needs_human",
        "autonomous",
    ]


@pytest.mark.parametrize(
    "classification",
    [DecisionQuality.AUTONOMOUS, DecisionQuality.NEEDS_HUMAN],
)
def test_result_rejects_classification_inconsistent_with_incomplete_information(
    classification,
):
    with pytest.raises(ValueError):
        DecisionQualityResult(
            risk="low",
            confidence=0.0,
            rule_coverage=0.0,
            information_completeness=0.0,
            classification=classification,
        )


def test_stored_needs_human_projection_requires_the_full_typed_rule_decision():
    valid = {
        "outcome": "needs_human",
        "risk": "high",
        "confidence": 0.2,
        "rule_coverage": 1.0,
        "information_completeness": 1.0,
        "decision_options": [
            {
                "key": "one_time",
                "label": "仅本次处理",
                "instruction": "本次按该规则处理",
                "consequence": "不修改 Skill",
            },
            {
                "key": "skill_update",
                "label": "更新 Skill",
                "instruction": "更新适用规则后继续",
                "consequence": "后续同类任务自动处理",
            },
        ],
    }

    assert (
        classify_stored_needs_human_projection(valid)
        is StoredNeedsHumanProjection.NEEDS_HUMAN
    )
    assert (
        classify_stored_needs_human_projection(
            {**valid, "confidence": 0.9}
        )
        is StoredNeedsHumanProjection.INVALID
    )
    assert (
        classify_stored_needs_human_projection(
            {key: value for key, value in valid.items() if key != "rule_coverage"}
        )
        is StoredNeedsHumanProjection.INVALID
    )
