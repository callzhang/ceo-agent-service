from __future__ import annotations

from datetime import UTC, date, datetime
import json
from pathlib import Path

import pytest

from app.dws_client import DwsError
from app.store import AutoReplyStore
from app.weekly_report_materials import (
    collect_weekly_report_materials,
    meeting_title,
    weekly_window,
)


def test_saturday_run_reports_for_the_monday_two_days_later():
    # Saturday 12:00 Pacific = Sunday 03:00 Beijing.
    window = weekly_window(datetime(2026, 9, 26, 19, 0, tzinfo=UTC))

    assert window.target_monday == date(2026, 9, 28)
    assert window.start == datetime(2026, 9, 20, 16, 0, tzinfo=UTC)
    assert window.end == datetime(2026, 9, 27, 16, 0, tzinfo=UTC)
    assert window.cutoff == datetime(2026, 9, 26, 19, 0, tzinfo=UTC)


def test_monday_run_targets_the_next_monday_not_the_one_being_held():
    window = weekly_window(datetime(2026, 9, 28, 2, 0, tzinfo=UTC))

    assert window.target_monday == date(2026, 10, 5)


def test_run_after_beijing_midnight_uses_the_beijing_date():
    # Sunday 17:00 UTC is already Monday 01:00 in Beijing.
    window = weekly_window(datetime(2026, 9, 27, 17, 0, tzinfo=UTC))

    assert window.target_monday == date(2026, 10, 5)


def test_meeting_title_has_no_zero_padding():
    assert meeting_title(date(2026, 9, 7)) == "2026年9月7日管理层周会"
    assert meeting_title(date(2026, 12, 21)) == "2026年12月21日管理层周会"


class FakeDws:
    dws_bin = "dws"

    def __init__(self, tree: dict[str | None, list[dict]], spaces=None):
        self.tree = tree
        self.spaces = spaces if spaces is not None else [
            {"name": "🎯  目标与执行", "workspaceId": "ws1"}
        ]

    def run_json(self, command):
        if command[1:3] == ["wiki", "+space-list"]:
            return {"data": {"spaces": self.spaces, "hasMore": False}}
        folder = command[command.index("--folder") + 1] if "--folder" in command else None
        return {"data": {"nodes": self.tree.get(folder, []), "hasMore": False}}


def _node(name, node_id, has_children=False, url=""):
    return {"name": name, "nodeId": node_id, "hasChildren": has_children, "url": url}


def _tree(*titles, year_page="2026 年：管理层周会"):
    return {
        None: [_node("1. 管理层周会", "root")],
        "root": [_node(year_page, "year", has_children=True)],
        "year": [_node(title, f"id-{index}", url=f"https://doc/{index}") for index, title in enumerate(titles)],
    }


def _materials(tmp_path: Path, dws: FakeDws, store=None):
    store = store or AutoReplyStore(tmp_path / "db.sqlite3")
    return collect_weekly_report_materials(
        store, dws, tmp_path / "archive", datetime(2026, 9, 26, 19, 0, tzinfo=UTC)
    )


def test_finds_target_and_previous_documents_by_title(tmp_path):
    dws = FakeDws(_tree("2026年9月21日管理层周会", "2026年9月28日管理层周会 "))

    documents = _materials(tmp_path, dws)["documents"]

    assert documents["target"]["exists"] is True
    assert documents["target"]["node_id"] == "id-1"
    assert documents["target"]["url"] == "https://doc/1"
    assert documents["previous"]["node_id"] == "id-0"
    assert documents["target"]["year_page_id"] == "year"


def test_missing_target_is_reported_not_guessed(tmp_path):
    dws = FakeDws(_tree("2026年9月21日管理层周会"))

    documents = _materials(tmp_path, dws)["documents"]

    assert documents["target"]["exists"] is False
    assert documents["target"]["node_id"] == ""
    assert documents["target"]["year_page_id"] == "year"
    assert documents["previous"]["exists"] is True


def test_year_page_name_spacing_variants_match(tmp_path):
    dws = FakeDws(_tree("2026年9月28日管理层周会", year_page="2026年：管理层周会"))

    assert _materials(tmp_path, dws)["documents"]["target"]["exists"] is True


def test_two_documents_with_the_target_title_is_an_error(tmp_path):
    dws = FakeDws(_tree("2026年9月28日管理层周会", "2026年9月28日管理层周会"))

    with pytest.raises(DwsError, match="more than one"):
        _materials(tmp_path, dws)


def test_incomplete_listing_is_an_error(tmp_path):
    dws = FakeDws(_tree("2026年9月28日管理层周会"))
    original = dws.run_json
    dws.run_json = lambda command: (
        {"data": {"nodes": [], "hasMore": True}} if "--folder" in command else original(command)
    )

    with pytest.raises(DwsError, match="incomplete"):
        _materials(tmp_path, dws)


def test_wiki_that_cannot_be_found_is_an_error(tmp_path):
    dws = FakeDws(_tree(), spaces=[])

    with pytest.raises(DwsError, match="exactly one wiki"):
        _materials(tmp_path, dws)


def _job(store, meeting_id, ended_at, *, started_at="2026-09-21T01:00:00+00:00"):
    source = {
        "discovery": {"started_at": started_at},
        "minutes_info": {
            "result": {
                "title": f"{meeting_id} 会",
                "startTime": int(datetime.fromisoformat(started_at).timestamp() * 1000),
                "url": f"https://shanji/{meeting_id}",
            }
        },
    }
    store.upsert_meeting_alignment_job(
        meeting_id=meeting_id,
        title=f"{meeting_id} 会",
        source_json=json.dumps(source),
        participants_json=json.dumps([{"name": "Alice", "user_id": "u1"}]),
        ended_at=ended_at,
        eligible_at=ended_at,
        status="sent",
    )


def test_lists_the_meetings_that_ended_inside_the_window(tmp_path):
    store = AutoReplyStore(tmp_path / "db.sqlite3")
    _job(store, "before", "2026-09-20T15:59:00+00:00")
    _job(store, "inside", "2026-09-21T01:30:00+00:00")
    _job(store, "after-cutoff", "2026-09-26T19:00:01+00:00")
    dws = FakeDws(_tree("2026年9月28日管理层周会"))

    materials = _materials(tmp_path, dws, store)

    assert [entry["meeting_id"] for entry in materials["minutes"]] == ["inside"]
    entry = materials["minutes"][0]
    assert entry["participants"] == [{"name": "Alice", "user_id": "u1"}]
    assert entry["source_url"] == "https://shanji/inside"
    assert materials["coverage"]["meetings"] == 1


def test_archive_path_is_reported_only_when_the_file_exists(tmp_path):
    store = AutoReplyStore(tmp_path / "db.sqlite3")
    _job(store, "with-file", "2026-09-21T01:30:00+00:00")
    _job(store, "without-file", "2026-09-21T02:30:00+00:00")
    archive_dir = tmp_path / "archive"
    (archive_dir / "with-file 会").mkdir(parents=True)
    started = datetime.fromisoformat("2026-09-21T01:00:00+00:00").astimezone()
    (archive_dir / "with-file 会" / f"with-file 会 {started:%Y-%m-%d %H:%M}.md").write_text("x")
    dws = FakeDws(_tree("2026年9月28日管理层周会"))

    minutes = _materials(tmp_path, dws, store)["minutes"]

    by_id = {entry["meeting_id"]: entry for entry in minutes}
    assert by_id["with-file"]["archive_path"].endswith(".md")
    assert by_id["without-file"]["archive_path"] == ""
    assert _materials(tmp_path, dws, store)["coverage"]["archived_transcripts"] == 1
