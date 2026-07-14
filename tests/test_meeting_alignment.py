import json
from datetime import datetime, timedelta

import pytest

import app.meeting_alignment as meeting_alignment
from app.dws_client import (
    DwsCalendarAttendee,
    DwsCalendarEvent,
    DwsError,
)
from app.meeting_alignment import (
    consume_meeting_alignment_jobs,
    produce_meeting_alignment_jobs,
    recover_meeting_alignment_jobs,
)
from app.meeting_alignment_models import MeetingAlignmentDecision
from app.store import AutoReplyStore


NOW = datetime.fromisoformat("2026-07-14T10:10:00+08:00")


def ended_meeting(
    *,
    meeting_id: str = "minutes-1",
    title: str = "上线评审",
    start: str = "2026-07-14T09:00:00+08:00",
    end: str = "2026-07-14T10:00:00+08:00",
) -> dict:
    return {
        "taskUuid": meeting_id,
        "title": title,
        "startTimeISO": start,
        "endTimeISO": end,
        "status": "ended",
    }


def matching_calendar_event(
    *,
    event_id: str = "event-1",
    title: str = "上线评审",
    self_user: str | None = "u-derek",
) -> DwsCalendarEvent:
    attendees = [
        DwsCalendarAttendee(
            display_name="Derek",
            is_self=True,
            user_id=self_user or "",
            open_dingtalk_id="open-derek",
        ),
        DwsCalendarAttendee(
            display_name="A",
            user_id="u-a",
            open_dingtalk_id="open-a",
        ),
    ]
    return DwsCalendarEvent(
        event_id=event_id,
        title=title,
        start_time="2026-07-14T09:00:00+08:00",
        end_time="2026-07-14T10:00:00+08:00",
        status="confirmed",
        attendee_details=attendees,
    )


class FakeDws:
    def __init__(
        self,
        *,
        minutes_pages: dict[str, dict] | None = None,
        calendar_pages: dict[str, dict] | None = None,
        info: dict[str, dict] | None = None,
    ):
        meeting = ended_meeting()
        self.minutes_pages = minutes_pages or {
            "": {"items": [meeting], "has_more": False, "next_token": ""}
        }
        self.calendar_pages = calendar_pages or {
            "": {
                "events": [matching_calendar_event()],
                "has_more": False,
                "next_cursor": "",
            }
        }
        self.info = info or {meeting["taskUuid"]: meeting}
        self.minutes_calls: list[dict] = []
        self.calendar_calls: list[str] = []
        self.info_calls: list[str] = []

    def list_minutes_page(
        self, *, limit: int, cursor: str, start: str, end: str
    ) -> dict:
        assert limit == 50
        self.minutes_calls.append(
            {"cursor": cursor, "start": start, "end": end}
        )
        return self.minutes_pages[cursor]

    def get_minutes_info(self, meeting_id: str) -> dict:
        self.info_calls.append(meeting_id)
        return self.info[meeting_id]

    def get_current_user_id(self) -> str:
        return "u-derek"

    def list_calendar_events_page(
        self, *, start: str, end: str, limit: int, cursor: str
    ) -> dict:
        assert start
        assert end
        assert limit == 50
        self.calendar_calls.append(cursor)
        return self.calendar_pages[cursor]


