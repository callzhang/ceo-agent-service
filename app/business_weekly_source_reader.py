"""Unselected exact weekly-report read from a body-free document locator."""

from __future__ import annotations

import json
from typing import Any

from app.dws_client import DwsError
from app.task_models import WorkItemSourceType
from app.task_report_scanner import ReportDocument, _work_item, classify_report_title, report_period


WEEKLY_SOURCE_TYPES = frozenset({
    WorkItemSourceType.MANAGEMENT_WEEKLY_REPORT.value,
    WorkItemSourceType.PROJECT_WEEKLY_REPORT.value,
    WorkItemSourceType.DEPARTMENT_WEEKLY_REPORT.value,
})


class WeeklyInvalidLocator(ValueError):
    """The locator does not identify a canonical report source."""


class WeeklySourceIncomplete(ValueError):
    """The provider returned an incomplete or contradictory document."""


class WeeklySourceUnavailable(LookupError):
    """The exact provider document is unreadable."""


class WeeklySourceVersionChanged(LookupError):
    """The returned source identity or version differs from the requested one."""


def _ref_node(source_ref: str) -> str:
    prefix = "dingtalk-doc:"
    if not isinstance(source_ref, str) or not source_ref.startswith(prefix):
        raise WeeklyInvalidLocator("invalid weekly source reference")
    node, separator, digest = source_ref[len(prefix):].partition("#sha256=")
    if not node or not separator or len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
        raise WeeklyInvalidLocator("invalid weekly source reference")
    return node


def _document(*, node: str, url: str, title: str, markdown: str) -> ReportDocument:
    kind = classify_report_title(title)
    if kind is None or kind.value not in WEEKLY_SOURCE_TYPES:
        raise WeeklySourceIncomplete("unclassified weekly report title")
    return ReportDocument(
        node_id=node, title=title, url=url, report_type=kind,
        period=report_period(title, markdown), markdown=markdown,
    )


def weekly_provider_locator(signal: Any, *, history_version: int | None = None) -> dict[str, object]:
    """Validate the transient producer body and retain only source coordinates."""
    if signal.source_type not in WEEKLY_SOURCE_TYPES:
        raise ValueError("source is not a weekly report")
    node = _ref_node(signal.source_ref)
    if history_version is not None and (type(history_version) is not int or history_version < 0):
        raise ValueError("history version must be a nonnegative integer")
    try:
        original = json.loads(signal.evidence_text)
    except (TypeError, json.JSONDecodeError) as exc:
        raise ValueError("invalid canonical weekly body") from exc
    if not isinstance(original, dict) or set(original) != {"report", "markdown"}:
        raise ValueError("unsupported weekly body")
    report = original["report"]
    if not isinstance(report, dict) or set(report) != {
        "title", "document_id", "url", "report_type", "reporting_period",
    }:
        raise ValueError("unsupported weekly report metadata")
    title, markdown, url = report["title"], original["markdown"], report["url"]
    if (
        not isinstance(title, str) or not title or not isinstance(markdown, str)
        or not markdown or not isinstance(url, str) or report["document_id"] != node
    ):
        raise ValueError("invalid weekly report fields")
    document = _document(node=node, url=url, title=title, markdown=markdown)
    if (
        document.report_type.value != signal.source_type
        or report["report_type"] != document.report_type.value
        or report["reporting_period"] != document.period
        or document.source_ref != signal.source_ref
        or _work_item(document).summary != signal.evidence_text
    ):
        raise ValueError("weekly body is not producer canonical format")
    locator: dict[str, object] = {"kind": "weekly_report", "node_id": node, "url": url}
    if history_version is not None:
        locator["historyVersion"] = history_version
    return locator


def read_weekly_source_body(
    locator: object, *, source_type: str, source_ref: str, dws: Any,
) -> str:
    """Reconstruct the producer body from one exact native document response."""
    if source_type not in WEEKLY_SOURCE_TYPES:
        raise WeeklyInvalidLocator("unsupported weekly source type")
    node = _ref_node(source_ref)
    if not isinstance(locator, dict) or set(locator) not in (
        {"kind", "node_id", "url"}, {"kind", "node_id", "url", "historyVersion"},
    ):
        raise WeeklyInvalidLocator("invalid weekly locator")
    if (
        locator["kind"] != "weekly_report" or locator["node_id"] != node
        or not isinstance(locator["url"], str)
    ):
        raise WeeklyInvalidLocator("invalid weekly locator identity")
    version = locator.get("historyVersion")
    if "historyVersion" in locator and (type(version) is not int or version < 0):
        raise WeeklyInvalidLocator("invalid history version")
    command = [dws.dws_bin, "doc", "+fetch", "--node", node, "--format", "json"]
    if version is not None:
        command.extend(["--version", str(version)])
    try:
        payload = dws.run_json(command)
    except DwsError as exc:
        raise WeeklySourceUnavailable("weekly provider source unreadable") from exc
    if not isinstance(payload, dict):
        raise WeeklySourceIncomplete("invalid weekly provider response")
    content, target = payload.get("content"), payload.get("target")
    if (
        payload.get("complete") is not True or payload.get("status") != "success"
        or not isinstance(content, dict) or content.get("success") is not True
        or not isinstance(target, dict)
    ):
        raise WeeklySourceIncomplete("weekly provider response incomplete")
    if content.get("nodeId") != node or target.get("canonicalId") != node:
        raise WeeklySourceVersionChanged("weekly provider node changed")
    if version is not None and (
        type(content.get("historyVersion")) is not int
        or content["historyVersion"] != version
    ):
        raise WeeklySourceVersionChanged("weekly provider history version changed")
    title, markdown = content.get("title"), content.get("markdown")
    if not isinstance(title, str) or not title.strip() or not isinstance(markdown, str) or not markdown.strip():
        raise WeeklySourceIncomplete("weekly provider title or markdown missing")
    document = _document(node=node, url=locator["url"], title=title.strip(), markdown=markdown.strip())
    if document.report_type.value != source_type or document.source_ref != source_ref:
        raise WeeklySourceVersionChanged("weekly provider content changed")
    return _work_item(document).summary
