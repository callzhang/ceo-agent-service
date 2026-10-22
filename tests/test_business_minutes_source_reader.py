"""Synthetic provider responses for exact historical AI Minutes source reads."""

import json
from dataclasses import replace
from datetime import datetime, timezone

import pytest

from app.business_minutes_source_reader import minutes_provider_locator
from app.business_source_reader import (
    AvailableSource,
    RequestSourceReader,
    SourceUnavailableReason,
    SourceVersionBinding,
)
from app.dws_client import DwsError
from app.task_scanners import _canonical_minutes_todos_payload
from test_business_source_reader import signal


UUID = "a36a9b28-25f5-4a7e-8f85-18009e9ea771"
REF = f"{UUID}#todos-sha256={'a' * 64}"
START_MS = int(datetime(2026, 10, 20, 20, tzinfo=timezone.utc).timestamp() * 1000)
MEETING = {"taskUuid": UUID, "title": "会议🙂", "startTime": START_MS, "endTime": START_MS + 3_600_000}
TODOS = {"result": {"actions": [{"title": "办事"}]}}
PARAGRAPHS = [{"startTime": 0, "endTime": 2000, "nickName": "甲", "paragraph": "请办事。"}]


class FakeDws:
    def __init__(self, pages=None, todos=TODOS, transcript=None, summary=None):
        self.pages = pages or [{"items": [MEETING], "has_more": False, "next_token": ""}]
        self.todos = todos
        self.transcript = transcript
        self.summary = summary
        self.calls = []

    def list_minutes_page(self, **kwargs):
        self.calls.append(("list", kwargs))
        return self.pages[len([c for c in self.calls if c[0] == "list"]) - 1]

    def get_minutes_info(self, uuid):
        self.calls.append(("info", uuid))
        item = next(
            (item for page in self.pages for item in page["items"] if item.get("taskUuid") == uuid),
            MEETING,
        )
        return {"result": {key: item[key] for key in ("taskUuid", "title", "startTime", "endTime")}}

    def get_minutes_todos(self, uuid):
        self.calls.append(("todos", uuid))
        return self.todos

    def get_all_minutes_transcription(self, uuid):
        self.calls.append(("transcript", uuid))
        return self.transcript

    def get_minutes_summary(self, uuid):
        self.calls.append(("summary", uuid))
        return self.summary


def bound(body):
    item = signal(body, source_type="ai_minutes", source_ref=REF)
    return SourceVersionBinding.from_signal(item, provider_locator=minutes_provider_locator(item))


def test_two_components_exact_multibyte_and_once_per_version():
    body = _canonical_minutes_todos_payload(minutes=MEETING, todos_payload=TODOS, transcript_excerpts=[])
    binding = bound(body)
    assert json.loads(binding.provider_locator_json) == {
        "kind": "ai_minutes", "minutes_id": UUID, "components": ["meeting", "todos"]
    }
    fake = FakeDws()
    reader = RequestSourceReader(provider_reader=fake)
    assert isinstance(reader.resolve(binding, source_ref=REF, quote="会议🙂"), AvailableSource)
    assert isinstance(reader.resolve(binding), AvailableSource)
    assert [name for name, _ in fake.calls] == ["info", "list", "todos"]
    assert fake.calls[1][1] == {
        "scope": "all", "limit": 50, "cursor": "",
        "start": "2026-10-19T00:00:00+00:00", "end": "2026-10-22T00:00:00+00:00",
    }
    assert reader.resolve(binding, quote="absent").reason is SourceUnavailableReason.QUOTE_MISSING
    assert reader.resolve(binding, source_ref="wrong").reason is SourceUnavailableReason.SOURCE_REF_MISMATCH
    assert reader.resolve(replace(binding, provider_locator_json='{"other":1}')).reason is SourceUnavailableReason.BINDING_CONFLICT
    assert RequestSourceReader().resolve(binding).reason is SourceUnavailableReason.READER_UNAVAILABLE