class ConsumerDws(FakeDws):
    def __init__(self):
        super().__init__()
        self.calendar_pages[""]["events"][0].attendee_details.append(
            DwsCalendarAttendee(
                display_name="B",
                user_id="u-b",
                open_dingtalk_id="open-b",
            )
        )
        self.send_calls: list[dict] = []
        self.verify_calls: list[dict] = []
        self.send_result = {
            "success": True,
            "result": {"openMessageId": "msg-1"},
        }
        self.verification_states = ["sent"]

    def get_minutes_summary(self, meeting_id: str) -> dict:
        return {"result": {"fullSummary": "存在上线范围分歧。"}}

    def get_all_minutes_transcription(self, meeting_id: str) -> dict:
        return {
            "paragraphs": [
                {"nickName": "A", "paragraph": "建议全量"},
                {"nickName": "Derek", "paragraph": "先定义风险预算"},
            ]
        }

    def get_conversation_info(self, conversation_id: str) -> dict:
        return {
            "openConversationId": conversation_id,
            "title": "项目群",
            "singleChat": False,
            "memberCount": 3,
        }

    def search_user_profiles(self, query: str) -> list:
        return []

    def read_recent_messages(self, conversation, limit=50) -> list:
        return []

    def send_message(self, conversation_id, text, **kwargs):
        self.send_calls.append(
            {"conversation_id": conversation_id, "text": text, **kwargs}
        )
        return self.send_result

    def verify_message_send_result(self, send_result: dict) -> dict:
        self.verify_calls.append(send_result)
        state = self.verification_states.pop(0)
        return {
            "state": state,
            "open_task_id": send_result.get("result", {}).get(
                "openTaskId", ""
            ),
            "status_result": {"state": state},
        }


class FakeMeetingRunner:
    last_session_id = "meeting-session-1"
    last_transcript_start_line = 4
    last_transcript_end_line = 19
    last_audit_tool_events = [{"tool": "dws", "command": "group search"}]

    def __init__(self, decision: MeetingAlignmentDecision):
        self.decision = decision
        self.calls = 0

    def decide(self, *, prompt: str) -> MeetingAlignmentDecision:
        self.calls += 1
        return self.decision


def no_action_decision() -> MeetingAlignmentDecision:
    return MeetingAlignmentDecision.model_validate(
        {
            "action": "no_action",
            "trigger_reasons": [],
            "topics": [],
            "derek_viewpoint": None,
            "key_questions": [],
            "mention_names": [],
            "target": None,
            "final_message": "",
            "audit_summary": "没有需要发布的观点分歧。",
            "confidence": 0.9,
        }
    )


def consumer_send_decision() -> MeetingAlignmentDecision:
    return MeetingAlignmentDecision.model_validate(
        {
            "action": "send",
            "trigger_reasons": ["unresolved_disagreement"],
            "topics": [
                {
                    "title": "上线范围",
                    "state": "unresolved",
                    "views": [
                        {"speaker": "A", "view": "全量", "reason": "收入"},
                        {"speaker": "B", "view": "灰度", "reason": "风险"},
                    ],
                    "conclusion": "",
                    "alignment_reason": "",
                }
            ],
            "derek_viewpoint": None,
            "key_questions": [
                {
                    "question": "最多接受多大故障面？",
                    "answer_owner_names": ["A", "B"],
                }
            ],
            "mention_names": [],
            "target": {
                "kind": "group",
                "conversation_id": "cid-first",
                "direct_user_id": "",
                "title": "项目群",
                "candidates": [
                    {
                        "conversation_id": "cid-first",
                        "title": "项目群",
                        "evidence": ["会前后讨论同一议题"],
                    }
                ],
            },
            "final_message": "会后对齐｜上线评审\n\n请确认故障面。",
            "audit_summary": "存在未对齐的上线范围取舍。",
            "confidence": 0.88,
        }
    )


def seed_consumer_job(store: AutoReplyStore, dws: ConsumerDws) -> int:
    assert produce_meeting_alignment_jobs(store, dws, now=NOW) == 1
    job = store.get_meeting_alignment_job_by_meeting_id("minutes-1")
    assert job is not None
    return job.id


