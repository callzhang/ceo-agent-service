import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta

from types import SimpleNamespace

import pytest

import app.meeting_alignment as meeting_alignment
from app.agent_cron.commands import (
    SERVICE_COMMAND_OPTIONS,
    ServiceCommandConsumerContext,
    ServiceCommandRegistry,
    ServiceCommandSkillMaterial,
)
from tests.scheduled_command_results import unknown_service_result
from app.agent_runtime_contracts import RuntimeFailureClass
from app.agent_runtime_router import RoutedCodexExecutionError
from app.dws_client import (
    DwsCalendarAttendee,
    DwsCalendarEvent,
    DwsError,
    DwsUserProfile,
)
from app.dingtalk_models import DingTalkConversation
from app.external_retry import ExternalDependencyError
from app.meeting_alignment import (
    consume_meeting_alignment_jobs,
    deliver_ready_meeting_alignment_jobs,
    produce_meeting_alignment_jobs,
    queue_recent_meeting_alignment_replay,
    recover_meeting_alignment_jobs,
)
from app.meeting_alignment_models import (
    MeetingAlignmentAuditFeedback,
    MeetingAlignmentAuditResult,
    MeetingAlignmentDecision,
    MeetingSource,
    meeting_alignment_decision_digest,
    persist_audited_meeting_decision,
)
from tests.support.native_task_fields import set_native_task_fields
from app.action_task_delivery_definitions import task_delivery_projection
from app.store import MEETING_LOGICAL_RUN_SQL
from app.store import AutoReplyStore
from app.skill_features import FeatureRegistry

NOW = datetime.fromisoformat("2026-07-14T10:10:00+08:00")


def _set_meeting_job_fields(store: AutoReplyStore, job_id: int, **values: object) -> None:
    with store._connect() as db:
        set_native_task_fields(db, "meeting_alignment_jobs", job_id, values)


@pytest.mark.parametrize("status", ["pending", "processing", "retry", "ready_to_send"])
def test_discovery_failure_preserves_active_meeting_source(tmp_path, status):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    original_source = json.dumps({"calendar_evidence": {"event_id": "event-1"}})
    original_participants = '[{"user_id":"owner-1","name":"Owner"}]'
    job_id = store.upsert_meeting_alignment_job(
        meeting_id="minutes-1", title="Meeting", source_json=original_source,
        participants_json=original_participants, ended_at=NOW.isoformat(),
        eligible_at=NOW.isoformat(), status=status,
    )
    meeting_alignment._store_meeting_discovery_terminal_job(
        store, meeting_id="minutes-1", title="Meeting", list_item={}, info={},
        status="skipped", error='{"kind":"meeting_roster","message":"DNS failed"}',
        now=NOW,
    )
    job = store.get_meeting_alignment_job(job_id)
    assert job.status == status
    assert job.source_json == original_source
    assert job.participants_json == original_participants


