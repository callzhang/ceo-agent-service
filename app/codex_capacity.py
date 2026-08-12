from __future__ import annotations


CODEX_PROVIDER_UNAVAILABLE = "codex_provider_unavailable"
CODEX_PROVIDER_CAPACITY_EXHAUSTED = "codex_provider_capacity_exhausted"
CODEX_CAPACITY_EXHAUSTED_MESSAGE = (
    "Codex workspace credits are exhausted; work is paused until the next "
    "capacity check"
)


def codex_provider_failure_code(value: object) -> str:
    """Classify provider output without persisting its unbounded raw response."""
    text = str(value).strip().casefold()
    if text == CODEX_PROVIDER_CAPACITY_EXHAUSTED:
        return CODEX_PROVIDER_CAPACITY_EXHAUSTED
    if text == CODEX_PROVIDER_UNAVAILABLE:
        return CODEX_PROVIDER_UNAVAILABLE
    if any(
        marker in text
        for marker in (
            "workspace is out of credits",
            "hit your usage limit",
            "quota exceeded",
        )
    ):
        return CODEX_PROVIDER_CAPACITY_EXHAUSTED
    return CODEX_PROVIDER_UNAVAILABLE


def is_codex_capacity_exhausted(value: object) -> bool:
    return codex_provider_failure_code(value) == CODEX_PROVIDER_CAPACITY_EXHAUSTED


def is_codex_provider_recovery_code(value: object) -> bool:
    return str(value).strip() in {
        CODEX_PROVIDER_UNAVAILABLE,
        CODEX_PROVIDER_CAPACITY_EXHAUSTED,
    }
