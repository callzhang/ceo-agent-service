from __future__ import annotations

from pathlib import Path


FINANCE_RULE_CARD_PATHS = [
    Path("ci/shared-skills/stardust-oa-finance-review/references/finance-rule-cards.md"),
]
RUNTIME_RULE_CARD = Path("/Users/derek/.agents/skills/stardust-oa-finance-review/references/finance-rule-cards.md")
if RUNTIME_RULE_CARD.is_file():
    FINANCE_RULE_CARD_PATHS.append(RUNTIME_RULE_CARD)
FINANCE_SKILL = Path("ci/shared-skills/stardust-oa-finance-review/SKILL.md")
FINANCE_SKILL_PATHS = [FINANCE_SKILL]
RUNTIME_SKILL = Path("/Users/derek/.agents/skills/stardust-oa-finance-review/SKILL.md")
if RUNTIME_SKILL.is_file():
    FINANCE_SKILL_PATHS.append(RUNTIME_SKILL)
OA_SKILL_PATHS = [Path("ci/shared-skills/dingtalk-oa-approval/SKILL.md")]
RUNTIME_OA_SKILL = Path("/Users/derek/.agents/skills/dingtalk-oa-approval/SKILL.md")


def test_supplier_payment_distinguishes_email_reply_from_customer_acceptance() -> None:
    for path in FINANCE_RULE_CARD_PATHS:
        text = path.read_text(encoding="utf-8")
        assert "已回复邮件" in text
        assert "不等于客户验收" in text
        assert "never paraphrase it as “未回复邮件”" in text
        assert "不能把审核方无法读取" in text


def test_supplier_payment_output_is_limited_to_sourced_material_conditions() -> None:
    skill_text = FINANCE_SKILL.read_text(encoding="utf-8")
    assert "limit every result ledger to the active card's sourced payment conditions" in skill_text
    assert "Do not include or indirectly label material outside that scope" in skill_text
    text = FINANCE_RULE_CARD_PATHS[0].read_text(encoding="utf-8")
    assert "only include facts that verify a sourced payment condition" in text


def test_supplier_payment_does_not_evaluate_or_report_signature_observations() -> None:
    for path in FINANCE_SKILL_PATHS:
        text = path.read_text(encoding="utf-8")
        lowered = text.lower()
        assert "do not assess" in lowered
        assert "signatures" in lowered and "seals" in lowered and "stamps" in lowered
        assert "signature blocks" in lowered
        assert (
            "must not appear in internal or applicant-facing review results" in lowered
            or "from every user-facing and internal result field" in lowered
        )
    for path in FINANCE_RULE_CARD_PATHS:
        text = path.read_text(encoding="utf-8")
        supplier_section = text.split("## supplier_payment", 1)[1].split("\n## ", 1)[0]
        lowered = supplier_section.lower()
        assert "do not assess" in lowered
        assert "signatures" in lowered and "seals" in lowered and "stamps" in lowered
        assert "signature blocks" in lowered


def test_supplier_payment_decision_output_omits_stamp_and_signature_observations() -> None:
    skill_text = FINANCE_SKILL.read_text(encoding="utf-8")
    assert "Prior history reports only the number of independently verified substantive-gap rounds" in skill_text
    output_exclusion = (
        "omit seals, stamps, signatures, signature/stamp blocks, and page-layout observations",
        "Do not state that a seal/signature is present, absent, blank, unreadable, unverified, irrelevant, or “not a blocker”",
        "do not quote, summarize, or enumerate excluded past rationale",
        "Do not let any of these observations lower `information_completeness`, confidence, or the decision",
    )
    assert all(rule in skill_text for rule in output_exclusion)
    for path in FINANCE_RULE_CARD_PATHS:
        text = path.read_text(encoding="utf-8")
        assert "only include facts that verify a sourced payment condition" in text
        assert "historical rationale for excluded rounds is omitted" in text


def test_historical_supplier_payment_review_is_analytical_only() -> None:
    text = FINANCE_SKILL.read_text(encoding="utf-8")
    assert "when the request is to assess a prior supplier-payment decision" in text
    assert "outcome = no_action`, no `proposal`, no applicant-facing question" in text
    assert "A newly submitted flow is a separate approval" in text


def test_supplier_payment_rule_coverage_ignores_untriggered_branches() -> None:
    text = FINANCE_RULE_CARD_PATHS[0].read_text(encoding="utf-8")
    assert "未触及的未覆盖分支不得降低 rule_coverage" in text
    assert "材料未核实只影响 information_completeness" in text


