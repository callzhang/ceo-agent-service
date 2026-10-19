"""Weekly calendar and destination policy, owned by this Skill."""

from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
import json
import argparse
import subprocess
from pathlib import Path
from zoneinfo import ZoneInfo
from typing import Any

DwsError = ValueError
REPORT_TIME_ZONE = ZoneInfo("Asia/Shanghai")
MANAGEMENT_WIKI_NAME = "🎯  目标与执行"
MEETING_ROOT_FOLDER_NAME = "1. 管理层周会"


class Dws:
    dws_bin = "dws"

    def run_json(self, argv):
        return json.loads(
            subprocess.run(argv, check=True, capture_output=True, text=True).stdout
        )

    def fetch_report_document_full(self, node_id):
        return self.run_json(
            [
                self.dws_bin,
                "doc",
                "+fetch",
                "--node",
                node_id,
                "--detail",
                "full",
                "--scope",
                "full",
                "--format",
                "json",
            ]
        )


@dataclass(frozen=True)
class WeeklyWindow:
    target_monday: date
    start: datetime
    end: datetime
    cutoff: datetime


def weekly_window(run_at: datetime) -> WeeklyWindow:
    """The week before the first Monday after the run's Beijing date.

    A Saturday run reports for the Monday meeting two days later, never for
    the one already held. The window closes at that Monday 00:00 Beijing, or
    at the run itself when it runs early.
    """
    local_date = run_at.astimezone(REPORT_TIME_ZONE).date()
    days_ahead = (7 - local_date.weekday()) % 7 or 7
    target = local_date + timedelta(days=days_ahead)
    end = datetime.combine(target, time.min, tzinfo=REPORT_TIME_ZONE).astimezone(UTC)
    start = end - timedelta(days=7)
    return WeeklyWindow(
        target_monday=target,
        start=start,
        end=end,
        cutoff=min(run_at.astimezone(UTC), end),
    )


def meeting_title(monday: date) -> str:
    return f"{monday.year}年{monday.month}月{monday.day}日管理层周会"


def year_page_name(year: int) -> str:
    return f"{year} 年：管理层周会"


def resolve_meeting_documents(dws, window: WeeklyWindow) -> dict[str, Any]:
    """Find this week's and last week's meeting documents by title.

    Each year's meetings are child documents of that year's page (a document,
    not a folder) under the meeting folder. Past titles vary in spacing
    (`2026 年：管理层周会`, `2025年：管理层周会`, a trailing space), so names
    are compared without whitespace. When the prior Monday title is absent,
    use the nearest exact canonical meeting title dated in the preceding
    reporting week; suffixed drafts are not meeting-document matches.
    """
    workspace_id = _single(
        [
            space
            for space in _data_list(
                dws.run_json(
                    [
                        dws.dws_bin,
                        "wiki",
                        "+space-list",
                        "--type",
                        "orgWikiSpace",
                        "--limit",
                        "50",
                        "--page-all",
                        "--format",
                        "json",
                    ]
                ),
                "spaces",
            )
            if space.get("name") == MANAGEMENT_WIKI_NAME
        ],
        f"wiki {MANAGEMENT_WIKI_NAME!r}",
    )["workspaceId"]
    root = _single(
        [
            node
            for node in _nodes(dws, workspace_id, parent_id=None)
            if _same_name(node, MEETING_ROOT_FOLDER_NAME)
        ],
        f"folder {MEETING_ROOT_FOLDER_NAME!r}",
    )
    root_children = _nodes(dws, workspace_id, parent_id=root["nodeId"])
    documents: dict[str, Any] = {
        "workspace_id": workspace_id,
        "meeting_folder_id": root["nodeId"],
    }
    year_pages: dict[int, dict[str, Any] | None] = {}
    year_documents: dict[int, list[dict[str, Any]]] = {}

    def page_for(day: date) -> dict[str, Any] | None:
        if day.year not in year_pages:
            year_pages[day.year] = _at_most_one(
                [
                    node
                    for node in root_children
                    if _same_name(node, year_page_name(day.year))
                ],
                f"year page {year_page_name(day.year)!r}",
            )
        return year_pages[day.year]

    def document_for(day: date) -> dict[str, Any] | None:
        year_page = page_for(day)
        if year_page is None or not year_page.get("hasChildren"):
            return None
        if day.year not in year_documents:
            year_documents[day.year] = _nodes(
                dws, workspace_id, parent_id=year_page["nodeId"]
            )
        title = meeting_title(day)
        return _at_most_one(
            [node for node in year_documents[day.year] if _same_name(node, title)],
            f"document {title!r}",
        )

    previous_monday = window.target_monday - timedelta(days=7)
    for label, expected_day in (
        ("target", window.target_monday),
        ("previous", previous_monday),
    ):
        resolved_day = expected_day
        found = document_for(expected_day)
        if label == "previous" and found is None:
            for offset in range(1, 7):
                candidate_day = expected_day - timedelta(days=offset)
                found = document_for(candidate_day)
                if found is not None:
                    resolved_day = candidate_day
                    break
        year_page = page_for(resolved_day)
        title = (
            meeting_title(resolved_day)
            if found is not None
            else meeting_title(expected_day)
        )
        documents[label] = {
            "title": title,
            "expected_title": meeting_title(expected_day),
            "year_page_name": year_page_name(resolved_day.year),
            "year_page_id": year_page["nodeId"] if year_page is not None else "",
            "exists": found is not None,
            "node_id": found["nodeId"] if found is not None else "",
            "url": (found.get("url") or found.get("docUrl") or "")
            if found is not None
            else "",
        }
    return documents


