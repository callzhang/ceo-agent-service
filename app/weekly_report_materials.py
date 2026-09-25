"""Collect the fixed inputs of one CEO weekly report run.

The weekly report is an Agent task, and its runs used to stop before writing
anything: the Skill asked for the target document's link as an input the
trigger never had, and one 20-minute turn could not also inventory a week of
minutes one API call at a time (runs 384443/384444). This module is the
deterministic half: it works out which Monday meeting the report is for,
finds that meeting's document and the previous one by their titles, and
lists the week's meetings from the service's meeting queue, pointing at
the transcripts the minutes sync archived. It only reads.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
import json
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from app.dws_client import DwsError
from app.minutes_sync import archive_path_for


REPORT_TIME_ZONE = ZoneInfo("Asia/Shanghai")
MANAGEMENT_WIKI_NAME = "🎯  目标与执行"
MEETING_ROOT_FOLDER_NAME = "1. 管理层周会"


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
        target_monday=target, start=start, end=end, cutoff=min(run_at.astimezone(UTC), end)
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
    are compared without whitespace.
    """
    workspace_id = _single(
        [
            space for space in _data_list(
                dws.run_json([
                    dws.dws_bin, "wiki", "+space-list", "--type", "orgWikiSpace",
                    "--limit", "50", "--page-all", "--format", "json",
                ]),
                "spaces",
            )
            if space.get("name") == MANAGEMENT_WIKI_NAME
        ],
        f"wiki {MANAGEMENT_WIKI_NAME!r}",
    )["workspaceId"]
    root = _single(
        [
            node for node in _nodes(dws, workspace_id, parent_id=None)
            if _same_name(node, MEETING_ROOT_FOLDER_NAME)
        ],
        f"folder {MEETING_ROOT_FOLDER_NAME!r}",
    )
    root_children = _nodes(dws, workspace_id, parent_id=root["nodeId"])
    documents: dict[str, Any] = {
        "workspace_id": workspace_id,
        "meeting_folder_id": root["nodeId"],
    }
    previous_monday = window.target_monday - timedelta(days=7)
    for label, monday in (("target", window.target_monday), ("previous", previous_monday)):
        year_page = _at_most_one(
            [node for node in root_children if _same_name(node, year_page_name(monday.year))],
            f"year page {year_page_name(monday.year)!r}",
        )
        title = meeting_title(monday)
        found = (
            _at_most_one(
                [
                    node for node in _nodes(dws, workspace_id, parent_id=year_page["nodeId"])
                    if _same_name(node, title)
                ],
                f"document {title!r}",
            )
            if year_page is not None and year_page.get("hasChildren")
            else None
        )
        documents[label] = {
            "title": title,
            "year_page_name": year_page_name(monday.year),
            "year_page_id": year_page["nodeId"] if year_page is not None else "",
            "exists": found is not None,
            "node_id": found["nodeId"] if found is not None else "",
            "url": (found.get("url") or found.get("docUrl") or "") if found is not None else "",
        }
    return documents


def minutes_index(store, archive_dir: Path, window: WeeklyWindow) -> list[dict[str, Any]]:
    """Every meeting the service discovered that ended inside the window.

    The meeting queue is the service's record of what was held; the archive
    path comes from the minutes sync's own layout, and is empty when that
    minute has no archived transcript.
    """
    entries = []
    for job in store.list_meeting_alignment_jobs_ended_between(
        window.start.isoformat(), window.cutoff.isoformat()
    ):
        source = json.loads(job.source_json or "{}")
        basic = (source.get("minutes_info") or {}).get("result") or {}
        discovery = source.get("discovery") or {}
        path = archive_path_for(archive_dir, basic, task_uuid=job.meeting_id) if basic else None
        entries.append({
            "meeting_id": job.meeting_id,
            "title": job.title,
            "started_at": discovery.get("started_at") or "",
            "ended_at": job.ended_at,
            "participants": json.loads(job.participants_json or "[]"),
            "source_url": basic.get("url") or "",
            "archive_path": str(path) if path is not None and path.is_file() else "",
            "follow_up_status": job.status,
            "follow_up_message": job.final_message,
        })
    return entries


def collect_weekly_report_materials(
    store, dws, archive_dir: Path, run_at: datetime
) -> dict[str, Any]:
    window = weekly_window(run_at)
    minutes = minutes_index(store, archive_dir, window)
    return {
        "time_zone": str(REPORT_TIME_ZONE),
        "target_monday": window.target_monday.isoformat(),
        "window_utc": {
            "start": window.start.isoformat(),
            "end": window.end.isoformat(),
            "cutoff": window.cutoff.isoformat(),
        },
        "documents": resolve_meeting_documents(dws, window),
        "minutes": minutes,
        "coverage": {
            "meetings": len(minutes),
            "archived_transcripts": sum(1 for entry in minutes if entry["archive_path"]),
            "minutes_archive": str(archive_dir),
        },
    }


def _nodes(dws, workspace_id: str, *, parent_id: str | None) -> list[dict[str, Any]]:
    command = [dws.dws_bin, "wiki", "+node-list", "--workspace", workspace_id]
    if parent_id:
        command += ["--folder", parent_id]
    return _data_list(dws.run_json(command + ["--page-all", "--format", "json"]), "nodes")


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