def test_supplier_payment_unreadable_submitted_evidence_stays_a_reviewer_blocker() -> None:
    for path in FINANCE_RULE_CARD_PATHS:
        text = path.read_text(encoding="utf-8")
        assert "keep the approval pending" in text
        assert "do not turn an unknown condition into non-compliance" in text
        assert "Only independently readable and source-verified evidence" in text
        assert "Prior return count is history, not an action rule" in text
        assert "do not ask the applicant to resubmit the same unreadable file" in text
        assert "only when independently verified as applicant-resolvable material omissions" in text
        assert "never because the reviewer cannot read a submitted item" in text


def test_supplier_payment_tracker_status_is_not_customer_acceptance_evidence() -> None:
    for path in FINANCE_RULE_CARD_PATHS:
        text = path.read_text(encoding="utf-8")
        assert "tracker status alone" in text
        assert "unverified acceptance is not evidence of failed acceptance" in text
        assert "read the underlying customer correspondence" in text
        assert "count of prior returns" in text


def test_unknown_customer_correspondence_is_not_an_applicant_omission() -> None:
    skill_text = FINANCE_SKILL.read_text(encoding="utf-8")
    card_text = FINANCE_RULE_CARD_PATHS[0].read_text(encoding="utf-8")
    for text in (skill_text, card_text):
        assert "未取得客户邮件正文不等于申请人未提交客户确认" in text
        assert "不得仅因审阅方未取得第三方客户回复而要求申请人补交" in text
        assert "只有独立证据证明申请材料确实缺少该项" in text


def test_supplier_payment_tracks_readability_per_attachment() -> None:
    text = FINANCE_RULE_CARD_PATHS[0].read_text(encoding="utf-8")
    assert "Per-attachment read ledger (supplier payment)" in text
    assert "assess each submitted file independently" in text
    assert "never generalize it to sibling files" in text
    assert "without a separate failed read for each one" in text
    assert "Keep the applicant's submitted set distinct from the subset the reviewer could read" in text
    assert "resolve the discrepancy from the current per-file read" in text


def test_reviewer_side_unreadable_returns_do_not_trigger_three_return_refusal() -> None:
    for path in OA_SKILL_PATHS:
        text = path.read_text(encoding="utf-8")
        assert "因审阅端权限、下载、解析、OCR 或客户端读失败而要求申请人重交的退回" in text
        assert "均不得计数" in text
        assert "记录具体阻塞并保持审批待处理" in text
        assert "若附件已提交而障碍仅在我方权限或读取能力" in text
        assert "只有独立证据确认源文件确实缺失、空白、损坏或缺页" in text
        assert "已提交材料未读不等于申请人缺材料" in text
        assert "权限修复失败后的处理" in text
        assert "协调文件所有者/管理员或审批负责人解决访问问题" in text
        assert "申请人可提供可读副本时优先退回补正" not in text
        assert "若正文无法取得且申请人能补正，优先退回" not in text
        assert "有经独立证据确认属于申请人可补正的真实材料缺口时" in text


def test_three_return_threshold_requires_verified_per_return_evidence() -> None:
    text = OA_SKILL_PATHS[0].read_text(encoding="utf-8")
    assert "为每一轮退回建立有效性记录" in text
    assert "该轮提出的具体问题、当时可读且来源可核验的证据" in text
    assert "申请人针对该轮的回应及获得公平补正机会的事实" in text
    assert "未完成逐轮核验的退回次数不得计入拒绝阈值" in text
    assert "只有逐轮证据证明三轮都针对同一项真实、实质且由申请人或前序审批人可处理的缺口" in text


def test_supplier_payment_pressure_case_does_not_convert_unknown_acceptance_to_refusal() -> None:
    text = FINANCE_RULE_CARD_PATHS[0].read_text(encoding="utf-8")
    assert "压力案例：表格记录‘已回复邮件’，但邮件正文尚不可读" in text
    assert "客户验收属于未核实，而不是验收失败" in text
    assert "因审阅端 OCR/读取失败要求重交的轮次不得计入三次拒绝阈值" in text
    assert "count only independently verified applicant-resolvable substantive-gap rounds" in text
    assert "若没有其他独立可读证据证明付款条件未满足，保持待处理且不执行终局动作" in text


def test_runtime_oa_skill_does_not_treat_reader_failure_as_applicant_material_gap() -> None:
    if not RUNTIME_OA_SKILL.is_file():
        return
    text = RUNTIME_OA_SKILL.read_text(encoding="utf-8")
    assert "有经独立证据确认属于申请人可补正的真实材料缺口时" in text
    assert "审阅端权限、下载、解析、OCR 或客户端读失败不属于此类缺口" in text