def _nodes(dws, workspace_id: str, *, parent_id: str | None) -> list[dict[str, Any]]:
    command = [dws.dws_bin, "wiki", "+node-list", "--workspace", workspace_id]
    if parent_id:
        command += ["--folder", parent_id]
    return _data_list(
        dws.run_json(command + ["--page-all", "--format", "json"]), "nodes"
    )


def _same_name(node: dict[str, Any], name: str) -> bool:
    return "".join(str(node.get("name") or "").split()) == "".join(name.split())


def _data_list(payload: Any, key: str) -> list[dict[str, Any]]:
    data = payload.get("data") if isinstance(payload, dict) else None
    items = data.get(key) if isinstance(data, dict) else None
    if not isinstance(items, list):
        raise DwsError(f"dws listing did not return data.{key}")
    if data.get("hasMore"):
        raise DwsError(f"dws listing of {key} is incomplete")
    return [item for item in items if isinstance(item, dict)]


def _single(matches: list[dict[str, Any]], what: str) -> dict[str, Any]:
    if len(matches) != 1:
        raise DwsError(f"expected exactly one {what}, found {len(matches)}")
    return matches[0]


def _at_most_one(matches: list[dict[str, Any]], what: str) -> dict[str, Any] | None:
    if len(matches) > 1:
        raise DwsError(f"more than one {what}")
    return matches[0] if matches else None


def _weekly_report_jsonml_is_blank(document: object) -> bool:
    """Recognize only an empty DingTalk paragraph skeleton as a blank report."""
    if not isinstance(document, list) or len(document) < 3 or document[0] != "root":
        return False

    def blank_node(node: object) -> bool:
        if not isinstance(node, list) or len(node) < 2:
            return False
        tag, attributes = node[0], node[1]
        if tag == "p":
            if not isinstance(attributes, dict):
                return False
        elif tag == "span":
            if not isinstance(attributes, dict) or attributes.get("data-type") not in {
                "text",
                "leaf",
            }:
                return False
        else:
            return False
        for child in node[2:]:
            if isinstance(child, str):
                if child.strip():
                    return False
            elif isinstance(child, list):
                if not blank_node(child):
                    return False
            else:
                return False
        return True

    for child in document[2:]:
        if not blank_node(child):
            return False
    return True


def render_source(dws, documents):
    target = documents["target"]
    exists = target["exists"]
    selected = target if exists else documents["previous"]

    def fetch(item):
        if not item.get("exists") or not item.get("node_id"):
            raise ValueError("meeting template is unavailable")
        payload = dws.fetch_report_document_full(item["node_id"])
        content = payload.get("content")
        if (
            payload.get("complete") is not True
            or not isinstance(content, dict)
            or content.get("success") is not True
            or content.get("nodeId") != item["node_id"]
        ):
            raise ValueError("meeting document full content is unavailable")
        raw = content.get("jsonml")
        doc = json.loads(raw) if isinstance(raw, str) else raw
        if not isinstance(doc, list) or len(doc) < 3 or doc[0] != "root":
            raise ValueError("meeting template must be complete JSONML")
        return content, doc

    content, before = fetch(selected)
    revision = None
    if exists:
        raw = content.get("revision")
        revision = int(raw)
        if revision < 0 or str(revision) != str(raw):
            raise ValueError("invalid document revision")
        if _weekly_report_jsonml_is_blank(before):
            selected = documents["previous"]
            _content, before = fetch(selected)
    return before, {
        **target,
        "expected_revision": revision,
        "render_source_title": selected["title"],
    }


def prepare(context, dws, ensure_year_page=False):
    run_at = datetime.fromisoformat(context["scheduled_for"])
    window = weekly_window(run_at)
    documents = resolve_meeting_documents(dws, window)
    if ensure_year_page and not documents["target"]["year_page_id"]:
        dws.run_json(
            [
                dws.dws_bin,
                "wiki",
                "+node-create",
                "--workspace",
                documents["workspace_id"],
                "--folder",
                documents["meeting_folder_id"],
                "--name",
                documents["target"]["year_page_name"],
                "--type",
                "adoc",
                "--format",
                "json",
            ]
        )
        documents = resolve_meeting_documents(dws, window)
    before, target = render_source(dws, documents)
    return {
        "target_monday": window.target_monday.isoformat(),
        "window_utc": {
            "start": window.start.isoformat(),
            "end": window.cutoff.isoformat(),
        },
        "documents": documents,
        "target": target,
    }, before


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--context", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--before", required=True)
    parser.add_argument("--ensure-year-page", action="store_true")
    args = parser.parse_args()
    materials, before = prepare(
        json.loads(Path(args.context).read_text()), Dws(), args.ensure_year_page
    )
    Path(args.output).write_text(json.dumps(materials, ensure_ascii=False))
    Path(args.before).write_text(json.dumps(before, ensure_ascii=False))
    print(json.dumps(materials, ensure_ascii=False))
