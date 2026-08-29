"""Shared response models and safe display/JSON conversion helpers."""

import json
from datetime import date, datetime, time, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ApiMeta(BaseModel):
    model_config = ConfigDict(extra="forbid")

    snapshot_at: str


class ApiListMeta(ApiMeta):
    model_config = ConfigDict(extra="forbid")

    page: int | None = None
    page_size: int | None = None
    total: int | None = None
    next_cursor: str = ""
    has_more: bool = False


class ApiListEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[Any] = Field(default_factory=list)
    meta: ApiListMeta


class ApiItemEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid")

    item: Any
    meta: ApiMeta


def snapshot_at() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def json_safe(value: Any) -> Any:
    """Convert model and provider values into JSON-compatible values."""
    if isinstance(value, BaseModel):
        return json_safe(value.model_dump(mode="json"))
    if isinstance(value, Enum):
        return json_safe(value.value)
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {str(key): json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [json_safe(item) for item in value]
    return str(value)


def normalize_display_value(value: Any, *, max_length: int = 2000) -> str:
    """Return a bounded human-readable string for values used in display fields.

    API payloads may contain provider dictionaries or lists.  They must never be
    passed through JavaScript's implicit string conversion, which produces the
    unhelpful ``[object Object]``.  Known display keys are preferred; otherwise
    a compact JSON representation is returned.
    """
    if value is None:
        return ""
    if isinstance(value, str):
        text = value
    elif isinstance(value, dict):
        preferred = next(
            (
                value[key]
                for key in ("title", "text", "content")
                if key in value and value[key] not in (None, "")
            ),
            None,
        )
        if preferred is not None and not isinstance(preferred, (dict, list, tuple)):
            text = normalize_display_value(preferred, max_length=max_length)
        else:
            text = json.dumps(
                json_safe(value), ensure_ascii=False, separators=(", ", ": ")
            )
    elif isinstance(value, (list, tuple, set, frozenset)):
        text = json.dumps(
            json_safe(value), ensure_ascii=False, separators=(", ", ": ")
        )
    else:
        text = str(json_safe(value))
    if len(text) <= max_length:
        return text
    return text[: max(0, max_length - 1)].rstrip() + "…"
