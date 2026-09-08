from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Event, Thread

import pytest

from app.dispatcher.adapters import (
    MeetingQueueAdapter,
    ReplyQueueAdapter,
    ScheduledTaskQueueAdapter,
    WorkSummaryQueueAdapter,
)
from app.dispatcher.models import DispatchEnvelope, QueueMetrics
from app.dispatcher.service import ConsumerDispatcher
from app.store import AutoReplyStore


NOW = datetime(2026, 9, 8, 12, 0, tzinfo=UTC)


def _store(tmp_path: Path) -> AutoReplyStore:
    return AutoReplyStore(tmp_path / "worker.sqlite3")


def _scheduled_run(store: AutoReplyStore):
    task = store.create_scheduled_task(
        name="Scheduled check",
        prompt="Check the configured source.",
        cron_expression="0 * * * * *",
        timezone_name="UTC",
        runtime_id="codex_oauth",
        runtime_options={},
        working_directory="/tmp",
        skill_refs=(),
        now=NOW,
    )
    return store.create_scheduled_task_run(
        task.id,
        trigger_kind="manual",
        scheduled_for=NOW,
        now=NOW,
    )


def _reply(store: AutoReplyStore, *, generation: str = "generation-7") -> None:
    assert store.enqueue_reply_task(
        conversation_id="cid",
        conversation_title="Conversation",
        single_chat=False,
        trigger_message_id="msg-1",
        trigger_create_time=NOW.isoformat(),
        trigger_sender="Derek",
        trigger_text="Please check this.",
        execution_generation=generation,
    )


def _meeting(store: AutoReplyStore) -> int:
    return store.upsert_meeting_alignment_job(
        meeting_id="meeting-1",
        title="Weekly review",
        source_json="{}",
        participants_json="[]",
        ended_at=(NOW - timedelta(minutes=20)).isoformat(),
        eligible_at=(NOW - timedelta(minutes=10)).isoformat(),
        status="pending",
    )


def _work_summary(store: AutoReplyStore) -> int:
    return store.enqueue_work_summary_input("reply_attempt", "task-1", "{}")


def test_dispatcher_lease_ledger_stores_only_claim_ownership(tmp_path: Path):
    store = _store(tmp_path)

    with store._connect() as db:
        columns = {
            row["name"]
            for row in db.execute("pragma table_info(dispatcher_claim_leases)")
        }

    assert columns == {
        "adapter_name",
        "source_id",
        "owner",
        "generation",
        "lease_expires_at",
        "created_at",
        "updated_at",
    }


def test_all_source_adapters_report_and_atomically_claim_due_facts(tmp_path: Path):
    store = _store(tmp_path)
    run = _scheduled_run(store)
    _reply(store)
    meeting_id = _meeting(store)
    work_id = _work_summary(store)
    adapters = (
        ScheduledTaskQueueAdapter(store),
        ReplyQueueAdapter(store),
        MeetingQueueAdapter(store),
        WorkSummaryQueueAdapter(store),
    )

    metrics = [adapter.metrics(NOW) for adapter in adapters]
    assert [item.pending for item in metrics] == [1, 1, 1, 1]
    assert [item.due for item in metrics] == [1, 1, 1, 1]
    assert all(item.oldest_available_at is not None for item in metrics)
    assert [item.running for item in metrics] == [0, 0, 0, 0]
    assert [item.latest_error for item in metrics] == ["", "", "", ""]
    claims = [
        adapter.claim(NOW, owner="dispatcher-a", lease=timedelta(minutes=5))
        for adapter in adapters
    ]

    assert [claim.source_id for claim in claims if claim] == [
        str(run.id),
        "1",
        str(meeting_id),
        str(work_id),
    ]
    assert claims[1] is not None
    assert claims[1].generation > 0
    assert [adapter.metrics(NOW).due for adapter in adapters] == [0, 0, 0, 0]
    assert all(
        adapter.claim(NOW, owner="dispatcher-b", lease=timedelta(minutes=5)) is None
        for adapter in adapters
    )


def test_scheduled_claim_is_recoverable_after_lease_expiry(tmp_path: Path):
    store = _store(tmp_path)
    _scheduled_run(store)
    adapter = ScheduledTaskQueueAdapter(store)

    first = adapter.claim(NOW, owner="dispatcher-a", lease=timedelta(seconds=1))
    assert first is not None
    assert adapter.claim(NOW, owner="dispatcher-b", lease=timedelta(seconds=1)) is None

    recovered = adapter.claim(
        NOW + timedelta(seconds=2),
        owner="dispatcher-b",
        lease=timedelta(seconds=1),
    )
    assert recovered == first


