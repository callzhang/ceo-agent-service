"""Provider-neutral important-signal normalization."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal


ImportantProvider = Literal["dingtalk", "dingmail", "gmail", "imap", "microsoft_graph"]

_TRUSTED_IMPORTANT_SIGNALS: dict[str, frozenset[str]] = {
    "dingtalk": frozenset({"11", "1", "107", "pry_high"}),
    "dingmail": frozenset({"11", "1", "107", "pry_high"}),
    "gmail": frozenset({"starred", "important"}),
    "imap": frozenset({"\\flagged", "$important"}),
    "microsoft_graph": frozenset({"flagged", "importance=high", "focused"}),
}


@dataclass(frozen=True)
class ImportantSignals:
    """Immutable raw provider attention signals and their normalized union."""

    raw_signal_names: tuple[str, ...]
    provider_important: bool

    def __post_init__(self) -> None:
        if type(self.raw_signal_names) is not tuple:
            raise TypeError("raw_signal_names must be an exact tuple")
        if any(type(name) is not str for name in self.raw_signal_names):
            raise TypeError("raw signal names must be exact strings")
        if any(not name or name != name.strip() for name in self.raw_signal_names):
            raise ValueError("raw signal names must be non-blank and unmodified")
        if len(self.raw_signal_names) != len(set(self.raw_signal_names)):
            raise ValueError("raw signal names must be unique")
        if type(self.provider_important) is not bool:
            raise TypeError("provider_important must be an exact boolean")


def normalize_important_signals(
    *,
    provider: ImportantProvider,
    raw_signal_names: tuple[str, ...],
) -> ImportantSignals:
    """Normalize only the trusted attention signals owned by one provider."""

    if type(provider) is not str or provider not in _TRUSTED_IMPORTANT_SIGNALS:
        raise ValueError("unsupported important-signal provider")
    observed = ImportantSignals(raw_signal_names, False)
    trusted = _TRUSTED_IMPORTANT_SIGNALS[provider]
    return ImportantSignals(
        raw_signal_names=observed.raw_signal_names,
        provider_important=any(name.casefold() in trusted for name in raw_signal_names),
    )


def important_effective(
    *,
    category: str,
    provider_signals: ImportantSignals,
    model_important: bool,
) -> bool:
    """Combine independent provider/model attention while suppressing junk."""

    if type(category) is not str or not category or category != category.strip():
        raise ValueError("category must be an exact non-blank string")
    if type(provider_signals) is not ImportantSignals:
        raise TypeError("provider_signals must be ImportantSignals")
    if type(model_important) is not bool:
        raise TypeError("model_important must be an exact boolean")
    return category != "junk" and (
        provider_signals.provider_important or model_important
    )


_OWNER_ACTION_CATEGORIES = frozenset({
    "work",
    "human_resources",
    "legal",
    "financing",
    "finance",
    "external_billing",
    "personal",
})

_BULK_SENDER_NAMES = (
    "noreply",
    "no-reply",
    "no.reply",
    "donotreply",
    "do-not-reply",
    "notification",
    "notifications",
    "notify",
    "alert",
    "alerts",
    "mailer",
    "mail",
    "bounce",
    "postmaster",
    "automated",
    "robot",
    "news",
    "newsletter",
    "update",
    "updates",
    "digest",
    "info",
    "hello",
    "team",
    "support",
    "service",
    "services",
    "billing",
    "invoice",
    "invoices",
    "receipt",
    "receipts",
    "payment",
    "payments",
    "memberservices",
    "marketing",
    "promo",
    "promotions",
    "sales",
    "statements",
)


# The domains the owner's own people write from.
OWN_DOMAINS = ("stardust.ai", "preseen.ai")


def internal_sender(sender: str) -> bool:
    """Say whether a colleague sent this, rather than the outside world."""

    if type(sender) is not str:
        raise TypeError("sender must be an exact string")
    _local, separator, domain = sender.strip().casefold().rpartition("@")
    return bool(separator) and any(
        domain == item or domain.endswith("." + item) for item in OWN_DOMAINS
    )


def bulk_sender(sender: str) -> bool:
    """Say whether an address sends broadcasts rather than correspondence."""

    if type(sender) is not str:
        raise TypeError("sender must be an exact string")
    local, separator, domain = sender.strip().casefold().rpartition("@")
    if not separator:
        local, domain = sender.strip().casefold(), ""
    if "+acct_" in local or local.startswith("upcoming-invoice"):
        return True
    parts = [part for part in re.split(r"[.\-_+]", local) if part]
    if any(part in _BULK_SENDER_NAMES for part in parts):
        return True
    return domain.startswith("notify.") or domain.startswith("alert.")


def important_training_label(*, category: str, sender: str) -> bool:
    """Important means the owner still owes this message an action.

    The Agent's own flag was the training label until 2026-09-18, and it
    disagreed with itself: 116 of 232 work messages carried it with no
    readable difference between the two halves, and a label correction
    replaces the ActionPlan, silently dropping the flag. Mail that asks
    something of the owner is mail a person sent him about his own
    business; broadcasts never are, whatever they are about.
    """

    if type(category) is not str or not category or category != category.strip():
        raise ValueError("category must be an exact non-blank string")
    if type(sender) is not str:
        raise TypeError("sender must be an exact string")
    return category in _OWNER_ACTION_CATEGORIES and not bulk_sender(sender)
