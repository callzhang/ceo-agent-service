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
    detail_label: str = ""
    detail: str = ""
    detail_url: str = ""


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
    detail_label: str = ""
    detail: str = ""
    updated_at: str
    records: list[AttentionRecord] = Field(default_factory=list)


class AttentionListEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[AttentionGroup] = Field(default_factory=list)
    meta: ApiListMeta


def _record(row: dict[str, Any]) -> AttentionRecord:
    error = normalize_display_value(row.get("error"))
    status = normalize_display_value(row.get("status"))
    detail_label, detail = _detail(status, error)
    context = normalize_display_value(row.get("context"))
    root_cause = normalize_display_value(row.get("root_cause"))
    error_code = normalize_display_value(row.get("error_code"))
    if not root_cause:
        root_cause = error or error_code or detail or context or "unknown"
    return AttentionRecord(
        id=normalize_display_value(row.get("id")),
        category=normalize_display_value(row.get("category")),
        status=status,
        context=context,
        root_cause=root_cause,
        error_code=error_code,
        summary=normalize_display_value(row.get("summary")),
        updated_at=normalize_display_value(row.get("updated_at")),
        error=error,
        detail_label=detail_label,
        detail=detail,
        detail_url=normalize_display_value(row.get("detail_url")),
    )


def _detail(status: str, error: str) -> tuple[str, str]:
    if error:
        return "错误", error
    normalized = status.strip().lower()
    if normalized == "pending":
        return "状态", "已入队，等待执行。"
    if normalized == "processing":
        return "状态", "正在执行。"
    if normalized == "failed":
        return "错误", "任务失败，但未记录具体错误；请检查执行历史和服务日志。"
    return "状态", f"当前状态：{status or '未提供'}。"


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
                detail_label=latest.detail_label,
                detail=latest.detail,
                updated_at=latest.updated_at,
                records=records,
            )
        )
    return sorted(result, key=lambda group: (group.updated_at, group.category), reverse=True)
