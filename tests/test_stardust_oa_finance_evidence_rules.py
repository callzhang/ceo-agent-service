from __future__ import annotations

from pathlib import Path


FINANCE_RULE_CARD_PATHS = [
    Path("ci/shared-skills/stardust-oa-finance-review/references/finance-rule-cards.md"),
]
RUNTIME_RULE_CARD = Path(
    "/Users/derek/.agents/skills/stardust-oa-finance-review/references/finance-rule-cards.md"
)
if RUNTIME_RULE_CARD.is_file():
    FINANCE_RULE_CARD_PATHS.append(RUNTIME_RULE_CARD)
OA_SKILL_PATHS = [Path("ci/shared-skills/dingtalk-oa-approval/SKILL.md")]
RUNTIME_OA_SKILL = Path("/Users/derek/.agents/skills/dingtalk-oa-approval/SKILL.md")


def test_supplier_payment_distinguishes_email_reply_from_customer_acceptance() -> None:
    for path in FINANCE_RULE_CARD_PATHS:
        text = path.read_text(encoding="utf-8")
        assert "已回复邮件" in text
        assert "不等于客户验收" in text
        assert "never paraphrase it as “未回复邮件”" in text
        assert "不能把审核方无法读取" in text


def test_supplier_payment_unreadable_submitted_evidence_stays_a_reviewer_blocker() -> None:
    for path in FINANCE_RULE_CARD_PATHS:
        text = path.read_text(encoding="utf-8")
        assert "keep the approval pending" in text
        assert "do not turn an unknown condition into non-compliance" in text
        assert "Only independently readable and source-verified evidence" in text
        assert "Prior return count is history, not an action rule" in text
        assert "do not ask the applicant to resubmit the same unreadable file" in text
        assert "without a stamp on each related attachment" in text
        assert "A blank signature page alone is not a reason to return" in text
        assert "only when independently verified as applicant-resolvable material omissions" in text
        assert "never because the reviewer cannot read a submitted item" in text


def test_supplier_payment_tracker_status_is_not_customer_acceptance_evidence() -> None:
    for path in FINANCE_RULE_CARD_PATHS:
        text = path.read_text(encoding="utf-8")
        assert "tracker status alone" in text
        assert "unverified acceptance is not evidence of failed acceptance" in text
        assert "read the underlying customer correspondence" in text
        assert "count of prior returns" in text


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
        assert "因审阅端权限、解析、OCR 或客户端问题要求申请人补交的错误退回，不得计入拒绝次数" in text
        assert "记录具体阻塞并保持审批待处理" in text
        assert "若附件已提交而障碍仅在我方权限或读取能力" in text
        assert "只有独立证据确认源文件确实缺失、空白、损坏或缺页" in text
        assert "已提交材料未读不等于申请人缺材料" in text
        assert "权限修复失败后的处理" in text
        assert "协调文件所有者/管理员或审批负责人解决访问问题" in text
        assert "申请人可提供可读副本时优先退回补正" not in text
        assert "若正文无法取得且申请人能补正，优先退回" not in text
        assert "有经独立证据确认属于申请人可补正的真实材料缺口时" in text


def test_runtime_oa_skill_does_not_treat_reader_failure_as_applicant_material_gap() -> None:
    if not RUNTIME_OA_SKILL.is_file():
        return
    text = RUNTIME_OA_SKILL.read_text(encoding="utf-8")
    assert "有经独立证据确认属于申请人可补正的真实材料缺口时" in text
    assert "审阅端权限、下载、解析、OCR 或客户端读失败不属于此类缺口" in text
