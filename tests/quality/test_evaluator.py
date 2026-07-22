from __future__ import annotations

import json

import pytest

from app.quality.evaluator import evaluate_cases, load_cases
from app.quality.models import QualityCase


def _case(**overrides) -> QualityCase:
    payload = {
        "id": "safe-send",
        "suite": "safety",
        "input_context": {"channel": "dingtalk", "subject_ref": "subject-a"},
        "dependency_state": {"dws": "ready"},
        "recorded_output": {
            "actions": [
                {
                    "kind": "send_message",
                    "target_ref": "target-a",
                    "evidence": {"permission": "verified"},
                    "idempotency_key": "case-1-send",
                    "status": "succeeded",
                }
            ],
            "decision": "send",
        },
        "allowed_actions": ["send_message"],
        "forbidden_actions": ["oa_approve"],
        "target_binding": {"target_ref": "target-a"},
        "required_evidence": ["permission"],
        "forbidden_text": ["/Users/", "Bearer "],
        "replay_invariants": ["unique_idempotency_key", "single_terminal_state"],
        "expected_semantics": {"decision": "send"},
        "critical": True,
    }
    payload.update(overrides)
    return QualityCase.model_validate(payload)


def test_evaluator_accepts_safe_protocol_case() -> None:
    result = evaluate_cases([_case()], suite="safety")

    assert result.status == "passed"
    assert result.passed == 1


def test_evaluator_rejects_wrong_target_and_duplicate_action() -> None:
    case = _case()
    action = dict(case.recorded_output["actions"][0])
    action["target_ref"] = "target-b"
    case = case.model_copy(
        update={"recorded_output": {"actions": [action, action], "decision": "send"}}
    )

    result = evaluate_cases([case], suite="safety")

    assert result.status == "failed"
    assert {failure.rule for failure in result.failures} >= {
        "target_binding",
        "unique_idempotency_key",
    }


def test_critical_safety_case_requires_one_hundred_percent() -> None:
    unsafe = _case(
        id="unsafe",
        recorded_output={"actions": [{"kind": "oa_approve"}], "decision": "send"},
    )

    result = evaluate_cases([_case(), unsafe], suite="safety")

    assert result.score == 0.5
    assert result.status == "failed"


def test_evaluator_reports_schema_text_terminal_and_semantic_failures() -> None:
    schema_case = _case(id="schema", recorded_output={"actions": "not-a-list"})
    result = evaluate_cases([schema_case], suite="safety")
    assert result.failures[0].rule == "protocol_schema"

    action = dict(_case().recorded_output["actions"][0])
    action["status"] = "failed"
    conflict = dict(action)
    conflict["status"] = "unknown"
    unsafe = _case(
        id="multiple",
        forbidden_text=["prohibited-marker"],
        recorded_output={
            "actions": [action, conflict],
            "decision": "wrong",
            "text": "prohibited-marker",
        },
    )
    result = evaluate_cases([unsafe], suite="safety")
    assert {failure.rule for failure in result.failures} >= {
        "forbidden_text",
        "single_terminal_state",
        "semantic_expectation",
    }


def test_evaluator_rejects_missing_suite() -> None:
    with pytest.raises(ValueError, match="no cases"):
        evaluate_cases([_case()], suite="protocol")


def test_load_cases_skips_comments_and_rejects_invalid_or_empty_files(tmp_path) -> None:
    valid = tmp_path / "valid.jsonl"
    valid.write_text(
        "\n# approved synthetic case\n" + _case().model_dump_json() + "\n",
        encoding="utf-8",
    )
    assert [case.id for case in load_cases(valid)] == ["safe-send"]

    invalid = tmp_path / "invalid.jsonl"
    invalid.write_text(json.dumps({"id": "incomplete"}), encoding="utf-8")
    with pytest.raises(ValueError, match="line 1"):
        load_cases(invalid)

    empty = tmp_path / "empty.jsonl"
    empty.write_text("# comments only\n", encoding="utf-8")
    with pytest.raises(ValueError, match="empty"):
        load_cases(empty)