def test_producer_leaves_no_calendar_match_unqueued(tmp_path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    dws = FakeDws(
        calendar_pages={
            "": {"events": [], "has_more": False, "next_cursor": ""}
        }
    )

    assert produce_meeting_alignment_jobs(store, dws, now=NOW) == 0
    assert store.get_meeting_alignment_job_by_meeting_id("minutes-1") is None


def test_producer_leaves_ambiguous_calendar_matches_unqueued(tmp_path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    dws = FakeDws(
        calendar_pages={
            "": {
                "events": [
                    matching_calendar_event(event_id="event-1"),
                    matching_calendar_event(event_id="event-2"),
                ],
                "has_more": False,
                "next_cursor": "",
            }
        }
    )

    assert produce_meeting_alignment_jobs(store, dws, now=NOW) == 0
    assert store.get_meeting_alignment_job_by_meeting_id("minutes-1") is None


def test_producer_leaves_meeting_without_exactly_one_self_attendee_unqueued(tmp_path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    event = matching_calendar_event()
    event.attendee_details[0].is_self = False
    dws = FakeDws(
        calendar_pages={
            "": {"events": [event], "has_more": False, "next_cursor": ""}
        }
    )

    assert produce_meeting_alignment_jobs(store, dws, now=NOW) == 0
    assert store.get_meeting_alignment_job_by_meeting_id("minutes-1") is None


def test_producer_stores_waiting_job_before_ten_minutes(tmp_path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")

    assert (
        produce_meeting_alignment_jobs(
            store,
            FakeDws(),
            now=datetime.fromisoformat("2026-07-14T10:09:59+08:00"),
            settle_seconds=600,
        )
        == 1
    )
    job = store.get_meeting_alignment_job_by_meeting_id("minutes-1")
    assert job is not None
    assert job.status == "waiting"


def test_producer_queues_at_exactly_ten_minutes_and_preserves_evidence(tmp_path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")

    assert produce_meeting_alignment_jobs(
        store, FakeDws(), now=NOW, settle_seconds=600
    ) == 1

    job = store.get_meeting_alignment_job_by_meeting_id("minutes-1")
    assert job is not None
    assert job.status == "pending"
    assert job.eligible_at == "2026-07-14T10:10:00+08:00"
    source = json.loads(job.source_json)
    assert source["calendar_evidence"]["event_id"] == "event-1"
    assert source["discovery"]["meeting_id"] == "minutes-1"
    assert json.loads(job.participants_json) == source["calendar_evidence"][
        "participants"
    ]


def test_producer_deduplicates_by_stable_minutes_id(tmp_path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    dws = FakeDws()

    assert produce_meeting_alignment_jobs(store, dws, now=NOW) == 1
    first = store.get_meeting_alignment_job_by_meeting_id("minutes-1")
    assert produce_meeting_alignment_jobs(store, dws, now=NOW) == 0
    second = store.get_meeting_alignment_job_by_meeting_id("minutes-1")

    assert first is not None
    assert second is not None
    assert second.id == first.id


def test_producer_paginates_minutes_and_calendar_before_matching(tmp_path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    dws = FakeDws(
        minutes_pages={
            "": {"items": [], "has_more": True, "next_token": "minutes-2"},
            "minutes-2": {
                "items": [ended_meeting()],
                "has_more": False,
                "next_token": "",
            },
        },
        calendar_pages={
            "": {"events": [], "has_more": True, "next_cursor": "calendar-2"},
            "calendar-2": {
                "events": [matching_calendar_event()],
                "has_more": False,
                "next_cursor": "",
            },
        },
    )

    assert produce_meeting_alignment_jobs(store, dws, now=NOW) == 1
    assert [call["cursor"] for call in dws.minutes_calls] == ["", "minutes-2"]
    assert dws.calendar_calls == ["", "calendar-2"]


def test_producer_passes_bounded_minutes_discovery_window(tmp_path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    dws = FakeDws()

    produce_meeting_alignment_jobs(
        store,
        dws,
        now=NOW,
        discovery_lookback=timedelta(days=3),
    )

    assert dws.minutes_calls == [
        {
            "cursor": "",
            "start": "2026-07-11T10:10:00+08:00",
            "end": "2026-07-14T10:10:00+08:00",
        }
    ]


def test_producer_defaults_to_seven_day_discovery_window(tmp_path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    dws = FakeDws()

    produce_meeting_alignment_jobs(store, dws, now=NOW)

    assert dws.minutes_calls[0]["start"] == "2026-07-07T10:10:00+08:00"
    assert dws.minutes_calls[0]["end"] == "2026-07-14T10:10:00+08:00"


def test_producer_skips_meetings_before_persisted_live_activation(tmp_path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    store.set_service_state(
        "meeting_alignment_discovery_activated_at",
        "2026-07-14T10:01:00+08:00",
    )
    dws = FakeDws()

    assert produce_meeting_alignment_jobs(store, dws, now=NOW) == 0
    assert store.get_meeting_alignment_job_by_meeting_id("minutes-1") is None
    assert dws.calendar_calls == []


def test_consumer_records_no_action_run_and_terminal_job(tmp_path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    dws = ConsumerDws()
    job_id = seed_consumer_job(store, dws)
    runner = FakeMeetingRunner(no_action_decision())

    assert consume_meeting_alignment_jobs(
        store, dws, runner, now=NOW, limit=1
    ) == 1

    job = store.get_meeting_alignment_job(job_id)
    assert job.status == "no_action"
    assert job.locked_at is None
    [run] = store.list_meeting_alignment_runs(job_id)
    assert run.status == "no_action"
    assert run.codex_session_id == "meeting-session-1"
    assert run.codex_transcript_start_line == 4
    assert run.codex_transcript_end_line == 19
    assert json.loads(run.audit_tool_events_json)[0]["tool"] == "dws"


def test_consumer_retries_invalid_model_decision(tmp_path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    dws = ConsumerDws()
    job_id = seed_consumer_job(store, dws)

    class InvalidDecisionRunner:
        last_session_id = "meeting-invalid-decision"
        last_transcript_start_line = 0
        last_transcript_end_line = 12
        last_audit_tool_events = []

        def decide(self, *, prompt: str):
            raise RuntimeError(
                "Codex did not return a valid MeetingAlignmentDecision"
            )

    assert consume_meeting_alignment_jobs(
        store,
        dws,
        InvalidDecisionRunner(),
        now=NOW,
        limit=1,
    ) == 1

    job = store.get_meeting_alignment_job(job_id)
    assert job.status == "retry"
    assert job.available_at == (NOW + timedelta(minutes=1)).isoformat()
    [run] = store.list_meeting_alignment_runs(job_id)
    assert run.status == "retry"
    assert json.loads(run.error)["kind"] == "meeting_agent"
    assert dws.send_calls == []


def test_consumer_persists_ready_before_external_send_and_marks_sent(tmp_path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    dws = ConsumerDws()
    job_id = seed_consumer_job(store, dws)
    runner = FakeMeetingRunner(consumer_send_decision())
    seen_statuses: list[str] = []
    original_send = dws.send_message

    def observing_send(conversation_id, text, **kwargs):
        seen_statuses.append(store.get_meeting_alignment_job(job_id).status)
        return original_send(conversation_id, text, **kwargs)

    dws.send_message = observing_send

    assert consume_meeting_alignment_jobs(
        store, dws, runner, now=NOW, limit=1
    ) == 1

    job = store.get_meeting_alignment_job(job_id)
    assert seen_statuses == ["ready_to_send"]
    assert job.status == "sent"
    assert job.final_message == consumer_send_decision().final_message
    assert job.target_kind == "group"
    assert job.target_id == "cid-first"
    assert json.loads(job.decision_json)["action"] == "send"
    assert json.loads(job.send_result_json)["status"] == "sent"
    [run] = store.list_meeting_alignment_runs(job_id)
    assert run.status == "ready_to_send"


def test_consumer_dry_run_analyzes_but_does_not_claim_or_send(tmp_path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    dws = ConsumerDws()
    job_id = seed_consumer_job(store, dws)
    runner = FakeMeetingRunner(consumer_send_decision())

    assert consume_meeting_alignment_jobs(
        store,
        dws,
        runner,
        now=NOW,
        limit=1,
        deliver=False,
    ) == 1

    assert store.get_meeting_alignment_job(job_id).status == "ready_to_send"
    assert dws.send_calls == []


def test_consumer_reconciles_ambiguous_task_without_duplicate_send(tmp_path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    dws = ConsumerDws()
    dws.send_result = {
        "success": True,
        "result": {"openTaskId": "task-1"},
    }
    dws.verification_states = ["ambiguous", "sent"]
    job_id = seed_consumer_job(store, dws)
    runner = FakeMeetingRunner(consumer_send_decision())

    consume_meeting_alignment_jobs(store, dws, runner, now=NOW, limit=1)
    first = store.get_meeting_alignment_job(job_id)
    assert first.status == "ready_to_send"
    assert len(dws.send_calls) == 1
    assert json.loads(first.send_result_json)["send_verification"][
        "open_task_id"
    ] == "task-1"

    consume_meeting_alignment_jobs(store, dws, runner, now=NOW, limit=1)
    assert len(dws.verify_calls) == 1
    assert len(dws.send_calls) == 1

    consume_meeting_alignment_jobs(
        store, dws, runner, now=NOW + timedelta(minutes=1), limit=1
    )
    second = store.get_meeting_alignment_job(job_id)
    assert second.status == "sent"
    assert len(dws.send_calls) == 1
    assert runner.calls == 1
    assert len(dws.verify_calls) == 2


def test_confirmed_failed_reconciliation_uses_counted_retry_backoff(tmp_path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    dws = ConsumerDws()
    dws.send_result = {
        "success": True,
        "result": {"openTaskId": "task-1"},
    }
    dws.verification_states = ["ambiguous", "failed"]
    job_id = seed_consumer_job(store, dws)
    runner = FakeMeetingRunner(consumer_send_decision())

    consume_meeting_alignment_jobs(store, dws, runner, now=NOW, limit=1)
    consume_meeting_alignment_jobs(
        store, dws, runner, now=NOW + timedelta(minutes=1), limit=1
    )

    job = store.get_meeting_alignment_job(job_id)
    assert job.status == "retry"
    assert job.available_at == "2026-07-14T10:12:00+08:00"
    assert json.loads(job.error)["kind"] == (
        "meeting_send_reconcile_failed"
    )
    assert len(dws.send_calls) == 1
    assert runner.calls == 1


def test_ambiguous_reconciliation_hits_max_without_resend_or_reanalysis(tmp_path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    dws = ConsumerDws()
    dws.send_result = {
        "success": True,
        "result": {"openTaskId": "task-1"},
    }
    dws.verification_states = ["ambiguous", "ambiguous"]
    job_id = seed_consumer_job(store, dws)
    runner = FakeMeetingRunner(consumer_send_decision())

    consume_meeting_alignment_jobs(
        store, dws, runner, now=NOW, limit=1, max_attempts=2
    )
    first = store.get_meeting_alignment_job(job_id)
    assert first.status == "ready_to_send"
    assert first.attempts == 2

    consume_meeting_alignment_jobs(
        store,
        dws,
        runner,
        now=NOW + timedelta(minutes=1),
        limit=1,
        max_attempts=2,
    )
    final = store.get_meeting_alignment_job(job_id)
    assert final.status == "failed"
    assert json.loads(final.error)["kind"] == "meeting_send_reconcile_max"
    assert json.loads(final.send_result_json)["send_verification"][
        "open_task_id"
    ] == "task-1"
    assert len(dws.send_calls) == 1
    assert runner.calls == 1
    assert len(dws.verify_calls) == 2


def test_ready_delivery_source_failure_uses_counted_retry(tmp_path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    dws = ConsumerDws()
    job_id = seed_consumer_job(store, dws)
    decision = consumer_send_decision()
    store.update_meeting_alignment_job(
        job_id,
        status="ready_to_send",
        decision_json=decision.model_dump_json(),
        final_message=decision.final_message,
    )

    def fail_info(meeting_id: str):
        raise DwsError("minutes temporarily unavailable")

    dws.get_minutes_info = fail_info
    runner = FakeMeetingRunner(decision)

    consume_meeting_alignment_jobs(
        store, dws, runner, now=NOW + timedelta(minutes=1), limit=1
    )
    job = store.get_meeting_alignment_job(job_id)
    assert job.status == "retry"
    assert job.available_at == "2026-07-14T10:12:00+08:00"
    assert json.loads(job.error)["kind"] == "meeting_source"
    assert dws.send_calls == []
    assert runner.calls == 0


def test_consumer_quarantines_ambiguous_send_without_verifiable_id(tmp_path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    dws = ConsumerDws()
    dws.send_result = {"success": True, "result": {}}
    dws.verification_states = ["ambiguous"]
    job_id = seed_consumer_job(store, dws)
    runner = FakeMeetingRunner(consumer_send_decision())

    consume_meeting_alignment_jobs(
        store, dws, runner, now=NOW + timedelta(minutes=1), limit=1
    )
    job = store.get_meeting_alignment_job(job_id)
    assert job.status == "failed"
    assert json.loads(job.error)["kind"] == "meeting_send_ambiguous_no_id"
    assert len(dws.send_calls) == 1

    consume_meeting_alignment_jobs(store, dws, runner, now=NOW, limit=1)
    assert len(dws.send_calls) == 1
    assert runner.calls == 1


def test_consumer_quarantines_corrupt_persisted_send_evidence(tmp_path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    dws = ConsumerDws()
    dws.send_result = {
        "success": True,
        "result": {"openTaskId": "task-1"},
    }
    dws.verification_states = ["ambiguous"]
    job_id = seed_consumer_job(store, dws)
    runner = FakeMeetingRunner(consumer_send_decision())

    consume_meeting_alignment_jobs(store, dws, runner, now=NOW, limit=1)
    assert len(dws.send_calls) == 1
    store.update_meeting_alignment_job(
        job_id,
        send_result_json='{"status":"ambiguous","truncated":true}',
    )

    consume_meeting_alignment_jobs(
        store, dws, runner, now=NOW + timedelta(minutes=1), limit=1
    )
    job = store.get_meeting_alignment_job(job_id)
    assert job.status == "failed"
    assert json.loads(job.error)["kind"] == "meeting_send_evidence"
    assert len(dws.send_calls) == 1


def test_startup_recovery_only_requeues_processing_and_unlocks_ready(tmp_path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    dws = ConsumerDws()
    processing_id = seed_consumer_job(store, dws)
    [processing] = store.claim_meeting_alignment_jobs(
        limit=1, now=NOW.isoformat()
    )
    assert processing.id == processing_id
    store.update_meeting_alignment_job(
        processing_id,
        decision_json='{"preserve":"analysis"}',
        send_result_json='{"preserve":"send"}',
    )

    ready_id = store.upsert_meeting_alignment_job(
        meeting_id="minutes-ready",
        title="另一个会议",
        source_json='{"preserve":"source"}',
        participants_json="[]",
        ended_at="2026-07-14T10:00:00+08:00",
        eligible_at=NOW.isoformat(),
        status="pending",
    )
    store.update_meeting_alignment_job(
        ready_id,
        status="ready_to_send",
        decision_json='{"preserve":"ready"}',
        send_result_json='{"preserve":"ambiguous"}',
    )
    [ready] = store.claim_ready_to_send_meeting_alignment_jobs(
        limit=1, now=NOW.isoformat()
    )
    assert ready.id == ready_id

    assert recover_meeting_alignment_jobs(store) == 2

    processing_after = store.get_meeting_alignment_job(processing_id)
    assert processing_after.status == "retry"
    assert processing_after.attempts == 1
    assert processing_after.decision_json == '{"preserve":"analysis"}'
    assert processing_after.send_result_json == '{"preserve":"send"}'
    assert json.loads(processing_after.error)["kind"] == (
        "meeting_alignment_service_startup_requeue"
    )
    ready_after = store.get_meeting_alignment_job(ready_id)
    assert ready_after.status == "ready_to_send"
    assert ready_after.locked_at is None
    assert ready_after.decision_json == '{"preserve":"ready"}'
    assert ready_after.send_result_json == '{"preserve":"ambiguous"}'
    assert json.loads(ready_after.error)["kind"] == (
        "meeting_alignment_service_startup_requeue"
    )


@pytest.mark.parametrize("lookback", [timedelta(0), timedelta(seconds=-1)])
def test_producer_rejects_nonpositive_discovery_lookback(tmp_path, lookback):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")

    with pytest.raises(ValueError, match="lookback must be positive"):
        produce_meeting_alignment_jobs(
            store,
            FakeDws(),
            now=NOW,
            discovery_lookback=lookback,
        )


def test_malformed_minutes_record_does_not_prevent_later_valid_meeting(tmp_path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    malformed = ended_meeting(meeting_id="minutes-bad")
    malformed["endTimeISO"] = float("inf")
    valid = ended_meeting()
    dws = FakeDws(
        minutes_pages={
            "": {
                "items": [malformed, valid],
                "has_more": False,
                "next_token": "",
            }
        },
        info={"minutes-bad": malformed, "minutes-1": valid},
    )

    assert produce_meeting_alignment_jobs(store, dws, now=NOW) == 1
    assert store.get_meeting_alignment_job_by_meeting_id("minutes-bad") is None
    assert store.get_meeting_alignment_job_by_meeting_id("minutes-1") is not None


@pytest.mark.parametrize(
    ("target", "conflicting_alias", "conflicting_value"),
    [
        ("list", "uuid", "minutes-other"),
        ("info", "state", "running"),
        ("info", "startedAt", "2026-07-14T09:01:00+08:00"),
        ("info", "endedAt", "2026-07-14T10:01:00+08:00"),
        ("info", "name", "另一个会议"),
    ],
)
def test_producer_leaves_conflicting_minutes_aliases_unqueued(
    tmp_path, target, conflicting_alias, conflicting_value
):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    list_item = ended_meeting()
    info = ended_meeting()
    (list_item if target == "list" else info)[conflicting_alias] = conflicting_value
    dws = FakeDws(
        minutes_pages={
            "": {
                "items": [list_item],
                "has_more": False,
                "next_token": "",
            }
        },
        info={"minutes-1": info},
    )

    assert produce_meeting_alignment_jobs(store, dws, now=NOW) == 0
    assert store.get_meeting_alignment_job_by_meeting_id("minutes-1") is None


@pytest.mark.parametrize("status", ["processing", "unknown"])
def test_producer_requires_explicit_status_to_be_ended(tmp_path, status):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    meeting = ended_meeting()
    meeting["status"] = status
    dws = FakeDws(
        minutes_pages={
            "": {
                "items": [meeting],
                "has_more": False,
                "next_token": "",
            }
        },
        info={"minutes-1": meeting},
    )

    assert produce_meeting_alignment_jobs(store, dws, now=NOW) == 0
    assert store.get_meeting_alignment_job_by_meeting_id("minutes-1") is None


@pytest.mark.parametrize(
    ("pages_attribute", "initial_page"),
    [
        (
            "minutes_pages",
            {"items": [], "has_more": True, "next_token": "repeat"},
        ),
        (
            "calendar_pages",
            {"events": [], "has_more": True, "next_cursor": "repeat"},
        ),
    ],
)
def test_producer_hard_fails_repeated_pagination_cursor(
    tmp_path, pages_attribute, initial_page
):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    dws = FakeDws()
    setattr(dws, pages_attribute, {"": initial_page, "repeat": initial_page})

    with pytest.raises(DwsError, match="repeated"):
        produce_meeting_alignment_jobs(store, dws, now=NOW)


@pytest.mark.parametrize("pages_attribute", ["minutes_pages", "calendar_pages"])
def test_producer_rejects_terminal_page_with_continuation_cursor(
    tmp_path, pages_attribute
):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    dws = FakeDws()
    if pages_attribute == "minutes_pages":
        pages = {
            "": {"items": [], "has_more": False, "next_token": "unexpected"}
        }
    else:
        pages = {
            "": {
                "events": [matching_calendar_event()],
                "has_more": False,
                "next_cursor": "unexpected",
            }
        }
    setattr(dws, pages_attribute, pages)

    with pytest.raises(DwsError, match="continuation"):
        produce_meeting_alignment_jobs(store, dws, now=NOW)


@pytest.mark.parametrize("pages_attribute", ["minutes_pages", "calendar_pages"])
def test_producer_rejects_continuing_page_without_cursor(
    tmp_path, pages_attribute
):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    dws = FakeDws()
    if pages_attribute == "minutes_pages":
        pages = {"": {"items": [], "has_more": True, "next_token": ""}}
    else:
        pages = {"": {"events": [], "has_more": True, "next_cursor": ""}}
    setattr(dws, pages_attribute, pages)

    with pytest.raises(DwsError, match="without next"):
        produce_meeting_alignment_jobs(store, dws, now=NOW)


@pytest.mark.parametrize("pages_attribute", ["minutes_pages", "calendar_pages"])
@pytest.mark.parametrize("invalid_has_more", [None, "false", 0])
def test_producer_rejects_missing_or_nonboolean_has_more(
    tmp_path, pages_attribute, invalid_has_more
):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    dws = FakeDws()
    if pages_attribute == "minutes_pages":
        page = {"items": [], "next_token": "page-2"}
        if invalid_has_more is not None:
            page["has_more"] = invalid_has_more
        pages = {"": page}
    else:
        page = {"events": [], "next_cursor": "page-2"}
        if invalid_has_more is not None:
            page["has_more"] = invalid_has_more
        pages = {"": page}
    setattr(dws, pages_attribute, pages)

    with pytest.raises(DwsError, match="has_more must be boolean"):
        produce_meeting_alignment_jobs(store, dws, now=NOW)


@pytest.mark.parametrize("pages_attribute", ["minutes_pages", "calendar_pages"])
def test_producer_hard_fails_pagination_page_limit(
    tmp_path, monkeypatch, pages_attribute
):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    dws = FakeDws()
    if pages_attribute == "minutes_pages":
        pages = {
            "": {"items": [], "has_more": True, "next_token": "page-2"},
            "page-2": {
                "items": [],
                "has_more": True,
                "next_token": "page-3",
            },
        }
    else:
        pages = {
            "": {"events": [], "has_more": True, "next_cursor": "page-2"},
            "page-2": {
                "events": [],
                "has_more": True,
                "next_cursor": "page-3",
            },
        }
    setattr(dws, pages_attribute, pages)
    monkeypatch.setattr(meeting_alignment, "DISCOVERY_PAGE_LIMIT", 2)

    with pytest.raises(DwsError, match="exceeded 2 pages"):
        produce_meeting_alignment_jobs(store, dws, now=NOW)


def test_producer_never_reenters_terminal_job(tmp_path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    dws = FakeDws()
    produce_meeting_alignment_jobs(store, dws, now=NOW)
    job = store.get_meeting_alignment_job_by_meeting_id("minutes-1")
    assert job is not None
    store.update_meeting_alignment_job(job.id, status="no_action")

    assert produce_meeting_alignment_jobs(store, dws, now=NOW) == 0
    assert store.get_meeting_alignment_job(job.id).status == "no_action"
