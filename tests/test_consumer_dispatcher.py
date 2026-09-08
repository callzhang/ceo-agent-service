from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Event, Lock, Thread, enumerate as enumerate_threads
from types import SimpleNamespace

import pytest

import app.dispatcher.adapters as dispatcher_adapters
import app.dispatcher.service as dispatcher_service
from app.dispatcher.adapters import (
    MeetingQueueAdapter,
    OkrReviewQueueAdapter,
    ReplyQueueAdapter,
    ScheduledTaskQueueAdapter,
    WorkSummaryQueueAdapter,
)
from app.dispatcher.models import ClaimGuard, DispatchEnvelope, QueueMetrics
from app.dispatcher.service import ConsumerDispatcher
from app.meeting_alignment import consume_claimed_meeting_alignment_job
from app.store import AutoReplyStore
from app.worker import DingTalkAutoReplyWorker


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


def test_reply_dispatcher_claims_only_dingtalk_and_wechat_channels(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    for channel in ("email", "dingtalk", "wechat"):
        assert store.enqueue_reply_task(
            conversation_id=f"{channel}-cid",
            conversation_title=channel,
            single_chat=True,
            trigger_message_id=f"{channel}-message",
            trigger_create_time=NOW.isoformat(),
            trigger_sender="Derek",
            trigger_text="Check",
            channel=channel,
        )
    adapter = ReplyQueueAdapter(store, owner_alive=lambda _pid: False)

    first = adapter.claim(
        NOW, owner="dispatcher", owner_pid=42, lease=timedelta(minutes=5)
    )
    second = adapter.claim(
        NOW, owner="dispatcher", owner_pid=42, lease=timedelta(minutes=5)
    )
    third = adapter.claim(
        NOW, owner="dispatcher", owner_pid=42, lease=timedelta(minutes=5)
    )

    assert first is not None and second is not None
    assert {
        store.get_reply_task(int(first.source_id)).channel,
        store.get_reply_task(int(second.source_id)).channel,
    } == {"dingtalk", "wechat"}
    assert third is None


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


def _okr_review(store: AutoReplyStore, *, index: int = 1) -> int:
    return store.create_okr_review_request(
        conversation_id=f"okr-cid-{index}",
        conversation_title=f"OKR {index}",
        trigger_message_id=f"okr-message-{index}",
        trigger_sender="Derek",
        trigger_sender_user_id="derek",
        trigger_text="Review this OKR.",
        period_label="2026 Q3",
        period_start="2026-07-01",
        period_end="2026-09-30",
        okr_source_json="{}",
    )


def _todo_outbox(store: AutoReplyStore, *, index: int = 1) -> int:
    store.enqueue_task_todo_sync_outbox(
        operation_key=f"task-agent:{index}:todo:{index}:create",
        work_todo_id=index,
        operation="create",
    )
    return int(store.list_task_todo_sync_outbox()[-1]["id"])


def test_task_todo_outbox_adapter_claims_due_failed_work_without_new_input(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    source_id = _todo_outbox(store)
    with store._connect() as db:
        db.execute(
            "update task_todo_sync_outbox set status='failed', attempt_count=1, "
            "next_attempt_at=? where id=?",
            ((NOW - timedelta(seconds=1)).strftime("%Y-%m-%d %H:%M:%S"), source_id),
        )
    adapter = dispatcher_adapters.TaskTodoSyncOutboxQueueAdapter(store)

    envelope = adapter.claim(
        NOW,
        owner="dispatcher-a",
        owner_pid=101,
        lease=timedelta(minutes=5),
    )

    assert envelope is not None
    assert envelope.source_id == str(source_id)
    assert envelope.attempt == 2
    assert adapter.metrics(NOW).running == 1


def test_task_todo_outbox_adapter_fences_owner_and_generation(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _todo_outbox(store)
    adapter = dispatcher_adapters.TaskTodoSyncOutboxQueueAdapter(
        store, owner_alive=lambda _pid: False
    )
    first = adapter.claim(
        NOW,
        owner="dispatcher-a",
        owner_pid=101,
        lease=timedelta(minutes=5),
    )
    assert first is not None

    assert (
        adapter.claim(
            NOW,
            owner="dispatcher-b",
            owner_pid=202,
            lease=timedelta(minutes=5),
        )
        is None
    )
    with pytest.raises(ValueError, match="no longer owned"):
        adapter.release(first, owner="dispatcher-b", now=NOW)


def test_task_todo_outbox_dispatcher_processes_due_retry_once(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    source_id = _todo_outbox(store)
    with store._connect() as db:
        db.execute(
            "update task_todo_sync_outbox set status='failed', attempt_count=1, "
            "next_attempt_at=? where id=?",
            ((NOW - timedelta(seconds=1)).strftime("%Y-%m-%d %H:%M:%S"), source_id),
        )
    wake = Event()
    adapter = dispatcher_adapters.TaskTodoSyncOutboxQueueAdapter(store)
    processed: list[str] = []
    executor = ThreadPoolExecutor(max_workers=1)
    def consume(envelope, guard):
        processed.append(envelope.source_id)
        store.finish_task_todo_sync_outbox(
            outbox_id=int(envelope.source_id),
            owner=guard.token.owner,
            status="completed",
        )
        guard.finish_source(NOW, status="completed")

    dispatcher = ConsumerDispatcher(
        adapters=(adapter,),
        consumers={"task_todo_sync_outbox": consume},
        executors={"task_todo_sync_outbox": executor},
        max_in_flight={"task_todo_sync_outbox": 1},
        owner="dispatcher-a",
        owner_pid=101,
        lease=timedelta(minutes=5),
        wake_event=wake,
    )

    assert dispatcher.dispatch_available(NOW, limit=2) == 1
    for _ in range(100):
        if store.get_task_todo_sync_outbox(source_id)["status"] == "completed":
            break
        Event().wait(0.01)
    assert processed == [str(source_id)]
    assert store.get_task_todo_sync_outbox(source_id)["status"] == "completed"
    assert dispatcher.dispatch_available(NOW, limit=2) == 0
    executor.shutdown()


def test_task_todo_outbox_enqueue_wake_is_not_lost_after_empty_scan(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    wake = Event()
    stop = Event()
    processed = Event()
    adapter = dispatcher_adapters.TaskTodoSyncOutboxQueueAdapter(store)
    executor = ThreadPoolExecutor(max_workers=1)

    def consume(envelope, guard):
        item = store.get_task_todo_sync_outbox(int(envelope.source_id))
        assert item is not None
        store.finish_task_todo_sync_outbox(
            outbox_id=int(envelope.source_id),
            owner=guard.token.owner,
            status="completed",
        )
        guard.finish_source(datetime.now(UTC), status="completed")
        processed.set()

    dispatcher = ConsumerDispatcher(
        adapters=(adapter,),
        consumers={"task_todo_sync_outbox": consume},
        executors={"task_todo_sync_outbox": executor},
        max_in_flight={"task_todo_sync_outbox": 1},
        owner="dispatcher-a",
        lease=timedelta(minutes=5),
        wake_event=wake,
        fallback_wait_seconds=30,
    )
    thread = Thread(target=dispatcher.run, kwargs={"stop_event": stop})
    thread.start()

    source_id = _todo_outbox(store)
    wake.set()

    assert processed.wait(timeout=2)
    stop.set()
    wake.set()
    thread.join(timeout=2)
    assert store.get_task_todo_sync_outbox(source_id)["status"] == "completed"
    assert not thread.is_alive()
    executor.shutdown()


def _two_due_sources(store: AutoReplyStore, adapter_name: str):
    return _many_due_sources(store, adapter_name, count=2)


def _many_due_sources(
    store: AutoReplyStore, adapter_name: str, *, count: int
) -> tuple[type, tuple[str, ...]]:
    if adapter_name == "scheduled":
        first = _scheduled_run(store)
        ids = [str(first.id)]
        for _ in range(count - 1):
            run = store.create_scheduled_task_run(
                first.scheduled_task_id,
                trigger_kind="manual",
                scheduled_for=NOW,
                now=NOW,
            )
            ids.append(str(run.id))
        return ScheduledTaskQueueAdapter, tuple(ids)
    if adapter_name == "reply":
        ids = []
        for index in range(count):
            assert store.enqueue_reply_task(
                conversation_id=f"cid-{index}",
                conversation_title=f"Conversation {index}",
                single_chat=False,
                trigger_message_id=f"msg-{index}",
                trigger_create_time=NOW.isoformat(),
                trigger_sender="Derek",
                trigger_text="Please check this.",
                execution_generation=f"generation-{index}",
            )
            ids.append(str(index + 1))
        return ReplyQueueAdapter, tuple(ids)
    if adapter_name == "meeting":
        ids = []
        for index in range(count):
            source_id = store.upsert_meeting_alignment_job(
                meeting_id=f"meeting-{index}",
                title=f"Review {index}",
                source_json="{}",
                participants_json="[]",
                ended_at=(NOW - timedelta(minutes=20)).isoformat(),
                eligible_at=(NOW - timedelta(minutes=10)).isoformat(),
                status="pending",
            )
            ids.append(str(source_id))
        return MeetingQueueAdapter, tuple(ids)
    if adapter_name == "okr_review":
        return OkrReviewQueueAdapter, tuple(
            str(_okr_review(store, index=index)) for index in range(count)
        )
    if adapter_name == "task_todo_sync_outbox":
        return dispatcher_adapters.TaskTodoSyncOutboxQueueAdapter, tuple(
            str(_todo_outbox(store, index=index + 1)) for index in range(count)
        )
    ids = tuple(
        str(store.enqueue_work_summary_input("reply_attempt", f"task-{index}", "{}"))
        for index in range(count)
    )
    return WorkSummaryQueueAdapter, ids


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
        "owner_pid",
        "generation",
        "lease_expires_at",
        "terminal_at",
        "last_error",
        "created_at",
        "updated_at",
    }


def test_reply_single_item_boundary_processes_preclaimed_task_without_scanning():
    worker = object.__new__(DingTalkAutoReplyWorker)
    task = SimpleNamespace(
        id=7,
        status="processing",
        conversation_id="cid",
        conversation_title="Title",
        single_chat=False,
    )
    seen: list[int] = []
    worker._process_queued_task = lambda _conversation, item: (
        seen.append(item.id) or True
    )

    assert worker.process_claimed_reply_task(task) is True
    assert seen == [7]


def test_meeting_single_item_boundary_processes_preclaimed_job_without_reclaim(
    tmp_path: Path,
    monkeypatch,
):
    store = _store(tmp_path)
    _meeting(store)
    [job] = store.claim_meeting_alignment_jobs(1, NOW.isoformat())
    seen: list[int] = []
    monkeypatch.setattr(
        "app.meeting_alignment._analyze_meeting_job",
        lambda _store, _dws, _runner, item, **_kwargs: seen.append(item.id),
    )
    monkeypatch.setattr(
        store,
        "claim_meeting_alignment_jobs",
        lambda *_args, **_kwargs: pytest.fail("single-item handler must not claim"),
    )

    consume_claimed_meeting_alignment_job(
        store,
        object(),
        object(),
        job,
        now=NOW,
    )

    assert seen == [job.id]


def test_all_source_adapters_report_and_atomically_claim_due_facts(tmp_path: Path):
    store = _store(tmp_path)
    run = _scheduled_run(store)
    _reply(store)
    meeting_id = _meeting(store)
    work_id = _work_summary(store)
    okr_id = _okr_review(store)
    adapters = (
        ScheduledTaskQueueAdapter(store),
        ReplyQueueAdapter(store),
        MeetingQueueAdapter(store),
        WorkSummaryQueueAdapter(store),
        OkrReviewQueueAdapter(store),
    )

    metrics = [adapter.metrics(NOW) for adapter in adapters]
    assert [item.pending for item in metrics] == [1, 1, 1, 1, 1]
    assert [item.due for item in metrics] == [1, 1, 1, 1, 1]
    assert all(item.oldest_available_at is not None for item in metrics)
    assert [item.running for item in metrics] == [0, 0, 0, 0, 0]
    assert [item.latest_error for item in metrics] == ["", "", "", "", ""]
    claims = [
        adapter.claim(NOW, owner="dispatcher-a", lease=timedelta(minutes=5))
        for adapter in adapters
    ]

    assert [claim.source_id for claim in claims if claim] == [
        str(run.id),
        "1",
        str(meeting_id),
        str(work_id),
        str(okr_id),
    ]
    assert claims[1] is not None
    assert claims[1].generation > 0
    assert [adapter.metrics(NOW).due for adapter in adapters] == [0, 0, 0, 0, 0]
    assert all(
        adapter.claim(NOW, owner="dispatcher-b", lease=timedelta(minutes=5)) is None
        for adapter in adapters
    )


def test_scheduled_claim_is_recoverable_after_lease_expiry(tmp_path: Path):
    store = _store(tmp_path)
    _scheduled_run(store)
    adapter = ScheduledTaskQueueAdapter(store, owner_alive=lambda _pid: False)

    first = adapter.claim(NOW, owner="dispatcher-a", lease=timedelta(seconds=1))
    assert first is not None
    assert adapter.claim(NOW, owner="dispatcher-b", lease=timedelta(seconds=1)) is None

    recovered = adapter.claim(
        NOW + timedelta(seconds=2),
        owner="dispatcher-b",
        lease=timedelta(seconds=2),
    )
    assert recovered.source_id == first.source_id
    assert recovered.generation > first.generation


def test_legacy_source_leases_recover_with_fencing_and_monotonic_generation(
    tmp_path: Path,
):
    store = _store(tmp_path)
    _reply(store)
    _meeting(store)
    _work_summary(store)
    adapters = (
        ReplyQueueAdapter(store, owner_alive=lambda _pid: False),
        MeetingQueueAdapter(store, owner_alive=lambda _pid: False),
        WorkSummaryQueueAdapter(store, owner_alive=lambda _pid: False),
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

        with pytest.raises(ValueError, match="no longer owned"):
            adapter.renew(
                first,
                owner="owner-a",
                now=NOW + timedelta(seconds=2),
                lease=timedelta(seconds=2),
            )

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


@pytest.mark.parametrize(
    "adapter_name",
    (
        "scheduled",
        "reply",
        "meeting",
        "work_summary",
        "okr_review",
        "task_todo_sync_outbox",
    ),
)
def test_claim_skips_expired_head_owned_by_live_process(
    tmp_path: Path, adapter_name: str
):
    store = _store(tmp_path)
    adapter_type, source_ids = _two_due_sources(store, adapter_name)
    adapter = adapter_type(store, owner_alive=lambda pid: pid == 101)

    first = adapter.claim(
        NOW,
        owner="owner-a",
        owner_pid=101,
        lease=timedelta(seconds=1),
    )
    assert first is not None
    assert first.source_id == source_ids[0]

    second = adapter.claim(
        NOW + timedelta(seconds=2),
        owner="owner-b",
        owner_pid=202,
        lease=timedelta(seconds=5),
    )

    assert second is not None
    assert second.source_id == source_ids[1]
    if adapter_name == "task_todo_sync_outbox":
        first_source = store.get_task_todo_sync_outbox(int(source_ids[0]))
        assert first_source is not None
        assert first_source["status"] == "running"
        assert first_source["lease_owner"] == "owner-a"


@pytest.mark.parametrize(
    "adapter_name",
    (
        "scheduled",
        "reply",
        "meeting",
        "work_summary",
        "okr_review",
        "task_todo_sync_outbox",
    ),
)
def test_claim_scans_past_full_page_of_live_protected_sources(
    tmp_path: Path, adapter_name: str
):
    store = _store(tmp_path)
    adapter_type, source_ids = _many_due_sources(store, adapter_name, count=33)
    adapter = adapter_type(store, owner_alive=lambda pid: pid == 101)

    protected = []
    for index in range(32):
        envelope = adapter.claim(
            NOW,
            owner=f"owner-{index}",
            owner_pid=101,
            lease=timedelta(seconds=1),
        )
        assert envelope is not None
        protected.append(envelope.source_id)
    assert tuple(protected) == source_ids[:32]

    available = adapter.claim(
        NOW + timedelta(seconds=2),
        owner="owner-b",
        owner_pid=202,
        lease=timedelta(seconds=5),
    )

    assert available is not None
    assert available.source_id == source_ids[32]


def test_claim_guard_rejects_assertion_and_completion_after_reclaim(tmp_path: Path):
    store = _store(tmp_path)
    _reply(store)
    adapter = ReplyQueueAdapter(store, owner_alive=lambda _pid: False)
    first = adapter.claim(
        NOW,
        owner="owner-a",
        owner_pid=101,
        lease=timedelta(seconds=1),
    )
    assert first is not None
    guard = ClaimGuard(adapter=adapter, envelope=first, owner="owner-a")

    second = adapter.claim(
        NOW + timedelta(seconds=2),
        owner="owner-b",
        owner_pid=202,
        lease=timedelta(seconds=5),
    )
    assert second is not None

    with pytest.raises(ValueError, match="no longer owned"):
        guard.assert_current(NOW + timedelta(seconds=2))
    with pytest.raises(ValueError, match="no longer owned"):
        guard.complete(NOW + timedelta(seconds=2))


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


def test_okr_review_source_cannot_be_claimed_twice_concurrently(tmp_path: Path):
    store = _store(tmp_path)
    request_id = _okr_review(store)
    adapter = OkrReviewQueueAdapter(store)
    start = Event()

    def claim(owner: str):
        assert start.wait(timeout=2)
        return adapter.claim(NOW, owner=owner, lease=timedelta(minutes=5))

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(claim, owner) for owner in ("one", "two")]
        start.set()
        claims = [future.result(timeout=2) for future in futures]

    assert sum(item is not None for item in claims) == 1
    assert store.get_okr_review_request(request_id).status == "processing"


def test_task_todo_outbox_source_cannot_be_claimed_twice_concurrently(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    source_id = _todo_outbox(store)
    adapter = dispatcher_adapters.TaskTodoSyncOutboxQueueAdapter(store)
    start = Event()

    def claim(owner: str):
        assert start.wait(timeout=2)
        return adapter.claim(NOW, owner=owner, lease=timedelta(minutes=5))

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(claim, owner) for owner in ("one", "two")]
        start.set()
        claims = [future.result(timeout=2) for future in futures]

    assert sum(item is not None for item in claims) == 1
    assert store.get_task_todo_sync_outbox(source_id)["status"] == "running"


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
    _okr_review(store)
    adapters = (
        ScheduledTaskQueueAdapter(store),
        ReplyQueueAdapter(store),
        MeetingQueueAdapter(store),
        WorkSummaryQueueAdapter(store),
        OkrReviewQueueAdapter(store),
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
        self,
        now: datetime,
        *,
        owner: str,
        owner_pid: int | None = None,
        lease: timedelta,
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

    def renew(
        self,
        envelope: DispatchEnvelope,
        *,
        owner: str,
        now: datetime,
        lease: timedelta,
    ) -> None:
        return None

    def assert_current(self, envelope, *, owner, now):
        return None

    def complete(self, envelope, *, owner, now):
        return None

    def record_lease_error(self, envelope, *, owner, error, now):
        return None


class _RecordingExecutor:
    def __init__(self) -> None:
        self.submissions: list[tuple[object, DispatchEnvelope]] = []
        self.futures: list[Future[None]] = []

    def submit(self, fn, envelope: DispatchEnvelope, guard) -> Future[None]:
        self.submissions.append((fn, envelope))
        future: Future[None] = Future()
        self.futures.append(future)
        return future


def test_adapter_worker_pools_do_not_let_one_blocked_adapter_starve_another():
    first = _FakeAdapter("first", 1)
    second = _FakeAdapter("task_todo_sync_outbox", 1)
    first_started = Event()
    second_started = Event()
    release_first = Event()

    def consume_first(_envelope, _guard):
        first_started.set()
        assert release_first.wait(timeout=2)

    def consume_second(_envelope, _guard):
        second_started.set()

    pools = dispatcher_service.AdapterWorkerPools(
        (first.name, second.name),
        workers_per_adapter=1,
        thread_name_prefix="test-isolated-dispatcher",
    )
    dispatcher = ConsumerDispatcher(
        adapters=(first, second),
        consumers={"first": consume_first, "task_todo_sync_outbox": consume_second},
        executors=pools.executors,
        max_in_flight=pools.max_in_flight,
        owner="dispatcher-a",
        lease=timedelta(minutes=5),
    )

    try:
        assert dispatcher.dispatch_available(NOW, limit=2) == 2
        assert first_started.wait(timeout=1)
        assert second_started.wait(timeout=1)
    finally:
        release_first.set()
        pools.shutdown()


def test_adapter_worker_pool_capacity_prevents_claiming_more_than_its_workers():
    adapter = _FakeAdapter("scheduled", 4)
    release = Event()
    both_started = Event()
    lock = Lock()
    running = 0
    peak_running = 0

    def consume(_envelope, _guard):
        nonlocal running, peak_running
        with lock:
            running += 1
            peak_running = max(peak_running, running)
            if running == 2:
                both_started.set()
        assert release.wait(timeout=2)
        with lock:
            running -= 1

    pools = dispatcher_service.AdapterWorkerPools(
        (adapter.name,),
        workers_per_adapter=2,
        thread_name_prefix="test-capacity-dispatcher",
    )
    dispatcher = ConsumerDispatcher(
        adapters=(adapter,),
        consumers={adapter.name: consume},
        executors=pools.executors,
        max_in_flight=pools.max_in_flight,
        owner="dispatcher-a",
        lease=timedelta(minutes=5),
    )

    try:
        assert dispatcher.dispatch_available(NOW, limit=4) == 2
        assert both_started.wait(timeout=1)
        assert adapter.claimed == 2
        assert adapter.remaining == 2
        assert peak_running == 2
    finally:
        release.set()
        pools.shutdown()


def test_adapter_worker_pools_shutdown_waits_and_leaves_no_worker_threads():
    prefix = "test-shutdown-dispatcher"
    task_started = Event()
    allow_finish = Event()
    task_finished = Event()
    pools = dispatcher_service.AdapterWorkerPools(
        ("scheduled", "reply"),
        workers_per_adapter=1,
        thread_name_prefix=prefix,
    )

    def work():
        task_started.set()
        assert allow_finish.wait(timeout=2)
        task_finished.set()

    pools.executors["scheduled"].submit(work)
    assert task_started.wait(timeout=1)
    releaser = Thread(target=lambda: (Event().wait(0.05), allow_finish.set()))
    releaser.start()

    pools.shutdown()
    releaser.join(timeout=1)

    assert task_finished.is_set()
    assert not [
        thread
        for thread in enumerate_threads()
        if thread.name.startswith(prefix) and thread.is_alive()
    ]


def test_dispatcher_is_round_robin_and_does_not_wait_for_long_consumers():
    first = _FakeAdapter("first", 2)
    second = _FakeAdapter("second", 2)
    executor = _RecordingExecutor()
    dispatcher = ConsumerDispatcher(
        adapters=(first, second),
        consumers={
            "first": lambda _item, _guard: None,
            "second": lambda _item, _guard: None,
        },
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
        consumers={"reply": lambda _item, _guard: None},
        executors={"reply": RejectingExecutor()},
        max_in_flight={"reply": 1},
        owner="dispatcher-a",
        lease=timedelta(minutes=5),
    )

    assert dispatcher.dispatch_available(NOW, limit=1) == 0
    assert [item.source_id for item in adapter.released] == ["reply-1"]


def test_renew_failure_keeps_dispatching_other_adapter_and_reports_error(
    tmp_path: Path,
):
    store = _store(tmp_path)
    _reply(store)
    _scheduled_run(store)
    reply = ReplyQueueAdapter(store, owner_alive=lambda _pid: False)
    scheduled = ScheduledTaskQueueAdapter(store, owner_alive=lambda _pid: False)
    executor = _RecordingExecutor()
    dispatcher = ConsumerDispatcher(
        adapters=(reply, scheduled),
        consumers={
            "reply": lambda _item, _guard: None,
            "scheduled": lambda _item, _guard: None,
        },
        executors={"reply": executor, "scheduled": executor},
        max_in_flight={"reply": 1, "scheduled": 1},
        owner="owner-a",
        owner_pid=101,
        lease=timedelta(seconds=2),
    )

    assert dispatcher.dispatch_available(NOW, limit=1) == 1
    reply_envelope = executor.submissions[0][1]
    with store._connect() as db:
        db.execute(
            "update dispatcher_claim_leases set owner='owner-b', owner_pid=202, "
            "generation=generation+1, lease_expires_at=? "
            "where adapter_name='reply' and source_id=?",
            (
                (NOW + timedelta(seconds=10)).strftime("%Y-%m-%d %H:%M:%S"),
                reply_envelope.source_id,
            ),
        )

    assert dispatcher.dispatch_available(NOW + timedelta(seconds=1), limit=1) == 1
    assert [item.adapter_name for _, item in executor.submissions] == [
        "reply",
        "scheduled",
    ]
    assert "no longer owned" in reply.metrics(NOW + timedelta(seconds=1)).latest_error


def test_completion_prunes_terminal_ledger_without_removing_active_generation(
    tmp_path: Path,
):
    store = _store(tmp_path)
    _reply(store)
    adapter = ReplyQueueAdapter(store)
    envelope = adapter.claim(
        NOW,
        owner="owner-a",
        owner_pid=101,
        lease=timedelta(minutes=5),
    )
    assert envelope is not None
    now_text = NOW.strftime("%Y-%m-%d %H:%M:%S")
    with store._connect() as db:
        db.executemany(
            "insert into dispatcher_claim_leases "
            "(adapter_name, source_id, owner, owner_pid, generation, "
            "lease_expires_at, terminal_at, last_error, created_at, updated_at) "
            "values ('meeting', ?, '', 0, ?, '', ?, '', ?, ?)",
            (
                (f"terminal-{index}", index + 1, now_text, now_text, now_text)
                for index in range(10005)
            ),
        )
        db.execute(
            "insert into dispatcher_claim_leases "
            "(adapter_name, source_id, owner, owner_pid, generation, "
            "lease_expires_at, terminal_at, last_error, created_at, updated_at) "
            "values ('work_summary', 'active', 'owner-b', 202, 77, ?, '', '', ?, ?)",
            (
                (NOW + timedelta(minutes=5)).strftime("%Y-%m-%d %H:%M:%S"),
                now_text,
                now_text,
            ),
        )

    ClaimGuard(adapter=adapter, envelope=envelope, owner="owner-a").complete(NOW)

    with store._connect() as db:
        terminal_count = db.execute(
            "select count(*) from dispatcher_claim_leases where terminal_at<>''"
        ).fetchone()[0]
        active = db.execute(
            "select owner, generation, terminal_at from dispatcher_claim_leases "
            "where adapter_name='work_summary' and source_id='active'"
        ).fetchone()
    assert terminal_count == 10000
    assert tuple(active) == ("owner-b", 77, "")


def test_event_wakes_dispatcher_before_bounded_fallback_wait():
    wake = Event()
    stop = Event()
    adapter = _FakeAdapter("scheduled", 0)
    executor = _RecordingExecutor()
    dispatcher = ConsumerDispatcher(
        adapters=(adapter,),
        consumers={"scheduled": lambda _item, _guard: None},
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
        consumers={"scheduled": lambda _item, _guard: None},
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
        consumers={"scheduled": lambda _item, _guard: None},
        executors={"scheduled": executor},
        max_in_flight={"scheduled": 1},
        owner="dispatcher-a",
        lease=timedelta(seconds=2),
    )

    assert dispatcher.dispatch_available(NOW, limit=2) == 1
    # The unresolved Future keeps the only slot occupied and heartbeats its
    # source claim, so it cannot be enqueued a second time.
    assert dispatcher.dispatch_available(NOW + timedelta(seconds=1), limit=2) == 0
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


def test_dispatcher_heartbeats_in_flight_scheduled_claim_across_two_dispatchers(
    tmp_path: Path,
):
    store = _store(tmp_path)
    _scheduled_run(store)
    alive = {101: True}

    def owner_alive(pid: int) -> bool:
        return alive.get(pid, False)

    adapter_a = ScheduledTaskQueueAdapter(store, owner_alive=owner_alive)
    adapter_b = ScheduledTaskQueueAdapter(store, owner_alive=owner_alive)
    executor = _RecordingExecutor()
    dispatcher_a = ConsumerDispatcher(
        adapters=(adapter_a,),
        consumers={"scheduled": lambda _item, _guard: None},
        executors={"scheduled": executor},
        max_in_flight={"scheduled": 1},
        owner="owner-a",
        owner_pid=101,
        lease=timedelta(seconds=2),
    )

    assert dispatcher_a.dispatch_available(NOW, limit=1) == 1
    dispatcher_a.renew_in_flight(NOW + timedelta(seconds=1))
    assert (
        adapter_b.claim(
            NOW + timedelta(seconds=2, microseconds=500_000),
            owner="owner-b",
            lease=timedelta(seconds=2),
        )
        is None
    )

    assert (
        adapter_b.claim(
            NOW + timedelta(seconds=4),
            owner="owner-b",
            owner_pid=202,
            lease=timedelta(seconds=2),
        )
        is None
    )
    alive[101] = False
    recovered = adapter_b.claim(
        NOW + timedelta(seconds=4),
        owner="owner-b",
        owner_pid=202,
        lease=timedelta(seconds=2),
    )
    assert recovered is not None
    with pytest.raises(ValueError, match="no longer owned"):
        adapter_a.renew(
            executor.submissions[0][1],
            owner="owner-a",
            now=NOW + timedelta(seconds=4),
            lease=timedelta(seconds=2),
        )
    with pytest.raises(ValueError, match="no longer owned"):
        adapter_a.release(
            executor.submissions[0][1],
            owner="owner-a",
            now=NOW + timedelta(seconds=4),
        )


def test_event_set_after_empty_scan_is_not_lost_before_wait():
    wake = Event()
    stop = Event()
    scan_entered = Event()
    producer_done = Event()

    class InterleavedAdapter(_FakeAdapter):
        def claim(self, now, *, owner, owner_pid=None, lease):
            result = super().claim(now, owner=owner, owner_pid=owner_pid, lease=lease)
            scan_entered.set()
            assert producer_done.wait(timeout=2)
            return result

    adapter = InterleavedAdapter("scheduled", 0)
    executor = _RecordingExecutor()
    dispatcher = ConsumerDispatcher(
        adapters=(adapter,),
        consumers={"scheduled": lambda _item, _guard: None},
        executors={"scheduled": executor},
        max_in_flight={"scheduled": 1},
        owner="owner-a",
        lease=timedelta(seconds=2),
        wake_event=wake,
        fallback_wait_seconds=30,
    )
    thread = Thread(target=dispatcher.run, kwargs={"stop_event": stop})
    thread.start()
    assert scan_entered.wait(timeout=2)
    adapter.remaining = 1
    wake.set()
    producer_done.set()

    for _ in range(100):
        if executor.submissions:
            break
        Event().wait(0.01)
    stop.set()
    wake.set()
    thread.join(timeout=2)

    assert len(executor.submissions) == 1
    assert not thread.is_alive()