def test_distinct_minutes_sources_reuse_one_complete_list_within_request():
    other_uuid = "b46b9c39-36f6-5b8f-9f96-29110fafb882"
    other_ref = f"{other_uuid}#todos-sha256={'b' * 64}"
    other_meeting = {"taskUuid": other_uuid, "title": "另一场会议", "startTime": START_MS, "endTime": START_MS + 3_600_000}
    first_body = _canonical_minutes_todos_payload(
        minutes=MEETING, todos_payload=TODOS, transcript_excerpts=[]
    )
    other_body = _canonical_minutes_todos_payload(
        minutes=other_meeting, todos_payload=TODOS, transcript_excerpts=[]
    )
    other_signal = signal(other_body, source_type="ai_minutes", source_ref=other_ref)
    other_binding = SourceVersionBinding.from_signal(
        other_signal, provider_locator=minutes_provider_locator(other_signal)
    )
    fake = FakeDws(pages=[
        {"items": [MEETING], "has_more": True, "next_token": "next"},
        {"items": [other_meeting], "has_more": False, "next_token": ""},
    ])
    reader = RequestSourceReader(provider_reader=fake)

    assert isinstance(reader.resolve(bound(first_body)), AvailableSource)
    assert isinstance(reader.resolve(other_binding), AvailableSource)
    assert [name for name, _ in fake.calls] == ["info", "list", "list", "todos", "info", "todos"]

    changed = FakeDws(pages=[
        {"items": [{**MEETING, "title": "已修改"}, other_meeting],
         "has_more": False, "next_token": ""},
    ])
    assert RequestSourceReader(provider_reader=changed).resolve(
        bound(first_body)
    ).reason is SourceUnavailableReason.VERSION_MISMATCH
    assert [name for name, _ in changed.calls] == ["info", "list", "todos"]


def test_exact_info_bounds_list_and_reuses_pages_for_same_day_sources():
    other_uuid = "b46b9c39-36f6-5b8f-9f96-29110fafb882"
    start_ms = int(datetime(2026, 10, 20, 20, tzinfo=timezone.utc).timestamp() * 1000)
    meetings = [
        {**MEETING, "startTime": start_ms, "endTime": start_ms + 3_600_000},
        {"taskUuid": other_uuid, "title": "另一场会议", "startTime": start_ms + 60_000,
         "endTime": start_ms + 3_660_000},
    ]

    class TimedDws(FakeDws):
        def __init__(self):
            super().__init__(pages=[
                {"items": [meetings[0]], "has_more": True, "next_token": "next"},
                {"items": [meetings[1]], "has_more": False, "next_token": ""},
            ])

        def get_minutes_info(self, uuid):
            self.calls.append(("info", uuid))
            item = next(item for item in meetings if item["taskUuid"] == uuid)
            return {"result": {key: item[key] for key in ("taskUuid", "title", "startTime", "endTime")}}

    bindings = []
    for item in meetings:
        ref = f"{item['taskUuid']}#todos-sha256={'a' * 64}"
        body = _canonical_minutes_todos_payload(minutes=item, todos_payload=TODOS, transcript_excerpts=[])
        fact = signal(body, source_type="ai_minutes", source_ref=ref)
        bindings.append(SourceVersionBinding.from_signal(fact, provider_locator=minutes_provider_locator(fact)))

    fake = TimedDws()
    reader = RequestSourceReader(provider_reader=fake)
    assert all(isinstance(reader.resolve(binding), AvailableSource) for binding in bindings)
    assert [name for name, _ in fake.calls] == ["info", "list", "list", "todos", "info", "todos"]
    list_calls = [args for name, args in fake.calls if name == "list"]
    assert [args["cursor"] for args in list_calls] == ["", "next"]
    assert all(args["start"] == "2026-10-19T00:00:00+00:00" for args in list_calls)
    assert all(args["end"] == "2026-10-22T00:00:00+00:00" for args in list_calls)