def test_legacy_source_leases_recover_with_fencing_and_monotonic_generation(
    tmp_path: Path,
):
    store = _store(tmp_path)
    _reply(store)
    _meeting(store)
    _work_summary(store)
    adapters = (
        ReplyQueueAdapter(store),
        MeetingQueueAdapter(store),
        WorkSummaryQueueAdapter(store),
    )

    for adapter in adapters:
        first = adapter.claim(NOW, owner="owner-a", lease=timedelta(seconds=1))
        assert first is not None
        assert adapter.claim(NOW, owner="owner-b", lease=timedelta(seconds=1)) is None
        second = adapter.claim(
            NOW + timedelta(seconds=2),
            owner="owner-b",
            lease=timedelta(seconds=1),
        )
        assert second is not None
        assert second.source_id == first.source_id
        assert second.generation > first.generation

        expired_metrics = adapter.metrics(NOW + timedelta(seconds=4))
        assert expired_metrics.due == 1
        assert expired_metrics.running == 0

        with pytest.raises(ValueError, match="no longer owned"):
            adapter.release(first, owner="owner-a", now=NOW + timedelta(seconds=2))
        assert adapter.metrics(NOW + timedelta(seconds=2)).running == 1

        adapter.release(
            second,
            owner="owner-b",
            now=NOW + timedelta(seconds=2),
        )
        third = adapter.claim(
            NOW + timedelta(seconds=2),
            owner="owner-c",
            lease=timedelta(seconds=1),
        )
        assert third is not None
        assert third.generation > second.generation


def test_reply_source_cannot_be_claimed_twice_concurrently(tmp_path: Path):
    store = _store(tmp_path)
    _reply(store)
    adapter = ReplyQueueAdapter(store)
    start = Event()

    def claim(owner: str):
        assert start.wait(timeout=2)
        return adapter.claim(NOW, owner=owner, lease=timedelta(minutes=5))

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(claim, owner) for owner in ("one", "two")]
        start.set()
        claims = [future.result(timeout=2) for future in futures]

    assert sum(item is not None for item in claims) == 1


