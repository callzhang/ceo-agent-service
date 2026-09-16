from __future__ import annotations

import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.meeting_memory_write import (
    MEETING_MEMORY_WRITE_LEASE_SECONDS,
    enqueue_sent_meeting_memory_writes,
    meeting_memory_write_lease_seconds,
    meeting_memory_payload,
    process_meeting_memory_writes,
)
import app.meeting_memory_write as meeting_memory_write
from app.agent_runtime_router import RoutedCodexExecutionError
from app.store import AutoReplyStore


class _FakeRoutedExecution:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def execute(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(
            value=json.dumps(
                {
                    "status": "success",
                    "memory_id": "meeting-memory-7",
                    "retryable": False,
                    "source_code": "",
                    "detail": "",
                }
            )
        )


class _ActiveMemoryRuntime:
    def execute(self, **kwargs):
        raise RoutedCodexExecutionError("runtime_attempt_active")


class _InvalidMemoryResultRuntime:
    def __init__(self, failure_code: str) -> None:
        self.failure_code = failure_code

    def execute(self, **kwargs):
        raise RoutedCodexExecutionError(
            "runtime_execution_failed",
            "result_parse",
            failure_code=self.failure_code,
        )


class _UnexpectedMemoryRuntime:
    def execute(self, **kwargs):
        raise AttributeError("runtime output was missing source_code")


def _sent_job() -> SimpleNamespace:
    return SimpleNamespace(
        id=7,
        status="sent",
        meeting_id="minutes-7",
        title="经营复盘",
        ended_at="2026-09-14T09:30:00+08:00",
        updated_at="2026-09-14T10:00:00+08:00",
        final_message="【经营复盘结论】\n本周先完成客户验证。",
        source_json=json.dumps({"summary": "这是听记摘要"}, ensure_ascii=False),
        decision_json=json.dumps(
            {"derek_viewpoint": {"expressed_view": "这不是已发送文本"}},
            ensure_ascii=False,
        ),
    )


def _store_sent_job(store: AutoReplyStore, *, meeting_id: str = "minutes-7") -> int:
    job_id = store.upsert_meeting_alignment_job(
        meeting_id=meeting_id,
        title="经营复盘",
        source_json=json.dumps({"summary": "这是听记摘要"}, ensure_ascii=False),
        participants_json="[]",
        ended_at="2026-09-14T09:30:00+08:00",
        eligible_at="2026-09-14T09:40:00+08:00",
        status="pending",
    )
    store.update_meeting_alignment_job(
        job_id,
        status="sent",
        final_message="【经营复盘结论】\n本周先完成客户验证。",
    )
    return job_id


class _BlockingMemoryRuntime:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.active = 0
        self.max_active = 0
        self.workload_keys: list[str] = []

    def execute(self, **kwargs):
        with self._lock:
            self.active += 1
            self.max_active = max(self.max_active, self.active)
            self.workload_keys.append(str(kwargs["workload_key"]))
        try:
            time.sleep(0.04)
            return SimpleNamespace(
                value=json.dumps(
                    {
                        "status": "success",
                        "memory_id": f"memory-{kwargs['workload_key']}",
                        "retryable": False,
                        "source_code": "",
                        "detail": "",
                    }
                )
            )
        finally:
            with self._lock:
                self.active -= 1


def test_meeting_memory_payload_contains_only_delivered_conclusion() -> None:
    payload = meeting_memory_payload(_sent_job())

    assert payload["created_at"] == "2026-09-14T09:30:00+08:00"
    assert payload["source_description"] == "本周先完成客户验证"
    assert payload["data"].splitlines()[0] == "本周先完成客户验证"
    assert "本周先完成客户验证" in payload["data"]
    assert "听记摘要" not in payload["data"]
    assert "这不是已发送文本" not in payload["data"]
    assert payload["data"].endswith("[meeting-alignment:minutes-7]")


def test_meeting_memory_title_uses_first_substantive_conclusion() -> None:
    job = _sent_job()
    job.final_message = (
        "【会议跟进】投资人跟进话术（2026-07-17 05:30-05:50）\n\n"
        "我收一下刚才几个口径，后面按这个执行：\n\n"
        "1. 阳光保险今天这轮先按财务和公司价值沟通处理。\n\n"
        "2. 后续需要稳定运行本地服务。"
    )

    payload = meeting_memory_payload(job)

    assert payload["source_description"] == "阳光保险今天这轮先按财务和公司价值沟通处理"
    assert payload["data"].splitlines()[0] == "阳光保险今天这轮先按财务和公司价值沟通处理"


def test_meeting_memory_title_stops_after_the_first_conclusion_sentence() -> None:
    job = _sent_job()
    job.final_message = "【会议结论】\n\n1. 先完成客户验证。下周再扩大投入。"

    payload = meeting_memory_payload(job)

    assert payload["source_description"] == "先完成客户验证"


def test_meeting_memory_title_uses_structured_topic_titles_over_conclusions() -> None:
    job = _sent_job()
    job.final_message = (
        "【会议跟进】客户验证会\n\n"
        "1. 先完成客户验证。\n\n"
        "2. 再决定是否扩大投入。"
    )
    job.decision_json = json.dumps(
        {
            "topics": [
                {
                    "title": "客户验证与投入节奏",
                    "conclusion": "先用真实客户验证产品价值，再依据反馈决定投入与扩张节奏。",
                },
                {
                    "title": "验证结果进入经营决策",
                    "conclusion": "验证结果必须进入下一轮经营决策。",
                },
            ]
        },
        ensure_ascii=False,
    )

    payload = meeting_memory_payload(job)

    assert payload["source_description"] == "客户验证与投入节奏；验证结果进入经营决策"


def test_meeting_memory_title_summarizes_reported_ingest_topics() -> None:
    job = _sent_job()
    job.final_message = "【会议跟进】Friday日会\n\nrecipe、定价和项目进展需要闭环。"
    job.decision_json = json.dumps(
        {
            "topics": [
                {"title": "recipe 卡点升级机制", "conclusion": "算法问题解不了就升级。"},
                {"title": "定价讨论方法", "conclusion": "先做竞品调研再定价。"},
                {"title": "项目进展同步机制", "conclusion": "每天同步进展与风险。"},
            ]
        },
        ensure_ascii=False,
    )

    payload = meeting_memory_payload(job)

    assert payload["source_description"] == "recipe 卡点升级机制；定价讨论方法；项目进展同步机制"


def test_meeting_memory_title_uses_content_heading_before_feedback_link() -> None:
    job = _sent_job()
    job.final_message = (
        "【会议跟进】曾诗维 - 招聘专员 - 三面\n\n"
        "【招聘专员岗位面试跟进】本场已完成招聘画像校准。\n\n"
        "反馈：[👍 有帮助](https://example.test/feedback)"
    )
    job.decision_json = json.dumps({"topics": []}, ensure_ascii=False)

    payload = meeting_memory_payload(job)

    assert payload["source_description"] == "招聘专员岗位面试跟进"
    assert not payload["source_description"].startswith("反馈：")


def test_sent_meetings_are_queued_once_and_written_to_memory(tmp_path: Path) -> None:
    store = AutoReplyStore(tmp_path / "store.sqlite3")
    job_id = _store_sent_job(store)

    assert enqueue_sent_meeting_memory_writes(store) == 1
    assert enqueue_sent_meeting_memory_writes(store) == 0
    with store._connect() as db:
        event = db.execute(
            "select * from meeting_memory_write_events where meeting_job_id=?", (job_id,)
        ).fetchone()
    assert event is not None
    assert event["status"] == "pending"
    assert event["execution_generation"]
    assert event["payload_json"] == "{}"

    routed = _FakeRoutedExecution()
    processed = process_meeting_memory_writes(
        store,
        workspace=tmp_path,
        routed_execution=routed,
    )

    assert processed.processed == 1
    assert routed.calls[0]["workload_key"] == (
        f"meeting_memory_write_event:{event['id']}:"
        f"{event['execution_generation']}"
    )
    assert (
        'source_description set to the exact literal "本周先完成客户验证".'
    ) in routed.calls[0]["prompt"]
    with store._connect() as db:
        written = db.execute(
            "select status, memory_id from meeting_memory_write_events where id=?",
            (event["id"],),
        ).fetchone()
        meeting = db.execute(
            "select status from meeting_alignment_jobs where id=?", (job_id,)
        ).fetchone()
    assert dict(written) == {"status": "done", "memory_id": "meeting-memory-7"}
    assert meeting["status"] == "sent"


def test_meeting_memory_claims_are_disjoint_and_reclaim_only_expired_leases(
    tmp_path: Path,
) -> None:
    store = AutoReplyStore(tmp_path / "store.sqlite3")
    _store_sent_job(store)
    assert enqueue_sent_meeting_memory_writes(store) == 1
    now = "2026-09-15T10:00:00+00:00"

    first = store.claim_due_meeting_memory_write_events(
        now=now,
        limit=1,
        owner="worker-a",
        lease_seconds=30,
    )
    second = store.claim_due_meeting_memory_write_events(
        now=now,
        limit=1,
        owner="worker-b",
        lease_seconds=30,
    )
    reclaimed = store.claim_due_meeting_memory_write_events(
        now="2026-09-15T10:00:31+00:00",
        limit=1,
        owner="worker-b",
        lease_seconds=30,
    )

    assert len(first) == 1
    assert second == []
    assert [event.id for event in reclaimed] == [first[0].id]
    assert reclaimed[0].lease_owner == "worker-b"
    assert reclaimed[0].status == "processing"


def test_simultaneous_meeting_memory_claimers_cannot_claim_the_same_event(
    tmp_path: Path,
) -> None:
    store = AutoReplyStore(tmp_path / "store.sqlite3")
    _store_sent_job(store)
    assert enqueue_sent_meeting_memory_writes(store) == 1
    start = threading.Barrier(2)

    def claim(owner: str) -> list[int]:
        worker_store = AutoReplyStore(store.path)
        start.wait()
        return [
            event.id
            for event in worker_store.claim_due_meeting_memory_write_events(
                now="2026-09-15T10:00:00+00:00",
                limit=1,
                owner=owner,
                lease_seconds=30,
            )
        ]

    with ThreadPoolExecutor(max_workers=2) as executor:
        first, second = list(executor.map(claim, ("worker-a", "worker-b")))

    assert sorted(first + second) == [1]


def test_meeting_memory_terminal_transition_requires_the_current_lease_owner(
    tmp_path: Path,
) -> None:
    store = AutoReplyStore(tmp_path / "store.sqlite3")
    _store_sent_job(store)
    assert enqueue_sent_meeting_memory_writes(store) == 1
    event = store.claim_due_meeting_memory_write_events(
        now="2026-09-15T10:00:00+00:00",
        limit=1,
        owner="worker-a",
        lease_seconds=30,
    )[0]

    assert not store.complete_meeting_memory_write_event(
        event.id,
        owner="worker-b",
        memory_id="wrong-owner",
        now=datetime.fromisoformat("2026-09-15T10:00:01+00:00"),
    )
    assert store.complete_meeting_memory_write_event(
        event.id,
        owner="worker-a",
        memory_id="correct-owner",
        now=datetime.fromisoformat("2026-09-15T10:00:01+00:00"),
    )
    with store._connect() as db:
        row = db.execute(
            "select status, memory_id, lease_owner, lease_expires_at "
            "from meeting_memory_write_events where id=?",
            (event.id,),
        ).fetchone()
    assert dict(row) == {
        "status": "done",
        "memory_id": "correct-owner",
        "lease_owner": "",
        "lease_expires_at": "",
    }


@pytest.mark.parametrize("operation", ["complete", "retry", "fail"])
def test_expired_meeting_memory_owner_cannot_settle_an_event(
    tmp_path: Path,
    operation: str,
) -> None:
    store = AutoReplyStore(tmp_path / "store.sqlite3")
    _store_sent_job(store)
    assert enqueue_sent_meeting_memory_writes(store) == 1
    event = store.claim_due_meeting_memory_write_events(
        now="2026-09-15T10:00:00+00:00",
        limit=1,
        owner="expired-worker",
        lease_seconds=30,
    )[0]

    if operation == "complete":
        settled = store.complete_meeting_memory_write_event(
            event.id,
            owner="expired-worker",
            memory_id="late-memory",
            now=datetime.fromisoformat("2026-09-15T10:00:31+00:00"),
        )
    elif operation == "retry":
        settled = store.retry_meeting_memory_write_event(
            event.id,
            owner="expired-worker",
            error="late retry",
            available_at="2026-09-15T10:01:31+00:00",
            now=datetime.fromisoformat("2026-09-15T10:00:31+00:00"),
        )
    else:
        settled = store.fail_meeting_memory_write_event(
            event.id,
            owner="expired-worker",
            error="late failure",
            now=datetime.fromisoformat("2026-09-15T10:00:31+00:00"),
        )

    assert not settled
    with store._connect() as db:
        row = db.execute(
            "select status, lease_owner from meeting_memory_write_events where id=?",
            (event.id,),
        ).fetchone()
    assert dict(row) == {"status": "processing", "lease_owner": "expired-worker"}


def test_meeting_memory_lease_renewal_requires_the_live_current_owner(
    tmp_path: Path,
) -> None:
    store = AutoReplyStore(tmp_path / "store.sqlite3")
    _store_sent_job(store)
    assert enqueue_sent_meeting_memory_writes(store) == 1
    event = store.claim_due_meeting_memory_write_events(
        now="2026-09-15T10:00:00+00:00",
        limit=1,
        owner="worker-a",
        lease_seconds=30,
    )[0]

    assert not store.renew_meeting_memory_write_event_lease(
        event.id,
        lease_owner="worker-b",
        now=datetime.fromisoformat("2026-09-15T10:00:10+00:00"),
        lease_seconds=30,
    )
    assert store.renew_meeting_memory_write_event_lease(
        event.id,
        lease_owner="worker-a",
        now=datetime.fromisoformat("2026-09-15T10:00:10+00:00"),
        lease_seconds=30,
    )
    assert not store.renew_meeting_memory_write_event_lease(
        event.id,
        lease_owner="worker-a",
        now=datetime.fromisoformat("2026-09-15T10:00:41+00:00"),
        lease_seconds=30,
    )
    reclaimed = store.claim_due_meeting_memory_write_events(
        now="2026-09-15T10:00:41+00:00",
        limit=1,
        owner="worker-b",
        lease_seconds=30,
    )

    assert [claimed.id for claimed in reclaimed] == [event.id]


def test_meeting_memory_lease_covers_configured_runtime_timeout() -> None:
    assert meeting_memory_write_lease_seconds(1200, 900) == 2700
    assert meeting_memory_write_lease_seconds(3600, 60) == 3900
    assert meeting_memory_write_lease_seconds(60, 3600) == 3900


def test_meeting_memory_processing_uses_the_configured_long_runtime_lease(
    tmp_path: Path,
) -> None:
    store = AutoReplyStore(tmp_path / "store.sqlite3")
    _store_sent_job(store)
    current_time = datetime.fromisoformat("2026-09-15T10:00:00+00:00")

    def clock() -> datetime:
        return current_time

    class _LongRunningRuntime:
        def __init__(self) -> None:
            self.intruder_claim_count = -1

        def execute(self, **_kwargs):
            nonlocal current_time
            current_time += timedelta(seconds=3000)
            self.intruder_claim_count = len(
                store.claim_due_meeting_memory_write_events(
                    now=current_time.isoformat(),
                    limit=1,
                    owner="intruder",
                    lease_seconds=30,
                )
            )
            return SimpleNamespace(
                value=json.dumps(
                    {
                        "status": "success",
                        "memory_id": "long-runtime-memory",
                        "retryable": False,
                        "source_code": "",
                        "detail": "",
                    }
                )
            )

    runtime = _LongRunningRuntime()
    outcome = process_meeting_memory_writes(
        store,
        workspace=tmp_path,
        routed_execution=runtime,
        lease_seconds=meeting_memory_write_lease_seconds(3600, 60),
        clock=clock,
    )

    assert outcome.completed == 1
    assert outcome.lost_lease == 0
    assert runtime.intruder_claim_count == 0


def test_meeting_memory_lease_renewal_blocks_reclaim_during_a_long_write(
    tmp_path: Path,
) -> None:
    store = AutoReplyStore(tmp_path / "store.sqlite3")
    _store_sent_job(store)
    current_time = datetime.fromisoformat("2026-09-15T10:00:00+00:00")
    clock_lock = threading.Lock()
    renewed = threading.Event()
    renewal_results: list[bool] = []

    def clock() -> datetime:
        with clock_lock:
            return current_time

    def advance(seconds: int) -> None:
        nonlocal current_time
        with clock_lock:
            current_time += timedelta(seconds=seconds)

    def renewer(
        heartbeat_store: AutoReplyStore,
        event_id: int,
        owner: str,
        now: datetime,
        lease_seconds: int,
    ) -> bool:
        renewed_lease = heartbeat_store.renew_meeting_memory_write_event_lease(
            event_id,
            lease_owner=owner,
            now=now,
            lease_seconds=lease_seconds,
        )
        renewal_results.append(renewed_lease)
        renewed.set()
        return renewed_lease

    class _LongRunningRuntime:
        def __init__(self) -> None:
            self.intruder_claim_count = -1

        def execute(self, **_kwargs):
            advance(8)
            assert renewed.wait(timeout=1)
            advance(4)
            self.intruder_claim_count = len(
                store.claim_due_meeting_memory_write_events(
                    now=clock().isoformat(),
                    limit=1,
                    owner="intruder",
                    lease_seconds=10,
                )
            )
            return SimpleNamespace(
                value=json.dumps(
                    {
                        "status": "success",
                        "memory_id": "renewed-memory",
                        "retryable": False,
                        "source_code": "",
                        "detail": "",
                    }
                )
            )

    runtime = _LongRunningRuntime()
    outcome = process_meeting_memory_writes(
        store,
        workspace=tmp_path,
        routed_execution=runtime,
        lease_seconds=10,
        lease_heartbeat_interval_seconds=0.01,
        lease_renewer=renewer,
        clock=clock,
    )

    assert outcome.completed == 1
    assert outcome.lost_lease == 0
    assert runtime.intruder_claim_count == 0
    assert renewal_results == [True]
    time.sleep(0.03)
    assert renewal_results == [True]
    assert not any(
        thread.name.startswith("meeting-memory-lease-")
        for thread in threading.enumerate()
    )


def test_meeting_memory_worker_does_not_settle_after_lease_renewal_fails(
    tmp_path: Path,
) -> None:
    store = AutoReplyStore(tmp_path / "store.sqlite3")
    _store_sent_job(store)
    renewal_failed = threading.Event()

    def failed_renewer(
        _heartbeat_store: AutoReplyStore,
        _event_id: int,
        _owner: str,
        _now: datetime,
        _lease_seconds: int,
    ) -> bool:
        renewal_failed.set()
        return False

    class _WaitForRenewalRuntime:
        def execute(self, **_kwargs):
            assert renewal_failed.wait(timeout=1)
            return SimpleNamespace(
                value=json.dumps(
                    {
                        "status": "success",
                        "memory_id": "must-not-settle",
                        "retryable": False,
                        "source_code": "",
                        "detail": "",
                    }
                )
            )

    outcome = process_meeting_memory_writes(
        store,
        workspace=tmp_path,
        routed_execution=_WaitForRenewalRuntime(),
        lease_seconds=30,
        lease_heartbeat_interval_seconds=0.01,
        lease_renewer=failed_renewer,
    )

    assert outcome.completed == 0
    assert outcome.lost_lease == 1
    with store._connect() as db:
        row = db.execute(
            "select status, memory_id from meeting_memory_write_events"
        ).fetchone()
    assert dict(row) == {"status": "processing", "memory_id": ""}


def test_idle_meeting_memory_enqueue_does_not_mutate_rows_or_sequence(
    tmp_path: Path,
) -> None:
    store = AutoReplyStore(tmp_path / "store.sqlite3")
    _store_sent_job(store)

    assert enqueue_sent_meeting_memory_writes(store) == 1
    with store._connect() as db:
        before = db.execute(
            "select id, meeting_job_id, execution_generation, created_at, updated_at "
            "from meeting_memory_write_events"
        ).fetchall()
        sequence_before = db.execute(
            "select seq from sqlite_sequence where name='meeting_memory_write_events'"
        ).fetchone()["seq"]

    assert enqueue_sent_meeting_memory_writes(store) == 0
    assert enqueue_sent_meeting_memory_writes(store) == 0
    with store._connect() as db:
        after = db.execute(
            "select id, meeting_job_id, execution_generation, created_at, updated_at "
            "from meeting_memory_write_events"
        ).fetchall()
        sequence_after = db.execute(
            "select seq from sqlite_sequence where name='meeting_memory_write_events'"
        ).fetchone()["seq"]

    assert [dict(row) for row in after] == [dict(row) for row in before]
    assert sequence_after == sequence_before


def test_meeting_memory_processing_uses_bounded_parallelism_and_returns_outcome(
    tmp_path: Path,
) -> None:
    store = AutoReplyStore(tmp_path / "store.sqlite3")
    for index in range(3):
        _store_sent_job(store, meeting_id=f"minutes-parallel-{index}")
    runtime = _BlockingMemoryRuntime()

    outcome = process_meeting_memory_writes(
        store,
        workspace=tmp_path,
        routed_execution=runtime,
        limit=3,
        concurrency=2,
    )

    assert outcome.claimed == 3
    assert outcome.completed == 3
    assert outcome.retried == 0
    assert outcome.failed == 0
    assert outcome.processed == 3
    assert runtime.max_active == 2
    assert len(runtime.workload_keys) == 3
    assert len(set(runtime.workload_keys)) == 3


def test_meeting_memory_does_not_claim_a_later_wave_before_it_can_start(
    tmp_path: Path,
) -> None:
    store = AutoReplyStore(tmp_path / "store.sqlite3")
    for index in range(3):
        _store_sent_job(store, meeting_id=f"minutes-wave-{index}")

    class _InspectingRuntime:
        def __init__(self) -> None:
            self.statuses_while_running: list[list[str]] = []

        def execute(self, **kwargs):
            with store._connect() as db:
                self.statuses_while_running.append(
                    [
                        str(row["status"])
                        for row in db.execute(
                            "select status from meeting_memory_write_events order by id"
                        )
                    ]
                )
            return SimpleNamespace(
                value=json.dumps(
                    {
                        "status": "success",
                        "memory_id": f"memory-{kwargs['workload_key']}",
                        "retryable": False,
                        "source_code": "",
                        "detail": "",
                    }
                )
            )

    runtime = _InspectingRuntime()
    outcome = process_meeting_memory_writes(
        store,
        workspace=tmp_path,
        routed_execution=runtime,
        limit=3,
        concurrency=1,
    )

    assert outcome.completed == 3
    assert runtime.statuses_while_running[0] == ["processing", "pending", "pending"]


def test_later_meeting_memory_wave_leases_from_its_actual_start_time(
    tmp_path: Path,
) -> None:
    store = AutoReplyStore(tmp_path / "store.sqlite3")
    for index in range(2):
        _store_sent_job(store, meeting_id=f"minutes-lease-wave-{index}")
    current_time = datetime.fromisoformat("2026-09-15T10:00:00+00:00")

    def clock() -> datetime:
        return current_time

    class _AdvancingRuntime:
        def __init__(self) -> None:
            self.calls = 0
            self.second_wave_lease = ""
            self.intruder_claim_count = -1

        def execute(self, **kwargs):
            nonlocal current_time
            self.calls += 1
            if self.calls == 1:
                current_time += timedelta(seconds=MEETING_MEMORY_WRITE_LEASE_SECONDS - 1)
            else:
                with store._connect() as db:
                    self.second_wave_lease = str(
                        db.execute(
                            "select lease_expires_at from meeting_memory_write_events "
                            "where status='processing'"
                        ).fetchone()["lease_expires_at"]
                    )
                self.intruder_claim_count = len(
                    store.claim_due_meeting_memory_write_events(
                        now=current_time.isoformat(),
                        limit=1,
                        owner="intruder",
                        lease_seconds=30,
                    )
                )
            return SimpleNamespace(
                value=json.dumps(
                    {
                        "status": "success",
                        "memory_id": f"memory-{kwargs['workload_key']}",
                        "retryable": False,
                        "source_code": "",
                        "detail": "",
                    }
                )
            )

    runtime = _AdvancingRuntime()
    outcome = process_meeting_memory_writes(
        store,
        workspace=tmp_path,
        routed_execution=runtime,
        clock=clock,
        limit=2,
        concurrency=1,
    )

    assert outcome.completed == 2
    assert outcome.lost_lease == 0
    assert runtime.second_wave_lease == "2026-09-15T11:29:59+00:00"
    assert runtime.intruder_claim_count == 0


def test_meeting_memory_retry_delay_starts_when_the_worker_finishes(
    tmp_path: Path,
) -> None:
    store = AutoReplyStore(tmp_path / "store.sqlite3")
    _store_sent_job(store)
    current_time = datetime.fromisoformat("2026-09-15T10:00:00+00:00")

    def clock() -> datetime:
        return current_time

    class _LateRetryRuntime:
        def execute(self, **kwargs):
            nonlocal current_time
            current_time += timedelta(seconds=120)
            raise RoutedCodexExecutionError("runtime_attempt_active")

    outcome = process_meeting_memory_writes(
        store,
        workspace=tmp_path,
        routed_execution=_LateRetryRuntime(),
        clock=clock,
    )

    assert outcome.retried == 1
    with store._connect() as db:
        available_at = str(
            db.execute(
                "select available_at from meeting_memory_write_events"
            ).fetchone()["available_at"]
        )
    assert available_at == "2026-09-15T10:03:00+00:00"


def test_failed_meeting_memory_write_can_be_requeued_only_for_sent_conclusion(
    tmp_path: Path,
) -> None:
    store = AutoReplyStore(tmp_path / "store.sqlite3")
    job_id = _store_sent_job(store)
    assert enqueue_sent_meeting_memory_writes(store) == 1
    with store._connect() as db:
        created_event = db.execute(
            "select id, execution_generation from meeting_memory_write_events "
            "where meeting_job_id=?",
            (job_id,),
        ).fetchone()
    assert created_event is not None
    event_id = int(created_event["id"])
    original_generation = str(created_event["execution_generation"])

    claimed = store.claim_due_meeting_memory_write_events(
        now="2026-09-15T10:00:00+00:00",
        limit=1,
        owner="test-requeue",
        lease_seconds=30,
    )
    assert [event.id for event in claimed] == [event_id]
    assert store.fail_meeting_memory_write_event(
        event_id,
        owner="test-requeue",
        error="provider configuration failed",
        now=datetime.fromisoformat("2026-09-15T10:00:01+00:00"),
    )

    assert store.requeue_failed_meeting_memory_write_event(
        event_id,
        reason="provider configuration repaired",
    )
    assert not store.requeue_failed_meeting_memory_write_event(
        event_id,
        reason="duplicate recovery",
    )
    with store._connect() as db:
        event = db.execute(
            "select status, attempts, error, available_at, execution_generation "
            "from meeting_memory_write_events where id=?",
            (event_id,),
        ).fetchone()
    assert event["status"] == "pending"
    assert event["attempts"] == 1
    assert event["error"] == "provider configuration repaired"
    assert event["available_at"] == ""
    assert event["execution_generation"]
    assert event["execution_generation"] != original_generation


def test_active_meeting_memory_runtime_defers_instead_of_failing(tmp_path: Path) -> None:
    store = AutoReplyStore(tmp_path / "store.sqlite3")
    job_id = _store_sent_job(store)
    assert enqueue_sent_meeting_memory_writes(store) == 1

    assert process_meeting_memory_writes(
        store,
        workspace=tmp_path,
        routed_execution=_ActiveMemoryRuntime(),
    ).processed == 1

    with store._connect() as db:
        event = db.execute(
            "select status, attempts, error, available_at from meeting_memory_write_events "
            "where meeting_job_id=?",
            (job_id,),
        ).fetchone()
    assert event is not None
    assert event["status"] == "pending"
    assert event["attempts"] == 1
    assert event["error"].startswith("runtime_attempt_active:")
    assert event["available_at"]


@pytest.mark.parametrize(
    "failure_code",
    ["runtime_result_invalid", "friday_runtime_result_invalid"],
)
def test_invalid_memory_result_defers_instead_of_failing(
    tmp_path: Path, failure_code: str
) -> None:
    store = AutoReplyStore(tmp_path / "store.sqlite3")
    job_id = _store_sent_job(store)
    assert enqueue_sent_meeting_memory_writes(store) == 1

    assert process_meeting_memory_writes(
        store,
        workspace=tmp_path,
        routed_execution=_InvalidMemoryResultRuntime(failure_code),
    ).processed == 1

    with store._connect() as db:
        event = db.execute(
            "select status, attempts, error, available_at from meeting_memory_write_events "
            "where meeting_job_id=?",
            (job_id,),
        ).fetchone()
    assert event is not None
    assert event["status"] == "pending"
    assert event["attempts"] == 1
    assert event["error"].startswith(f"{failure_code}:")
    assert event["available_at"]


def test_unexpected_memory_runtime_error_defers_one_event(tmp_path: Path) -> None:
    store = AutoReplyStore(tmp_path / "store.sqlite3")
    job_id = _store_sent_job(store)
    assert enqueue_sent_meeting_memory_writes(store) == 1

    assert process_meeting_memory_writes(
        store,
        workspace=tmp_path,
        routed_execution=_UnexpectedMemoryRuntime(),
    ).processed == 1

    with store._connect() as db:
        event = db.execute(
            "select status, attempts, error, available_at from meeting_memory_write_events "
            "where meeting_job_id=?",
            (job_id,),
        ).fetchone()
    assert event is not None
    assert event["status"] == "pending"
    assert event["attempts"] == 1
    assert event["error"] == (
        "meeting_memory_runtime_error: AttributeError: "
        "runtime output was missing source_code"
    )
    assert event["available_at"]


@pytest.mark.parametrize("initial_status", ["pending", "failed"])
def test_unsettled_meeting_memory_write_can_be_closed_from_verified_memory_readback(
    tmp_path: Path, initial_status: str
) -> None:
    store = AutoReplyStore(tmp_path / "store.sqlite3")
    job_id = _store_sent_job(store)
    assert enqueue_sent_meeting_memory_writes(store) == 1
    with store._connect() as db:
        event_id = int(
            db.execute(
                "select id from meeting_memory_write_events where meeting_job_id=?",
                (job_id,),
            ).fetchone()["id"]
        )
    if initial_status == "failed":
        claimed = store.claim_due_meeting_memory_write_events(
            now="2026-09-15T10:00:00+00:00",
            limit=1,
            owner="test-reconcile",
            lease_seconds=30,
        )
        assert [event.id for event in claimed] == [event_id]
        assert store.fail_meeting_memory_write_event(
            event_id,
            owner="test-reconcile",
            error="result parser failed",
            now=datetime.fromisoformat("2026-09-15T10:00:01+00:00"),
        )

    assert store.reconcile_failed_meeting_memory_write_event(
        event_id,
        memory_id="verified-memory-7",
    )
    assert not store.reconcile_failed_meeting_memory_write_event(
        event_id,
        memory_id="verified-memory-7",
    )
    with store._connect() as db:
        event = db.execute(
            "select status, attempts, error, memory_id from meeting_memory_write_events "
            "where id=?",
            (event_id,),
        ).fetchone()
    assert dict(event) == {
        "status": "done",
        "attempts": 1 if initial_status == "failed" else 0,
        "error": "",
        "memory_id": "verified-memory-7",
    }


def test_a_retryable_dependency_that_never_recovers_stops_at_the_ceiling(
    tmp_path: Path,
) -> None:
    """An unauthorized MCP dependency must end as a visible failure, not churn."""
    store = AutoReplyStore(tmp_path / "store.sqlite3")
    _store_sent_job(store)
    assert enqueue_sent_meeting_memory_writes(store) == 1
    with store._connect() as db:
        db.execute(
            "update meeting_memory_write_events set attempts=?",
            (meeting_memory_write.MEETING_MEMORY_WRITE_MAX_ATTEMPTS - 1,),
        )

    class _NeverRecoveringDependency:
        def execute(self, **kwargs):
            raise RoutedCodexExecutionError("runtime_attempt_active")

    outcome = process_meeting_memory_writes(
        store,
        workspace=tmp_path,
        routed_execution=_NeverRecoveringDependency(),
        clock=lambda: datetime.fromisoformat("2026-09-16T11:00:00+00:00"),
    )

    assert outcome.retried == 0
    with store._connect() as db:
        row = db.execute(
            "select status, attempts, error, available_at "
            "from meeting_memory_write_events"
        ).fetchone()
    assert row["status"] == "failed"
    assert row["available_at"] == ""
    assert "meeting_memory_retry_exhausted" in str(row["error"])
    assert "runtime_attempt_active" in str(row["error"])


def test_a_retryable_dependency_still_retries_below_the_ceiling(
    tmp_path: Path,
) -> None:
    store = AutoReplyStore(tmp_path / "store.sqlite3")
    _store_sent_job(store)
    assert enqueue_sent_meeting_memory_writes(store) == 1

    class _TransientDependency:
        def execute(self, **kwargs):
            raise RoutedCodexExecutionError("runtime_attempt_active")

    outcome = process_meeting_memory_writes(
        store,
        workspace=tmp_path,
        routed_execution=_TransientDependency(),
        clock=lambda: datetime.fromisoformat("2026-09-16T11:00:00+00:00"),
    )

    assert outcome.retried == 1
    with store._connect() as db:
        row = db.execute(
            "select status, attempts from meeting_memory_write_events"
        ).fetchone()
    assert row["status"] == "pending"
    assert row["attempts"] == 1
