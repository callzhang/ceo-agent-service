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
        assert "不能表述为“未回复邮件”" in text
        assert "不能把审核方无法读取" in text