def test_exact_info_conflict_or_scoped_duplicate_rejects_source():
    start_ms = int(datetime(2026, 10, 20, 20, tzinfo=timezone.utc).timestamp() * 1000)
    meeting = {**MEETING, "startTime": start_ms, "endTime": start_ms + 3_600_000}
    body = _canonical_minutes_todos_payload(minutes=meeting, todos_payload=TODOS, transcript_excerpts=[])

    class TimedDws(FakeDws):
        def __init__(self, *, wrong_title=False, duplicate=False):
            items = [meeting, meeting] if duplicate else [meeting]
            super().__init__(pages=[{"items": items, "has_more": False, "next_token": ""}])
            self.wrong_title = wrong_title

        def get_minutes_info(self, uuid):
            self.calls.append(("info", uuid))
            return {"result": {"taskUuid": uuid, "title": "不一致" if self.wrong_title else meeting["title"],
                               "startTime": start_ms, "endTime": meeting["endTime"]}}

    for fake in (TimedDws(wrong_title=True), TimedDws(duplicate=True)):
        assert RequestSourceReader(provider_reader=fake).resolve(bound(body)).reason is SourceUnavailableReason.SOURCE_INCOMPLETE
        assert [name for name, _ in fake.calls] == ["info", "list"]


def test_exact_info_without_start_time_stops_before_list_read():
    body = _canonical_minutes_todos_payload(
        minutes=MEETING, todos_payload=TODOS, transcript_excerpts=[]
    )

    class IncompleteInfoDws(FakeDws):
        def get_minutes_info(self, uuid):
            self.calls.append(("info", uuid))
            return {"result": {"taskUuid": uuid}}

    fake = IncompleteInfoDws()
    assert RequestSourceReader(provider_reader=fake).resolve(
        bound(body)
    ).reason is SourceUnavailableReason.SOURCE_INCOMPLETE
    assert [name for name, _ in fake.calls] == ["info"]


def test_scoped_absence_is_source_missing_without_unbounded_scan():
    start_ms = int(datetime(2026, 10, 20, 20, tzinfo=timezone.utc).timestamp() * 1000)
    meeting = {**MEETING, "startTime": start_ms, "endTime": start_ms + 3_600_000}
    body = _canonical_minutes_todos_payload(minutes=meeting, todos_payload=TODOS, transcript_excerpts=[])

    class DelayedIndexDws(FakeDws):
        def get_minutes_info(self, uuid):
            self.calls.append(("info", uuid))
            return {"result": {"taskUuid": uuid, "title": meeting["title"],
                               "startTime": start_ms, "endTime": meeting["endTime"]}}

        def list_minutes_page(self, **kwargs):
            self.calls.append(("list", kwargs))
            return {"items": [] if kwargs.get("start") else [meeting],
                    "has_more": False, "next_token": ""}

    fake = DelayedIndexDws()
    assert RequestSourceReader(provider_reader=fake).resolve(bound(body)).reason is SourceUnavailableReason.SOURCE_MISSING
    assert [args.get("start", "") for name, args in fake.calls if name == "list"] == [
        "2026-10-19T00:00:00+00:00",
    ]


def test_four_components_and_changed_current_body():
    from app.minutes_todo_context import todo_transcript_excerpts

    timed_todos = {"result": {"actions": [], "dingtalkTodoList": [{"title": "办事", "createdTime": 1000}]}}
    excerpts = todo_transcript_excerpts(timed_todos, PARAGRAPHS)
    body = _canonical_minutes_todos_payload(
        minutes=MEETING, todos_payload=timed_todos,
        transcript_excerpts=excerpts, meeting_summary="摘要中文",
    )
    fake = FakeDws(todos=timed_todos, transcript={"paragraphs": PARAGRAPHS}, summary={"result": {"fullSummary": "摘要中文"}})
    binding = bound(body)
    assert json.loads(binding.provider_locator_json)["components"] == ["meeting", "todos", "transcript_excerpts", "meeting_summary"]
    assert isinstance(RequestSourceReader(provider_reader=fake).resolve(binding, quote="甲：请办事。"), AvailableSource)
    assert [name for name, _ in fake.calls] == ["info", "list", "todos", "transcript", "summary"]
    changed = FakeDws(todos=timed_todos, transcript={"paragraphs": PARAGRAPHS}, summary={"result": {"fullSummary": "changed"}})
    assert RequestSourceReader(provider_reader=changed).resolve(binding).reason is SourceUnavailableReason.VERSION_MISMATCH


