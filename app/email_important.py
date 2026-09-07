"""Provider-neutral important-signal normalization."""

from __future__ import annotations

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
