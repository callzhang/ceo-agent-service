"""Attention row DTOs and root-cause grouping."""

from typing import Any, Iterable

from pydantic import BaseModel, ConfigDict, Field

from app.web_api.common import ApiListMeta, normalize_display_value


class AttentionRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    category: str
    status: str
    context: str
    root_cause: str = ""
    error_code: str = ""
    summary: str
    updated_at: str
    error: str = ""


class AttentionGroup(BaseModel):
    model_config = ConfigDict(extra="forbid")

    category: str
    root_cause: str
    error_code: str = ""
    context: str
    severity: str
    count: int
    summary: str
    error: str = ""
    updated_at: str
    records: list[AttentionRecord] = Field(default_factory=list)


class AttentionListEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[AttentionGroup] = Field(default_factory=list)
    meta: ApiListMeta


def _record(row: dict[str, Any]) -> AttentionRecord:
    error = normalize_display_value(row.get("error"))
    context = normalize_display_value(row.get("context"))
    root_cause = normalize_display_value(row.get("root_cause"))
    error_code = normalize_display_value(row.get("error_code"))
    if not root_cause:
        root_cause = error or error_code or context or "unknown"
    return AttentionRecord(
        id=normalize_display_value(row.get("id")),
        category=normalize_display_value(row.get("category")),
        status=normalize_display_value(row.get("status")),
        context=context,
        root_cause=root_cause,
        error_code=error_code,
        summary=normalize_display_value(row.get("summary")),
        updated_at=normalize_display_value(row.get("updated_at")),
        error=error,
    )


def group_attention_rows(rows: Iterable[dict[str, Any]]) -> list[AttentionGroup]:
    grouped: dict[tuple[str, str, str], list[AttentionRecord]] = {}
    for row in rows:
        record = _record(row)
        cause_key = record.root_cause or record.error_code or "unknown"
        key = (record.category, cause_key, record.context)
        grouped.setdefault(key, []).append(record)
    result = []
    for (category, root_cause, context), records in grouped.items():
        latest = max(records, key=lambda item: item.updated_at)
        severity = "error" if any(item.status == "failed" for item in records) else "warning"
        result.append(
            AttentionGroup(
                category=category,
                root_cause=root_cause,
                error_code=next(
                    (record.error_code for record in records if record.error_code),
                    "",
                ),
                context=context,
                severity=severity,
                count=len(records),
                summary=latest.summary,
                error=latest.error,
                updated_at=latest.updated_at,
                records=records,
            )
        )
    return sorted(result, key=lambda group: (group.updated_at, group.category), reverse=True)