def test_producer_does_not_create_new_job_when_feature_disabled(tmp_path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    dws = FakeDws()
    registry = FeatureRegistry(
        state_path=tmp_path / "skill-state.json"
    )
    registry.set_enabled("meeting_summary", False)

    assert (
        produce_meeting_alignment_jobs(
            store,
            dws,
            now=NOW,
            feature_registry=registry,
        )
        == 0
    )
    assert store.get_meeting_alignment_job_by_meeting_id("minutes-1") is None


def test_meeting_producer_carries_scheduled_consumer_context(tmp_path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    context = ServiceCommandConsumerContext(
        scheduled_task_id=3,
        scheduled_task_run_id=5,
        prompt="使用 $ceo-meeting-work 处理真实会议。",
        skill_names=("ceo-meeting-work",),
        skill_protocol="# Targeted Meeting Skill",
    )
    implementations = {
        option.name: lambda: unknown_service_result("unused")
        for option in SERVICE_COMMAND_OPTIONS
    }
    implementations["scan-meetings-once"] = lambda: unknown_service_result(str(
        produce_meeting_alignment_jobs(store, FakeDws(), now=NOW)
    ))

    ServiceCommandRegistry(implementations).run(
        "scan-meetings-once",
        consumer_context=context,
    )

    job = store.get_meeting_alignment_job_by_meeting_id("minutes-1")
    assert job is not None
    assert json.loads(job.source_json)["scheduled_consumer"] == context.to_payload()


def test_disabled_feature_still_processes_existing_meeting_job(tmp_path, monkeypatch):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    dws = FakeDws()
    assert produce_meeting_alignment_jobs(store, dws, now=NOW) == 1
    job = store.get_meeting_alignment_job_by_meeting_id("minutes-1")
    assert job is not None
    registry = FeatureRegistry(
        state_path=tmp_path / "skill-state.json"
    )
    registry.set_enabled("meeting_summary", False)
    analyzed: list[int] = []
    monkeypatch.setattr(
        meeting_alignment,
        "_analyze_meeting_job",
        lambda _store, _dws, _runner, claimed, **_kwargs: analyzed.append(claimed.id),
    )

    assert (
        consume_meeting_alignment_jobs(
            store,
            dws,
            object(),
            now=NOW,
            deliver=False,
            feature_registry=registry,
        )
        == 1
    )
    assert analyzed == [job.id]
    assert store.get_meeting_alignment_job(job.id).status == "processing"


def test_disabled_feature_still_delivers_existing_ready_meeting_job(tmp_path, monkeypatch):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    dws = FakeDws()
    assert produce_meeting_alignment_jobs(store, dws, now=NOW) == 1
    job = store.get_meeting_alignment_job_by_meeting_id("minutes-1")
    assert job is not None
    store.update_meeting_alignment_job(job.id, status="ready_to_send")
    registry = FeatureRegistry(state_path=tmp_path / "skill-state.json")
    registry.set_enabled("meeting_summary", False)
    delivered: list[int] = []
    monkeypatch.setattr(
        meeting_alignment,
        "_deliver_meeting_job",
        lambda _store, _dws, claimed, **_kwargs: delivered.append(claimed.id),
    )

    assert (
        consume_meeting_alignment_jobs(
            store,
            dws,
            object(),
            now=NOW,
            feature_registry=registry,
        )
        == 1
    )
    assert delivered == [job.id]


def test_internal_delivery_pass_never_claims_pending_analysis_jobs(
    tmp_path, monkeypatch
):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    dws = FakeDws()
    assert produce_meeting_alignment_jobs(store, dws, now=NOW) == 1
    job = store.get_meeting_alignment_job_by_meeting_id("minutes-1")
    assert job is not None
    store.update_meeting_alignment_job(job.id, status="ready_to_send")
    delivered: list[int] = []
    monkeypatch.setattr(
        store,
        "claim_meeting_alignment_jobs",
        lambda *_args, **_kwargs: pytest.fail("delivery must not claim analysis"),
    )
    monkeypatch.setattr(
        meeting_alignment,
        "_deliver_meeting_job",
        lambda _store, _dws, claimed, **_kwargs: delivered.append(claimed.id),
    )

    result = deliver_ready_meeting_alignment_jobs(
        store, dws, now=NOW, limit=1
    )

    assert result == {job.id}
    assert delivered == [job.id]


def test_delivery_terminal_write_failure_releases_claim_without_resending(
    tmp_path, monkeypatch
):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    dws = ConsumerDws()
    job_id = seed_consumer_job(store, dws)
    runner = FakeMeetingRunner(consumer_send_decision())
    consume_meeting_alignment_jobs(store, dws, runner, now=NOW, limit=1)
    original = store.get_meeting_alignment_job(job_id)
    store.update_meeting_alignment_job(
        job_id, status="ready_to_send", calendar_summary_status="not_started"
    )
    update = store.update_meeting_alignment_job

    def fail_terminal_write(job_id, **values):
        if values.get("status") == "sent":
            raise sqlite3.OperationalError("database is locked")
        return update(job_id, **values)

    monkeypatch.setattr(store, "update_meeting_alignment_job", fail_terminal_write)
    with pytest.raises(sqlite3.OperationalError, match="database is locked"):
        deliver_ready_meeting_alignment_jobs(store, dws, now=NOW, limit=1)

    interrupted = store.get_meeting_alignment_job(job_id)
    assert interrupted.status == "ready_to_send"
    assert interrupted.locked_at is None
    assert interrupted.send_result_json == original.send_result_json
    assert len(dws.send_calls) == 1

    monkeypatch.setattr(store, "update_meeting_alignment_job", update)
    assert deliver_ready_meeting_alignment_jobs(
        store, dws, now=NOW + timedelta(minutes=1), limit=1
    ) == {job_id}
    assert store.get_meeting_alignment_job(job_id).status == "sent"
    assert len(dws.send_calls) == 1
    assert runner.calls == 1


def test_delivery_exception_releases_unprocessed_batch_claims(tmp_path, monkeypatch):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    ids = [
        store.upsert_meeting_alignment_job(
            meeting_id=f"minutes-{index}", title="Meeting", source_json="{}",
            participants_json="[]", ended_at=NOW.isoformat(),
            eligible_at=NOW.isoformat(), status="ready_to_send",
        )
        for index in range(2)
    ]

    def interrupt(*_args, **_kwargs):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(meeting_alignment, "_deliver_meeting_job", interrupt)
    with pytest.raises(sqlite3.OperationalError, match="database is locked"):
        deliver_ready_meeting_alignment_jobs(store, object(), now=NOW, limit=2)

    assert [store.get_meeting_alignment_job(i).locked_at for i in ids] == [None, None]
    assert [j.id for j in store.claim_ready_to_send_meeting_alignment_jobs(
        limit=2, now=NOW.isoformat()
    )] == ids


def test_delivery_cleanup_cannot_release_a_successor_claim(tmp_path, monkeypatch):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    job_id = store.upsert_meeting_alignment_job(
        meeting_id="minutes-1", title="Meeting", source_json="{}",
        participants_json="[]", ended_at=NOW.isoformat(),
        eligible_at=NOW.isoformat(), status="ready_to_send",
    )

    def successor_claim(*_args, **_kwargs):
        _set_meeting_job_fields(store, job_id, locked_at="2099-01-01 00:00:00")

    monkeypatch.setattr(meeting_alignment, "_deliver_meeting_job", successor_claim)
    deliver_ready_meeting_alignment_jobs(store, object(), now=NOW, limit=1)
    assert store.get_meeting_alignment_job(job_id).locked_at == "2099-01-01 00:00:00"


def test_delivery_cleanup_cannot_release_same_timestamp_successor(tmp_path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    job_id = store.upsert_meeting_alignment_job(
        meeting_id="minutes-1", title="Meeting", source_json="{}",
        participants_json="[]", ended_at=NOW.isoformat(),
        eligible_at=NOW.isoformat(), status="ready_to_send",
    )
    first = store.claim_ready_to_send_meeting_alignment_job(job_id, now=NOW.isoformat())
    store.reset_ready_to_send_meeting_alignment_jobs()
    second = store.claim_ready_to_send_meeting_alignment_job(job_id, now=NOW.isoformat())
    # Model claims on opposite sides of a one-second SQLite timestamp boundary.
    later_lock = (datetime.fromisoformat(first.locked_at) + timedelta(seconds=1)).strftime(
        "%Y-%m-%d %H:%M:%S"
    )
    second = second.model_copy(update={"locked_at": later_lock})
    assert first.locked_at != second.locked_at
    _set_meeting_job_fields(store, job_id, locked_at=later_lock)
    assert store.get_meeting_alignment_job(job_id).locked_at == second.locked_at
    _set_meeting_job_fields(store, job_id, locked_at=first.locked_at)
    # The release requires the successor's token and its observed row lock time.
    # Match the DTO to the row after deliberately making their timestamps equal.
    second = second.model_copy(update={"locked_at": first.locked_at})
    store.release_ready_to_send_meeting_alignment_claims([first])
    remaining = store.get_meeting_alignment_job(job_id)
    assert remaining.locked_at == first.locked_at
    with store._connect() as db:
        assert db.execute(
            f"select claim_token from ({task_delivery_projection('meeting_alignment_delivery_claims')}) where job_id=?",
            (job_id,),
        ).fetchone()[0] == second.delivery_claim_token
    assert first.delivery_claim_token != second.delivery_claim_token
    store.release_ready_to_send_meeting_alignment_claims([second])
    assert store.get_meeting_alignment_job(job_id).locked_at is None


def test_delivery_and_cleanup_failure_preserve_both_errors(tmp_path, monkeypatch):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    store.upsert_meeting_alignment_job(
        meeting_id="minutes-1", title="Meeting", source_json="{}",
        participants_json="[]", ended_at=NOW.isoformat(),
        eligible_at=NOW.isoformat(), status="ready_to_send",
    )

    def delivery_failure(*_args, **_kwargs):
        raise ValueError("original delivery failure")

    def cleanup_failure(*_args, **_kwargs):
        raise sqlite3.OperationalError("claim cleanup database is locked")

    monkeypatch.setattr(meeting_alignment, "_deliver_meeting_job", delivery_failure)
    monkeypatch.setattr(store, "release_ready_to_send_meeting_alignment_claims", cleanup_failure)
    with pytest.raises(ExceptionGroup) as raised:
        deliver_ready_meeting_alignment_jobs(store, object(), now=NOW, limit=1)
    assert "original delivery failure" in str(raised.value)
    assert "claim cleanup database is locked" in str(raised.value)
    assert [type(e) for e in raised.value.exceptions] == [ValueError, sqlite3.OperationalError]


def test_successful_delivery_cleanup_failure_propagates(tmp_path, monkeypatch):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")

    def cleanup_failure(*_args, **_kwargs):
        raise sqlite3.OperationalError("claim cleanup database is locked")

    monkeypatch.setattr(store, "release_ready_to_send_meeting_alignment_claims", cleanup_failure)
    with pytest.raises(sqlite3.OperationalError, match="claim cleanup database is locked"):
        deliver_ready_meeting_alignment_jobs(store, object(), now=NOW, limit=1)


def test_typed_delivery_claim_after_reopen_preserves_existing_receipt(tmp_path):
    path = tmp_path / "worker.sqlite3"
    store = AutoReplyStore(path)
    job_id = store.upsert_meeting_alignment_job(
        meeting_id="minutes-1", title="Meeting", source_json="{}",
        participants_json="[]", ended_at=NOW.isoformat(),
        eligible_at=NOW.isoformat(), status="ready_to_send",
    )
    store.update_meeting_alignment_job(job_id, send_result_json='{"status":"sent"}')
    copy_path = tmp_path / "reopened.sqlite3"
    with sqlite3.connect(path) as source, sqlite3.connect(copy_path) as target:
        source.backup(target)
    reopened = AutoReplyStore(copy_path)
    job = reopened.get_meeting_alignment_job(job_id)
    assert job.send_result_json == '{"status":"sent"}'
    assert job.delivery_claim_token == ""
    with reopened._connect() as db:
        assert db.execute(
            "select 1 from sqlite_master where type='table' "
            "and name='meeting_alignment_delivery_claims'"
        ).fetchone() is None
    claimed = reopened.claim_ready_to_send_meeting_alignment_job(job_id, now=NOW.isoformat())
    assert claimed.delivery_claim_token
    assert claimed.send_result_json == job.send_result_json
    reopened.release_ready_to_send_meeting_alignment_claims([claimed])
    assert reopened.get_meeting_alignment_job(job_id).locked_at is None


@pytest.mark.parametrize("entry", ["single", "batch_first", "batch_second"])
def test_delivery_claim_insert_failure_rolls_back_job_lock(tmp_path, entry):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    ids = [store.upsert_meeting_alignment_job(
        meeting_id=f"minutes-{index}", title="Meeting", source_json="{}",
        participants_json="[]", ended_at=NOW.isoformat(),
        eligible_at=NOW.isoformat(), status="ready_to_send",
    ) for index in range(2)]
    failure_id = ids[1] if entry == "batch_second" else ids[0]
    with store._connect() as db:
        db.execute(f"""create trigger reject_claim before insert on actions
                      when new.record_type='meeting_alignment_delivery_claims'
                        and new.td_claim_job_id={failure_id} begin
                      select raise(abort, 'claim insert failed'); end""")
    with pytest.raises(sqlite3.IntegrityError, match="claim insert failed"):
        if entry != "single":
            store.claim_ready_to_send_meeting_alignment_jobs(limit=2, now=NOW.isoformat())
        else:
            store.claim_ready_to_send_meeting_alignment_job(ids[0], now=NOW.isoformat())
    assert [store.get_meeting_alignment_job(job_id).locked_at for job_id in ids] == [None, None]
    with store._connect() as db:
        assert db.execute(
            "select count(*) from actions where record_type='meeting_alignment_delivery_claims'"
        ).fetchone()[0] == 0


@pytest.mark.parametrize("queue", ["empty", "settled", "future", "locked"])
def test_idle_meeting_delivery_poll_does_not_wait_for_writer(tmp_path, monkeypatch, queue):
    import app.store as store_module

    store = AutoReplyStore(tmp_path / "idle-meeting.sqlite3")
    if queue != "empty":
        job_id = store.upsert_meeting_alignment_job(
            meeting_id="idle-meeting", title="Meeting", source_json="{}",
            participants_json="[]", ended_at=NOW.isoformat(),
            eligible_at=NOW.isoformat(),
            status="sent" if queue == "settled" else "ready_to_send",
        )
        if queue == "future":
            store.update_meeting_alignment_job(
                job_id, available_at=(NOW + timedelta(hours=1)).isoformat(),
            )
        elif queue == "locked":
            store.update_meeting_alignment_job(job_id, locked_at=NOW.isoformat())
    original_connect = store._connect

    @contextmanager
    def no_wait_connection():
        with original_connect() as database:
            database.execute("pragma busy_timeout=0")
            yield database

    monkeypatch.setattr(store, "_connect", no_wait_connection)
    monkeypatch.setattr(store_module, "STORE_WRITE_LOCK_RETRY_ATTEMPTS", 1)
    writer = sqlite3.connect(store.path, timeout=0)
    try:
        writer.execute("begin immediate")
        assert store.claim_ready_to_send_meeting_alignment_jobs(
            limit=2, now=NOW.isoformat(),
        ) == []
    finally:
        writer.rollback()
        writer.close()


def test_meeting_delivery_probe_cannot_overwrite_competing_claim(tmp_path, monkeypatch):
    store = AutoReplyStore(tmp_path / "competing-meeting.sqlite3")
    other = AutoReplyStore(store.path)
    job_id = store.upsert_meeting_alignment_job(
        meeting_id="competing-meeting", title="Meeting", source_json="{}",
        participants_json="[]", ended_at=NOW.isoformat(),
        eligible_at=NOW.isoformat(), status="ready_to_send",
    )
    transaction = store._immediate_write_transaction
    competing_claims = []

    @contextmanager
    def claim_before_transaction():
        competing_claims.append(other.claim_ready_to_send_meeting_alignment_job(
            job_id, now=NOW.isoformat(),
        ))
        with transaction() as database:
            yield database

    monkeypatch.setattr(store, "_immediate_write_transaction", claim_before_transaction)
    assert store.claim_ready_to_send_meeting_alignment_jobs(limit=2, now=NOW.isoformat()) == []
    assert len(competing_claims) == 1
    assert competing_claims[0] is not None
    with store._connect() as database:
        token = database.execute(
            f"select claim_token from ({task_delivery_projection('meeting_alignment_delivery_claims')}) where job_id=?",
            (job_id,),
        ).fetchone()[0]
    assert token == competing_claims[0].delivery_claim_token


def test_delivery_claim_delete_failure_rolls_back_release(tmp_path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    job_id = store.upsert_meeting_alignment_job(
        meeting_id="minutes-1", title="Meeting", source_json="{}",
        participants_json="[]", ended_at=NOW.isoformat(),
        eligible_at=NOW.isoformat(), status="ready_to_send",
    )
    claim = store.claim_ready_to_send_meeting_alignment_job(job_id, now=NOW.isoformat())
    with store._connect() as db:
        db.execute("""create trigger reject_release before delete on actions
                      when old.record_type='meeting_alignment_delivery_claims' begin
                      select raise(abort, 'claim delete failed'); end""")
    with pytest.raises(sqlite3.IntegrityError, match="claim delete failed"):
        store.release_ready_to_send_meeting_alignment_claims([claim])
    assert store.get_meeting_alignment_job(job_id).locked_at == claim.locked_at
    with store._connect() as db:
        assert db.execute(
            f"select claim_token from ({task_delivery_projection('meeting_alignment_delivery_claims')}) where job_id=?",
            (job_id,),
        ).fetchone()[0] == claim.delivery_claim_token


def test_startup_reset_removes_interrupted_and_terminal_delivery_claims(tmp_path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    ids = [store.upsert_meeting_alignment_job(
        meeting_id=f"minutes-{index}", title="Meeting", source_json="{}",
        participants_json="[]", ended_at=NOW.isoformat(),
        eligible_at=NOW.isoformat(), status="ready_to_send",
    ) for index in range(2)]
    store.claim_ready_to_send_meeting_alignment_jobs(limit=2, now=NOW.isoformat())
    store.update_meeting_alignment_job(ids[1], status="sent", send_result_json='{"status":"sent"}')
    terminal = store.get_meeting_alignment_job(ids[1])
    assert [job.id for job in store.reset_ready_to_send_meeting_alignment_jobs()] == [ids[0]]
    assert store.get_meeting_alignment_job(ids[0]).locked_at is None
    assert store.get_meeting_alignment_job(ids[1]) == terminal
    with store._connect() as db:
        assert db.execute(
            "select count(*) from actions where record_type='meeting_alignment_delivery_claims'"
        ).fetchone()[0] == 0


def test_delivery_cleanup_leaves_terminal_rows_unchanged(tmp_path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    job_id = store.upsert_meeting_alignment_job(
        meeting_id="minutes-1", title="Meeting", source_json="{}",
        participants_json="[]", ended_at=NOW.isoformat(),
        eligible_at=NOW.isoformat(), status="ready_to_send",
    )
    claim = store.claim_ready_to_send_meeting_alignment_job(job_id, now=NOW.isoformat())
    store.update_meeting_alignment_job(job_id, status="sent", send_result_json='{"status":"sent"}')
    before = store.get_meeting_alignment_job(job_id)
    store.release_ready_to_send_meeting_alignment_claims([claim])
    assert store.get_meeting_alignment_job(job_id) == before


def test_disabled_feature_does_not_queue_new_meeting_replay(tmp_path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    registry = FeatureRegistry(state_path=tmp_path / "skill-state.json")
    registry.set_enabled("meeting_summary", False)

    class Dws:
        def list_minutes_page(self, **_kwargs):
            raise AssertionError("disabled replay must not read meeting minutes")

    assert (
        queue_recent_meeting_alignment_replay(
            store,
            Dws(),
            now=NOW,
            limit=1,
            feature_registry=registry,
        )
        == []
    )
    assert store.get_meeting_alignment_job_by_meeting_id("minutes-1") is None


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
    organizer: str = "Derek",
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
        organizer=organizer,
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
        self.profiles: dict[str, list[DwsUserProfile]] = {}

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

    def get_all_minutes_transcription(self, meeting_id: str) -> dict:
        raise DwsError("transcript roster unavailable")

    def search_user_profiles(self, query: str) -> list[DwsUserProfile]:
        return list(self.profiles.get(query, []))

    def search_conversations(self, query: str) -> list[DingTalkConversation]:
        return []

    def is_hr_user(self, user_id: str) -> bool:
        return False

    def list_calendar_events_page(
        self, *, start: str, end: str, limit: int, cursor: str
    ) -> dict:
        assert start
        assert end
        assert limit == 50
        self.calendar_calls.append(cursor)
        return self.calendar_pages[cursor]


def test_producer_hydrates_missing_calendar_attendee_identity(tmp_path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    event = matching_calendar_event()
    event.attendee_details[1] = DwsCalendarAttendee(display_name="A")
    dws = FakeDws(
        calendar_pages={
            "": {"events": [event], "has_more": False, "next_cursor": ""}
        }
    )
    dws.profiles["A"] = [
        DwsUserProfile(user_id="u-a", name="A", open_dingtalk_id="open-a")
    ]

    assert produce_meeting_alignment_jobs(store, dws, now=NOW) == 1

    job = store.get_meeting_alignment_job_by_meeting_id("minutes-1")
    assert job is not None
    participants = json.loads(job.source_json)["calendar_evidence"]["participants"]
    assert participants[1] == {
        "name": "A",
        "user_id": "u-a",
        "open_dingtalk_id": "open-a",
    }


def test_producer_uses_minutes_creator_to_match_overlapping_calendar_event(
    tmp_path,
):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    meeting = ended_meeting()
    meeting["flashUserInfo"] = {"name": "A", "userId": "u-a"}
    event = matching_calendar_event(title="HSW孙老师交流")
    dws = FakeDws(
        minutes_pages={"": {"items": [meeting], "has_more": False, "next_token": ""}},
        calendar_pages={"": {"events": [event], "has_more": False, "next_cursor": ""}},
        info={"minutes-1": ended_meeting()},
    )

    assert produce_meeting_alignment_jobs(store, dws, now=NOW) == 1

    job = store.get_meeting_alignment_job_by_meeting_id("minutes-1")
    assert job is not None
    assert job.status == "pending"
    evidence = json.loads(job.source_json)["calendar_evidence"]
    assert evidence["event_id"] == "event-1"
    assert {participant["user_id"] for participant in evidence["participants"]} == {
        "u-derek",
        "u-a",
    }


def _fake_resolve_sent_message_reference(self, open_task_id: str) -> dict:
    """Answer where a send landed, the way the provider's status call does."""
    self.__dict__.setdefault("resolved", []).append(open_task_id)
    return {
        "openConversationId": f"cid-for-{open_task_id}",
        "openMessageId": f"msg-for-{open_task_id}",
    }


FakeDws.resolve_sent_message_reference = _fake_resolve_sent_message_reference


def _fake_recall_sent_message(self, open_task_id: str) -> bool:
    """Record what the service asked to withdraw, and say the provider took it."""
    self.__dict__.setdefault("recalled", []).append(open_task_id)
    return True


FakeDws.recall_sent_message = _fake_recall_sent_message


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
        # What DingTalk actually answers a send with: a task handle and nothing
        # else. The conversation and message ids have to be asked for separately,
        # which is why the stored receipt used to carry neither.
        self.send_result = {
            "success": True,
            "result": {"openTaskId": "task-1"},
        }
        self.verification_states = ["sent"]
        self.calendar_update_calls: list[dict] = []

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

    def list_group_member_open_dingtalk_ids(
        self, conversation_id: str
    ) -> set[str]:
        assert conversation_id == "cid-first"
        return {"open-derek", "open-a", "open-b"}

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

    def get_calendar_event(self, event_id: str) -> DwsCalendarEvent | None:
        event = self.calendar_pages[""]["events"][0]
        return event if event.event_id == event_id else None

    def update_calendar_event_description(
        self, event_id: str, description: str
    ) -> dict:
        event = self.get_calendar_event(event_id)
        assert event is not None
        event.description = description
        self.calendar_update_calls.append(
            {"event_id": event_id, "description": description}
        )
        return {"success": True, "result": {"eventId": event_id}}


class FakeMeetingRunner:
    last_session_id = "meeting-session-1"
    last_transcript_start_line = 4
    last_transcript_end_line = 19
    last_audit_tool_events = [{"tool": "dws", "command": "group search"}]

    def __init__(self, decision: MeetingAlignmentDecision):
        self.decision = decision
        self.calls = 0
        self.prompts: list[str] = []
        self.compact_prompts: list[str | None] = []

    def decide(self, *, prompt: str, run_id=None, compact_prompt=None) -> MeetingAlignmentDecision:
        self.calls += 1
        self.prompts.append(prompt)
        self.compact_prompts.append(compact_prompt)
        return self.decision

    def audit(self, *, prompt: str, run_id: int):
        self.audit_prompt = prompt
        self.audit_run_id = run_id
        return MeetingAlignmentAuditResult(
            outcome="approve",
            summary="exact candidate and recipient supported by the supplied evidence",
            feedback=None,
        )


def summary_decision() -> MeetingAlignmentDecision:
    return MeetingAlignmentDecision.model_validate(
            {
                "action": "send",
                "audience_scope": "business",
            "trigger_reasons": ["meeting_summary"],
            "topics": [],
            "derek_viewpoint": None,
            "key_questions": [],
            "mention_names": [],
            "target": {
                "kind": "group",
                "conversation_id": "cid-summary",
                "direct_user_id": "",
                "title": "业务群",
                "candidates": [
                    {
                        "conversation_id": "cid-summary",
                        "title": "业务群",
                        "evidence": ["承接本次会议主题"],
                    }
                ],
            },
            "final_message": "会议总结｜已确认事项与下一步请以本次会议结论执行。",
            "audit_summary": "会议没有实质分歧，仍发送简短总结。",
            "confidence": 0.9,
        }
    )


def consumer_send_decision() -> MeetingAlignmentDecision:
    return MeetingAlignmentDecision.model_validate(
            {
                "action": "send",
                "message_title": "上线评审",
                "audience_scope": "business",
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


def audited_decision_json(decision: MeetingAlignmentDecision) -> str:
    return persist_audited_meeting_decision(
        decision,
        MeetingAlignmentAuditResult(
            outcome="approve",
            summary="exact candidate approved for this test fixture",
            feedback=None,
        ),
    )


def seed_consumer_job(store: AutoReplyStore, dws: ConsumerDws) -> int:
    assert produce_meeting_alignment_jobs(store, dws, now=NOW) == 1
    job = store.get_meeting_alignment_job_by_meeting_id("minutes-1")
    assert job is not None
    return job.id


def test_producer_skips_no_roster_source_without_creating_needs_human(tmp_path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    dws = FakeDws(
        calendar_pages={
            "": {"events": [], "has_more": False, "next_cursor": ""}
        }
    )

    assert produce_meeting_alignment_jobs(store, dws, now=NOW) == 1
    job = store.get_meeting_alignment_job_by_meeting_id("minutes-1")
    assert job is not None
    assert job.status == "skipped"
    assert json.loads(job.error) == {
        "kind": "meeting_roster",
        "message": (
            "calendar roster unavailable and transcript roster unavailable: "
            "transcript roster unavailable"
        ),
    }
    assert store.claim_meeting_alignment_jobs(limit=1, now=NOW.isoformat()) == []
    assert produce_meeting_alignment_jobs(store, dws, now=NOW) == 0


def test_replay_reports_transcript_source_failure_without_calendar_label(tmp_path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    dws = FakeDws(
        calendar_pages={
            "": {"events": [], "has_more": False, "next_cursor": ""}
        }
    )

    results = meeting_alignment.queue_recent_meeting_alignment_replay(
        store,
        dws,
        now=NOW,
        limit=50,
    )

    assert results[0]["outcome"] == "source_unavailable"
    assert "transcript roster unavailable" in results[0]["error"]


def test_exact_replay_refreshes_needs_human_source_before_ordinary_audit(tmp_path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    meeting = ended_meeting()
    meeting["flashUserInfo"] = {"name": "A", "userId": "u-a"}
    dws = FakeDws(
        minutes_pages={"": {"items": [meeting], "has_more": False, "next_token": ""}},
        calendar_pages={
            "": {
                "events": [matching_calendar_event(title="HSW孙老师交流")],
                "has_more": False,
                "next_cursor": "",
            }
        },
    )
    job_id = store.upsert_meeting_alignment_job(
        meeting_id="minutes-1",
        title="HSW主观任务评估范式与成本优化",
        source_json='{"old_source":true}',
        participants_json="[]",
        ended_at=meeting["endTimeISO"],
        eligible_at=NOW.isoformat(),
        status="needs_human",
    )

    results = queue_recent_meeting_alignment_replay(
        store,
        dws,
        now=NOW,
        limit=1,
        meeting_id="minutes-1",
    )

    assert results[0]["outcome"] == "queued"
    assert results[0]["job_id"] == job_id
    replayed = store.get_meeting_alignment_job(job_id)
    assert replayed.status == "pending"
    assert json.loads(replayed.source_json)["calendar_evidence"]["source"] == "calendar"
    assert {
        participant["user_id"]
        for participant in json.loads(replayed.source_json)["calendar_evidence"]["participants"]
    } == {"u-derek", "u-a"}


def test_producer_skips_ambiguous_calendar_matches_without_creating_needs_human(tmp_path):
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

    assert produce_meeting_alignment_jobs(store, dws, now=NOW) == 1
    job = store.get_meeting_alignment_job_by_meeting_id("minutes-1")
    assert job is not None
    assert job.status == "skipped"


def test_producer_queues_ad_hoc_one_to_one_from_two_speaker_transcript(
    tmp_path, monkeypatch
):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")

    class AdHocOneToOneDws(FakeDws):
        def get_all_minutes_transcription(self, meeting_id: str) -> dict:
            assert meeting_id == "minutes-1"
            return {
                "paragraphs": [
                    {"nickName": "Derek", "paragraph": "先确认投入产出。"},
                    {"nickName": "Casey", "paragraph": "我补齐调研。"},
                ]
            }

        def search_user_profiles(self, query: str) -> list[DwsUserProfile]:
            profiles = {
                "Derek": DwsUserProfile(
                    user_id="u-derek",
                    name="Derek",
                    open_dingtalk_id="open-derek",
                ),
                "Casey": DwsUserProfile(
                    user_id="u-claire",
                    name="Casey",
                    open_dingtalk_id="open-claire",
                ),
            }
            return [profiles[query]]

    dws = AdHocOneToOneDws(
        calendar_pages={
            "": {"events": [], "has_more": False, "next_cursor": ""}
        }
    )
    monkeypatch.setattr(meeting_alignment, "principal_display_name", lambda: "Derek")

    assert produce_meeting_alignment_jobs(store, dws, now=NOW) == 1

    job = store.get_meeting_alignment_job_by_meeting_id("minutes-1")
    assert job is not None
    evidence = json.loads(job.source_json)["calendar_evidence"]
    assert evidence["source"] == "transcript"
    assert evidence["event_id"] == "transcript:minutes-1"
    assert evidence["participants"] == [
        {
                "name": "Derek",
                "user_id": "u-derek",
                "open_dingtalk_id": "open-derek",
        },
        {
            "name": "Casey",
            "user_id": "u-claire",
            "open_dingtalk_id": "open-claire",
        },
    ]


def test_producer_queues_transcript_roster_for_multi_speaker_recording(
    tmp_path, monkeypatch
):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")

    class TranscriptRosterDws(FakeDws):
        def get_all_minutes_transcription(self, meeting_id: str) -> dict:
            assert meeting_id == "minutes-1"
            return {
                "paragraphs": [
                    {"nickName": "Derek", "paragraph": "先确认范围。"},
                    {"nickName": "Casey", "paragraph": "我补齐调研。"},
                    {"nickName": "外部专家", "paragraph": "我补充案例。"},
                ]
            }

        def search_user_profiles(self, query: str) -> list[DwsUserProfile]:
            profiles = {
                "Derek": DwsUserProfile(user_id="u-derek", name="Derek"),
                "Casey": DwsUserProfile(user_id="u-claire", name="Casey"),
            }
            return [profiles[query]] if query in profiles else []

    dws = TranscriptRosterDws(
        calendar_pages={
            "": {"events": [], "has_more": False, "next_cursor": ""}
        }
    )
    monkeypatch.setattr(meeting_alignment, "principal_display_name", lambda: "Derek")

    assert produce_meeting_alignment_jobs(store, dws, now=NOW) == 1
    job = store.get_meeting_alignment_job_by_meeting_id("minutes-1")
    assert job is not None
    assert job.status == "pending"
    assert json.loads(job.source_json)["calendar_evidence"]["participants"] == [
        {"name": "Derek", "user_id": "u-derek", "open_dingtalk_id": ""},
        {"name": "Casey", "user_id": "u-claire", "open_dingtalk_id": ""},
        {"name": "外部专家", "user_id": "", "open_dingtalk_id": ""},
    ]


def test_transcript_roster_includes_resolved_minutes_creator_even_when_not_speaking(
    tmp_path, monkeypatch
):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    minutes_item = {**ended_meeting(), "flashUserInfo": {"name": "胡明"}}

    class CreatorRosterDws(FakeDws):
        def get_all_minutes_transcription(self, meeting_id: str) -> dict:
            assert meeting_id == "minutes-1"
            return {
                "paragraphs": [
                    {"nickName": "Derek", "paragraph": "先确认范围。"},
                    {"nickName": "Casey", "paragraph": "我补齐调研。"},
                    {"nickName": "外部专家", "paragraph": "我补充案例。"},
                ]
            }

        def search_user_profiles(self, query: str) -> list[DwsUserProfile]:
            profiles = {
                "Derek": DwsUserProfile(user_id="u-derek", name="Derek"),
                "Casey": DwsUserProfile(user_id="u-claire", name="Casey"),
                "胡明": DwsUserProfile(user_id="u-hu-ming", name="胡明"),
            }
            return [profiles[query]] if query in profiles else []

    dws = CreatorRosterDws(
        minutes_pages={
            "": {"items": [minutes_item], "has_more": False, "next_token": ""}
        },
        calendar_pages={
            "": {"events": [], "has_more": False, "next_cursor": ""}
        },
    )
    monkeypatch.setattr(meeting_alignment, "principal_display_name", lambda: "Derek")

    assert produce_meeting_alignment_jobs(store, dws, now=NOW) == 1

    job = store.get_meeting_alignment_job_by_meeting_id("minutes-1")
    assert job is not None
    evidence = json.loads(job.source_json)["calendar_evidence"]
    assert evidence["source"] == "transcript"
    assert {participant["user_id"] for participant in evidence["participants"]} == {
        "u-derek",
        "u-claire",
        "u-hu-ming",
        "",
    }


def test_producer_skips_missing_self_attendee_without_creating_needs_human(tmp_path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    event = matching_calendar_event()
    event.attendee_details[0].is_self = False
    dws = FakeDws(
        calendar_pages={
            "": {"events": [event], "has_more": False, "next_cursor": ""}
        }
    )

    assert produce_meeting_alignment_jobs(store, dws, now=NOW) == 1
    job = store.get_meeting_alignment_job_by_meeting_id("minutes-1")
    assert job is not None
    assert job.status == "skipped"


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


def test_producer_defaults_to_one_week_discovery_window(tmp_path):
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


def test_producer_skips_recording_shorter_than_five_minutes(tmp_path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    meeting = ended_meeting(end="2026-07-14T09:04:59.999000+08:00")
    dws = FakeDws(
        minutes_pages={
            "": {"items": [meeting], "has_more": False, "next_token": ""}
        },
        info={"minutes-1": meeting},
    )

    assert produce_meeting_alignment_jobs(store, dws, now=NOW) == 1
    job = store.get_meeting_alignment_job_by_meeting_id("minutes-1")
    assert job is not None
    assert job.status == "skipped"
    assert json.loads(job.error)["kind"] == "meeting_duration"
    assert dws.calendar_calls == []


def test_producer_keeps_recording_exactly_five_minutes(tmp_path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    meeting = ended_meeting(end="2026-07-14T09:05:00+08:00")
    dws = FakeDws(
        minutes_pages={
            "": {"items": [meeting], "has_more": False, "next_token": ""}
        },
        info={"minutes-1": meeting},
    )

    assert produce_meeting_alignment_jobs(store, dws, now=NOW) == 1
    assert store.get_meeting_alignment_job_by_meeting_id("minutes-1") is not None
    assert dws.calendar_calls == [""]


@pytest.mark.parametrize("provider_status", ["ended", 2, 4])
def test_replay_requeues_recent_failed_unsent_meeting_and_refreshes_source(tmp_path, provider_status):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    short = ended_meeting(
        meeting_id="minutes-short",
        title="短会",
        end="2026-07-14T09:04:59+08:00",
    )
    eligible = ended_meeting(meeting_id="minutes-replay")
    eligible["status"] = provider_status

    class ReplayDws(FakeDws):
        def list_minutes_page(
            self, *, limit: int, cursor: str, start: str, end: str
        ) -> dict:
            self.minutes_calls.append(
                {"limit": limit, "cursor": cursor, "start": start, "end": end}
            )
            return self.minutes_pages[cursor]

    dws = ReplayDws(
        minutes_pages={
            "": {
                "items": [short, eligible],
                "has_more": True,
                "next_token": "not-read",
            }
        },
        info={"minutes-short": short, "minutes-replay": eligible},
    )
    old_id = store.upsert_meeting_alignment_job(
        meeting_id="minutes-replay",
        title="上线评审",
        source_json="{}",
        participants_json="[]",
        ended_at=eligible["endTimeISO"],
        eligible_at=NOW.isoformat(),
        status="pending",
    )
    store.update_meeting_alignment_job(
        old_id,
        status="failed",
        error='{"kind":"meeting_target"}',
        decision_json='{"action":"send"}',
    )

    results = queue_recent_meeting_alignment_replay(
        store,
        dws,
        now=NOW,
        limit=2,
    )

    assert [result["outcome"] for result in results] == [
        "short_recording",
        "queued",
    ]
    assert dws.minutes_calls == [
        {
            "limit": 2,
            "cursor": "",
            "start": "2026-07-07T10:10:00+08:00",
            "end": "2026-07-14T10:10:00+08:00",
        }
    ]
    replayed = store.get_meeting_alignment_job(old_id)
    assert replayed.status == "pending"
    assert replayed.error == ""
    assert replayed.decision_json == "{}"
    assert json.loads(replayed.source_json)["calendar_evidence"]["creator"][
        "name"
    ] == "Derek"
    assert results[1]["job_id"] == old_id


@pytest.mark.parametrize("status", ["failed", "needs_human"])
def test_replay_keeps_prepared_delivery_on_recovery_path(tmp_path, status):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    meeting = ended_meeting(meeting_id="minutes-prepared")
    dws = FakeDws(
        minutes_pages={"": {"items": [meeting], "has_more": False, "next_token": ""}},
        info={meeting["taskUuid"]: meeting},
    )
    job_id = store.upsert_meeting_alignment_job(
        meeting_id=meeting["taskUuid"],
        title="Original title",
        source_json="{\"original\":true}",
        participants_json="[]",
        ended_at=meeting["endTimeISO"],
        eligible_at=NOW.isoformat(),
        status=status,
    )
    delivery_key = f"meeting-alignment:{job_id}:{meeting['taskUuid']}"
    store.prepare_meeting_alignment_message(
        delivery_key=delivery_key,
        body="Prepared message",
        original_text="Meeting source",
        target_kind="group",
        target_id="cid-meeting",
        feedback_base_url="",
    )

    results = queue_recent_meeting_alignment_replay(
        store,
        dws,
        now=NOW,
        limit=50,
        meeting_id=meeting["taskUuid"] if status == "needs_human" else None,
    )

    assert results[0]["outcome"] == status
    assert results[0]["job_id"] == job_id
    replayed = store.get_meeting_alignment_job(job_id)
    assert replayed.status == status
    assert replayed.title == "Original title"
    assert replayed.source_json == "{\"original\":true}"
    assert store.get_outbound_postfix("dingtalk", delivery_key).final_body.startswith(
        "Prepared message"
    )


def test_replay_keeps_failed_job_with_target_scoped_prepared_delivery(tmp_path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    meeting = ended_meeting(meeting_id="minutes-scoped-prepared")
    dws = FakeDws(
        minutes_pages={"": {"items": [meeting], "has_more": False, "next_token": ""}},
        info={meeting["taskUuid"]: meeting},
    )
    job_id = store.upsert_meeting_alignment_job(
        meeting_id=meeting["taskUuid"],
        title="Original title",
        source_json="{\"original\":true}",
        participants_json="[]",
        ended_at=meeting["endTimeISO"],
        eligible_at=NOW.isoformat(),
        status="failed",
    )
    legacy_key = f"meeting-alignment:{job_id}:{meeting['taskUuid']}"
    delivery_key = store.resolve_meeting_alignment_delivery_key(
        legacy_key, target_kind="group", target_id="cid-meeting"
    )
    store.prepare_meeting_alignment_message(
        delivery_key=delivery_key,
        body="Prepared message",
        original_text="Meeting source",
        target_kind="group",
        target_id="cid-meeting",
        feedback_base_url="",
    )

    results = queue_recent_meeting_alignment_replay(store, dws, now=NOW, limit=50)

    assert results[0]["outcome"] == "failed"
    assert results[0]["job_id"] == job_id
    assert store.get_meeting_alignment_job(job_id).status == "failed"
    assert store.get_outbound_postfix("dingtalk", delivery_key).final_body.startswith(
        "Prepared message"
    )


def test_meeting_delivery_identity_is_scoped_to_immutable_recipient(tmp_path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    legacy_key = "meeting-alignment:416946:minutes-1"
    old_group = store.prepare_meeting_alignment_message(
        delivery_key=legacy_key,
        body="Existing group message",
        original_text="Meeting source",
        target_kind="group",
        target_id="group-cid",
        feedback_base_url="",
    )

    group_key = store.resolve_meeting_alignment_delivery_key(
        legacy_key, target_kind="group", target_id="group-cid"
    )
    direct_key = store.resolve_meeting_alignment_delivery_key(
        legacy_key, target_kind="user", target_id="user-id"
    )

    assert group_key == legacy_key
    assert direct_key != legacy_key
    direct = store.prepare_meeting_alignment_message(
        delivery_key=direct_key,
        body="New direct message",
        original_text="Meeting source",
        target_kind="user",
        target_id="user-id",
        feedback_base_url="",
    )
    assert direct.delivery_key == direct_key
    assert store.get_outbound_postfix("dingtalk", legacy_key).final_body == old_group.final_body
    assert store.get_outbound_postfix("dingtalk", direct_key).final_body == direct.final_body
    assert store.resolve_meeting_alignment_delivery_key(
        legacy_key, target_kind="user", target_id="user-id"
    ) == direct_key


def test_replay_item_failure_does_not_block_later_meeting(tmp_path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    first = ended_meeting(meeting_id="minutes-fails", title="失败会议")
    second = ended_meeting(meeting_id="minutes-later")

    class ReplayDws(FakeDws):
        def __init__(self):
            super().__init__(
                minutes_pages={
                    "": {
                        "items": [first, second],
                        "has_more": False,
                        "next_token": "",
                    }
                },
                info={"minutes-fails": first, "minutes-later": second},
            )
            self.calendar_attempts = 0

        def list_minutes_page(
            self, *, limit: int, cursor: str, start: str, end: str
        ) -> dict:
            return self.minutes_pages[cursor]

        def list_calendar_events_page(
            self, *, start: str, end: str, limit: int, cursor: str
        ) -> dict:
            self.calendar_attempts += 1
            if self.calendar_attempts == 1:
                raise DwsError("calendar temporarily unavailable")
            return self.calendar_pages[cursor]

    results = queue_recent_meeting_alignment_replay(
        store,
        ReplayDws(),
        now=NOW,
        limit=2,
    )

    assert [result["outcome"] for result in results] == ["failed", "queued"]
    assert "calendar temporarily unavailable" in results[0]["error"]
    assert results[1]["job_id"] is not None


def test_replay_offset_selects_nonoverlapping_recent_window(tmp_path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    items = [
        ended_meeting(
            meeting_id=f"minutes-{index}",
            title=f"短会 {index}",
            end="2026-07-14T09:05:00+08:00",
        )
        for index in range(3)
    ]

    class ReplayDws(FakeDws):
        def __init__(self):
            super().__init__(
                minutes_pages={
                    "": {
                        "items": items,
                        "has_more": False,
                        "next_token": "",
                    }
                },
                info={item["taskUuid"]: item for item in items},
            )

        def list_minutes_page(
            self, *, limit: int, cursor: str, start: str, end: str
        ) -> dict:
            self.minutes_calls.append({"limit": limit})
            return self.minutes_pages[cursor]

    dws = ReplayDws()
    results = queue_recent_meeting_alignment_replay(
        store,
        dws,
        now=NOW,
        limit=2,
        offset=1,
    )

    assert [result["meeting_id"] for result in results] == [
        "minutes-1",
        "minutes-2",
    ]
    assert dws.minutes_calls == [{"limit": 3}]


def test_consumer_delivers_summary_when_there_is_no_disagreement(tmp_path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    dws = ConsumerDws()
    job_id = seed_consumer_job(store, dws)
    runner = FakeMeetingRunner(summary_decision())

    assert consume_meeting_alignment_jobs(
        store, dws, runner, now=NOW, limit=1
    ) == 1

    job = store.get_meeting_alignment_job(job_id)
    assert job.status == "sent"
    assert job.locked_at is None
    [run] = store.list_meeting_alignment_runs(job_id)
    assert run.status == "ready_to_send"
    assert run.codex_session_id == "meeting-session-1"
    assert run.codex_transcript_start_line == 4
    assert run.codex_transcript_end_line == 19
    assert run.audit_tool_events_json == "[]"
    assert run.native_available is False
    with store._connect() as db:
        columns = {column[0] for column in db.execute(
            f"select * from ({MEETING_LOGICAL_RUN_SQL}) where id=?", (run.id,)
        ).description}
    assert not {"decision_json", "audit_summary", "audit_tool_events_json"} & columns
    assert store.get_meeting_alignment_job(job_id).decision_json != "{}"


def test_meeting_audit_return_blocks_delivery_and_persists_exact_candidate(tmp_path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    dws = ConsumerDws()
    job_id = seed_consumer_job(store, dws)

    class ReturningAuditRunner(FakeMeetingRunner):
        def audit(self, *, prompt, run_id):
            self.audit_prompt = prompt
            return MeetingAlignmentAuditResult(
                outcome="return",
                summary="recipient disclosure evidence is insufficient",
                feedback=MeetingAlignmentAuditFeedback(
                    rule="recipient evidence must establish disclosure permission",
                    observation="the candidate does not cite authority for all group members",
                    requested_revision="use an evidenced audience or return for human review",
                ),
            )

    runner = ReturningAuditRunner(summary_decision())
    consume_meeting_alignment_jobs(store, dws, runner, now=NOW, limit=1)

    job = store.get_meeting_alignment_job(job_id)
    assert job.status == "retry"
    assert dws.send_calls == []
    assert "规划 Agent 的 audit_summary 只是候选文本" in runner.audit_prompt
    persisted = json.loads(job.decision_json)
    assert persisted["_service_audit"]["result"]["outcome"] == "return"
    assert persisted["_service_audit"]["candidate_sha256"] == meeting_alignment_decision_digest(
        summary_decision()
    )
    assert persisted["_service_audit"]["feedback_cycles"] == 1


def test_meeting_audit_dependency_outage_uses_existing_meeting_retry(tmp_path):
    from app.external_retry import ExternalDependencyError

    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    dws = ConsumerDws()
    job_id = seed_consumer_job(store, dws)

    class UnavailableAuditRunner(FakeMeetingRunner):
        def audit(self, *, prompt, run_id):
            raise ExternalDependencyError(
                "meeting audit", RuntimeError("provider temporarily unavailable"),
                dependency="codex",
            )

    consume_meeting_alignment_jobs(
        store, dws, UnavailableAuditRunner(summary_decision()), now=NOW, limit=1
    )

    job = store.get_meeting_alignment_job(job_id)
    assert job.status == "retry"
    assert json.loads(job.error)["kind"] == "meeting_audit"
    assert dws.send_calls == []
    [run] = store.list_meeting_alignment_runs(job_id)
    assert run.status == "retry"


def test_meeting_delivery_fails_closed_without_independent_audit(tmp_path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    dws = ConsumerDws()
    job_id = store.upsert_meeting_alignment_job(
        meeting_id="minutes-legacy",
        title="Meeting",
        source_json=json.dumps({"calendar_evidence": {"event_id": "event-1"}}),
        participants_json="[]",
        ended_at=NOW.isoformat(),
        eligible_at=NOW.isoformat(),
        status="ready_to_send",
    )
    decision = summary_decision()
    store.update_meeting_alignment_job(
        job_id,
        decision_json=decision.model_dump_json(),
        target_kind="group",
        target_id="cid-summary",
        target_title="业务群",
        final_message=decision.final_message,
    )

    deliver_ready_meeting_alignment_jobs(store, dws, now=NOW, limit=1)

    job = store.get_meeting_alignment_job(job_id)
    assert job.status == "failed"
    assert json.loads(job.error)["kind"] == "meeting_audit"
    assert dws.send_calls == []


def test_tool_free_meeting_planner_preserves_all_frozen_skill_requirements(tmp_path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    dws = ConsumerDws()
    context = ServiceCommandConsumerContext(
        scheduled_task_id=3,
        scheduled_task_run_id=5,
        prompt="使用 $ceo-meeting-work 处理真实会议。",
        skill_names=("ceo-meeting-work", "dingtalk-minutes", "dingtalk-calendar"),
        skill_protocol="SHORT CATALOG MUST NOT SUBSTITUTE",
        skill_materials=(
            ServiceCommandSkillMaterial(
                name="ceo-meeting-work",
                content="## Managed Skill: ceo-meeting-work\nsha256: frozen\n\nFROZEN MEETING SKILL BODY",
            ),
            ServiceCommandSkillMaterial(
                name="dingtalk-minutes",
                content="## Operation Skill: dingtalk-minutes\nsha256: frozen\n\nFROZEN MINUTES SKILL BODY",
            ),
            ServiceCommandSkillMaterial(
                name="dingtalk-calendar",
                content="## Operation Skill: dingtalk-calendar\nsha256: frozen\n\nFROZEN CALENDAR SKILL BODY",
            ),
        ),
    )
    implementations = {
        option.name: lambda: unknown_service_result("unused")
        for option in SERVICE_COMMAND_OPTIONS
    }
    implementations["scan-meetings-once"] = lambda: unknown_service_result(str(
        produce_meeting_alignment_jobs(store, dws, now=NOW)
    ))
    ServiceCommandRegistry(implementations).run(
        "scan-meetings-once", consumer_context=context
    )
    runner = FakeMeetingRunner(summary_decision())

    assert consume_meeting_alignment_jobs(store, dws, runner, now=NOW, limit=1) == 1

    [prompt] = runner.prompts
    assert "FROZEN MEETING SKILL BODY" in prompt
    assert "FROZEN MINUTES SKILL BODY" in prompt
    assert "FROZEN CALENDAR SKILL BODY" in prompt
    assert "SHORT CATALOG MUST NOT SUBSTITUTE" not in prompt
    [compact_prompt] = runner.compact_prompts
    assert compact_prompt is not None
    assert "FROZEN MEETING SKILL BODY" in compact_prompt
    assert "FROZEN MINUTES SKILL BODY" not in compact_prompt
    assert "FROZEN CALENDAR SKILL BODY" not in compact_prompt


def _seed_native_meeting_session(store, tmp_path, monkeypatch, *, session_id, title, final_message):
    """A finished Meeting Run whose transcript the similar-session search can read."""
    from app import native_trajectory
    from app.meeting_alignment_models import MeetingSource
    from tests.support.native_run_rows import insert_runtime_attempt_row

    source = MeetingSource.model_validate({
        "meeting_id": f"history-{session_id}", "title": title,
        "status": "ended", "started_at": "2026-07-14T07:00:00+08:00",
        "ended_at": "2026-07-14T08:00:00+08:00",
        "participants": [{"name": "Derek", "user_id": "derek"}],
        "attendee_evidence": "calendar", "attendee_roster_complete": True,
        "current_user_id": "derek", "summary": "会议摘要", "transcript": [],
    })
    decision = summary_decision().model_copy(update={"final_message": final_message})
    native = tmp_path / f"{session_id}.jsonl"
    native.write_text("\n".join(json.dumps({"type": "response_item", "payload": {
        "type": "message", "role": role, "content": [{"type": kind, "text": text}],
    }}, ensure_ascii=False) for role, kind, text in (
        ("user", "input_text", "完整会议来源 JSON：\n" + source.model_dump_json()),
        ("assistant", "output_text", decision.model_dump_json()),
    )) + "\n")
    monkeypatch.setattr(native_trajectory, "find_codex_session_path", lambda *a, **k: native)
    job_id = store.upsert_meeting_alignment_job(
        meeting_id=source.meeting_id, title=title, source_json="{}", participants_json="[]",
        ended_at="2026-07-14T08:00:00+08:00", eligible_at="2026-07-14T08:10:00+08:00",
        status="no_action",
    )
    run_id = store.create_professional_run(
        job_id, session_id=session_id, status="ready_to_send", transcript_start=0, transcript_end=2,
    )
    with store._connect() as db:
        insert_runtime_attempt_row(db, {
            "workload_kind": "meeting", "workload_key": str(run_id), "attempt_number": 1,
            "route_name": "test", "runtime_kind": "codex_cli", "credential_mode": "local_oauth",
            "model": "test", "session_id": session_id, "status": "completed",
            "transcript_start": 0, "transcript_end": 2,
        })


def test_consumer_injects_similar_codex_sessions_into_meeting_prompt(tmp_path, monkeypatch):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    _seed_native_meeting_session(
        store, tmp_path, monkeypatch, session_id="session-risk-budget",
        title="历史上线评审", final_message="先定义上线风险预算与故障面",
    )
    dws = ConsumerDws()
    seed_consumer_job(store, dws)
    runner = FakeMeetingRunner(summary_decision())

    assert consume_meeting_alignment_jobs(
        store,
        dws,
        runner,
        now=NOW,
        limit=1,
        embedding_client=lambda texts: [[1.0, 0.0] for _ in texts],
    ) == 1

    [prompt] = runner.prompts
    assert "相似历史 Codex sessions" in prompt
    assert "session-risk-budget" in prompt
    assert "历史相似会议：上线范围和风险预算" not in prompt


def test_consumer_retries_invalid_model_decision(tmp_path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    dws = ConsumerDws()
    job_id = seed_consumer_job(store, dws)

    class InvalidDecisionRunner:
        last_session_id = "meeting-invalid-decision"
        last_transcript_start_line = 0
        last_transcript_end_line = 12
        last_audit_tool_events = []

        def decide(self, *, prompt: str, run_id=None):
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


def test_consumer_keeps_external_agent_failure_retryable_after_limit(tmp_path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    dws = ConsumerDws()
    job_id = seed_consumer_job(store, dws)

    class ExternalFailureRunner:
        last_session_id = "meeting-external-failure"
        last_transcript_start_line = 0
        last_transcript_end_line = 0
        last_audit_tool_events = []

        def decide(self, *, prompt: str, run_id=None):
            raise ExternalDependencyError(
                "codex meeting alignment",
                RuntimeError("provider temporarily unavailable"),
                dependency="codex",
            )

    assert consume_meeting_alignment_jobs(
        store,
        dws,
        ExternalFailureRunner(),
        now=NOW,
        limit=1,
        max_attempts=1,
    ) == 1

    job = store.get_meeting_alignment_job(job_id)
    assert job.status == "retry"
    assert job.available_at == (NOW + timedelta(minutes=1)).isoformat()
    [run] = store.list_meeting_alignment_runs(job_id)
    assert run.status == "retry"


def test_runtime_attempt_conflict_stays_retryable_at_attempt_limit(tmp_path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    dws = ConsumerDws()
    job_id = seed_consumer_job(store, dws)

    class ConflictingRunner:
        last_session_id = ""
        last_transcript_start_line = 0
        last_transcript_end_line = 0
        last_audit_tool_events = []

        def decide(self, *, prompt: str, run_id=None):
            raise RoutedCodexExecutionError("runtime_attempt_active")

    assert consume_meeting_alignment_jobs(
        store,
        dws,
        ConflictingRunner(),
        now=NOW,
        limit=1,
        max_attempts=1,
    ) == 1

    job = store.get_meeting_alignment_job(job_id)
    assert job.status == "retry"
    assert job.available_at == (NOW + timedelta(minutes=1)).isoformat()
    [run] = store.list_meeting_alignment_runs(job_id)
    assert run.status == "retry"


def test_meeting_no_route_outage_defers_without_consuming_attempt(tmp_path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    dws = ConsumerDws()
    job_id = seed_consumer_job(store, dws)
    reason = (
        "no_eligible_route:codex_oauth=paused:codex_provider_overloaded;"
        "codex_api=paused:codex_provider_overloaded;"
        "friday_runtime=paused:friday_runtime_unreachable"
    )

    class NoRouteRunner:
        last_session_id = ""
        last_transcript_start_line = 0
        last_transcript_end_line = 0
        last_audit_tool_events = []

        def decide(self, *, prompt: str, run_id=None):
            # Mirrors MeetingAlignmentCodexRunner.decide wrapping the router's
            # no-route raise (runtime_execution_failed, runtime_unavailable).
            routed = RoutedCodexExecutionError(
                "runtime_execution_failed",
                reason,
                retryable_external_dependency=True,
                runtime_unavailable=True,
            )
            raise ExternalDependencyError(
                "codex meeting alignment", routed, dependency="codex"
            ) from routed

    runner = NoRouteRunner()
    assert consume_meeting_alignment_jobs(
        store, dws, runner, now=NOW, limit=1, max_attempts=1
    ) == 1

    job = store.get_meeting_alignment_job(job_id)
    assert job.status == "retry"
    assert job.attempts == 0
    assert job.available_at == (NOW + timedelta(minutes=2)).isoformat()
    error = json.loads(job.error)
    assert error["kind"] == "runtime_provider_unreachable"
    assert error["runtime"] == {"code": "runtime_execution_failed", "reason": reason}
    assert [run.status for run in store.list_meeting_alignment_runs(job_id)] == [
        "retry"
    ]
    assert store.list_errors() == []
    assert store.active_codex_capacity_pause(now=NOW) == ""

    assert consume_meeting_alignment_jobs(
        store,
        dws,
        runner,
        now=NOW + timedelta(minutes=2),
        limit=1,
        max_attempts=1,
    ) == 1
    job = store.get_meeting_alignment_job(job_id)
    assert job.status == "retry"
    assert job.attempts == 0
    assert job.available_at == (NOW + timedelta(minutes=6)).isoformat()
    assert [run.status for run in store.list_meeting_alignment_runs(job_id)] == [
        "retry",
        "retry",
    ]
    assert store.list_errors() == []


def test_meeting_outage_backoff_stops_at_cap_over_a_long_outage(tmp_path):
    # A multi-hour outage keeps deferring: the per-turn doubling stops at the
    # shared 15-minute cap, so the turn count can never grow the delay past it
    # (an unbounded 2**turn overflowed timedelta after 41 turns).
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    dws = ConsumerDws()
    job_id = seed_consumer_job(store, dws)

    class NoRouteRunner:
        last_session_id = ""
        last_transcript_start_line = 0
        last_transcript_end_line = 0
        last_audit_tool_events = []

        def decide(self, *, prompt: str, run_id=None):
            raise RoutedCodexExecutionError(
                "runtime_execution_failed",
                "no_eligible_route:codex_oauth=paused:codex_provider_overloaded",
                retryable_external_dependency=True,
                runtime_unavailable=True,
            )

    runner = NoRouteRunner()
    now = NOW
    delays: list[timedelta] = []
    for _ in range(45):
        assert consume_meeting_alignment_jobs(
            store, dws, runner, now=now, limit=1, max_attempts=1
        ) == 1
        job = store.get_meeting_alignment_job(job_id)
        assert job.status == "retry"
        assert job.attempts == 0
        available_at = datetime.fromisoformat(job.available_at)
        delays.append(available_at - now)
        now = available_at

    assert delays[:4] == [
        timedelta(minutes=2),
        timedelta(minutes=4),
        timedelta(minutes=8),
        timedelta(minutes=15),
    ]
    assert set(delays[4:]) == {timedelta(minutes=15)}
    assert len(store.list_meeting_alignment_runs(job_id)) == 45
    assert store.list_errors() == []


def test_meeting_outage_backoff_counts_only_trailing_outage_turns(tmp_path):
    # Earlier non-outage runs (result_invalid retries, startup requeues) do not
    # number the outage turn: the first outage deferral of a job with a long
    # history still waits one doubled retry_delay, not the cap.
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    dws = ConsumerDws()
    job_id = seed_consumer_job(store, dws)
    for _ in range(45):
        store.create_professional_run(job_id, session_id="", status="retry", error=json.dumps({"kind": "meeting_agent", "message": "result_invalid"}))

    class NoRouteRunner:
        last_session_id = ""
        last_transcript_start_line = 0
        last_transcript_end_line = 0
        last_audit_tool_events = []

        def decide(self, *, prompt: str, run_id=None):
            raise RoutedCodexExecutionError(
                "runtime_execution_failed",
                "no_eligible_route:codex_oauth=paused:codex_provider_overloaded",
                retryable_external_dependency=True,
                runtime_unavailable=True,
            )

    assert consume_meeting_alignment_jobs(
        store, dws, NoRouteRunner(), now=NOW, limit=1, max_attempts=1
    ) == 1

    job = store.get_meeting_alignment_job(job_id)
    assert job.status == "retry"
    assert job.attempts == 0
    assert job.available_at == (NOW + timedelta(minutes=2)).isoformat()
    assert store.list_errors() == []


def test_meeting_last_route_capacity_failure_defers_as_outage(tmp_path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    dws = ConsumerDws()
    job_id = seed_consumer_job(store, dws)

    class LastRouteCapacityRunner:
        last_session_id = ""
        last_transcript_start_line = 0
        last_transcript_end_line = 0
        last_audit_tool_events = []

        def decide(self, *, prompt: str, run_id=None):
            routed = RoutedCodexExecutionError(
                "runtime_execution_failed",
                "codex_provider_overloaded",
                failure_class=RuntimeFailureClass.CAPACITY,
                failure_code="codex_provider_overloaded",
                retryable_external_dependency=True,
            )
            raise ExternalDependencyError(
                "codex meeting alignment", routed, dependency="codex"
            ) from routed

    assert consume_meeting_alignment_jobs(
        store, dws, LastRouteCapacityRunner(), now=NOW, limit=1, max_attempts=1
    ) == 1

    job = store.get_meeting_alignment_job(job_id)
    assert job.status == "retry"
    assert job.attempts == 0
    assert job.available_at == (NOW + timedelta(minutes=2)).isoformat()
    error = json.loads(job.error)
    assert error["kind"] == "runtime_provider_unreachable"
    assert error["runtime"]["failure_class"] == "capacity"
    assert [run.status for run in store.list_meeting_alignment_runs(job_id)] == [
        "retry"
    ]
    # The route pause is the signal: no global capacity pause, no Attention row.
    assert store.active_codex_capacity_pause(now=NOW) == ""
    assert store.list_errors() == []


def test_meeting_authentication_failure_stays_terminal(tmp_path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    dws = ConsumerDws()
    job_id = seed_consumer_job(store, dws)

    class AuthFailureRunner:
        last_session_id = ""
        last_transcript_start_line = 0
        last_transcript_end_line = 0
        last_audit_tool_events = []

        def decide(self, *, prompt: str, run_id=None):
            raise RoutedCodexExecutionError(
                "codex_login_required",
                failure_class=RuntimeFailureClass.AUTHENTICATION,
                failure_code="codex_login_required",
                retryable_external_dependency=False,
            )

    assert consume_meeting_alignment_jobs(
        store, dws, AuthFailureRunner(), now=NOW, limit=1
    ) == 1

    job = store.get_meeting_alignment_job(job_id)
    assert job.status == "failed"
    assert job.attempts == 1
    error = json.loads(job.error)
    assert error["kind"] == "meeting_agent"
    assert error["message"] == "codex_login_required"
    assert [run.status for run in store.list_meeting_alignment_runs(job_id)] == [
        "failed"
    ]


def test_consumer_pauses_meeting_analysis_after_codex_capacity_exhaustion(tmp_path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    dws = ConsumerDws()
    job_id = seed_consumer_job(store, dws)

    class CapacityFailureRunner:
        last_session_id = "meeting-capacity"
        last_transcript_start_line = 0
        last_transcript_end_line = 0
        last_audit_tool_events = []

        def decide(self, *, prompt: str, run_id=None):
            raise ExternalDependencyError(
                "codex meeting alignment",
                RuntimeError("Your workspace is out of credits."),
                dependency="codex",
            )

    runner = CapacityFailureRunner()
    assert consume_meeting_alignment_jobs(
        store, dws, runner, now=NOW, limit=1
    ) == 1

    job = store.get_meeting_alignment_job(job_id)
    assert job.status == "retry"
    assert job.attempts == 0
    assert job.available_at == (NOW + timedelta(minutes=30)).isoformat()
    assert store.active_codex_capacity_pause(now=NOW) == job.available_at
    assert [error.kind for error in store.list_errors()] == ["codex_capacity_pause"]

    assert consume_meeting_alignment_jobs(
        store, dws, runner, now=NOW + timedelta(minutes=1), limit=1
    ) == 0
    assert len(store.list_meeting_alignment_runs(job_id)) == 1


def test_meeting_process_failure_continues_prior_capacity_wait(tmp_path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    dws = ConsumerDws()
    job_id = seed_consumer_job(store, dws)

    class CapacityThenProcessFailureRunner:
        last_session_id = "meeting-capacity"
        last_transcript_start_line = 0
        last_transcript_end_line = 0
        last_audit_tool_events = []
        calls = 0

        def decide(self, *, prompt: str, run_id=None):
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError("Your workspace is out of credits.")
            raise RuntimeError("[Errno 32] Broken pipe")

    runner = CapacityThenProcessFailureRunner()
    assert consume_meeting_alignment_jobs(
        store, dws, runner, now=NOW, limit=1, max_attempts=1
    ) == 1
    assert consume_meeting_alignment_jobs(
        store,
        dws,
        runner,
        now=NOW + timedelta(minutes=31),
        limit=1,
        max_attempts=1,
    ) == 1

    job = store.get_meeting_alignment_job(job_id)
    assert job.status == "retry"
    assert job.attempts == 0
    assert job.available_at == (NOW + timedelta(minutes=91)).isoformat()
    assert "codex_capacity_pause" in job.error


@pytest.mark.parametrize(
    ("failure_class", "failure_code"),
    [
        (RuntimeFailureClass.AUTHENTICATION, "codex_login_required"),
        (RuntimeFailureClass.CAPABILITY, "runtime_effect_policy_violation"),
        (RuntimeFailureClass.RESULT, "runtime_result_validation_failed"),
        (RuntimeFailureClass.SESSION, "runtime_session_conflict"),
    ],
)
def test_meeting_routed_terminal_failure_is_not_retried_or_capacity_reclassified(
    tmp_path, failure_class, failure_code
):
    store = AutoReplyStore(tmp_path / f"meeting-terminal-{failure_class}.sqlite3")
    dws = ConsumerDws()
    job_id = seed_consumer_job(store, dws)

    class CapacityThenTerminalRunner:
        last_session_id = "meeting-terminal"
        last_transcript_start_line = 0
        last_transcript_end_line = 0
        last_audit_tool_events = []
        calls = 0

        def decide(self, *, prompt: str, run_id=None):
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError("Your workspace is out of credits.")
            raise RoutedCodexExecutionError(
                failure_code,
                failure_class=failure_class,
                failure_code=failure_code,
                retryable_external_dependency=False,
            )

    runner = CapacityThenTerminalRunner()
    consume_meeting_alignment_jobs(store, dws, runner, now=NOW, limit=1)
    consume_meeting_alignment_jobs(
        store,
        dws,
        runner,
        now=NOW + timedelta(minutes=31),
        limit=1,
    )

    job = store.get_meeting_alignment_job(job_id)
    assert job.status == "failed"
    error = json.loads(job.error)
    assert error["kind"] == "meeting_agent"
    assert error["message"] == failure_code
    assert error["runtime"] == {
        "code": failure_code,
        "failure_class": failure_class.value,
        "failure_code": failure_code,
    }
    runs = store.list_meeting_alignment_runs(job_id)
    assert [run.status for run in runs] == ["failed", "retry"]


def test_meeting_routed_failure_persists_runtime_reason_and_cause(tmp_path):
    store = AutoReplyStore(tmp_path / "meeting-runtime-detail.sqlite3")
    dws = ConsumerDws()
    job_id = seed_consumer_job(store, dws)

    class RuntimeFailureRunner:
        last_session_id = ""
        last_transcript_start_line = 0
        last_transcript_end_line = 0
        last_audit_tool_events = []

        def decide(self, *, prompt: str, run_id=None):
            raise RoutedCodexExecutionError(
                "runtime_execution_failed",
                "no compatible configured runtime route",
                failure_class=RuntimeFailureClass.CAPABILITY,
                failure_code="runtime_route_capability_missing",
            ) from FileNotFoundError("runtime configuration is missing")

    assert consume_meeting_alignment_jobs(
        store, dws, RuntimeFailureRunner(), now=NOW, limit=1
    ) == 1

    [run] = store.list_meeting_alignment_runs(job_id)
    error = json.loads(run.error)
    assert error == {
        "kind": "meeting_agent",
        "message": "runtime_execution_failed",
        "runtime": {
            "code": "runtime_execution_failed",
            "reason": "no compatible configured runtime route",
            "failure_class": "capability",
            "failure_code": "runtime_route_capability_missing",
            "cause": "FileNotFoundError: runtime configuration is missing",
        },
    }


def test_meeting_wrapped_external_routed_failure_persists_runtime_detail(tmp_path):
    # A PROCESS-class failure is deliberately not an outage: only a no-route
    # turn or a capacity/transport failure on the last live route defers
    # (docs/architecture.md 'Agent 失败重试'); process failures stay bounded.
    store = AutoReplyStore(tmp_path / "meeting-wrapped-runtime-detail.sqlite3")
    dws = ConsumerDws()
    job_id = seed_consumer_job(store, dws)

    class WrappedRuntimeFailureRunner:
        last_session_id = ""
        last_transcript_start_line = 0
        last_transcript_end_line = 0
        last_audit_tool_events = []

        def decide(self, *, prompt: str, run_id=None):
            routed = RoutedCodexExecutionError(
                "runtime_execution_failed",
                "Codex runtime exceeded total timeout",
                failure_class=RuntimeFailureClass.PROCESS,
                failure_code="runtime_total_timeout",
            )
            raise ExternalDependencyError(
                "codex meeting alignment", routed, dependency="codex"
            ) from routed

    assert consume_meeting_alignment_jobs(
        store, dws, WrappedRuntimeFailureRunner(), now=NOW, limit=1
    ) == 1

    [run] = store.list_meeting_alignment_runs(job_id)
    error = json.loads(run.error)
    assert error["kind"] == "meeting_agent"
    assert error["message"] == "runtime_execution_failed"
    assert error["runtime"] == {
        "code": "runtime_execution_failed",
        "reason": "Codex runtime exceeded total timeout",
        "failure_class": "process",
        "failure_code": "runtime_total_timeout",
    }


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
    expected_message = (
        "# 上线评审\n*时间：2026-07-14 09:00-10:00*\n\n---\n\n"
        f"{consumer_send_decision().final_message}（by明哥分身）"
    )
    assert job.final_message == expected_message
    assert job.target_kind == "group"
    assert job.target_id == "cid-first"
    assert json.loads(job.decision_json)["action"] == "send"
    receipt = json.loads(job.send_result_json)
    assert receipt["status"] == "sent"
    # The desktop notification opens this conversation when Derek clicks it, and a
    # later withdrawal names the message; the send itself answers with neither.
    assert receipt["send_result"]["openConversationId"]
    assert receipt["send_result"]["openMessageId"]
    assert job.calendar_summary_status == "updated"
    calendar_receipt = json.loads(job.calendar_summary_result_json)
    assert calendar_receipt["event_id"] == "event-1"
    assert calendar_receipt["state"] == "updated"
    assert dws.calendar_update_calls == [
        {
            "event_id": "event-1",
            "description": (
                "【CEO 会议总结】\n"
                "# 上线评审\n*时间：2026-07-14 09:00-10:00*\n\n---\n\n"
                f"{consumer_send_decision().final_message}（by明哥分身）\n"
                "【/CEO 会议总结】"
            ),
        }
    ]
    [history] = store.list_history_items(object_types=("meeting",))
    assert history.status == "sent"
    assert history.output_text.endswith("日历备注：已写入原日程。")
    [run] = store.list_meeting_alignment_runs(job_id)
    assert run.status == "ready_to_send"


def test_meeting_agent_receives_live_group_candidates_before_deciding(tmp_path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    dws = ConsumerDws()
    searches: list[str] = []

    def search_conversations(query: str) -> list[DingTalkConversation]:
        searches.append(query)
        if query == "上线":
            return [
                DingTalkConversation(
                    open_conversation_id="cid-first",
                    title="项目群",
                    single_chat=False,
                    unread_point=0,
                )
            ]
        return []

    dws.search_conversations = search_conversations
    seed_consumer_job(store, dws)
    runner = FakeMeetingRunner(consumer_send_decision())

    assert consume_meeting_alignment_jobs(
        store, dws, runner, now=NOW, limit=1
    ) == 1

    assert "上线" in searches
    assert "cid-first" in runner.prompts[0]
    assert "项目群" in runner.prompts[0]


def test_recurring_group_candidate_requires_sent_history_and_live_roster(tmp_path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    for number in range(2):
        job_id = store.upsert_meeting_alignment_job(
            meeting_id=f"previous-{number}",
            title="项目周会",
            source_json="{}",
            participants_json="[]",
            ended_at=f"2026-07-{10 + number}T10:00:00+08:00",
            eligible_at="",
            status="sent",
        )
        store.update_meeting_alignment_job(
            job_id, target_kind="group", target_id="cid-project", target_title="项目群",
            final_message="已发送", send_result_json='{"success":true}',
        )
    source = MeetingSource.model_validate({
        "meeting_id": "current", "title": "项目周会", "status": "ended",
        "started_at": "2026-07-14T09:00:00+08:00",
        "ended_at": "2026-07-14T10:00:00+08:00",
        "participants": [
            {"name": "A", "user_id": "a", "open_dingtalk_id": "open-a"},
            {"name": "B", "user_id": "b", "open_dingtalk_id": "open-b"},
            {"name": "C", "user_id": "c", "open_dingtalk_id": "open-c"},
        ],
        "attendee_evidence": "calendar", "attendee_roster_complete": True,
        "current_user_id": "a", "summary": "项目进展", "transcript": [],
    })
    dws = ConsumerDws()
    dws.search_conversations = lambda query: [DingTalkConversation(
        open_conversation_id="cid-project", title="项目群",
        single_chat=False, unread_point=0,
    )] if query == "项目" else []
    dws.list_group_member_open_dingtalk_ids = lambda group: {
        "open-a", "open-b", "open-c", "open-other"
    }

    candidates = meeting_alignment._search_meeting_group_candidates(
        dws, source, store
    )

    assert candidates[0]["conversation_id"] == "cid-project"
    assert "verified_recurring_group" not in candidates[0]
    assert candidates[0]["prior_sent_count"] == 2
    assert candidates[0]["member_precision"] == "3/4"
    assert "audience_verification_required" not in candidates[0]


def test_meeting_group_candidates_prioritize_topic_discussion_over_roster(tmp_path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    source = MeetingSource.model_validate({
        "meeting_id": "new", "title": "项目内容进展", "status": "ended",
        "started_at": "2026-07-14T09:00:00+08:00",
        "ended_at": "2026-07-14T10:00:00+08:00",
        "participants": [
            {"name": "A", "user_id": "a", "open_dingtalk_id": "open-a"},
            {"name": "B", "user_id": "b", "open_dingtalk_id": "open-b"},
            {"name": "C", "user_id": "c", "open_dingtalk_id": "open-c"},
        ],
        "attendee_evidence": "calendar", "attendee_roster_complete": True,
        "current_user_id": "a", "summary": (
            '> <time data-ts="123">会议时间</time>\n'
            '![图片](https://example.test/cover.png?Signature=metadata)\n'
            '## 会议背景\nMarketing 内容生产计划：基础内容、深度访谈和发布渠道'
        ), "transcript": [],
    })
    dws = ConsumerDws()
    dws.search_conversations = lambda query: [DingTalkConversation(
        open_conversation_id="cid-product", title="项目群",
        single_chat=False, unread_point=0,
    )] if query == "项目" else [DingTalkConversation(
        open_conversation_id="cid-marketing", title="Marketing",
        single_chat=False, unread_point=0,
    )] if query == "Marketing" else []
    dws.list_group_member_open_dingtalk_ids = lambda group: (
        {"open-a", "open-b", "open-c", "open-other"}
        if group == "cid-product" else {"open-a", "open-b", "open-other"}
    )
    dws.read_recent_messages = lambda conversation, limit=50: [SimpleNamespace(
        content="基础内容完成后安排深度访谈，Marketing 确认发布渠道和内容生产计划",
        create_time="2026-07-13 10:00:00",
    )] if conversation.open_conversation_id == "cid-marketing" else [
        SimpleNamespace(
            content="项目会议记录：内容、生产、计划、渠道等信息" + "其他项目记录" * 200,
            create_time="2026-07-13 11:00:00",
        )
    ]

    candidates = meeting_alignment._search_meeting_group_candidates(
        dws, source, store
    )

    assert candidates[0]["conversation_id"] == "cid-marketing"
    assert candidates[0]["summary_title_overlap"] >= 1
    assert candidates[1]["summary_title_overlap"] == 1
    assert candidates[0]["topic_discussion_evidence"]
    assert candidates[0]["member_precision"] == "2/3"
    assert candidates[1]["member_precision"] == "3/4"


def test_meeting_group_candidates_check_discussion_after_raw_search_page(tmp_path):
    """A relevant group must not disappear because unrelated search hits came first."""
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    source = MeetingSource.model_validate({
        "meeting_id": "late-candidate", "title": "销售项目进展", "status": "ended",
        "started_at": "2026-07-14T09:00:00+08:00",
        "ended_at": "2026-07-14T10:00:00+08:00",
        "participants": [
            {"name": "A", "user_id": "a", "open_dingtalk_id": "open-a"},
            {"name": "B", "user_id": "b", "open_dingtalk_id": "open-b"},
        ],
        "attendee_evidence": "calendar", "attendee_roster_complete": True,
        "current_user_id": "a", "summary": "销售项目进展", "transcript": [],
    })
    candidates = [
        DingTalkConversation(
            open_conversation_id=f"cid-{index}",
            title=f"无关群{index}",
            single_chat=False,
            unread_point=0,
        )
        for index in range(12)
    ]
    candidates.append(DingTalkConversation(
        open_conversation_id="cid-sales-hiring",
        title="销售招聘进度沟通",
        single_chat=False,
        unread_point=0,
    ))
    dws = ConsumerDws()
    dws.search_conversations = lambda query: candidates
    member_reads: list[str] = []
    dws.list_group_member_open_dingtalk_ids = lambda _conversation_id: {
        "open-a", "open-b"
    }
    def read_recent_messages(conversation, limit=50):
        member_reads.append(conversation.open_conversation_id)
        return [SimpleNamespace(
            content="销售项目进展：招聘候选人面试结论和后续招聘安排",
            create_time="2026-07-13 10:00:00",
        )] if conversation.open_conversation_id == "cid-sales-hiring" else []

    dws.read_recent_messages = read_recent_messages

    discovered = meeting_alignment._search_meeting_group_candidates(
        dws, source, store
    )

    selected = next(
        item for item in discovered if item["conversation_id"] == "cid-sales-hiring"
    )
    assert selected["topic_discussion_evidence"]
    assert member_reads == ["cid-sales-hiring"]


def test_same_task_prior_group_candidate_survives_content_revision():
    prior_runs = [SimpleNamespace(
        decision_json=json.dumps({
            "action": "send",
            "target": {
                "kind": "group",
                "conversation_id": "cid-algorithm",
                "direct_user_id": "",
                "title": "算法交流群",
                "candidates": [{
                    "conversation_id": "cid-algorithm",
                    "title": "算法交流群",
                    "evidence": [
                        "完整名册：12名成员。",
                        "当日讨论了HSW方法论、建模和Benchmark。",
                    ],
                }],
            },
        }),
    )]

    candidates = meeting_alignment._carry_forward_same_task_group_candidate(
        [], prior_runs
    )

    assert candidates == [{
        "conversation_id": "cid-algorithm",
        "title": "算法交流群",
        "prior_same_task_target": True,
        "prior_same_task_evidence": [
            "完整名册：12名成员。",
            "当日讨论了HSW方法论、建模和Benchmark。",
        ],
    }]

    current = [{
        "conversation_id": "cid-algorithm",
        "title": "算法交流群",
        "member_roster_complete": True,
    }]
    merged = meeting_alignment._carry_forward_same_task_group_candidate(
        current, prior_runs
    )
    assert len(merged) == 1
    assert merged[0]["member_roster_complete"] is True
    assert merged[0]["prior_same_task_evidence"] == candidates[0][
        "prior_same_task_evidence"
    ]


def test_live_group_member_failure_retries_before_meeting_decision(tmp_path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    dws = ConsumerDws()
    dws.search_conversations = lambda query: [DingTalkConversation(
        open_conversation_id="cid-first", title="项目群",
        single_chat=False, unread_point=0,
    )] if query == "上线" else []

    def fail_members(_group: str) -> set[str]:
        raise DwsError("group members unavailable")

    dws.list_group_member_open_dingtalk_ids = fail_members
    job_id = seed_consumer_job(store, dws)
    runner = FakeMeetingRunner(consumer_send_decision())

    assert consume_meeting_alignment_jobs(
        store, dws, runner, now=NOW, limit=1
    ) == 1
    job = store.get_meeting_alignment_job(job_id)
    assert job.status == "retry"
    assert json.loads(job.error)["kind"] == "meeting_group_discovery"
    assert runner.calls == 0
    assert dws.send_calls == []


def test_meeting_group_discovery_failure_does_not_become_direct_fallback(tmp_path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    dws = ConsumerDws()

    def fail_search(_query: str) -> list[DingTalkConversation]:
        raise DwsError("group search unavailable")

    dws.search_conversations = fail_search
    job_id = seed_consumer_job(store, dws)
    runner = FakeMeetingRunner(consumer_send_decision())

    assert consume_meeting_alignment_jobs(
        store, dws, runner, now=NOW, limit=1
    ) == 1

    job = store.get_meeting_alignment_job(job_id)
    assert job.status == "retry"
    assert json.loads(job.error)["kind"] == "meeting_group_discovery"
    assert runner.calls == 0
    assert dws.send_calls == []


@pytest.mark.parametrize("prior_attempts", [0, 411])
def test_confidential_group_read_denial_reaches_agent_without_fallback(tmp_path, prior_attempts):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    dws = ConsumerDws()
    dws.search_conversations = lambda query: [DingTalkConversation(
        open_conversation_id="cid-first", title="上线项目群",
        single_chat=False, unread_point=0,
    )] if query == "上线" else []

    def fail_read(conversation, limit=50):
        raise DwsError(
            f"group={conversation.open_conversation_id} read refused",
            code="1001",
            business_message="该群为保密群，无法获取消息记录",
            server_key="chat",
        )

    dws.read_recent_messages = fail_read
    job_id = seed_consumer_job(store, dws)
    _set_meeting_job_fields(store, job_id, attempts=prior_attempts)
    class UnresolvedAudienceRunner(FakeMeetingRunner):
        def decide(self, *, prompt, run_id=None):
            self.calls += 1
            self.prompts.append(prompt)
            raise ValueError("Audience unresolved; no effects selected")

    runner = UnresolvedAudienceRunner(consumer_send_decision())

    assert consume_meeting_alignment_jobs(store, dws, runner, now=NOW, limit=1) == 1

    job = store.get_meeting_alignment_job(job_id)
    assert job.status == "failed"
    assert json.loads(job.error)["kind"] == "meeting_agent"
    assert runner.prompts, job.error
    assert '"history_read_status":"denied"' in runner.prompts[0]
    assert '"history_read_code":"1001"' in runner.prompts[0]
    assert "该群为保密群，无法获取消息记录" in runner.prompts[0]
    assert "live full relevant discussion" in runner.prompts[0]
    assert job.locked_at is None
    assert runner.calls == 1
    assert dws.send_calls == []
    assert consume_meeting_alignment_jobs(store, dws, runner, now=NOW + timedelta(hours=1), limit=1) == 0


@pytest.mark.parametrize("roster_complete", [True, False])
def test_denied_candidate_serializes_actual_roster_not_meeting_audience(roster_complete):
    source = MeetingSource.model_validate({
        "meeting_id": "unresolved", "title": "上线项目", "status": "ended",
        "started_at": "2026-07-14T09:00:00+08:00",
        "ended_at": "2026-07-14T10:00:00+08:00",
        "participants": [
            {"name": "A", "user_id": "a", "open_dingtalk_id": "open-a"},
            {"name": "B", "user_id": "b", "open_dingtalk_id": "open-b"},
        ],
        "attendee_evidence": "calendar", "attendee_roster_complete": roster_complete,
        "current_user_id": "a", "summary": "上线项目进展", "transcript": [],
    })
    dws = ConsumerDws()
    dws.search_conversations = lambda query: [DingTalkConversation(
        open_conversation_id="cid-first", title="上线项目群", single_chat=False, unread_point=0,
    )]
    dws.list_group_member_open_dingtalk_ids = lambda group: {"open-a", "open-b", "open-other"}
    def deny(conversation, limit):
        raise DwsError("read refused", code="1001", business_message="该群为保密群，无法获取消息记录")
    dws.read_recent_messages = deny
    store = SimpleNamespace(recurring_meeting_group_targets=lambda title: [("cid-first", "上线项目群", 2)])
    [candidate] = meeting_alignment._search_meeting_group_candidates(dws, source, store)
    assert candidate["title"] == "上线项目群"
    assert candidate["member_count"] == 3
    assert candidate["member_open_dingtalk_ids"] == ["open-a", "open-b", "open-other"]
    assert candidate["member_roster_complete"] is True
    assert candidate["history_read_status"] == "denied"
    assert candidate["history_read_code"] == "1001"
    assert candidate["history_read_reason"] == "该群为保密群，无法获取消息记录"
    assert "topic_discussion_evidence" not in candidate
    assert candidate.get("verified_recurring_group") is not True


def test_calendar_summary_retry_does_not_resend_meeting_message(tmp_path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")

    class CalendarFailsOnceDws(ConsumerDws):
        def __init__(self):
            super().__init__()
            self.calendar_attempts = 0

        def update_calendar_event_description(
            self, event_id: str, description: str
        ) -> dict:
            self.calendar_attempts += 1
            if self.calendar_attempts == 1:
                raise DwsError("calendar temporarily unavailable")
            return super().update_calendar_event_description(event_id, description)

    dws = CalendarFailsOnceDws()
    job_id = seed_consumer_job(store, dws)
    runner = FakeMeetingRunner(consumer_send_decision())

    consume_meeting_alignment_jobs(store, dws, runner, now=NOW, limit=1)
    first = store.get_meeting_alignment_job(job_id)
    assert first.status == "ready_to_send"
    assert first.calendar_summary_status == "failed"
    assert len(dws.send_calls) == 1
    legacy_decision = json.loads(first.decision_json)
    legacy_decision.pop("_service_audit")
    store.update_meeting_alignment_job(
        job_id,
        decision_json=json.dumps(legacy_decision, ensure_ascii=False),
    )

    consume_meeting_alignment_jobs(
        store,
        dws,
        runner,
        now=NOW + timedelta(minutes=1),
        limit=1,
    )
    recovered = store.get_meeting_alignment_job(job_id)
    assert recovered.status == "sent"
    assert recovered.calendar_summary_status == "updated"
    assert len(dws.send_calls) == 1


def test_calendar_summary_readback_already_contains_exact_sent_summary(tmp_path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    dws = ConsumerDws()
    job_id = seed_consumer_job(store, dws)
    runner = FakeMeetingRunner(consumer_send_decision())

    consume_meeting_alignment_jobs(store, dws, runner, now=NOW, limit=1)
    sent = store.get_meeting_alignment_job(job_id)
    assert sent.status == "sent"
    assert len(dws.send_calls) == 1

    existing_description = f"Prior note\n\n{sent.final_message}"
    dws.calendar_pages[""]["events"][0].description = existing_description
    store.update_meeting_alignment_job(
        job_id,
        status="ready_to_send",
        calendar_summary_status="failed",
        calendar_summary_result_json='{"state":"failed"}',
        available_at="",
        error="calendar summary readback mismatch",
    )
    calendar_writes_before_retry = len(dws.calendar_update_calls)

    consume_meeting_alignment_jobs(
        store, dws, runner, now=NOW + timedelta(minutes=1), limit=1
    )

    recovered = store.get_meeting_alignment_job(job_id)
    receipt = json.loads(recovered.calendar_summary_result_json)
    assert recovered.status == "sent"
    assert recovered.calendar_summary_status == "skipped"
    assert receipt["state"] == "already_present"
    assert receipt["event_id"] == json.loads(sent.source_json)[
        "calendar_evidence"
    ]["event_id"]
    assert len(dws.calendar_update_calls) == calendar_writes_before_retry
    assert len(dws.send_calls) == 1


def test_meeting_audit_return_runs_bounded_feedback_revision(tmp_path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    dws = ConsumerDws()
    job_id = seed_consumer_job(store, dws)
    first = summary_decision()
    revised = MeetingAlignmentDecision.model_validate({
        **first.model_dump(mode="json"),
        "final_message": first.final_message + "\n\n已补充有证据支持的收件范围。",
    })

    class RevisingRunner(FakeMeetingRunner):
        def __init__(self):
            super().__init__(first)
            self.prompts = []
            self.decisions = 0
            self.audits = 0

        def decide(self, *, prompt, run_id=None, compact_prompt=None):
            self.prompts.append(prompt)
            self.decisions += 1
            return first if self.decisions == 1 else revised

        def audit(self, *, prompt, run_id):
            self.audits += 1
            if self.audits == 1:
                return MeetingAlignmentAuditResult(
                    outcome="return",
                    summary="recipient evidence needs correction",
                    feedback=MeetingAlignmentAuditFeedback(
                        rule="recipient disclosure authorization",
                        observation="the first candidate lacks complete member evidence",
                        requested_revision="select only an audience supported by the source evidence",
                    ),
                )
            return MeetingAlignmentAuditResult(
                outcome="approve",
                summary="revised candidate is supported", feedback=None,
            )

    runner = RevisingRunner()
    consume_meeting_alignment_jobs(store, dws, runner, now=NOW, limit=1)
    assert store.get_meeting_alignment_job(job_id).status == "retry"
    consume_meeting_alignment_jobs(
        store, dws, runner, now=NOW + timedelta(minutes=2), limit=1
    )

    job = store.get_meeting_alignment_job(job_id)
    assert job.status == "sent"
    assert runner.decisions == runner.audits == 2
    assert "recipient disclosure authorization" in runner.prompts[1]
    assert "select only an audience supported by the source evidence" in runner.prompts[1]
    assert revised.final_message in job.final_message
    assert meeting_alignment.meeting_decision_audit_is_approved(job.decision_json)
    assert len(store.list_meeting_alignment_runs(job_id)) == 2


def _skip_decision(action):
    return MeetingAlignmentDecision.model_validate({
        **summary_decision().model_dump(mode="json"),
        "action": action, "trigger_reasons": [], "topics": [], "derek_viewpoint": None,
        "key_questions": [], "mention_names": [], "target": None, "final_message": "",
        "sensitive_private_message": None,
        "audit_summary": "No shareable content." if action == "skip_no_content" else "Interview.",
    })


@pytest.mark.parametrize("action,status", [("skip_interview", "skipped"), ("skip_no_content", "no_action")])
def test_approved_skip_decision_ends_without_sending(tmp_path, action, status):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    dws = ConsumerDws()
    job_id = seed_consumer_job(store, dws)
    runner = FakeMeetingRunner(_skip_decision(action))

    consume_meeting_alignment_jobs(store, dws, runner, now=NOW, limit=1)

    job = store.get_meeting_alignment_job(job_id)
    assert job.status == status
    assert dws.send_calls == []
    assert job.final_message == ""
    assert meeting_alignment.meeting_decision_audit_is_approved(job.decision_json)


def test_returned_skip_decision_goes_back_for_a_revision(tmp_path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    dws = ConsumerDws()
    job_id = seed_consumer_job(store, dws)

    class ReturningRunner(FakeMeetingRunner):
        def audit(self, *, prompt, run_id):
            return MeetingAlignmentAuditResult(
                outcome="return", summary="The meeting has shareable business content.",
                feedback=MeetingAlignmentAuditFeedback(
                    rule="skip needs a supported reason", observation="decisions were made",
                    requested_revision="send a summary of the decisions",
                ),
            )

    consume_meeting_alignment_jobs(
        store, dws, ReturningRunner(_skip_decision("skip_no_content")), now=NOW, limit=1,
    )

    assert store.get_meeting_alignment_job(job_id).status == "retry"
    assert dws.send_calls == []


def test_meeting_audit_feedback_exhaustion_escalates_after_three_revisions(tmp_path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    dws = ConsumerDws()
    job_id = seed_consumer_job(store, dws)

    class NeverApprovedRunner(FakeMeetingRunner):
        def audit(self, *, prompt, run_id):
            return MeetingAlignmentAuditResult(
                outcome="return",
                summary="the candidate remains unsupported",
                feedback=MeetingAlignmentAuditFeedback(
                    rule="evidence must support the exact recipient",
                    observation="the supplied audience evidence is still incomplete",
                    requested_revision="provide source-backed recipient authorization",
                ),
            )

    runner = NeverApprovedRunner(summary_decision())
    now = NOW
    from app.agent_orchestrator import MAX_CONTENT_FEEDBACK_CYCLES

    turns = MAX_CONTENT_FEEDBACK_CYCLES + 1
    for _ in range(turns):
        consume_meeting_alignment_jobs(store, dws, runner, now=now, limit=1)
        now += timedelta(minutes=2)

    job = store.get_meeting_alignment_job(job_id)
    assert job.status == "needs_human"
    assert dws.send_calls == []
    assert json.loads(job.decision_json)["_service_audit"]["feedback_cycles"] == turns
    assert len(store.list_meeting_alignment_runs(job_id)) == turns


def test_calendar_summary_non_organizer_permission_marks_sent_without_retry(tmp_path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")

    class CalendarWriteForbiddenDws(ConsumerDws):
        def update_calendar_event_description(
            self, event_id: str, description: str
        ) -> dict:
            raise DwsError(
                "code: 300000, developerMessage: Only organizer is allowed to patch event.",
                code="300000",
            )

    dws = CalendarWriteForbiddenDws()
    job_id = seed_consumer_job(store, dws)
    runner = FakeMeetingRunner(consumer_send_decision())

    assert consume_meeting_alignment_jobs(store, dws, runner, now=NOW, limit=1) == 1

    job = store.get_meeting_alignment_job(job_id)
    assert job.status == "sent"
    assert job.calendar_summary_status == "skipped"
    assert len(dws.send_calls) == 1
    receipt = json.loads(job.calendar_summary_result_json)
    assert receipt["reason"] == "current account is not the calendar organizer"
    assert store.claim_ready_to_send_meeting_alignment_jobs(limit=1, now=NOW.isoformat()) == []


def test_calendar_summary_does_not_update_description_for_other_organizer(tmp_path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")

    class OtherOrganizerDws(ConsumerDws):
        def __init__(self):
            super().__init__()
            self.calendar_pages[""]["events"][0].organizer = "A"

        def update_calendar_event_description(self, event_id: str, description: str) -> dict:
            raise AssertionError("non-organizer event must not update description")

    dws = OtherOrganizerDws()
    job_id = seed_consumer_job(store, dws)
    assert consume_meeting_alignment_jobs(
        store, dws, FakeMeetingRunner(consumer_send_decision()), now=NOW, limit=1
    ) == 1
    job = store.get_meeting_alignment_job(job_id)
    assert job.status == "sent"
    assert job.calendar_summary_status == "skipped"
    assert len(dws.send_calls) == 1


def test_first_meeting_follow_up_does_not_recall_its_own_send(tmp_path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    dws = ConsumerDws()
    dws.calendar_pages[""]["events"][0].organizer = "A"
    job_id = seed_consumer_job(store, dws)

    assert consume_meeting_alignment_jobs(
        store, dws, FakeMeetingRunner(consumer_send_decision()), now=NOW, limit=1
    ) == 1

    job = store.get_meeting_alignment_job(job_id)
    assert job.status == "sent"
    assert job.calendar_summary_status == "skipped"
    assert len(dws.send_calls) == 1
    assert dws.__dict__.get("recalled") is None


def test_transcript_meeting_without_calendar_event_marks_calendar_note_skipped(
    tmp_path,
):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    dws = ConsumerDws()
    job_id = seed_consumer_job(store, dws)
    runner = FakeMeetingRunner(consumer_send_decision())
    assert consume_meeting_alignment_jobs(
        store, dws, runner, now=NOW, limit=1
    ) == 1
    job = store.get_meeting_alignment_job(job_id)
    assert job.status == "sent"
    dws.calendar_update_calls.clear()
    source = json.loads(job.source_json)
    source["calendar_evidence"].update(
        source="transcript", event_id="transcript:minutes-1"
    )
    store.update_meeting_alignment_job(
        job_id,
        source_json=json.dumps(source, ensure_ascii=False),
        status="ready_to_send",
    )

    assert consume_meeting_alignment_jobs(
        store, dws, runner, now=NOW + timedelta(minutes=1), limit=1
    ) == 1

    completed = store.get_meeting_alignment_job(job_id)
    assert completed.status == "sent", completed.error
    assert completed.calendar_summary_status == "skipped"
    receipt = json.loads(completed.calendar_summary_result_json)
    assert receipt["state"] == "skipped"
    assert "no original calendar event" in receipt["reason"]
    assert dws.calendar_update_calls == []
    assert len(dws.send_calls) == 1


def test_consumer_notifies_once_after_confirmed_meeting_send(tmp_path, monkeypatch):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    dws = ConsumerDws()
    job_id = seed_consumer_job(store, dws)
    notifications: list[dict[str, str]] = []
    monkeypatch.setattr(
        meeting_alignment,
        "send_macos_notification",
        lambda **payload: notifications.append(payload),
    )

    consume_meeting_alignment_jobs(
        store,
        dws,
        FakeMeetingRunner(consumer_send_decision()),
        now=NOW,
        limit=1,
    )

    sent_job = store.get_meeting_alignment_job(job_id)
    assert sent_job.status == "sent"
    expected_message = (
        "# 上线评审\n*时间：2026-07-14 09:00-10:00*\n\n---\n\n"
        f"{consumer_send_decision().final_message}（by明哥分身）"
    )
    assert sent_job.final_message == expected_message
    assert notifications == [
        {
            "title": "CEO meeting follow-up: 上线评审",
            "message": expected_message,
            "url": (
                "http://127.0.0.1:8765/open-dingtalk"
                "?conversation_id=cid-first"
            ),
        }
    ]


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


def test_ready_delivery_source_failure_uses_counted_retry(tmp_path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    dws = ConsumerDws()
    job_id = seed_consumer_job(store, dws)
    decision = consumer_send_decision()
    store.update_meeting_alignment_job(
        job_id,
        status="ready_to_send",
        decision_json=audited_decision_json(decision),
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


def test_ready_delivery_retries_provider_outage_after_attempt_limit(tmp_path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    dws = ConsumerDws()
    job_id = seed_consumer_job(store, dws)
    decision = consumer_send_decision()
    store.update_meeting_alignment_job(
        job_id,
        status="ready_to_send",
        decision_json=audited_decision_json(decision),
        final_message=decision.final_message,
    )
    _set_meeting_job_fields(store, job_id, attempts=3)

    def unavailable_send(conversation_id, text, **kwargs):
        raise DwsError("THREADPOOL_BUSY", retryable_external_dependency=True)

    dws.send_message = unavailable_send
    assert deliver_ready_meeting_alignment_jobs(
        store, dws, now=NOW, limit=1
    ) == {job_id}

    job = store.get_meeting_alignment_job(job_id)
    assert job.status == "retry"
    assert "THREADPOOL_BUSY" in job.error
    assert job.available_at == "2026-07-14T10:14:00+08:00"
    assert job.send_result_json == "{}"


def test_requeue_failed_meeting_delivery_preserves_review_and_key(tmp_path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    dws = ConsumerDws()
    job_id = seed_consumer_job(store, dws)
    decision = consumer_send_decision()
    store.update_meeting_alignment_job(
        job_id,
        status="failed",
        decision_json=audited_decision_json(decision),
        target_kind="group",
        target_id="cid-first",
        final_message=decision.final_message,
        error="provider unavailable",
    )

    reopened = store.requeue_failed_meeting_delivery(job_id)
    assert reopened is not None
    assert reopened.status == "ready_to_send"
    assert reopened.decision_json == audited_decision_json(decision)
    assert reopened.target_id == "cid-first"
    assert reopened.final_message == decision.final_message
    assert store.requeue_failed_meeting_delivery(job_id) is None


def test_requeue_failed_meeting_delivery_rejects_unreviewed_candidate(tmp_path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    dws = ConsumerDws()
    job_id = seed_consumer_job(store, dws)
    decision = consumer_send_decision()
    store.update_meeting_alignment_job(
        job_id,
        status="failed",
        decision_json=decision.model_dump_json(),
        target_kind="group",
        target_id="cid-first",
        final_message=decision.final_message,
        error="delivery failed",
    )

    assert store.requeue_failed_meeting_delivery(job_id) is None


def test_failed_frozen_meeting_candidate_reaudit_stops_after_prior_receipt(tmp_path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    dws = ConsumerDws()
    job_id = seed_consumer_job(store, dws)
    decision = consumer_send_decision().model_copy(deep=True)
    decision.target.kind = "direct"
    decision.target.conversation_id = ""
    decision.target.direct_user_id = "user-z"
    decision.target.title = "Zhang"
    decision.target.candidates = []
    original_decision_json = decision.model_dump_json()
    store.update_meeting_alignment_job(
        job_id,
        status="failed",
        decision_json=original_decision_json,
        target_kind="direct",
        target_id="user-z",
        target_title="Zhang",
        final_message=decision.final_message,
        error=json.dumps({"kind": "meeting_target", "message": "prepared target binding conflicts"}),
        send_result_json="{}",
    )
    original = store.get_meeting_alignment_job(job_id)
    candidate_digest = meeting_alignment_decision_digest(decision)
    legacy_key = f"meeting-alignment:{job_id}:{original.meeting_id}"
    store.set_service_state(
        f"meeting_delivery_target:v1:dingtalk:{legacy_key}",
        json.dumps({"kind": "group", "id": "old-group"}, sort_keys=True),
    )
    store.prepare_outbound_postfix(
        "dingtalk", legacy_key, "此前发往群聊的正文", "此前发往群聊的正文",
    )
    store.record_outbound_postfix_receipt(
        "dingtalk", legacy_key, {"messageId": "old-group-receipt"},
        operation="meeting_alignment",
    )

    requested = store.request_failed_meeting_audit_recovery(
        job_id, expected_candidate_sha256=candidate_digest,
    )
    assert requested is None
    unchanged = store.get_meeting_alignment_job(job_id)
    assert unchanged.status == "failed"
    assert unchanged.decision_json == original_decision_json
    assert unchanged.final_message == original.final_message
    assert dws.send_calls == []


def test_failed_frozen_meeting_audit_return_stays_failed(tmp_path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    dws = ConsumerDws()
    job_id = seed_consumer_job(store, dws)
    decision = consumer_send_decision()
    store.update_meeting_alignment_job(
        job_id,
        status="failed",
        decision_json=decision.model_dump_json(),
        target_kind="group",
        target_id="cid-first",
        final_message=decision.final_message,
        error=json.dumps({"kind": "meeting_target", "message": "prepared target binding conflicts"}),
        send_result_json="{}",
    )
    digest = meeting_alignment_decision_digest(decision)
    assert store.request_failed_meeting_audit_recovery(
        job_id, expected_candidate_sha256=digest,
    ) is not None

    class ReturningAuditRunner(FakeMeetingRunner):
        def audit(self, *, prompt, run_id):
            self.audit_prompt = prompt
            return MeetingAlignmentAuditResult(
                outcome="return",
                summary="recipient evidence is insufficient",
                feedback=MeetingAlignmentAuditFeedback(
                    rule="recipient evidence", observation="missing proof",
                    requested_revision="provide current recipient evidence",
                ),
            )

    runner = ReturningAuditRunner(decision)
    consume_meeting_alignment_jobs(store, dws, runner, now=NOW, limit=1, deliver=False)
    failed = store.get_meeting_alignment_job(job_id)
    assert failed.status == "failed"
    assert json.loads(failed.error)["kind"] == "meeting_audit_recovery_unapproved", failed.error
    assert dws.send_calls == []


def test_returned_frozen_candidate_can_be_explicitly_reaudited_without_changes(tmp_path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    dws = ConsumerDws()
    job_id = seed_consumer_job(store, dws)
    decision = consumer_send_decision()
    digest = meeting_alignment_decision_digest(decision)
    returned_json = persist_audited_meeting_decision(
        decision,
        MeetingAlignmentAuditResult(
            outcome="return",
            summary="Current-duty evidence is missing.",
            feedback=MeetingAlignmentAuditFeedback(
                rule="Recipient evidence",
                observation="The recipient's current duties are unclear.",
                requested_revision="Use the explicit meeting assignment as evidence.",
            ),
        ),
    )
    store.update_meeting_alignment_job(
        job_id,
        status="failed",
        decision_json=returned_json,
        target_kind="group",
        target_id="cid-first",
        final_message=decision.final_message,
        error=json.dumps({
            "kind": "meeting_audit_recovery_unapproved",
            "message": "Frozen candidate Audit return: current-duty evidence is missing.",
        }),
        send_result_json="{}",
    )

    requested = store.request_failed_meeting_audit_recovery(
        job_id, expected_candidate_sha256=digest,
    )

    assert requested is not None
    assert requested.status == "retry"
    assert requested.final_message == decision.final_message
    assert requested.target_id == "cid-first"
    assert meeting_alignment_decision_digest(
        MeetingAlignmentDecision.model_validate_json(requested.decision_json)
    ) == digest
    assert json.loads(requested.error)["kind"] == "meeting_audit_recovery_requested"

    class ReturningAuditRunner(FakeMeetingRunner):
        def audit(self, *, prompt, run_id):
            return MeetingAlignmentAuditResult(
                outcome="return",
                summary="The follow-up is not supported by the source.",
                feedback=MeetingAlignmentAuditFeedback(
                    rule="Source evidence",
                    observation="No matching assignment was found.",
                    requested_revision="Correct the candidate from the source.",
                ),
            )

    runner = ReturningAuditRunner(decision)
    consume_meeting_alignment_jobs(
        store, dws, runner, now=NOW, limit=1, deliver=False,
    )
    failed_again = store.get_meeting_alignment_job(job_id)
    assert failed_again.status == "failed"
    assert json.loads(failed_again.error)["kind"] == "meeting_audit_recovery_unapproved"
    assert dws.send_calls == []


def test_rejected_frozen_candidate_cannot_be_reaudited_as_a_return(tmp_path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    dws = ConsumerDws()
    job_id = seed_consumer_job(store, dws)
    decision = consumer_send_decision()
    digest = meeting_alignment_decision_digest(decision)
    rejected_json = persist_audited_meeting_decision(
        decision,
        MeetingAlignmentAuditResult(
            outcome="reject",
            summary="The recipient is not appropriate for this content.",
            feedback=None,
        ),
    )
    store.update_meeting_alignment_job(
        job_id,
        status="failed",
        decision_json=rejected_json,
        target_kind="group",
        target_id="cid-first",
        final_message=decision.final_message,
        error=json.dumps({
            "kind": "meeting_audit_recovery_unapproved",
            "message": "Frozen candidate Audit reject: recipient is not appropriate.",
        }),
        send_result_json="{}",
    )

    assert store.request_failed_meeting_audit_recovery(
        job_id, expected_candidate_sha256=digest,
    ) is None
    assert store.get_meeting_alignment_job(job_id).status == "failed"


def test_failed_frozen_meeting_audit_recovery_requires_exact_digest(tmp_path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    dws = ConsumerDws()
    job_id = seed_consumer_job(store, dws)
    decision = consumer_send_decision()
    store.update_meeting_alignment_job(
        job_id,
        status="failed",
        decision_json=decision.model_dump_json(),
        target_kind="group",
        target_id="cid-first",
        final_message=decision.final_message,
        error=json.dumps({"kind": "meeting_target", "message": "prepared target binding conflicts"}),
        send_result_json="{}",
    )
    assert store.request_failed_meeting_audit_recovery(
        job_id, expected_candidate_sha256="0" * 64,
    ) is None
    assert store.get_meeting_alignment_job(job_id).status == "failed"
    assert store.get_meeting_alignment_job(job_id).status == "failed"


def test_ready_delivery_normalizes_legacy_scope_before_sending(tmp_path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    dws = ConsumerDws()
    job_id = seed_consumer_job(store, dws)
    decision = MeetingAlignmentDecision.model_validate(consumer_send_decision())
    decision_payload = json.loads(audited_decision_json(decision))
    del decision_payload["audience_scope"]
    store.update_meeting_alignment_job(
        job_id,
        status="ready_to_send",
        decision_json=json.dumps(decision_payload),
        final_message=decision.final_message,
    )

    consume_meeting_alignment_jobs(store, dws, object(), now=NOW, limit=1)

    job = store.get_meeting_alignment_job(job_id)
    assert job.status == "sent"
    assert json.loads(job.decision_json)["audience_scope"] == "business"
    assert len(dws.send_calls) == 1


def test_deleted_minutes_source_is_terminal_no_action(tmp_path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    dws = ConsumerDws()
    job_id = seed_consumer_job(store, dws)

    def fail_info(meeting_id: str):
        raise DwsError(
            "dws command failed: minutes has been deleted (operation: minutes/get_minutes_basic_info)"
        )

    dws.get_minutes_info = fail_info
    runner = FakeMeetingRunner(consumer_send_decision())

    consume_meeting_alignment_jobs(store, dws, runner, now=NOW, limit=1)

    job = store.get_meeting_alignment_job(job_id)
    assert job.status == "no_action"
    assert job.error == ""
    assert runner.calls == 0
    assert dws.send_calls == []


def test_source_read_failure_never_degrades_to_creator_direct(tmp_path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    dws = ConsumerDws()
    job_id = seed_consumer_job(store, dws)

    def fail_info(meeting_id: str):
        raise DwsError("calendar or minutes source temporarily unavailable")

    dws.get_minutes_info = fail_info
    direct = consumer_send_decision().model_copy(deep=True)
    direct.target.kind = "direct"
    direct.target.conversation_id = ""
    direct.target.direct_user_id = "u-a"
    direct.target.title = "A"
    direct.target.candidates = []
    runner = FakeMeetingRunner(direct)

    consume_meeting_alignment_jobs(store, dws, runner, now=NOW, limit=1)

    job = store.get_meeting_alignment_job(job_id)
    assert job.status == "retry"
    assert json.loads(job.error)["kind"] == "meeting_source"
    assert runner.calls == 0
    assert dws.send_calls == []


def test_unavailable_planned_group_fails_without_organizer_fallback(
    tmp_path,
):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    dws = ConsumerDws()
    dws.calendar_pages[""]["events"][0].organizer = "Unresolved organizer"
    dws.get_conversation_info = lambda _conversation_id: {
        "openConversationId": "cid-first",
        "title": "项目群",
        "singleChat": True,
        "memberCount": 3,
    }
    job_id = seed_consumer_job(store, dws)

    consume_meeting_alignment_jobs(
        store,
        dws,
        FakeMeetingRunner(consumer_send_decision()),
        now=NOW,
        limit=1,
    )

    job = store.get_meeting_alignment_job(job_id)
    assert job.status == "failed"
    assert json.loads(job.error) == {
        "kind": "meeting_target",
        "message": "planned group is unavailable; audience must be reviewed, not redirected",
    }
    assert json.loads(job.decision_json)["action"] == "send"
    assert json.loads(job.decision_json)["target"]["kind"] == "group"
    [run] = store.list_meeting_alignment_runs(job_id)
    assert run.status == "ready_to_send"
    assert run.decision_json == "{}"
    assert run.native_available is False
    assert run.native_reason == "native_source_unavailable"
    assert dws.send_calls == []


def test_sensitive_part_goes_to_the_verified_person_apart_from_the_group_summary(tmp_path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    dws = ConsumerDws()
    event = dws.calendar_pages[""]["events"][0]
    event.organizer = "Avery Zou"
    event.attendee_details[1] = DwsCalendarAttendee(display_name="Avery Zou")
    dws.search_user_profiles = lambda query: (
        [
            DwsUserProfile(
                user_id="u-mina",
                name="Zou Jingwei",
                nick="Avery Zou",
                title="HRVP",
                open_dingtalk_id="open-mina",
            )
        ]
        if query == "Avery Zou"
        else []
    )
    dws.verification_states = ["sent", "sent"]
    job_id = seed_consumer_job(store, dws)
    decision = MeetingAlignmentDecision.model_validate(
        {
            "action": "send",
            "audience_scope": "business",
            "trigger_reasons": ["meeting_summary"],
            "topics": [],
            "derek_viewpoint": None,
            "key_questions": [],
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
                        "evidence": ["Same project discussed before and after the meeting."],
                    }
                ],
            },
            "final_message": "Please follow up on the agreed meeting actions.",
            "sensitive_private_message": {
                "target": {
                    "kind": "direct",
                    "conversation_id": "",
                    "direct_user_id": "u-mina",
                    "title": "Avery Zou",
                    "candidates": [],
                },
                "message": "Please handle the private personnel follow-up.",
                "reason": "The meeting included personnel-sensitive discussion.",
                "recipient_evidence": ["Avery Zou is the meeting HR owner."],
            },
            "audit_summary": "The meeting created business and personnel follow-up.",
            "confidence": 0.95,
        }
    )
    runner = FakeMeetingRunner(decision)

    consume_meeting_alignment_jobs(store, dws, runner, now=NOW, limit=1)

    job = store.get_meeting_alignment_job(job_id)
    assert job.status == "sent", job.error
    assert runner.calls == 1
    source_json = runner.prompts[0].split("完整会议来源 JSON：\n", 1)[1]
    source = json.JSONDecoder().raw_decode(source_json)[0]
    assert any(participant["user_id"] == "u-mina" for participant in source["participants"])
    assert [call.get("user_id") for call in dws.send_calls] == [None, "u-mina"]
    assert decision.final_message in dws.send_calls[0]["text"]
    assert decision.sensitive_private_message.message not in dws.send_calls[0]["text"]
    assert decision.sensitive_private_message.message in dws.send_calls[1]["text"]
    assert decision.final_message not in dws.send_calls[1]["text"]


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
        status="ready_to_send",
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
    assert processing_after.attempts == 0
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


def test_startup_recovery_continues_locked_delivery_without_second_send(tmp_path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    dws = ConsumerDws()
    dws.verification_states = ["sent", "sent"]
    job_id = seed_consumer_job(store, dws)
    runner = FakeMeetingRunner(consumer_send_decision())

    # Finish analysis, then model an interrupted process after provider acceptance:
    # the durable provider receipt exists but the meeting job has not yet advanced.
    consume_meeting_alignment_jobs(store, dws, runner, now=NOW, limit=1, deliver=False)
    [claimed] = store.claim_ready_to_send_meeting_alignment_jobs(
        limit=1, now=NOW.isoformat()
    )
    meeting_alignment.deliver_meeting_alignment(
        consumer_send_decision(),
        meeting_alignment.read_meeting_source(
            dws,
            claimed.meeting_id,
            calendar_evidence=meeting_alignment.CalendarMeetingEvidence.model_validate(
                json.loads(claimed.source_json)["calendar_evidence"]
            ),
        ),
        dws,
        message_sender=meeting_alignment.ServiceMessageSender(store=store, dingtalk=dws),
        delivery_key=f"meeting-alignment:{claimed.id}:{claimed.meeting_id}",
    )
    assert len(dws.send_calls) == 1

    assert recover_meeting_alignment_jobs(store) == 1
    assert consume_meeting_alignment_jobs(store, dws, runner, now=NOW, limit=1) == 1

    recovered = store.get_meeting_alignment_job(job_id)
    assert recovered.status == "sent"
    assert len(dws.send_calls) == 1


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

    assert produce_meeting_alignment_jobs(store, dws, now=NOW) == 2
    malformed_job = store.get_meeting_alignment_job_by_meeting_id("minutes-bad")
    assert malformed_job is not None
    assert malformed_job.status == "skipped"
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
def test_producer_persists_conflicting_minutes_aliases_as_skipped(
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

    created = produce_meeting_alignment_jobs(store, dws, now=NOW)
    job = store.get_meeting_alignment_job_by_meeting_id("minutes-1")
    if target == "list":
        assert created == 0
        assert job is None
    else:
        assert created == 1
        assert job is not None
        assert job.status == "skipped"


def test_delivery_records_the_conversation_the_follow_up_reached():
    """The notification Derek clicks needs a conversation, and the send gives none.

    DingTalk answers with an openTaskId only, so the receipt used to carry no
    conversation id and the desktop notification had nothing to open.
    """
    dws = FakeDws()
    delivery = SimpleNamespace(
        send_result={"data": {"result": {"openTaskId": "task-1"}}},
        sensitive_private_delivery=None,
    )

    meeting_alignment._record_where_the_message_landed(dws, delivery)

    assert delivery.send_result["openConversationId"] == "cid-for-task-1"
    assert delivery.send_result["openMessageId"] == "msg-for-task-1"


def test_delivery_does_not_ask_twice_for_a_conversation_it_already_has():
    dws = FakeDws()
    delivery = SimpleNamespace(
        send_result={"openTaskId": "task-1", "openConversationId": "cid-known"},
        sensitive_private_delivery=None,
    )

    meeting_alignment._record_where_the_message_landed(dws, delivery)

    assert delivery.send_result["openConversationId"] == "cid-known"
    assert dws.__dict__.get("resolved") is None


def test_delivery_withdraws_the_follow_up_it_replaces():
    """The earlier follow-up is taken back only after the replacement is sent.

    Derek 2026-09-19: recall the old message before the second summary lands and
    a failure to produce the new one would leave the reader with nothing.
    """
    dws = FakeDws()
    previous = json.dumps({"send_result": {"data": {"result": {"openTaskId": "task-1"}}}})

    meeting_alignment._withdraw_superseded_follow_up(dws, previous)

    assert dws.__dict__.get("recalled") == ["task-1"]


def test_delivery_withdraws_nothing_on_a_first_follow_up():
    dws = FakeDws()

    meeting_alignment._withdraw_superseded_follow_up(dws, "")

    assert dws.__dict__.get("recalled") is None


def test_producer_attaches_a_later_recording_to_the_meeting_it_belongs_to(tmp_path):
    """The second recording joins the first meeting's job and reopens it.

    Derek 2026-09-18: merge the parts and summarise the whole meeting again,
    marked as a second pass -- not a separate follow-up, and not a delta.
    """
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    first = ended_meeting()
    first_end = datetime.fromisoformat(first["endTimeISO"])
    second = ended_meeting(
        meeting_id="minutes-2",
        start=(first_end + timedelta(minutes=20)).isoformat(),
        end=(first_end + timedelta(minutes=50)).isoformat(),
    )
    dws = FakeDws(
        minutes_pages={
            "": {"items": [first], "has_more": False, "next_token": ""}
        },
        info={"minutes-1": first},
    )
    produce_meeting_alignment_jobs(store, dws, now=NOW)
    original = store.get_meeting_alignment_job_by_meeting_id("minutes-1")
    assert original is not None

    dws.minutes_pages[""]["items"] = [first, second]
    dws.info["minutes-2"] = second
    produce_meeting_alignment_jobs(store, dws, now=NOW)

    reopened = store.get_meeting_alignment_job_by_meeting_id("minutes-1")
    assert json.loads(reopened.source_json)["extra_segment_ids"] == ["minutes-2"]
    assert reopened.status in {"pending", "waiting"}


@pytest.mark.parametrize(
    ("gap", "expected_jobs"),
    # Twenty minutes apart is one meeting recorded twice; three hours apart is two
    # meetings that happen to share a title, such as a recurring stand-up.
    [(timedelta(minutes=20), 1), (timedelta(hours=3), 2)],
)
def test_producer_treats_a_nearby_recording_of_the_same_title_as_one_meeting(
    tmp_path, gap, expected_jobs
):
    """A stopped and restarted recorder makes two provider ids for one meeting.

    Sending a follow-up per recording reaches the same people twice about the
    same meeting. Derek 2026-09-18: same title within two hours is one meeting;
    the gap decides, not the calendar day.
    """
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    first = ended_meeting()
    first_end = datetime.fromisoformat(first["endTimeISO"])
    second = ended_meeting(
        meeting_id="minutes-2",
        start=(first_end + gap).isoformat(),
        end=(first_end + gap + timedelta(minutes=30)).isoformat(),
    )
    dws = FakeDws(
        minutes_pages={
            "": {"items": [first, second], "has_more": False, "next_token": ""}
        },
        info={"minutes-1": first, "minutes-2": second},
    )

    produce_meeting_alignment_jobs(store, dws, now=NOW)

    second_job = store.get_meeting_alignment_job_by_meeting_id("minutes-2")
    assert second_job is not None
    joined = "already tracked" in (second_job.error or "")
    assert joined is (expected_jobs == 1)


@pytest.mark.parametrize("status", [4, 2, "processing", "unknown"])
def test_producer_keeps_meetings_whatever_status_the_provider_reports(tmp_path, status):
    """The provider's status vocabulary is not ours to gate on.

    DingTalk started reporting numeric codes on 2026-09-18; 2 and 4 both appear on
    recordings that have finished. Requiring the literal "ended" dropped every
    meeting without leaving a row, so the loss was invisible. A recording still in
    progress never reaches the list endpoint, so there is nothing to filter here.
    """
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

    assert produce_meeting_alignment_jobs(store, dws, now=NOW) == 1
    assert store.get_meeting_alignment_job_by_meeting_id("minutes-1") is not None


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
def test_producer_stops_on_repeated_pagination_cursor(
    tmp_path, pages_attribute, initial_page
):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    dws = FakeDws()
    setattr(dws, pages_attribute, {"": initial_page, "repeat": initial_page})

    assert produce_meeting_alignment_jobs(store, dws, now=NOW) == (
        0 if pages_attribute == "minutes_pages" else 1
    )
    if pages_attribute == "calendar_pages":
        job = store.get_meeting_alignment_job_by_meeting_id("minutes-1")
        assert job is not None
        assert job.status == "skipped"


def test_minutes_pagination_keeps_verified_pages_when_later_cursor_fails():
    meeting = ended_meeting()

    class CursorFailsDws:
        def __init__(self):
            self.calls = []

        def list_minutes_page(self, *, limit, cursor, start, end):
            self.calls.append(cursor)
            if cursor:
                raise DwsError("cursor business error")
            return {
                "items": [meeting],
                "has_more": True,
                "next_token": "unstable-cursor",
            }

    dws = CursorFailsDws()

    assert meeting_alignment._list_all_minutes(
        dws, start="2026-07-07T00:00:00+08:00",
        end="2026-07-14T00:00:00+08:00",
    ) == [meeting]
    assert dws.calls == ["", "unstable-cursor"]


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


def rejected_direct_decision() -> MeetingAlignmentDecision:
    """A business send addressed to an identity absent from the source."""
    payload = summary_decision().model_dump(mode="json")
    payload["target"] = {
        "kind": "direct",
        "conversation_id": "",
        "direct_user_id": "u-unverified",
        "title": "Unverified",
        "candidates": [],
    }
    return MeetingAlignmentDecision.model_validate(payload)


def test_a_rejected_target_is_kept_with_the_failure(tmp_path):
    """The failure has to say who the turn picked, not only which rule broke."""
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    dws = ConsumerDws()
    job_id = seed_consumer_job(store, dws)
    runner = FakeMeetingRunner(rejected_direct_decision())

    assert consume_meeting_alignment_jobs(store, dws, runner, now=NOW, limit=1) == 1

    job = store.get_meeting_alignment_job(job_id)
    assert job.status == "failed"
    assert json.loads(job.error)["kind"] == "meeting_target"
    assert job.target_kind == "direct"
    assert job.target_id == "u-unverified"
    assert job.target_title == "Unverified"
    assert json.loads(job.decision_json)["target"]["title"] == "Unverified"
