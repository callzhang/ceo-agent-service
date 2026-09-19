"""Model-family capabilities exposed by the Email staged-training lifecycle."""

from __future__ import annotations

import os
from collections.abc import Mapping
from typing import Final


MODEL_FAMILY_CATALOG: Final[tuple[dict[str, object], ...]] = (
    {
        "family": "tfidf-logistic-regression",
        "display_name": "TF-IDF",
        "supported": True,
        "configured": True,
        "reason": "已接入 durable staged controller",
    },
    {
        "family": "fasttext",
        "display_name": "fastText",
        "supported": True,
        "configured": True,
        "reason": "已接入 durable staged controller",
    },
    {
        "family": "embedding-mlp",
        "display_name": "语义嵌入 + 字片模型",
        "supported": True,
        "configured": True,
        "reason": "两种读法各判一次再取平均：语义嵌入读这封信在讲什么，字片模型读它是怎么写的",
    },
)
MODEL_FAMILY_BY_KEY: Final[dict[str, dict[str, object]]] = {
    str(row["family"]): dict(row) for row in MODEL_FAMILY_CATALOG
}
SUPPORTED_STAGED_MODEL_FAMILIES: Final[frozenset[str]] = frozenset(
    family for family, row in MODEL_FAMILY_BY_KEY.items() if row["supported"] is True
)


def model_family_catalog(
    *, environ: Mapping[str, str] | None = None
) -> list[dict[str, object]]:
    values = os.environ if environ is None else environ
    catalog = [dict(row) for row in MODEL_FAMILY_CATALOG]
    embedding = next(row for row in catalog if row["family"] == "embedding-mlp")
    missing = [
        key
        for key in (
            "CEO_EMAIL_EMBEDDING_URL",
            "CEO_EMAIL_EMBEDDING_DIMENSION",
            "CEO_EMAIL_EMBEDDING_REVISION",
        )
        if not values.get(key, "").strip()
    ]
    if missing:
        embedding["configured"] = False
        embedding["reason"] = "缺少 Embedding 训练配置：" + "、".join(missing)
    else:
        try:
            if int(values["CEO_EMAIL_EMBEDDING_DIMENSION"]) < 1:
                raise ValueError
        except ValueError:
            embedding["configured"] = False
            embedding["reason"] = "CEO_EMAIL_EMBEDDING_DIMENSION 必须是正整数"
    return catalog


def validate_model_families(values: list[str]) -> list[str]:
    if not values or any(
        not isinstance(value, str) or not value.strip() for value in values
    ):
        raise ValueError("model_families must be a non-empty list of strings")
    normalized = sorted({value.strip() for value in values})
    if len(normalized) != len(values):
        raise ValueError("model_families contains duplicates")
    if set(normalized) - set(MODEL_FAMILY_BY_KEY):
        raise ValueError("unknown model family")
    if set(normalized) - SUPPORTED_STAGED_MODEL_FAMILIES:
        raise ValueError("unsupported model family")
    return normalized
