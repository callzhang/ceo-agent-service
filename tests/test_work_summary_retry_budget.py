from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta

import app.cli as cli
from app.dispatcher.adapters import WorkSummaryQueueAdapter
from app.dispatcher.service import ConsumerDispatcher
from app.store import AutoReplyStore, TASK_LOGICAL_RUN_SQL
from app.task_agent_session import TASK_AGENT_SESSION_SCOPE_ID


def test_shared_session_wait_does_not_spend_work_summary_attempts(tmp_path):
    store = AutoReplyStore(tmp_path / "busy.sqlite3")
    input_id = store.enqueue_work_summary_input("ai_minutes", "meeting", "{}")
    assert store.acquire_codex_session_lock(TASK_AGENT_SESSION_SCOPE_ID, "peer")
    for _ in range(5):
        work_input = store.claim_work_summary_inputs(
            limit=1, now="2099-01-01 00:00:00"
        )[0]
        assert cli._process_claimed_work_summary_input(store, None, work_input) is False
        current = store.get_work_summary_input(input_id)
        assert current.status == "pending"
        assert current.attempts == 0
        assert current.available_at
    store.release_codex_session_lock(TASK_AGENT_SESSION_SCOPE_ID, "peer")


def test_exhausted_pending_work_summary_does_not_enter_agent(tmp_path):
    store = AutoReplyStore(tmp_path / "exhausted.sqlite3")
    input_id = store.enqueue_work_summary_input("ai_minutes", "meeting", "{}")
    for _ in range(46):
        store.claim_work_summary_inputs(
            limit=1, now="2099-01-01 00:00:00"
        )[0]
        store.schedule_work_summary_input_retry(
            input_id, "previous processing failure", available_at=""
        )
    store.schedule_work_summary_input_retry(
        input_id, "previous processing failure", available_at="2099-01-01 00:00:00"
    )
    adapter = WorkSummaryQueueAdapter(store, owner_alive=lambda _pid: False)
    assert adapter.claim(datetime.now(UTC), owner="dispatcher", owner_pid=42,
                         lease=timedelta(minutes=5)) is None
    current = store.get_work_summary_input(input_id)
    assert current.status == "failed"
    assert current.error == "work_summary_retry_exhausted"
    assert current.attempts == 47
    assert store.claim_work_summary_inputs(limit=1) == []


def test_pre_agent_dispatch_failure_is_bounded_and_delayed(tmp_path):
    store = AutoReplyStore(tmp_path / "dispatch.sqlite3")
    input_id = store.enqueue_work_summary_input("ai_minutes", "meeting", "{}")
    adapter = WorkSummaryQueueAdapter(store, owner_alive=lambda _pid: False)

    def consume(_envelope, _guard):
        raise RuntimeError("session acquisition failed")

    with ThreadPoolExecutor(max_workers=1) as executor:
        dispatcher = ConsumerDispatcher(
            adapters=(adapter,), consumers={"work_summary": consume},
            executors={"work_summary": executor},
            max_in_flight={"work_summary": 1}, owner="dispatcher", owner_pid=42,
            lease=timedelta(minutes=5),
        )
        for attempt in range(1, cli.WORK_SUMMARY_TRANSIENT_RETRY_ATTEMPTS + 1):
            now = datetime.now(UTC) + timedelta(days=attempt)
            assert dispatcher.dispatch_available(now, limit=1) == 1
            assert dispatcher._completion_event.wait(timeout=10)
            dispatcher._completion_event.clear()
            current = store.get_work_summary_input(input_id)
            assert current.error == "session acquisition failed"
            assert current.attempts == attempt
            if attempt < cli.WORK_SUMMARY_TRANSIENT_RETRY_ATTEMPTS:
                assert current.status == "pending"
                assert datetime.fromisoformat(current.available_at).replace(tzinfo=UTC) > datetime.now(UTC)
            else:
                assert current.status == "failed"
                assert current.available_at == ""
        assert adapter.claim(
            datetime.now(UTC) + timedelta(days=10), owner="dispatcher",
            owner_pid=42, lease=timedelta(minutes=5),
        ) is None


def test_explicit_retry_after_root_cause_fix_gets_a_new_budget(tmp_path):
    store = AutoReplyStore(tmp_path / "manual.sqlite3")
    input_id = store.enqueue_work_summary_input("ai_minutes", "meeting", "{}")
    for _ in range(cli.WORK_SUMMARY_TRANSIENT_RETRY_ATTEMPTS):
        store.claim_work_summary_inputs(limit=1)
        run_id = store.claim_professional_run(input_id)
        store.finish_professional_run(run_id, status="failed", error="processing failure")
        store.mark_work_summary_input_failed(input_id, "processing failure")
        if _ < cli.WORK_SUMMARY_TRANSIENT_RETRY_ATTEMPTS - 1:
            store.schedule_work_summary_input_retry(input_id, "processing failure", available_at="")
    assert store.get_work_summary_input(input_id).attempts == cli.WORK_SUMMARY_TRANSIENT_RETRY_ATTEMPTS
    assert store.requeue_failed_work_summary_input(input_id, "root cause fixed")
    claimed = store.claim_work_summary_inputs(limit=1)[0]
    assert claimed.attempts == 1
    with store._connect() as db:
        history = db.execute(
            f"select status,error from ({TASK_LOGICAL_RUN_SQL}) where summary_input_id=?",
            (input_id,),
        ).fetchall()
    assert [tuple(row) for row in history] == [("failed", "processing failure")] * cli.WORK_SUMMARY_TRANSIENT_RETRY_ATTEMPTS
