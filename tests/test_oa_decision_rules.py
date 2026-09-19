from __future__ import annotations

from app.oa_decision_rules import oa_decision_violations


def _shell(command: str, *, exit_code: int = 0):
    return {
        "type": "item.completed",
        "item": {
            "type": "command_execution",
            "command": f'/bin/zsh -lc "{command}"',
            "exit_code": exit_code,
            "aggregated_output": '{"success":true}',
        },
    }


APPROVE = (
    "dws oa approval approve --instance-id inst-1 --task-id t-1"
    " --remark 条件不变，三级已同意，依据劳动合同续签制度第3条 --yes --format json"
)
REJECT = (
    "dws oa approval reject --instance-id inst-1 --task-id t-1"
    " --remark 第5.5条与第9.3条歧义已查实，违反合同审查要点第2条 --yes --format json"
)
REVERT_ACTIVITIES = "dws oa approval revert-activities --task-id t-1 --format json"
REVERT = (
    "dws oa approval revert-task --instance-id inst-1 --task-id t-1"
    " --target-activity-id sid-startevent --action REVERT_FOR_RESUBMIT"
    " --remark 请补齐验收标准与归档链接后重新提交 --yes --format json"
)


def _result(**overrides):
    base = {
        "risk": "low",
        "confidence": 0.95,
        "information_completeness": 1.0,
        "rule_coverage": 1.0,
    }
    base.update(overrides)
    return base


def test_a_generation_with_no_decision_has_nothing_to_check():
    assert oa_decision_violations(
        result=_result(),
        tool_events=[_shell("dws oa approval detail --instance-id inst-1 --format json")],
    ) == ()


def test_a_decision_that_meets_its_band_passes():
    assert (
        oa_decision_violations(
            result=_result(risk="high", rule_coverage=1.0, confidence=0.95),
            tool_events=[_shell(APPROVE)],
        )
        == ()
    )


def test_high_risk_approval_below_full_rule_coverage_is_a_violation():
    """Run 20053 approved a labour-contract renewal at risk=high, rc=0.97.

    It scored itself below its own band and executed anyway, which is the
    reason these rules cannot stay as Skill text.
    """

    [violation] = oa_decision_violations(
        result=_result(
            risk="high",
            rule_coverage=0.97,
            confidence=0.96,
            information_completeness=1.0,
        ),
        tool_events=[_shell(APPROVE)],
    )

    assert violation.code == "oa_decision_below_score_band"
    assert "rule_coverage=0.97" in violation.detail
    assert "high" in violation.detail


def test_incomplete_material_never_supports_a_decision():
    [violation] = oa_decision_violations(
        result=_result(information_completeness=0.86),
        tool_events=[_shell(APPROVE)],
    )

    assert violation.code == "oa_decision_below_score_band"
    assert "information_completeness=0.86" in violation.detail


def test_confidence_exactly_at_the_boundary_is_not_enough():
    [violation] = oa_decision_violations(
        result=_result(confidence=0.9),
        tool_events=[_shell(APPROVE)],
    )

    assert "confidence=0.9" in violation.detail


def test_a_decision_without_a_risk_level_cannot_be_judged():
    [violation] = oa_decision_violations(
        result=_result(risk=""),
        tool_events=[_shell(APPROVE)],
    )

    assert violation.code == "oa_decision_without_risk_level"


def test_rejecting_without_checking_revert_is_a_violation():
    """135612 was rejected with a remark that asked for materials and a resubmit.

    `revert-activities` was never called in that generation; the approval was
    terminated instead of handed back, and 魏诗睿 has to start over.
    """

    codes = {
        violation.code
        for violation in oa_decision_violations(
            result=_result(), tool_events=[_shell(REJECT)]
        )
    }

    assert "oa_reject_without_checking_revert" in codes


def test_rejecting_after_checking_revert_is_allowed():
    codes = {
        violation.code
        for violation in oa_decision_violations(
            result=_result(),
            tool_events=[_shell(REVERT_ACTIVITIES), _shell(REJECT)],
        )
    }

    assert "oa_reject_without_checking_revert" not in codes


def test_an_approval_does_not_need_the_revert_check():
    codes = {
        violation.code
        for violation in oa_decision_violations(
            result=_result(), tool_events=[_shell(APPROVE)]
        )
    }

    assert "oa_reject_without_checking_revert" not in codes


def test_a_decision_without_a_remark_is_a_violation():
    """DWS marks remark optional, so a bare verdict is accepted by the provider."""

    [violation] = oa_decision_violations(
        result=_result(),
        tool_events=[
            _shell(
                "dws oa approval approve --instance-id inst-1 --task-id t-1"
                " --yes --format json"
            )
        ],
    )

    assert violation.code == "oa_decision_without_remark"


def test_an_empty_remark_flag_is_still_missing():
    violations = oa_decision_violations(
        result=_result(),
        tool_events=[
            _shell(
                "dws oa approval approve --instance-id inst-1 --task-id t-1"
                " --remark --yes --format json"
            )
        ],
    )

    assert any(v.code == "oa_decision_without_remark" for v in violations)


def test_a_revert_also_needs_a_remark():
    violations = oa_decision_violations(
        result=_result(),
        tool_events=[
            _shell(
                "dws oa approval revert-task --instance-id inst-1 --task-id t-1"
                " --target-activity-id sid-startevent"
                " --action REVERT_FOR_RESUBMIT --yes --format json"
            )
        ],
    )

    assert any(v.code == "oa_decision_without_remark" for v in violations)


def test_a_revert_with_a_remark_passes():
    assert (
        oa_decision_violations(result=_result(), tool_events=[_shell(REVERT)]) == ()
    )


def test_a_decision_run_through_the_reviewed_tool_is_seen_too():
    """The gate must not be escapable by using the reviewed CLI's MCP tool."""

    event = {
        "type": "item.completed",
        "item": {
            "type": "mcp_tool_call",
            "arguments": {
                "argv": [
                    "dws",
                    "oa",
                    "approval",
                    "approve",
                    "--instance-id",
                    "inst-1",
                    "--task-id",
                    "t-1",
                    "--yes",
                ]
            },
            "result": {"ok": True},
        },
    }

    violations = oa_decision_violations(result=_result(), tool_events=[event])

    assert any(v.code == "oa_decision_without_remark" for v in violations)


def test_a_decision_chained_behind_another_command_is_still_seen():
    violations = oa_decision_violations(
        result=_result(),
        tool_events=[
            _shell(
                "dws oa approval detail --instance-id inst-1 --format json && "
                "dws oa approval approve --instance-id inst-1 --task-id t-1 --yes"
            )
        ],
    )

    assert any(v.code == "oa_decision_without_remark" for v in violations)
