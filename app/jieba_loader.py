from __future__ import annotations

import importlib
import warnings
from functools import lru_cache
from typing import Any


_PKG_RESOURCES_WARNING = r"pkg_resources is deprecated as an API.*"


@lru_cache(maxsize=1)
def _jieba() -> Any:
    # jieba 0.42.1 still imports pkg_resources. Keep that upstream warning at
    # the dependency boundary without muting warnings from our own code.
    with warnings.catch_warnings():
        warnings.filterwarnings(
            "ignore",
            message=_PKG_RESOURCES_WARNING,
            category=UserWarning,
            module=r"jieba\._compat",
        )
        return importlib.import_module("jieba")


@lru_cache(maxsize=1)
def _jieba_analyse() -> Any:
    _jieba()
    return importlib.import_module("jieba.analyse")


def jieba_lcut(text: str) -> list[str]:
    return list(_jieba().lcut(text))


def jieba_extract_tags(
    text: str,
    *,
    top_k: int,
) -> list[tuple[str, float]]:
    return list(
        _jieba_analyse().extract_tags(
            text,
            topK=top_k,
            withWeight=True,
        )
    )