def test_work_summary_claim_uses_dispatcher_time_for_due_boundary(tmp_path: Path):
    store = _store(tmp_path)
    source_id = _work_summary(store)
    with store._connect() as db:
        db.execute(
            "update work_summary_inputs set available_at=? where id=?",
            ((NOW + timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S"), source_id),
        )
    adapter = WorkSummaryQueueAdapter(store)

    assert adapter.claim(NOW, owner="dispatcher-a", lease=timedelta(minutes=5)) is None
    assert (
        adapter.claim(
            NOW + timedelta(hours=1),
            owner="dispatcher-a",
            lease=timedelta(minutes=5),
        )
        is not None
    )


def test_release_returns_each_claim_to_its_fact_table_without_copying_payload(
    tmp_path: Path,
):
    store = _store(tmp_path)
    _scheduled_run(store)
    _reply(store)
    _meeting(store)
    _work_summary(store)
    adapters = (
        ScheduledTaskQueueAdapter(store),
        ReplyQueueAdapter(store),
        MeetingQueueAdapter(store),
        WorkSummaryQueueAdapter(store),
    )

    for adapter in adapters:
        envelope = adapter.claim(NOW, owner="dispatcher-a", lease=timedelta(seconds=1))
        assert envelope is not None
        adapter.release(
            envelope,
            owner="dispatcher-a",
            now=NOW,
        )
        assert adapter.metrics(NOW).due == 1


class _FakeAdapter:
    def __init__(self, name: str, count: int) -> None:
        self.name = name
        self.remaining = count
        self.claimed = 0
        self.released: list[DispatchEnvelope] = []

    def metrics(self, now: datetime) -> QueueMetrics:
        return QueueMetrics(
            pending=self.remaining,
            due=self.remaining,
            oldest_available_at=now if self.remaining else None,
            running=self.claimed,
            latest_error="",
        )

    def claim(
        self, now: datetime, *, owner: str, lease: timedelta
    ) -> DispatchEnvelope | None:
        if self.remaining == 0:
            return None
        self.remaining -= 1
        self.claimed += 1
        return DispatchEnvelope(
            adapter_name=self.name,
            source_id=f"{self.name}-{self.claimed}",
            available_at=now,
            priority=0,
            attempt=self.claimed,
            generation=self.claimed,
        )

    def release(
        self,
        envelope: DispatchEnvelope,
        *,
        owner: str,
        now: datetime,
    ) -> None:
        self.released.append(envelope)


class _RecordingExecutor:
    def __init__(self) -> None:
        self.submissions: list[tuple[object, DispatchEnvelope]] = []
        self.futures: list[Future[None]] = []

    def submit(self, fn, envelope: DispatchEnvelope) -> Future[None]:
        self.submissions.append((fn, envelope))
        future: Future[None] = Future()
        self.futures.append(future)
        return future


def test_dispatcher_is_round_robin_and_does_not_wait_for_long_consumers():
    first = _FakeAdapter("first", 2)
    second = _FakeAdapter("second", 2)
    executor = _RecordingExecutor()
    dispatcher = ConsumerDispatcher(
        adapters=(first, second),
        consumers={"first": lambda _item: None, "second": lambda _item: None},
        executors={"first": executor, "second": executor},
        max_in_flight={"first": 2, "second": 2},
        owner="dispatcher-a",
        lease=timedelta(minutes=5),
    )

    assert dispatcher.dispatch_available(NOW, limit=4) == 4
    assert [item.adapter_name for _, item in executor.submissions] == [
        "first",
        "second",
        "first",
        "second",
    ]


def test_dispatcher_releases_claim_when_worker_pool_rejects_submission():
    adapter = _FakeAdapter("reply", 1)

    class RejectingExecutor:
        def submit(self, *_args):
            raise RuntimeError("pool stopped")

    dispatcher = ConsumerDispatcher(
        adapters=(adapter,),
        consumers={"reply": lambda _item: None},
        executors={"reply": RejectingExecutor()},
        max_in_flight={"reply": 1},
        owner="dispatcher-a",
        lease=timedelta(minutes=5),
    )

    assert dispatcher.dispatch_available(NOW, limit=1) == 0
    assert [item.source_id for item in adapter.released] == ["reply-1"]


def test_event_wakes_dispatcher_before_bounded_fallback_wait():
    wake = Event()
    stop = Event()
    adapter = _FakeAdapter("scheduled", 0)
    executor = _RecordingExecutor()
    dispatcher = ConsumerDispatcher(
        adapters=(adapter,),
        consumers={"scheduled": lambda _item: None},
        executors={"scheduled": executor},
        max_in_flight={"scheduled": 1},
        owner="dispatcher-a",
        lease=timedelta(minutes=5),
        wake_event=wake,
        fallback_wait_seconds=10,
    )
    thread = Thread(target=dispatcher.run, kwargs={"stop_event": stop})
    thread.start()

    adapter.remaining = 1
    wake.set()
    deadline = Event()
    for _ in range(100):
        if executor.submissions:
            break
        deadline.wait(0.01)
    stop.set()
    wake.set()
    thread.join(timeout=2)

    assert [item.source_id for _, item in executor.submissions] == ["scheduled-1"]
    assert not thread.is_alive()


def test_empty_dispatch_does_not_create_user_visible_runs(tmp_path: Path):
    store = _store(tmp_path)
    adapter = ScheduledTaskQueueAdapter(store)
    executor = ThreadPoolExecutor(max_workers=1)
    dispatcher = ConsumerDispatcher(
        adapters=(adapter,),
        consumers={"scheduled": lambda _item: None},
        executors={"scheduled": executor},
        max_in_flight={"scheduled": 1},
        owner="dispatcher-a",
        lease=timedelta(minutes=5),
    )

    assert dispatcher.dispatch_available(NOW, limit=5) == 0
    with store._connect() as db:
        assert db.execute("select count(*) from scheduled_task_runs").fetchone()[0] == 0
    executor.shutdown()


def test_dispatcher_claims_scheduled_source_only_when_worker_capacity_is_available(
    tmp_path: Path,
):
    store = _store(tmp_path)
    first_run = _scheduled_run(store)
    adapter = ScheduledTaskQueueAdapter(store)
    executor = _RecordingExecutor()
    dispatcher = ConsumerDispatcher(
        adapters=(adapter,),
        consumers={"scheduled": lambda _item: None},
        executors={"scheduled": executor},
        max_in_flight={"scheduled": 1},
        owner="dispatcher-a",
        lease=timedelta(seconds=1),
    )

    assert dispatcher.dispatch_available(NOW, limit=2) == 1
    # The unresolved Future keeps the only slot occupied past the source lease,
    # so the expired source cannot be claimed a second time into a worker queue.
    assert dispatcher.dispatch_available(NOW + timedelta(seconds=2), limit=2) == 0
    assert len(executor.submissions) == 1

    store.finish_scheduled_task_dispatch(
        first_run.id,
        owner="dispatcher-a",
        status="failed",
        reason="capacity test",
        now=NOW,
    )
    store.create_scheduled_task_run(
        first_run.scheduled_task_id,
        trigger_kind="manual",
        scheduled_for=NOW + timedelta(seconds=2),
        now=NOW,
    )

    executor_future = executor.futures[0]
    executor_future.set_result(None)
    assert dispatcher.dispatch_available(NOW + timedelta(seconds=2), limit=2) == 1
