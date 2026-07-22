from pathlib import Path

import pytest

from app.quality.evaluator import evaluate_cases, load_cases


@pytest.mark.protocol
@pytest.mark.security
def test_committed_golden_cases_pass_all_suites() -> None:
    cases = load_cases(Path("quality/cases/golden.jsonl"))

    assert {case.input_context["channel"] for case in cases} >= {
        "dingtalk_group",
        "dingtalk_private",
        "oa",
        "calendar",
        "mail",
        "meeting",
        "task",
        "memory",
        "wechat",
    }
    for suite in ("protocol", "safety", "semantic"):
        assert evaluate_cases(cases, suite=suite).status == "passed"
