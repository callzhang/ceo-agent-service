from app.agent_contracts import ExternalBoundary
from app.reply_risk_controls import (
    missing_external_boundary_controls,
    missing_external_boundary_controls_from_argv,
)


def _boundary() -> ExternalBoundary:
    return ExternalBoundary(
        allowed_now="询问字段和价格",
        concrete_risk="对方可能误解为采购意向",
        do_not="不要报价承诺或购买",
        decision_boundary="预算和采购仍由 Derek 决定",
    )


def test_exact_body_must_contain_all_four_controls():
    boundary = _boundary()
    body = (
        "可以询问字段和价格。"
        "风险：对方可能误解为采购意向。"
        "不要报价承诺或购买。"
        "预算和采购仍由 Derek 决定。"
    )

    assert missing_external_boundary_controls(body, boundary) == ()


def test_missing_control_is_reported_by_stable_field_name():
    boundary = _boundary()

    assert missing_external_boundary_controls(
        "询问字段和价格；对方可能误解为采购意向；不要报价承诺或购买。",
        boundary,
    ) == ("decision_boundary",)


def test_validator_uses_exact_dingtalk_message_body_from_argv():
    body = (
        "询问字段和价格；对方可能误解为采购意向；"
        "不要报价承诺或购买；预算和采购仍由 Derek 决定。"
    )
    argv = (
        "dws",
        "chat",
        "message",
        "send",
        "--group",
        "cid-1",
        "--text",
        body,
        "--yes",
    )

    assert missing_external_boundary_controls_from_argv(argv, _boundary()) == ()


def test_non_message_argv_reports_missing_body():
    assert missing_external_boundary_controls_from_argv(
        ("dws", "chat", "+groups"),
        _boundary(),
    ) == ("message_body_missing",)

