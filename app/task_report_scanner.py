"""Discover authoritative weekly reports and queue Task-first Work Items.

The report itself is the source signal.  This scanner only discovers and
indexes documents; the Task Agent remains responsible for extracting explicit
projects, tasks, owners, dates, and risks from the report content.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from app.dws_client import DwsDocumentSearchResult
from app.store import AutoReplyStore
from app.task_models import WorkItem, WorkItemSourceType


REPORT_SCANNER = "task_reports"
REPORT_SEARCHES = (
    "管理周报",
    "项目管理部周报",
    "产研管理周报",
)
REPORT_SEARCH_PAGE_SIZE = 30
_PERIOD_PATTERNS = (
    re.compile(r"(?P<year>20\d{2})年(?P<month>\d{1,2})月(?P<day>\d{1,2})日"),
    re.compile(r"(?P<year>20\d{2})[.-](?P<month>\d{1,2})[.-](?P<day>\d{1,2})"),
    re.compile(r"(?P<year>20\d{2})(?P<month>\d{2})(?P<day>\d{2})"),
    re.compile(r"(?P<year>20\d{2})-?W(?P<week>\d{1,2})", re.IGNORECASE),
    re.compile(r"(?P<year>20\d{2})年?第(?P<week>\d{1,2})周"),
)


@dataclass(frozen=True)
class ReportDocument:
    node_id: str
    title: str
    url: str
    report_type: WorkItemSourceType
    period: str
    markdown: str

    @property
    def source_ref(self) -> str:
        digest = hashlib.sha256(self.markdown.encode("utf-8")).hexdigest()
        return f"dingtalk-doc:{self.node_id}#sha256={digest}"


def classify_report_title(title: str) -> WorkItemSourceType | None:
    """Classify only explicit report titles; templates and generic docs are ignored."""
    normalized = "".join(str(title or "").split()).casefold()
    if "周报" not in normalized or not report_period(title):
        return None
    if "项目管理部周报" in normalized or "项目管理周报" in normalized:
        return WorkItemSourceType.PROJECT_WEEKLY_REPORT
    if "产研管理周报" in normalized or "部门周报" in normalized:
        return WorkItemSourceType.DEPARTMENT_WEEKLY_REPORT
    if "管理周报" in normalized:
        return WorkItemSourceType.MANAGEMENT_WEEKLY_REPORT
    return None


def report_period(title: str, markdown: str = "") -> str:
    text = f"{title}\n{markdown[:1200]}"
    for pattern in _PERIOD_PATTERNS:
        match = pattern.search(text)
        if not match:
            continue
        groups = match.groupdict()
        if groups.get("week"):
            return f"{groups['year']}-W{int(groups['week']):02d}"
        return f"{groups['year']}-{int(groups['month']):02d}-{int(groups['day']):02d}"
    return ""


def _search_results(dws: Any) -> list[DwsDocumentSearchResult]:
    by_node: dict[str, DwsDocumentSearchResult] = {}
    for query in REPORT_SEARCHES:
        results = dws.search_documents(query, page_size=REPORT_SEARCH_PAGE_SIZE)
        for result in results:
            if isinstance(result, DwsDocumentSearchResult):
                document = result
            elif isinstance(result, dict):
                document = DwsDocumentSearchResult(
                    node_id=str(result.get("node_id") or result.get("nodeId") or ""),
                    name=str(result.get("name") or result.get("title") or ""),
                    extension=str(result.get("extension") or ""),
                    content_type=str(result.get("content_type") or result.get("contentType") or ""),
                    node_type=str(result.get("node_type") or result.get("nodeType") or ""),
                    doc_url=str(result.get("doc_url") or result.get("docUrl") or result.get("url") or ""),
                )
            else:
                continue
            if document.node_id:
                by_node[document.node_id] = document
    return list(by_node.values())


def discover_report_documents(dws: Any) -> list[ReportDocument]:
    documents: list[ReportDocument] = []
    for result in _search_results(dws):
        if result.extension and result.extension.casefold() != "adoc":
            continue
        report_type = classify_report_title(result.name)
        if report_type is None:
            continue
        payload = dws.read_doc(result.node_id)
        if not isinstance(payload, dict):
            continue
        markdown = str(payload.get("markdown") or payload.get("content") or payload.get("text") or "").strip()
        if not markdown:
            continue
        documents.append(
            ReportDocument(
                node_id=result.node_id,
                title=result.name.strip(),
                url=result.doc_url.strip(),
                report_type=report_type,
                period=report_period(result.name, markdown),
                markdown=markdown,
            )
        )
    return documents


def _work_item(document: ReportDocument) -> WorkItem:
    summary = json.dumps(
        {
            "report": {
                "title": document.title,
                "document_id": document.node_id,
                "url": document.url,
                "report_type": document.report_type.value,
                "reporting_period": document.period,
            },
            "markdown": document.markdown,
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return WorkItem.model_validate(
        {
            "source": {
                "type": document.report_type.value,
                "ref": document.source_ref,
                "title": document.title,
                "conversation_title": document.title,
            },
            "summary": summary,
            "context": {
                "assignment_authorized": True,
                "source_conversation_kind": "file",
                "source_conversation_title": document.title,
            },
        }
    )


def scan_task_reports(
    store: AutoReplyStore,
    dws: Any,
    *,
    max_new_items: int | None = None,
) -> int:
    """Queue changed report documents idempotently and return new revisions."""
    state = store.get_daily_scan_state(REPORT_SCANNER) or {}
    try:
        cursor = json.loads(state.get("cursor_json") or "{}")
    except (TypeError, json.JSONDecodeError):
        cursor = {}
    seen_refs = {str(value) for value in cursor.get("seen_refs", [])}
    queued = 0
    errors: list[str] = []
    try:
        documents = discover_report_documents(dws)
    except Exception as exc:
        store.set_daily_scan_state(
            REPORT_SCANNER,
            last_success_at=state.get("last_success_at") or "",
            cursor_json=json.dumps({"seen_refs": sorted(seen_refs)}, ensure_ascii=False),
            last_error=str(exc),
        )
        return 0
    for document in documents:
        if document.source_ref in seen_refs:
            continue
        if max_new_items is not None and queued >= max_new_items:
            continue
        try:
            item = _work_item(document)
            store.enqueue_work_summary_input(
                source_type=item.source.type.value,
                source_ref=item.source.ref,
                payload_json=item.model_dump_json(),
            )
            seen_refs.add(document.source_ref)
            queued += 1
        except Exception as exc:
            errors.append(f"{document.node_id}: {exc}")
    store.set_daily_scan_state(
        REPORT_SCANNER,
        last_success_at="" if errors else datetime.now(timezone.utc).isoformat(),
        cursor_json=json.dumps({"seen_refs": sorted(seen_refs)}, ensure_ascii=False),
        last_error="; ".join(errors),
    )
    return queued