@pytest.mark.parametrize("pages,reason", [
    ([{"items": [], "has_more": False, "next_token": ""}], SourceUnavailableReason.SOURCE_MISSING),
    ([{"items": [MEETING, MEETING], "has_more": False, "next_token": ""}], SourceUnavailableReason.SOURCE_INCOMPLETE),
    ([{"items": [MEETING], "has_more": True, "next_token": ""}], SourceUnavailableReason.SOURCE_INCOMPLETE),
    ([{"items": [MEETING], "has_more": False, "next_token": "extra"}], SourceUnavailableReason.SOURCE_INCOMPLETE),
    ([{"items": [MEETING], "has_more": None, "next_token": ""}], SourceUnavailableReason.SOURCE_INCOMPLETE),
    ([{"items": [MEETING], "has_more": True, "next_token": "next"}, {"items": [], "has_more": True, "next_token": "next"}], SourceUnavailableReason.SOURCE_INCOMPLETE),
])
def test_exhaustive_list_failures(pages, reason):
    body = _canonical_minutes_todos_payload(minutes=MEETING, todos_payload=TODOS, transcript_excerpts=[])
    assert RequestSourceReader(provider_reader=FakeDws(pages=pages)).resolve(bound(body)).reason is reason


def test_match_must_be_unambiguous_across_all_pages():
    pages = [{"items": [MEETING], "has_more": True, "next_token": "next"}, {"items": [MEETING], "has_more": False, "next_token": ""}]
    body = _canonical_minutes_todos_payload(minutes=MEETING, todos_payload=TODOS, transcript_excerpts=[])
    assert RequestSourceReader(provider_reader=FakeDws(pages=pages)).resolve(bound(body)).reason is SourceUnavailableReason.SOURCE_INCOMPLETE


def test_required_components_cannot_disappear():
    body = _canonical_minutes_todos_payload(minutes=MEETING, todos_payload=TODOS, transcript_excerpts=[{"todo": "办事"}], meeting_summary="摘要")
    binding = bound(body)
    assert RequestSourceReader(provider_reader=FakeDws(transcript=None)).resolve(binding).reason is SourceUnavailableReason.SOURCE_INCOMPLETE
    assert RequestSourceReader(provider_reader=FakeDws(transcript={"paragraphs": []}, summary={})).resolve(binding).reason is SourceUnavailableReason.SOURCE_INCOMPLETE
    two = bound(_canonical_minutes_todos_payload(minutes=MEETING, todos_payload=TODOS, transcript_excerpts=[]))
    assert RequestSourceReader(provider_reader=FakeDws(todos={"unknown": []})).resolve(two).reason is SourceUnavailableReason.SOURCE_INCOMPLETE


def test_locator_rejects_unsupported_original_and_other_kind():
    with pytest.raises(ValueError):
        minutes_provider_locator(signal('{"meeting":{},"todos":{},"extra":1}', source_type="ai_minutes", source_ref=REF))
    with pytest.raises(ValueError):
        minutes_provider_locator(signal("{}", source_type="ai_minutes", source_ref="not-a-minutes-ref"))
    weekly = SourceVersionBinding.from_signal(signal(source_type="weekly_report"), provider_locator={"kind": "ai_minutes"})
    assert RequestSourceReader(provider_reader=FakeDws()).resolve(weekly).reason is SourceUnavailableReason.READER_UNAVAILABLE


def test_provider_serialized_locator_wrong_type_is_invalid():
    body = _canonical_minutes_todos_payload(minutes=MEETING, todos_payload=TODOS, transcript_excerpts=[])
    binding = bound(body)
    assert RequestSourceReader(provider_reader=FakeDws()).resolve(
        replace(binding, provider_locator_json=123)
    ).reason is SourceUnavailableReason.INVALID_LOCATOR


