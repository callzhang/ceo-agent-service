from __future__ import annotations

from datetime import timedelta
import json
from pathlib import Path

import pytest

from app import minutes_sync
from app.dws_client import DwsError
from app.minutes_sync import (
    MINUTES_SYNC_SCANNER,
    MinutesSummaryShapeUnknown,
    MinutesSyncResult,
    render_archive,
    should_request_access,
    summary_markdown,
    sync_minutes_once,
)
from app.store import AutoReplyStore


class FakeDws:
    """Only the minutes surface the sync uses."""

    def __init__(self, items, *, basic=None, summary=None, paragraphs=None, errors=None):
        self._items = items
        self._basic = basic or {}
        self._summary = summary or {}
        self._paragraphs = paragraphs or {}
        self._errors = errors or {}
        self.permission_requests: list[str] = []

    @staticmethod
    def parse_minutes_list(payload):
        return payload

    def list_minutes(self):
        return self._items

    def get_minutes_info(self, task_uuid):
        error = self._errors.get(task_uuid)
        if error is not None and task_uuid not in self._basic:
            raise error
        return {"result": self._basic.get(task_uuid, {})}

    def get_minutes_summary(self, task_uuid):
        error = self._errors.get(task_uuid)
        if error is not None:
            raise error
        return {"result": self._summary.get(task_uuid, {})}

    def get_all_minutes_transcription(self, task_uuid):
        error = self._errors.get(task_uuid)
        if error is not None:
            raise error
        return {"paragraphs": self._paragraphs.get(task_uuid, [])}

    def add_minutes_member_permission(self, task_uuid):
        self.permission_requests.append(task_uuid)
        return {"ok": True}


class PaginatedFakeDws(FakeDws):
    def __init__(self, pages):
        super().__init__([])
        self.pages = pages
        self.calls: list[str] = []

    def list_minutes_page(self, *, scope="all", cursor="", **kwargs):
        del kwargs
        self.calls.append((scope, cursor))
        page = self.pages.get(cursor)
        if isinstance(page, Exception):
            raise page
        return page


def _denied() -> DwsError:
    return DwsError("no permission", "PAT_HIGH_RISK_NO_PERMISSION")


@pytest.fixture(autouse=True)
def _no_listing_backoff(monkeypatch):
    """The listing retry waits between attempts; a test must not."""

    monkeypatch.setattr(minutes_sync, "_sleep", lambda _seconds: None)


def _store(tmp_path: Path) -> AutoReplyStore:
    return AutoReplyStore(tmp_path / "minutes.sqlite3")


def _cursor(store: AutoReplyStore) -> dict:
    state = store.get_daily_scan_state(MINUTES_SYNC_SCANNER) or {}
    return json.loads(state.get("cursor_json") or "{}")


def test_archive_matches_the_existing_local_layout(tmp_path: Path) -> None:
    store = _store(tmp_path)
    dws = FakeDws(
        [{"taskUuid": "u1"}],
        basic={"u1": {"title": "周会", "startTime": 1789025858000, "duration": 600000}},
        summary={"u1": {"fullSummary": "要点"}},
        paragraphs={
            "u1": [
                {"startTime": 2000, "nickName": "张静", "paragraph": "开始了"},
                {"startTime": 1273000, "speakerDisplay": "Mina", "paragraph": "收到"},
                {"startTime": 5000, "nickName": "空", "paragraph": "   "},
            ]
        },
    )

    result = sync_minutes_once(store, dws, archive_dir=tmp_path / "AI听记")

    assert result.synced == 1 and result.discovered == 1
    [written] = list((tmp_path / "AI听记").rglob("*.md"))
    text = written.read_text(encoding="utf-8")
    assert text.startswith("<!-- row_key: u1 -->\n")
    assert "<!-- source_url: https://shanji.dingtalk.com/app/transcribes/u1 -->" in text
    assert "# AI Summary" in text and "# Transcript" in text
    assert "\n要点\n" in text
    assert "[00:02] 张静: 开始了" in text
    assert "[21:13] Mina: 收到" in text
    # An empty paragraph contributes no line.
    assert "空:" not in text
    assert _cursor(store)["archived_ids"] == ["u1"]


