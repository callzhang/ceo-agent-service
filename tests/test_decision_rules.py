from __future__ import annotations

from app.decision_rules import decision_violations


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
    assert decision_violations(
        result=_result(),
        tool_events=[_shell("dws oa approval detail --instance-id inst-1 --format json")],
    ) == ()


def test_a_decision_that_meets_its_band_passes():
    assert (
        decision_violations(
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

    [violation] = decision_violations(
        result=_result(
            risk="high",
            rule_coverage=0.97,
            confidence=0.96,
            information_completeness=1.0,
        ),
        tool_events=[_shell(APPROVE)],
    )

    assert violation.code == "decision_below_score_band"
    assert "rule_coverage=0.97" in violation.detail
    assert "high" in violation.detail


def test_incomplete_material_never_supports_a_decision():
    [violation] = decision_violations(
        result=_result(information_completeness=0.86),
        tool_events=[_shell(APPROVE)],
    )

    assert violation.code == "decision_below_score_band"
    assert "information_completeness=0.86" in violation.detail


def test_confidence_exactly_at_the_boundary_is_not_enough():
    [violation] = decision_violations(
        result=_result(confidence=0.9),
        tool_events=[_shell(APPROVE)],
    )

    assert "confidence=0.9" in violation.detail


def test_a_decision_without_a_risk_level_cannot_be_judged():
    [violation] = decision_violations(
        result=_result(risk=""),
        tool_events=[_shell(APPROVE)],
    )

    assert violation.code == "decision_without_risk_level"


def test_rejecting_without_checking_revert_is_a_violation():
    """135612 was rejected with a remark that asked for materials and a resubmit.

    `revert-activities` was never called in that generation; the approval was
    terminated instead of handed back, and 魏诗睿 has to start over.
    """

    codes = {
        violation.code
        for violation in decision_violations(
            result=_result(), tool_events=[_shell(REJECT)]
        )
    }

    assert "terminal_action_without_checking_reversible" in codes


def test_rejecting_after_checking_revert_is_allowed():
    codes = {
        violation.code
        for violation in decision_violations(
            result=_result(),
            tool_events=[_shell(REVERT_ACTIVITIES), _shell(REJECT)],
        )
    }

    assert "terminal_action_without_checking_reversible" not in codes


def test_an_approval_does_not_need_the_revert_check():
    codes = {
        violation.code
        for violation in decision_violations(
            result=_result(), tool_events=[_shell(APPROVE)]
        )
    }

    assert "terminal_action_without_checking_reversible" not in codes


def test_a_decision_without_a_remark_is_a_violation():
    """DWS marks remark optional, so a bare verdict is accepted by the provider."""

    [violation] = decision_violations(
        result=_result(),
        tool_events=[
            _shell(
                "dws oa approval approve --instance-id inst-1 --task-id t-1"
                " --yes --format json"
            )
        ],
    )

    assert violation.code == "decision_without_reason"


def test_an_empty_remark_flag_is_still_missing():
    violations = decision_violations(
        result=_result(),
        tool_events=[
            _shell(
                "dws oa approval approve --instance-id inst-1 --task-id t-1"
                " --remark --yes --format json"
            )
        ],
    )

    assert any(v.code == "decision_without_reason" for v in violations)


def test_a_revert_also_needs_a_remark():
    violations = decision_violations(
        result=_result(),
        tool_events=[
            _shell(
                "dws oa approval revert-task --instance-id inst-1 --task-id t-1"
                " --target-activity-id sid-startevent"
                " --action REVERT_FOR_RESUBMIT --yes --format json"
            )
        ],
    )

    assert any(v.code == "decision_without_reason" for v in violations)


def test_a_revert_with_a_remark_passes():
    assert (
        decision_violations(result=_result(), tool_events=[_shell(REVERT)]) == ()
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

    violations = decision_violations(result=_result(), tool_events=[event])

    assert any(v.code == "decision_without_reason" for v in violations)


def test_a_decision_chained_behind_another_command_is_still_seen():
    violations = decision_violations(
        result=_result(),
        tool_events=[
            _shell(
                "dws oa approval detail --instance-id inst-1 --format json && "
                "dws oa approval approve --instance-id inst-1 --task-id t-1 --yes"
            )
        ],
    )

    assert any(v.code == "decision_without_reason" for v in violations)


def test_a_calendar_response_is_held_to_the_same_band():
    """The rules are about deciding for someone else, not about approvals.

    A calendar response ends the matter for the organiser the same way a
    rejection ends it for an applicant, so it answers to the same band.
    """

    [violation] = decision_violations(
        result=_result(risk="high", rule_coverage=0.95, confidence=0.95),
        tool_events=[_shell("dws calendar event respond --id ev-1 --status accepted")],
    )

    assert violation.code == "decision_below_score_band"
    assert "calendar event respond" in violation.detail


def test_an_action_whose_provider_has_no_reason_field_is_not_faulted_for_it():
    assert (
        decision_violations(
            result=_result(risk="low", rule_coverage=1.0, confidence=0.95),
            tool_events=[_shell("dws calendar event respond --id ev-1 --status accepted")],
        )
        == ()
    )


def test_only_a_terminal_action_needs_the_reversible_check():
    """A revert is itself the reversible route; it has nothing to consult."""

    codes = {
        violation.code
        for violation in decision_violations(
            result=_result(), tool_events=[_shell(REVERT)]
        )
    }

    assert "terminal_action_without_checking_reversible" not in codes


def test_a_generation_that_only_sends_or_reads_costs_nothing():
    assert (
        decision_violations(
            result={"risk": "high"},
            tool_events=[
                _shell("dws chat +dm --to Wayne --content 你好"),
                _shell("dws oa approval detail --instance-id inst-1"),
            ],
        )
        == ()
    )


def test_a_message_is_not_held_to_the_score_band():
    """A chat send reaches a person but they can answer it.

    Holding these to the band would punish the very behaviour the rules ask
    for: when scores are low the turn is told to propose or ask rather than
    decide, and asking is a message.
    """

    assert (
        decision_violations(
            result={"risk": "high", "confidence": 0.4, "rule_coverage": 0.2},
            tool_events=[_shell("dws chat +messages-send --group cid-1 --text 请补充验收标准")],
        )
        == ()
    )


def test_every_registered_write_is_classified():
    """Adding a capability must force a decision about how far it reaches.

    This is the forcing function instead of refusing unclassified writes at
    runtime: whoever adds one cannot merge without classifying it, rather than
    whoever is on shift discovering it at the gate.
    """

    import json
    from pathlib import Path

    from app.decision_rules import DECISION_ACTION_BY_PATH

    registry = json.loads(
        (Path(__file__).resolve().parents[1] / "config" / "mcp-tool-effects.json").read_text(
            encoding="utf-8"
        )
    )
    writes: set[str] = set()

    def walk(value):
        if isinstance(value, dict):
            write = value.get("write")
            if isinstance(write, str):
                writes.add(write)
            for nested in value.values():
                walk(nested)
        elif isinstance(value, list):
            for nested in value:
                walk(nested)

    walk(registry)
    unclassified = sorted(
        write
        for write in writes
        if tuple(write.split()) not in DECISION_ACTION_BY_PATH
        and not write.startswith("app.cli ")
    )

    assert unclassified == [], (
        "每个受审写操作都必须在 DECISION_ACTIONS 里明确分档："
        f"{unclassified}"
    )


def test_low_risk_needs_full_rule_coverage_too():
    """Derek, 2026-09-23: the band follows the generic OA Skill, rc = 1.0 at every level.

    It was 0.8 for low risk, which let a low-risk decision through on a rule
    that did not fully cover the case while the Skill told the model 1.0.
    """

    [violation] = decision_violations(
        result=_result(risk="low", rule_coverage=0.9, confidence=0.95),
        tool_events=[_shell(APPROVE)],
    )

    assert violation.code == "decision_below_score_band"
    assert "rule_coverage=0.9" in violation.detail
