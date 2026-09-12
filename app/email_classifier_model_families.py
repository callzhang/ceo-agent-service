"""Model-family capabilities exposed by the Email staged-training lifecycle."""

from __future__ import annotations

from typing import Final


MODEL_FAMILY_CATALOG: Final[tuple[dict[str, object], ...]] = (
    {"family": "tfidf-logistic-regression", "display_name": "TF-IDF", "supported": False, "configured": False, "reason": "当前 durable staged controller 尚未接入 TF-IDF executor"},
    {"family": "fasttext", "display_name": "fastText", "supported": False, "configured": False, "reason": "当前没有 fastText executor"},
    {"family": "embedding-mlp", "display_name": "Embedding + MLP", "supported": True, "configured": True, "reason": "当前 durable staged controller 已接入 Embedding + MLP executor"},
)
MODEL_FAMILY_BY_KEY: Final[dict[str, dict[str, object]]] = {str(row["family"]): dict(row) for row in MODEL_FAMILY_CATALOG}
SUPPORTED_STAGED_MODEL_FAMILIES: Final[frozenset[str]] = frozenset(
    family for family, row in MODEL_FAMILY_BY_KEY.items() if row["supported"] is True
)


def model_family_catalog() -> list[dict[str, object]]:
    return [dict(row) for row in MODEL_FAMILY_CATALOG]


def validate_model_families(values: list[str]) -> list[str]:
    if not values or any(not isinstance(value, str) or not value.strip() for value in values):
        raise ValueError("model_families must be a non-empty list of strings")
    normalized = sorted({value.strip() for value in values})
    if len(normalized) != len(values):
        raise ValueError("model_families contains duplicates")
    if set(normalized) - set(MODEL_FAMILY_BY_KEY):
        raise ValueError("unknown model family")
    if set(normalized) - SUPPORTED_STAGED_MODEL_FAMILIES:
        raise ValueError("unsupported model family")
    return normalized