def test_a_minute_with_summary_but_no_transcript_is_still_synced(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    dws = FakeDws(
        [{"taskUuid": "summary-only"}],
        basic={
            "summary-only": {
                "title": "摘要已生成",
                "startTime": 1789025858000,
            }
        },
        summary={"summary-only": {"fullSummary": "只有摘要，没有转写。"}},
    )

    result = sync_minutes_once(store, dws, archive_dir=tmp_path / "AI听记")

    assert result.discovered == 1
    assert result.synced == 1
    assert result.failed == 0
    [written] = list((tmp_path / "AI听记").rglob("*.md"))
    text = written.read_text(encoding="utf-8")
    assert "只有摘要，没有转写。" in text
    assert "# Transcript" in text
    assert _cursor(store)["archived_ids"] == ["summary-only"]


def test_an_archived_minute_is_not_fetched_again(tmp_path: Path) -> None:
    store = _store(tmp_path)
    dws = FakeDws(
        [{"taskUuid": "u1"}],
        basic={"u1": {"title": "周会", "startTime": 1789025858000}},
        paragraphs={"u1": [{"startTime": 0, "nickName": "A", "paragraph": "x"}]},
    )
    sync_minutes_once(store, dws, archive_dir=tmp_path / "AI听记")

    second = sync_minutes_once(store, dws, archive_dir=tmp_path / "AI听记")

    assert second.discovered == 0 and second.synced == 0


@pytest.mark.parametrize(
    ("duration", "expected"),
    (
        (timedelta(minutes=5), True),
        (timedelta(minutes=30), True),
        (timedelta(minutes=4, seconds=59), False),
        (None, False),
    ),
)
def test_access_is_requested_only_above_the_duration_threshold(duration, expected):
    assert should_request_access(duration) is expected


def test_no_error_is_classified_as_a_restricted_minute_yet(tmp_path: Path) -> None:
    """The provider's per-minute denial code is not known, so nothing requests.

    DwsError.needs_authorization covers credential-level failures that hit
    every call. If those were read as "this minute is restricted", one broken
    credential would mail an access request to the owner of every minute in
    the list. Until a real per-minute denial is observed, the sync sends none.
    """
    store = _store(tmp_path)
    dws = FakeDws(
        [{"taskUuid": "a"}, {"taskUuid": "b"}],
        basic={
            "a": {"title": "长会", "duration": 3600000},
            "b": {"title": "更长的会", "duration": 7200000},
        },
        errors={"a": _denied(), "b": _denied()},
    )

    result = sync_minutes_once(store, dws, archive_dir=tmp_path / "AI听记")

    assert dws.permission_requests == []
    assert result.permission_requested == 0
    assert result.failed == 2


def test_a_short_restricted_minute_is_skipped_without_asking_its_owner(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    dws = FakeDws(
        [{"taskUuid": "short"}],
        basic={"short": {"title": "闲聊", "duration": 120000}},
        errors={"short": _denied()},
    )

    result = sync_minutes_once(store, dws, archive_dir=tmp_path / "AI听记")

    assert result.permission_requested == 0
    assert dws.permission_requests == []


def test_a_long_restricted_minute_asks_for_access_but_is_not_synced(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    dws = FakeDws(
        [{"taskUuid": "long"}],
        basic={"long": {"title": "评审", "duration": 1800000}},
        errors={"long": _denied()},
    )

    result = sync_minutes_once(store, dws, archive_dir=tmp_path / "AI听记")

    # No request is sent while the per-minute denial code is unverified.
    assert dws.permission_requests == []
    assert result.permission_requested == 0 and result.synced == 0
    assert list((tmp_path / "AI听记").rglob("*.md")) == []


def test_an_unreadable_duration_never_asks_for_access(tmp_path: Path) -> None:
    store = _store(tmp_path)
    dws = FakeDws([{"taskUuid": "unknown"}], errors={"unknown": _denied()})

    result = sync_minutes_once(store, dws, archive_dir=tmp_path / "AI听记")

    assert dws.permission_requests == []
    assert result.failed == 1


def test_a_non_permission_failure_is_failed_not_a_permission_decision(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    dws = FakeDws(
        [{"taskUuid": "broken"}],
        basic={"broken": {"title": "坏的", "duration": 1800000}},
        errors={"broken": DwsError("transcript unavailable", "SOME_OTHER_CODE")},
    )

    result = sync_minutes_once(store, dws, archive_dir=tmp_path / "AI听记")

    assert result.failed == 1
    assert dws.permission_requests == []


def test_every_discovered_minute_lands_in_exactly_one_outcome() -> None:
    with pytest.raises(ValueError, match="account for every item"):
        MinutesSyncResult(discovered=2, synced=1)


def test_render_archive_writes_the_provider_summary_markdown() -> None:
    text = render_archive(
        task_uuid="u",
        summary={"fullSummary": "> **主题**: 讨论\n\n## 背景\n\n- 一点"},
        paragraphs=[],
    )
    assert "> **主题**: 讨论\n\n## 背景\n\n- 一点" in text
    # Serialising the payload instead of reading it wrote escaped JSON into the
    # archive, which looks synced and is unreadable.
    assert "fullSummary" not in text
    assert "\\n" not in text


def test_render_archive_accepts_a_minute_without_a_summary_yet() -> None:
    text = render_archive(task_uuid="u", summary={}, paragraphs=[])
    assert "# AI Summary\n\n\n\n# Transcript" in text


def test_an_unreadable_summary_shape_fails_the_item_instead_of_archiving_it(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    dws = FakeDws(
        [{"taskUuid": "u1"}],
        basic={"u1": {"title": "会", "startTime": 1_700_000_000_000}},
        summary={"u1": {"sections": ["未知结构"]}},
        paragraphs={"u1": [{"startTime": 0, "nickName": "磊哥", "paragraph": "在"}]},
    )

    result = sync_minutes_once(store, dws, archive_dir=tmp_path / "AI听记")

    assert result.failed == 1 and result.synced == 0
    assert list((tmp_path / "AI听记").rglob("*.md")) == []
    assert _cursor(store)["archived_ids"] == []


def test_incomplete_minutes_pagination_does_not_claim_success(tmp_path: Path) -> None:
    store = _store(tmp_path)
    dws = PaginatedFakeDws(
        {
            "": {
                "items": [{"taskUuid": "u1"}],
                "has_more": True,
                "next_token": "page-2",
            },
            "page-2": DwsError("temporary page failure", "NETWORK_ERROR"),
        }
    )
    dws._basic = {"u1": {"title": "周会", "startTime": 1789025858000}}
    dws._paragraphs = {"u1": [{"startTime": 0, "paragraph": "内容"}]}

    result = sync_minutes_once(store, dws, archive_dir=tmp_path / "AI听记")

    assert result.synced == 1 and result.discovered == 1
    state = store.get_daily_scan_state(MINUTES_SYNC_SCANNER) or {}
    assert state["last_success_at"] == ""
    assert "temporary page failure" in state["last_error"]
    cursor = json.loads(state["cursor_json"])
    assert cursor["pagination_deferred"] is True
    assert cursor["pagination_error"] == (
        "all: temporary page failure; "
        "mine: temporary page failure; "
        "shared: temporary page failure"
    )
    assert cursor["archived_ids"] == ["u1"]


def test_the_walk_does_not_stop_at_an_already_archived_minute(
    tmp_path: Path,
) -> None:
    """Minutes are not archived in listing order.

    A minute whose owner grants access days later is archived long after the
    minutes above it. Stopping the walk at the first already-archived minute
    left every older unarchived minute below that boundary, and the boundary
    only moves further from them, so they were never offered again.
    """
    store = _store(tmp_path)
    dws = PaginatedFakeDws(
        {
            "": {
                "items": [{"taskUuid": "new"}, {"taskUuid": "archived"}],
                "has_more": True,
                "next_token": "page-2",
            },
            "page-2": {
                "items": [{"taskUuid": "granted-late"}],
                "has_more": False,
                "next_token": "",
            },
        }
    )
    store.set_daily_scan_state(
        MINUTES_SYNC_SCANNER,
        last_success_at="2026-09-10T00:00:00+00:00",
        cursor_json=json.dumps({"archived_ids": ["archived"]}),
    )
    for task_uuid in ("new", "granted-late"):
        dws._basic[task_uuid] = {"title": task_uuid, "startTime": 1789025858000}
        dws._paragraphs[task_uuid] = [{"startTime": 0, "paragraph": "内容"}]

    result = sync_minutes_once(store, dws, archive_dir=tmp_path / "AI听记")

    assert result.discovered == 2 and result.synced == 2
    assert _cursor(store)["archived_ids"] == ["archived", "granted-late", "new"]
    assert ("all", "page-2") in dws.calls


INSIGHT_REPORT = {
    "meta_info": {
        "title": "Friday日会纪要",
        "subtitle": "会议音频调试与人员确认",
        "tags": ["设备调试", "人员确认"],
        "minutes_start_time": 1787567736000,
    },
    "overview": "本次会议主要进行了会前的音频设备调试。",
    "menu": [
        {"slice_id": "s1", "title": "音频设备调试", "summary": "协调开启麦克风测试。"}
    ],
    "details": [
        {
            "slice_id": "s1",
            "detail_json": {
                "blocks": [
                    {"type": "callout", "content": ["尚未进入**实质性**议题。"]},
                    {
                        "type": "module",
                        "title": "会前设备调试",
                        "children": [
                            {
                                "type": "content-list",
                                "items": [
                                    {
                                        "title": "语音连接确认",
                                        "content": ["确认麦克风是否开启。", "测试连通性。"],
                                    }
                                ],
                            }
                        ],
                    },
                ]
            },
        }
    ],
}


def test_a_structured_insight_report_becomes_the_archive_markdown() -> None:
    text = summary_markdown({"fullSummary": json.dumps(INSIGHT_REPORT, ensure_ascii=False)})

    # The provider returns this shape for a large share of minutes; writing it
    # verbatim put a JSON document under "# AI Summary".
    assert "meta_info" not in text and '{"' not in text
    assert text.startswith("> **主题**: Friday日会纪要")
    assert "> **议题**: 会议音频调试与人员确认" in text
    assert "> **标签**: 设备调试, 人员确认" in text
    assert "本次会议主要进行了会前的音频设备调试。" in text
    assert "## 音频设备调试" in text
    assert "协调开启麦克风测试。" in text
    assert "尚未进入**实质性**议题。" in text
    assert "### 会前设备调试" in text
    assert "- **语音连接确认**" in text
    assert "    - 确认麦克风是否开启。" in text
    assert "    - 测试连通性。" in text


def test_markdown_summaries_are_still_passed_through_untouched() -> None:
    markdown = "> **主题**: 讨论\n\n## 背景\n\n- 一点"
    assert summary_markdown({"fullSummary": markdown}) == markdown


def test_a_json_summary_in_an_unknown_shape_fails_instead_of_being_written() -> None:
    with pytest.raises(MinutesSummaryShapeUnknown):
        summary_markdown({"fullSummary": json.dumps({"sections": ["未知结构"]})})


def test_a_structured_report_reaches_the_archive_file(tmp_path: Path) -> None:
    store = _store(tmp_path)
    dws = FakeDws(
        [{"taskUuid": "u1"}],
        basic={"u1": {"title": "会", "startTime": 1_700_000_000_000}},
        summary={"u1": {"fullSummary": json.dumps(INSIGHT_REPORT, ensure_ascii=False)}},
        paragraphs={"u1": [{"startTime": 0, "nickName": "磊哥", "paragraph": "在"}]},
    )

    result = sync_minutes_once(store, dws, archive_dir=tmp_path / "AI听记")

    assert result.synced == 1
    [written] = list((tmp_path / "AI听记").rglob("*.md"))
    text = written.read_text(encoding="utf-8")
    assert "# AI Summary\n\n> **主题**: Friday日会纪要" in text
    assert "meta_info" not in text


def test_the_daily_service_command_archives_every_minute_the_pass_discovered(
    tmp_path: Path, monkeypatch
) -> None:
    """The scheduled pass must not be capped by the queue batch size.

    `CEO_MAX_BATCHES=4` used to reach `sync-minutes-once` as a per-pass item
    cap. A minute the pass dropped fell below the newest listing page by the
    next day and was never offered again, so the archive silently kept only
    the four newest minutes a day.
    """
    from types import SimpleNamespace

    from app import cli

    minutes = [{"taskUuid": f"u{index}"} for index in range(9)]
    dws = FakeDws(
        minutes,
        basic={
            f"u{index}": {"title": f"会议{index}", "startTime": 1_700_000_000_000}
            for index in range(9)
        },
        summary={f"u{index}": {"fullSummary": "要点"} for index in range(9)},
        paragraphs={
            f"u{index}": [{"startTime": 0, "nickName": "磊哥", "paragraph": "在"}]
            for index in range(9)
        },
    )
    monkeypatch.setattr(cli, "DwsClient", lambda **kwargs: dws)
    settings = SimpleNamespace(
        db_path=tmp_path / "minutes.sqlite3",
        workspace=tmp_path,
        ding_robot_code="",
        ding_robot_name="磊哥",
        ding_receiver_user_id="",
        max_batches=4,
    )

    registry = cli._service_command_registry(
        AutoReplyStore(settings.db_path), object(), settings
    )

    assert registry.run("sync-minutes-once") == (
        "sync-minutes-once discovered=9 synced=9 skipped=0 "
        "permission_requested=0 permission_pending=0 failed=0"
    )
    assert len(list((tmp_path / "AI听记").rglob("*.md"))) == 9


class ScopedFakeDws(FakeDws):
    """A provider whose scopes return different minutes, as the live one does."""

    def __init__(self, pages_by_scope):
        super().__init__([])
        self.pages_by_scope = pages_by_scope
        self.calls: list[tuple[str, str]] = []

    def list_minutes_page(self, *, scope="all", cursor="", **kwargs):
        del kwargs
        self.calls.append((scope, cursor))
        page = self.pages_by_scope.get(scope, {}).get(cursor)
        if isinstance(page, Exception):
            raise page
        return page or {"items": [], "has_more": False, "next_token": ""}


def _one_page(*task_uuids: str) -> dict:
    return {
        "": {
            "items": [{"taskUuid": task_uuid} for task_uuid in task_uuids],
            "has_more": False,
            "next_token": "",
        }
    }


def _readable(dws: FakeDws, *task_uuids: str) -> None:
    for task_uuid in task_uuids:
        dws._basic[task_uuid] = {"title": f"会议{task_uuid}", "startTime": 1789025858000}
        dws._paragraphs[task_uuid] = [{"startTime": 0, "paragraph": "内容"}]


def test_a_minute_only_the_shared_scope_lists_still_reaches_the_archive(
    tmp_path: Path,
) -> None:
    """`all` is not the union of the scopes.

    Measured live 2026-09-17: 20 minutes appeared in `shared` that `all` never
    returned, so a pass that reads only `all` loses them with no error.
    """
    store = _store(tmp_path)
    dws = ScopedFakeDws(
        {
            "all": _one_page("u1"),
            "mine": _one_page("u1"),
            "shared": _one_page("u1", "u2"),
        }
    )
    _readable(dws, "u1", "u2")

    result = sync_minutes_once(store, dws, archive_dir=tmp_path / "AI听记")

    assert result.discovered == 2 and result.synced == 2
    assert _cursor(store)["archived_ids"] == ["u1", "u2"]


def test_one_failing_scope_keeps_the_others_and_still_defers_success(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    dws = ScopedFakeDws(
        {
            "all": _one_page("u1"),
            "mine": {"": DwsError("scope unavailable", "NETWORK_ERROR")},
            "shared": _one_page("u2"),
        }
    )
    _readable(dws, "u1", "u2")

    result = sync_minutes_once(store, dws, archive_dir=tmp_path / "AI听记")

    assert result.synced == 2
    state = store.get_daily_scan_state(MINUTES_SYNC_SCANNER) or {}
    assert state["last_success_at"] == ""
    assert state["last_error"] == "mine: scope unavailable"


def _restricted(message: str = "no permission") -> DwsError:
    """How the provider refuses one minute, observed live 2026-09-18."""
    return DwsError(
        "dws minutes get info failed",
        "1",
        business_message=message,
        server_key="minutes",
    )


def test_a_minute_the_provider_refuses_is_pending_not_a_failure(
    tmp_path: Path,
) -> None:
    """`server_key=minutes` + `message=no permission` refuses one minute.

    Before the signal was known every such minute counted as `failed`, which
    makes the whole scheduled command raise, so one unreadable minute could
    fail the daily archive for every other minute in the pass.
    """
    store = _store(tmp_path)
    dws = FakeDws(
        [{"taskUuid": "readable"}, {"taskUuid": "restricted"}],
        basic={
            "readable": {"title": "周会", "startTime": 1789025858000},
            "restricted": {"title": "他人会议", "duration": 1800000},
        },
        summary={"readable": {"fullSummary": "要点"}},
        paragraphs={"readable": [{"startTime": 0, "paragraph": "内容"}]},
        errors={"restricted": _restricted()},
    )

    result = sync_minutes_once(store, dws, archive_dir=tmp_path / "AI听记")

    assert result.synced == 1
    assert result.failed == 0
    assert result.permission_pending == 1
    assert _cursor(store)["permission_pending_ids"] == ["restricted"]


def test_the_service_never_sends_an_access_request_of_its_own(
    tmp_path: Path,
) -> None:
    """`dws minutes +apply-permission` returns a receipt for nothing sent.

    85 minutes were "requested" through it on 2026-09-18 and every page still
    offered `Send Application` afterwards, so a request only counts when a
    signed-in browser reads back `Applied, waiting for processing`.
    """
    store = _store(tmp_path)
    dws = FakeDws(
        [{"taskUuid": "restricted"}],
        basic={"restricted": {"title": "他人会议", "duration": 3600000}},
        errors={"restricted": _restricted()},
    )

    result = sync_minutes_once(store, dws, archive_dir=tmp_path / "AI听记")

    assert dws.permission_requests == []
    assert result.permission_requested == 0
    assert result.permission_pending == 1


def test_a_credential_failure_is_never_read_as_one_restricted_minute(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    dws = FakeDws(
        [{"taskUuid": "a"}, {"taskUuid": "b"}],
        basic={"a": {"duration": 3600000}, "b": {"duration": 3600000}},
        errors={"a": _denied(), "b": _denied()},
    )

    result = sync_minutes_once(store, dws, archive_dir=tmp_path / "AI听记")

    assert result.failed == 2 and result.permission_pending == 0
    assert _cursor(store).get("permission_pending_ids", []) == []


def test_both_spellings_of_the_refusal_mean_the_same_restricted_minute(
    tmp_path: Path,
) -> None:
    """The provider spells one refusal two ways over the same listing.

    Matching only `no permission` left 13 of 253 minutes unrecognised, which
    made them `failed` and so failed the whole scheduled command.
    """
    store = _store(tmp_path)
    dws = FakeDws(
        [{"taskUuid": "lower"}, {"taskUuid": "coded"}],
        basic={
            "lower": {"title": "他人会议", "duration": 1800000},
            "coded": {"title": "他人会议", "duration": 1800000},
        },
        errors={
            "lower": _restricted("no permission"),
            "coded": _restricted("B_PERMISSION_NoPermission"),
        },
    )

    result = sync_minutes_once(store, dws, archive_dir=tmp_path / "AI听记")

    assert result.failed == 0
    assert result.permission_pending == 2
    assert _cursor(store)["permission_pending_ids"] == ["coded", "lower"]


def test_a_failure_says_which_minute_and_why() -> None:
    """`failed=19` on its own is not something anyone can act on.

    The four failure branches -- a DWS error, an unreadable summary shape, a
    minute with neither summary nor transcript, and an unrenderable archive --
    need different answers, and on 2026-09-19 the daily pass reported 19
    failures out of 784 with no way to learn which minutes or why.
    """
    from app.minutes_sync import MinutesSyncResult

    result = MinutesSyncResult(
        discovered=3,
        synced=1,
        failed=2,
        failures=(
            ("uuid-a", "summary_shape_unknown"),
            ("uuid-b", "no_summary_and_no_transcript"),
        ),
    )

    summary = result.summary()
    assert "failed=2" in summary
    assert "uuid-a:summary_shape_unknown" in summary
    assert "uuid-b:no_summary_and_no_transcript" in summary


def test_a_clean_pass_says_nothing_extra() -> None:
    from app.minutes_sync import MinutesSyncResult

    assert "failures=" not in MinutesSyncResult(discovered=1, synced=1).summary()


def test_many_failures_are_summarised_not_dumped() -> None:
    from app.minutes_sync import MinutesSyncResult

    failures = tuple((f"uuid-{index}", "dws_error:boom") for index in range(9))
    result = MinutesSyncResult(discovered=9, failed=9, failures=failures)

    summary = result.summary()
    assert "uuid-0:dws_error:boom" in summary
    assert "(+4 more)" in summary


def test_a_minute_with_nothing_to_archive_is_skipped_not_failed() -> None:
    """A condition no run can clear must not be reported as a daily failure.

    A minute with neither summary nor transcript is most often a recording
    still in progress, which the read APIs cannot see. Counting it as a
    failure made the daily task report an error every day -- 14 of them on
    2026-09-19 -- and a daily error nobody can act on is how a real one gets
    missed. It is rediscovered and retried on every later pass either way.
    """
    from app.minutes_sync import MinutesSyncResult

    result = MinutesSyncResult(
        discovered=2,
        synced=1,
        skipped=1,
        skips=(("uuid-a", "no_summary_and_no_transcript_yet"),),
    )

    assert result.failed == 0
    assert "skipped=1" in result.summary()
    assert "skips=uuid-a:no_summary_and_no_transcript_yet" in result.summary()
    # A skip is never shown as a failure: that is what made the first version
    # of this unclear, with fourteen skips filling a list labelled failures.
    assert "failures=" not in result.summary()


def test_a_failing_transcript_does_not_discard_a_summary_that_read_fine(
    tmp_path: Path,
) -> None:
    """Five minutes failed every daily pass with a complete summary behind them.

    DingTalk answers `B_QUERY_MINUTES_PARAGRAPH_LIST_FAILED` for some older
    minutes however often it is asked. Fetching the transcript inside the same
    try as the summary threw the summary away with it, so a minute that could
    have been archived was reported as a failure, every day, forever.
    """
    store = _store(tmp_path)

    class TranscriptFails(FakeDws):
        def get_all_minutes_transcription(self, task_uuid):
            raise DwsError("B_QUERY_MINUTES_PARAGRAPH_LIST_FAILED")

    dws = TranscriptFails(
        [{"taskUuid": "u1"}],
        basic={"u1": {"title": "OpenAI合作交流", "startTime": 1778266926000}},
        summary={"u1": {"fullSummary": "要点"}},
    )

    result = sync_minutes_once(store, dws, archive_dir=tmp_path / "AI听记")

    assert result.synced == 1
    assert result.failed == 0
    assert any("transcript_unavailable" in reason for _, reason in result.skips)
    [written] = list((tmp_path / "AI听记").rglob("*.md"))
    assert "要点" in written.read_text(encoding="utf-8")


def test_a_listing_page_that_fails_once_is_asked_again(tmp_path: Path) -> None:
    """The upstream search answers the same cursor correctly moments later.

    On 2026-09-19 the `shared` scope stopped on
    `openSearchMinutesByKeywordAndTimeRange error`, and the identical command
    with the identical cursor succeeded on the next attempt. One failed page
    abandoned the rest of the scope and left a standing scanner error behind
    a transient answer.
    """
    from app.minutes_sync import _list_minutes_scope

    attempts: list[str] = []

    def list_page(*, scope: str, cursor: str):
        attempts.append(cursor)
        if len(attempts) == 1:
            raise RuntimeError("openSearchMinutesByKeywordAndTimeRange error")
        return {"items": [{"taskUuid": "u1"}], "has_more": False}

    items, error = _list_minutes_scope(list_page, "shared")

    assert error == ""
    assert [item["taskUuid"] for item in items] == ["u1"]
    assert len(attempts) == 2


def test_a_page_that_keeps_failing_still_reports(tmp_path: Path) -> None:
    from app.minutes_sync import _list_minutes_scope

    def list_page(*, scope: str, cursor: str):
        raise RuntimeError("openSearchMinutesByKeywordAndTimeRange error")

    items, error = _list_minutes_scope(list_page, "shared")

    assert items == []
    assert "openSearchMinutesByKeywordAndTimeRange" in error


def test_a_minute_missing_its_transcript_is_asked_again(tmp_path: Path) -> None:
    """A provider-side transcript failure is worth retrying, not freezing.

    Archiving the summary and marking the minute done would leave it
    half-archived forever. It stays on the list so a later pass can finish it.
    """
    store = _store(tmp_path)

    class TranscriptFails(FakeDws):
        def get_all_minutes_transcription(self, task_uuid):
            raise DwsError("B_QUERY_MINUTES_PARAGRAPH_LIST_FAILED")

    dws = TranscriptFails(
        [{"taskUuid": "u1"}],
        basic={"u1": {"title": "OpenAI合作交流", "startTime": 1778266926000}},
        summary={"u1": {"fullSummary": "要点"}},
    )

    first = sync_minutes_once(store, dws, archive_dir=tmp_path / "AI听记")
    assert first.synced == 1
    assert "u1" not in set(_cursor(store).get("archived_ids") or [])

    # The transcript answers on a later pass and the minute is finished.
    working = FakeDws(
        [{"taskUuid": "u1"}],
        basic={"u1": {"title": "OpenAI合作交流", "startTime": 1778266926000}},
        summary={"u1": {"fullSummary": "要点"}},
        paragraphs={"u1": [{"startTime": 0, "nickName": "张静", "paragraph": "开始"}]},
    )
    second = sync_minutes_once(store, working, archive_dir=tmp_path / "AI听记")

    assert second.synced == 1
    assert "u1" in set(_cursor(store).get("archived_ids") or [])
    [written] = list((tmp_path / "AI听记").rglob("*.md"))
    assert "开始" in written.read_text(encoding="utf-8")


def test_a_recording_too_short_to_matter_is_not_chased_forever(tmp_path: Path) -> None:
    """The five-minute floor now decides archiving too, not only access.

    Without it a 0.6-minute "Meeting Recording" and a 1.7-minute "Voice call"
    came back on every daily pass and never became anything.
    """
    store = _store(tmp_path)
    dws = FakeDws(
        [{"taskUuid": "short"}, {"taskUuid": "long"}],
        basic={
            "short": {"title": "Voice call", "startTime": 1789025858000, "duration": 103169},
            "long": {"title": "会议录制：标注培训", "startTime": 1789025858000, "duration": 3400233},
        },
        summary={"short": {}, "long": {}},
    )

    result = sync_minutes_once(store, dws, archive_dir=tmp_path / "AI听记")

    assert result.skipped == 2
    reasons = dict(result.skips)
    assert reasons["short"] == "too_short_to_archive"
    assert reasons["long"] == "no_summary_and_no_transcript_yet"
    seen = set(_cursor(store).get("archived_ids") or [])
    # The short one is not offered again; the long one still is.
    assert "short" in seen and "long" not in seen