@pytest.mark.parametrize("component", ["createdTime", "startTime", "endTime"])
def test_required_excerpt_with_nonfinite_provider_offset_is_incomplete(component):
    from app.minutes_todo_context import todo_transcript_excerpts

    original_todos = {"result": {"actions": [], "dingtalkTodoList": [{"title": "办事", "createdTime": 1000}]}}
    body = _canonical_minutes_todos_payload(
        minutes=MEETING, todos_payload=original_todos,
        transcript_excerpts=todo_transcript_excerpts(original_todos, PARAGRAPHS),
    )
    malformed = json.loads('{"offset": 1e999}')["offset"]
    todos = json.loads(json.dumps(original_todos))
    paragraphs = json.loads(json.dumps(PARAGRAPHS))
    if component == "createdTime":
        todos["result"]["dingtalkTodoList"][0][component] = malformed
    else:
        paragraphs[0][component] = malformed
    fake = FakeDws(todos=todos, transcript={"paragraphs": paragraphs})
    assert RequestSourceReader(provider_reader=fake).resolve(bound(body)).reason is SourceUnavailableReason.SOURCE_INCOMPLETE


def test_expected_dws_error_is_unavailable_and_programmer_error_surfaces():
    body = _canonical_minutes_todos_payload(minutes=MEETING, todos_payload=TODOS, transcript_excerpts=[])
    binding = bound(body)

    class Unreadable(FakeDws):
        def list_minutes_page(self, **kwargs):
            raise DwsError("unreadable")

    class Broken(FakeDws):
        def list_minutes_page(self, **kwargs):
            raise ZeroDivisionError

    class UnexpectedValueError(FakeDws):
        def list_minutes_page(self, **kwargs):
            raise ValueError("provider programmer bug")

    assert RequestSourceReader(provider_reader=Unreadable()).resolve(binding).reason is SourceUnavailableReason.SOURCE_INCOMPLETE
    with pytest.raises(ZeroDivisionError):
        RequestSourceReader(provider_reader=Broken()).resolve(binding)
    with pytest.raises(ValueError, match="provider programmer bug"):
        RequestSourceReader(provider_reader=UnexpectedValueError()).resolve(binding)


def test_historical_paragraph_coordinate_reconstructs_exact_original_without_guessing():
    from app.business_minutes_source_reader import read_minutes_source_body

    todos = {"result": {"actions": [], "dingtalkTodoList": [{"title": "办事", "createdTime": 1000}]}}
    paragraphs = [{"startTime": 0, "endTime": 2000, "nickName": "甲", "paragraph": "先办事。再核对！"}]
    body = _canonical_minutes_todos_payload(
        minutes=MEETING, todos_payload=todos,
        transcript_excerpts=[{"todo": "办事", "created_ms": 1000, "lines": ["甲：先办事。再核对！"]}],
    )
    item = signal(body, source_type="ai_minutes", source_ref=REF)
    locator = minutes_provider_locator(item, transcript_excerpt_format="paragraph_v1")
    assert locator["transcript_excerpt_format"] == "paragraph_v1"
    fake = FakeDws(todos=todos, transcript={"paragraphs": paragraphs})
    binding = SourceVersionBinding.from_signal(item, provider_locator=locator)
    resolved = RequestSourceReader(provider_reader=fake).resolve(binding)
    assert isinstance(resolved, AvailableSource)
    assert resolved.body == body
    current = {**locator, "transcript_excerpt_format": "sentence_v1"}
    current_fake = FakeDws(todos=todos, transcript={"paragraphs": paragraphs})
    assert read_minutes_source_body(current, source_ref=REF, dws=current_fake) != body
    current_binding = replace(binding, provider_locator_json=json.dumps(current))
    assert RequestSourceReader(provider_reader=FakeDws(todos=todos, transcript={"paragraphs": paragraphs})).resolve(
        current_binding
    ).reason is SourceUnavailableReason.VERSION_MISMATCH


@pytest.mark.parametrize("format_value", [None, "unproved", [], 1])
def test_excerpt_locator_requires_exact_format_before_provider_read(format_value):
    from app.business_minutes_source_reader import MinutesInvalidLocator, read_minutes_source_body

    locator = {"kind": "ai_minutes", "minutes_id": UUID, "components": ["meeting", "todos", "transcript_excerpts"]}
    if format_value is not None:
        locator["transcript_excerpt_format"] = format_value
    fake = FakeDws()
    with pytest.raises(MinutesInvalidLocator):
        read_minutes_source_body(locator, source_ref=REF, dws=fake)
    assert fake.calls == []
