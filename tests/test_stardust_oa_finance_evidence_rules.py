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


def test_supplier_payment_tracker_status_is_not_customer_acceptance_evidence() -> None:
    for path in FINANCE_RULE_CARD_PATHS:
        text = path.read_text(encoding="utf-8")
        assert "tracker status alone" in text
        assert "unverified acceptance is not evidence of failed acceptance" in text
        assert "read the underlying customer correspondence" in text
        assert "count of prior returns" in text
