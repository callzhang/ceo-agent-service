"""Real admission exclusion, across SQLite transactions and process lifetimes."""

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Event
import subprocess
import sys

import pytest

from app.store import AutoReplyStore
from app.dispatcher.adapters import ReplyQueueAdapter

NOW = datetime(2026, 10, 20, 22, tzinfo=UTC)


def enqueue(store, key="message"):
    assert store.enqueue_reply_task(
        conversation_id=key,
        conversation_title=key,
        single_chat=True,
        trigger_message_id=key,
        trigger_create_time=NOW.isoformat(),
        trigger_sender="Sender",
        trigger_text="request",
    )


def claim(store):
    return ReplyQueueAdapter(store).claim(
        NOW, owner="worker", owner_pid=12345, lease=timedelta(minutes=5)
    )


def test_drain_blocks_new_claims_but_preserves_intake_and_queued_work(tmp_path):
    from app.deployment_drain import deployment_drain

    store = AutoReplyStore(tmp_path / "worker.db")
    enqueue(store)
    with deployment_drain(store.path):
        assert claim(store) is None
        enqueue(store, "arrives-while-draining")
        assert claim(store) is None
    assert claim(store) is not None


def test_active_claim_renewal_and_release_continue_during_drain(tmp_path):
    from app.deployment_drain import deployment_drain

    store = AutoReplyStore(tmp_path / "worker.db")
    enqueue(store)
    adapter = ReplyQueueAdapter(store)
    envelope = claim(store)
    with deployment_drain(store.path):
        adapter.renew(envelope, owner="worker", now=NOW, lease=timedelta(minutes=5))
        adapter.release(envelope, owner="worker", now=NOW)
        assert claim(store) is None
    assert claim(store) is not None


def test_deploy_waits_for_an_admitted_transaction_to_commit(tmp_path):
    from app.deployment_drain import deployment_drain, claim_connection

    store = AutoReplyStore(tmp_path / "worker.db")
    admitted = Event()
    commit = Event()
    drained = Event()

    def writer():
        with claim_connection(store, immediate=True) as db:
            assert db is not None
            admitted.set()
            assert commit.wait(5)
            db.execute("update tasks set id=id where 0")

    def deployer():
        with deployment_drain(store.path):
            drained.set()

    with ThreadPoolExecutor(max_workers=2) as pool:
        a = pool.submit(writer)
        assert admitted.wait(5)
        b = pool.submit(deployer)
        assert not drained.wait(0.05)
        commit.set()
        a.result()
        b.result()
    assert drained.is_set()


def test_cancel_releases_admission_lock(tmp_path):
    from app.deployment_drain import deployment_drain

    store = AutoReplyStore(tmp_path / "worker.db")
    enqueue(store)
    with pytest.raises(KeyboardInterrupt):
        with deployment_drain(store.path):
            assert claim(store) is None
            raise KeyboardInterrupt
    assert claim(store) is not None


def test_crashed_deployer_does_not_leave_a_pause_flag(tmp_path):
    store = AutoReplyStore(tmp_path / "worker.db")
    enqueue(store)
    script = "from app.deployment_drain import deployment_drain; import sys; from pathlib import Path\nwith deployment_drain(Path(sys.argv[1])):\n print('held',flush=True)\n sys.stdin.read()\n"
    p = subprocess.Popen(
        [sys.executable, "-c", script, str(store.path)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
    )
    try:
        assert p.stdout.readline().strip() == "held"
        assert claim(store) is None
    finally:
        p.terminate()
        p.wait(timeout=5)
    assert claim(store) is not None


def test_workbench_claim_is_paused_but_new_user_turn_is_saved(tmp_path):
    from app.deployment_drain import deployment_drain
    from app.workbench.store import WorkbenchStore

    store = WorkbenchStore(tmp_path / "worker.db")
    task = store.create_task(title="Inspect", runtime_kind="codex")
    with deployment_drain(store.path):
        turn = store.create_turn(
            task.id, user_text="please inspect", client_request_id="one"
        )
        assert store.claim_next_turn(owner="web") is None
        assert store.get_turn(turn.id).status == "queued"
    assert store.claim_next_turn(owner="web").id == turn.id


def test_email_classifier_and_provider_action_claims_pause(tmp_path):
    from app.deployment_drain import deployment_drain
    from tests.test_email_store import _create_action_with_attempts
    from app.email_task_adapter import EmailClassificationTaskAdapter
    from app.task12_storage import insert_task12

    store, action_id = _create_action_with_attempts(tmp_path / "worker.db", ())
    with store._connect() as db:
        insert_task12(
            db,
            "email_agent_classification_tasks",
            dict(
                task_id="classifier",
                channel="email",
                stable_message_identity="message",
                status="pending",
                input_json="{}",
            ),
        )
    adapter = EmailClassificationTaskAdapter(store)
    with deployment_drain(store.path):
        assert adapter.claim_next(owner="email") is None
        assert (
            store.claim_direct_action(action_id=action_id, claimed_at=NOW.isoformat())
            is None
        )
        assert store.claim_next_direct_action(claimed_at=NOW.isoformat()) is None
    assert adapter.claim_next(owner="email") is not None
    assert (
        store.claim_direct_action(action_id=action_id, claimed_at=NOW.isoformat())
        is not None
    )


def test_updater_pauses_claims_before_wait_and_until_health(tmp_path):
    from app.repository_updater import RepositoryUpdater
    from tests.test_repository_updater import fixture_repo, operation, StateStore, git

    store = AutoReplyStore(tmp_path / "worker.db")
    enqueue(store)
    local, _ = fixture_repo(tmp_path)
    op = operation(local)
    phases = []

    def phase(name):
        assert claim(store) is None
        phases.append(name)

    updater = RepositoryUpdater(
        local,
        StateStore(),
        database_path=store.path,
        wait_for_quiet=lambda: phase("quiet"),
        stop=lambda: phase("stop"),
        dependency_sync=lambda: phase("build"),
        verification=lambda: phase("verify"),
        restart=lambda: phase("restart"),
        health=lambda: phase("health") or True,
    )
    updater.execute(op)
    assert phases == ["quiet", "stop", "build", "verify", "restart", "health"]
    assert git(local, "rev-parse", "HEAD") == op.target_commit
    assert claim(store) is not None


@pytest.mark.parametrize("error", [KeyboardInterrupt, RuntimeError])
def test_updater_wait_failure_resumes_admission_without_touching_service(
    tmp_path, error
):
    from app.repository_updater import RepositoryUpdater
    from tests.test_repository_updater import fixture_repo, operation, StateStore, git

    store = AutoReplyStore(tmp_path / "worker.db")
    enqueue(store)
    local, _ = fixture_repo(tmp_path)
    op = operation(local)
    stopped = []

    def wait():
        assert claim(store) is None
        raise error("cancel waiting")

    updater = RepositoryUpdater(
        local,
        StateStore(),
        database_path=store.path,
        wait_for_quiet=wait,
        stop=lambda: stopped.append(True),
    )
    with pytest.raises(error):
        updater.execute(op)
    assert not stopped
    assert git(local, "rev-parse", "HEAD") == op.original_commit
    assert claim(store) is not None


def test_restart_only_uses_the_same_pause_boundary(tmp_path, monkeypatch):
    import app.deploy as deploy
    from tests.test_repository_updater import fixture_repo

    store = AutoReplyStore(tmp_path / "worker.db")
    enqueue(store)
    local, _ = fixture_repo(tmp_path)
    monkeypatch.setattr(deploy, "service_root", lambda: local)

    def blocked(*args):
        assert claim(store) is None

    monkeypatch.setattr(deploy, "wait_until_quiet", blocked)
    monkeypatch.setattr(deploy, "wait_for_health", lambda: blocked() or True)
    assert deploy.restart_only(store.path, restart=blocked) == "restarted"
    assert claim(store) is not None


def test_independent_claims_count_before_any_native_runtime_starts(tmp_path):
    from app.repository_updater import in_flight_work
    from app.workbench.store import WorkbenchStore
    from tests.test_email_store import _create_action_with_attempts
    from app.email_task_adapter import EmailClassificationTaskAdapter
    from app.task12_storage import insert_task12

    store, action_id = _create_action_with_attempts(tmp_path / "worker.db", ())
    assert in_flight_work(store.path) == 0
    with store._connect() as db:
        insert_task12(
            db,
            "email_agent_classification_tasks",
            dict(
                task_id="classifier",
                channel="email",
                stable_message_identity="message",
                status="pending",
                input_json="{}",
            ),
        )
    assert EmailClassificationTaskAdapter(store).claim_next(owner="email") is not None
    assert in_flight_work(store.path) == 1
    wb = WorkbenchStore(store.path)
    task = wb.create_task(title="Inspect", runtime_kind="codex")
    wb.create_turn(task.id, user_text="check", client_request_id="turn")
    assert wb.claim_next_turn(owner="web") is not None
    assert in_flight_work(store.path) == 2
    assert (
        store.claim_direct_action(
            action_id=action_id, claimed_at=datetime.now(UTC).isoformat()
        )
        is not None
    )
    assert in_flight_work(store.path) == 3


def test_meeting_delivery_and_memory_claims_pause_and_stay_pending(tmp_path):
    from app.deployment_drain import deployment_drain
    from app.meeting_memory_write import enqueue_sent_meeting_memory_writes
    from tests.test_meeting_memory_write import _store_sent_job

    store = AutoReplyStore(tmp_path / "worker.db")
    job_id = _store_sent_job(store)
    assert enqueue_sent_meeting_memory_writes(store) == 1
    store.update_meeting_alignment_job(job_id, status="ready_to_send")
    with deployment_drain(store.path):
        assert (
            store.claim_due_meeting_memory_write_events(
                now=NOW.isoformat(), limit=1, owner="memory", lease_seconds=30
            )
            == []
        )
        assert (
            store.claim_ready_to_send_meeting_alignment_jobs(
                limit=1, now=NOW.isoformat()
            )
            == []
        )
        assert (
            store.claim_ready_to_send_meeting_alignment_job(job_id, now=NOW.isoformat())
            is None
        )
    events = store.claim_due_meeting_memory_write_events(
        now=NOW.isoformat(), limit=1, owner="memory", lease_seconds=30
    )
    assert len(events) == 1
    assert (
        len(
            store.claim_ready_to_send_meeting_alignment_jobs(
                limit=1, now=NOW.isoformat()
            )
        )
        == 1
    )


@pytest.mark.parametrize(
    "name",
    [
        "scheduled",
        "reply",
        "meeting",
        "okr_review",
        "work_summary",
    ],
)
def test_each_dispatcher_source_retains_its_due_item_during_drain(tmp_path, name):
    from app.deployment_drain import deployment_drain
    from tests.test_consumer_dispatcher import _many_due_sources

    store = AutoReplyStore(tmp_path / "worker.db")
    adapter_type, ids = _many_due_sources(store, name, count=1)
    adapter = adapter_type(store)
    with deployment_drain(store.path):
        assert (
            adapter.claim(
                NOW, owner="worker", owner_pid=12345, lease=timedelta(minutes=5)
            )
            is None
        )
    result = adapter.claim(
        NOW, owner="worker", owner_pid=12345, lease=timedelta(minutes=5)
    )
    assert result is not None
    assert result.source_id == ids[0]


@pytest.mark.parametrize("name", ["business", "memory"])
def test_remaining_dispatcher_outboxes_keep_pending_items(tmp_path, name):
    from app.deployment_drain import deployment_drain
    from app.dispatcher.adapters import (
        BusinessTaskTodoSyncOutboxQueueAdapter,
        TaskMemoryWriteQueueAdapter,
    )
    from tests.test_consumer_dispatcher import _business_task_todo_outbox
    from app.action_storage import insert_task_delivery_action

    store = AutoReplyStore(tmp_path / "worker.db")
    if name == "business":
        _business_task_todo_outbox(store)
        adapter = BusinessTaskTodoSyncOutboxQueueAdapter(store)
    else:
        enqueue(store)
        with store._connect() as db:
            parent = db.execute(
                "select id from tasks where record_type='reply_tasks'"
            ).fetchone()[0]
            insert_task_delivery_action(
                db,
                "task_memory_write_events",
                dict(
                    reply_task_id=parent,
                    execution_generation="initial",
                    consumer_run_id=0,
                    status="pending",
                ),
            )
        adapter = TaskMemoryWriteQueueAdapter(store)
    with deployment_drain(store.path):
        assert (
            adapter.claim(
                NOW, owner="worker", owner_pid=12345, lease=timedelta(minutes=5)
            )
            is None
        )
    assert (
        adapter.claim(NOW, owner="worker", owner_pid=12345, lease=timedelta(minutes=5))
        is not None
    )


def test_failed_upgrade_holds_admission_during_rollback_and_then_resumes(tmp_path):
    from app.repository_updater import RepositoryUpdater, UpgradeFailed
    from tests.test_repository_updater import fixture_repo, operation, StateStore, git

    store = AutoReplyStore(tmp_path / "worker.db")
    enqueue(store)
    local, _ = fixture_repo(tmp_path)
    op = operation(local)
    health = iter([False, True])

    def blocked():
        assert claim(store) is None

    def check():
        blocked()
        return next(health)

    updater = RepositoryUpdater(
        local,
        StateStore(),
        database_path=store.path,
        wait_for_quiet=blocked,
        stop=blocked,
        restart=blocked,
        health=check,
    )
    with pytest.raises(UpgradeFailed):
        updater.execute(op)
    assert git(local, "rev-parse", "HEAD") == op.original_commit
    assert claim(store) is not None


def test_waiting_deployer_closes_admission_before_old_claim_commits(tmp_path):
    from time import monotonic
    from app.deployment_drain import deployment_drain, claim_connection

    store = AutoReplyStore(tmp_path / "worker.db")
    deploying = Event()
    drained = Event()

    def deployer():
        deploying.set()
        with deployment_drain(store.path):
            drained.set()

    with ThreadPoolExecutor(max_workers=1) as pool:
        try:
            with claim_connection(store) as first:
                assert first is not None
                future = pool.submit(deployer)
                assert deploying.wait(5)
                deadline = monotonic() + 1
                refused = False
                while monotonic() < deadline:
                    with claim_connection(store) as late:
                        if late is None:
                            refused = True
                            break
                    drained.wait(0.01)
                assert refused, "waiting deployment kept admitting new claims"
                assert not drained.is_set()
        finally:
            future.result(timeout=5)
    assert drained.is_set()


@pytest.mark.parametrize("single", [False, True])
def test_direct_email_reply_claim_is_paused_and_counted_before_runtime(
    tmp_path, single
):
    from app.deployment_drain import deployment_drain
    from app.repository_updater import in_flight_work

    store = AutoReplyStore(tmp_path / "worker.db")
    store.enqueue_reply_task(
        conversation_id="email",
        conversation_title="email",
        single_chat=True,
        trigger_message_id="email",
        trigger_create_time=NOW.isoformat(),
        trigger_sender="Sender",
        trigger_text="request",
        channel="email",
    )
    task = store.peek_reply_tasks(1, channel="email")[0]

    def take():
        return (
            store.claim_reply_task(task.id)
            if single
            else store.claim_reply_tasks(1, channel="email")
        )

    with deployment_drain(store.path):
        assert not take()
        assert store.get_reply_task(task.id).status == "pending"
    assert take()
    assert in_flight_work(store.path) == 1


@pytest.fixture(scope="module")
def previous_store_source(tmp_path_factory):
    from tests.test_task_lifecycle_release import archived_source

    return archived_source.__wrapped__(tmp_path_factory)


def test_quiet_reads_supported_pre_lifecycle_publication_source(
    previous_store_source, tmp_path
):
    from tests.test_task_lifecycle_release import seed
    from app.repository_updater import in_flight_work

    path = tmp_path / "store19-7.db"
    seed(previous_store_source, path)
    assert in_flight_work(path) == 0


def test_email_provider_admission_lock_spans_writer_recheck_and_commit(
    tmp_path, monkeypatch
):
    import fcntl
    from tests.test_email_store import _create_action_with_attempts

    store, action_id = _create_action_with_attempts(tmp_path / "worker.db", ())
    original = store._next_direct_action_row
    checks = 0

    def checked(db, claimed_at, account_ids):
        nonlocal checks
        checks += 1
        if checks == 2:
            # The preflight is only a read. The second check is the actual
            # writer transaction, which must exclude a deployment boundary.
            path = store.path.with_name(store.path.name + ".deployment-drain.lock")
            with path.open("a+b") as lock:
                with pytest.raises(BlockingIOError):
                    fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        return original(db, claimed_at, account_ids)

    monkeypatch.setattr(store, "_next_direct_action_row", checked)
    action = store.claim_next_direct_action(claimed_at=NOW.isoformat())
    assert action.action_id == action_id
    assert checks == 2


@pytest.mark.parametrize(
    "generation, runtime_lease, expected",
    [
        ("initial", False, 1),
        ("superseded-generation", False, 0),
        ("superseded-generation", True, 1),
    ],
)
def test_quiet_excludes_only_superseded_runtime_without_live_ownership(
    tmp_path, generation, runtime_lease, expected
):
    from app.repository_updater import in_flight_work
    from app.run_storage import insert_run

    store = AutoReplyStore(tmp_path / "worker.db")
    enqueue(store)
    with store._immediate_write_transaction() as db:
        task_id = db.execute(
            "select id from tasks where record_type='reply_tasks'"
        ).fetchone()[0]
        parent = insert_run(
            db,
            "agent_runs",
            {
                "task_id": task_id,
                "execution_generation": generation,
                "role": "consumer",
                "status": "running",
            },
        ).lastrowid
        insert_run(
            db,
            "agent_runtime_attempts",
            {
                "parent_id": parent,
                "workload_kind": "agent_run",
                "workload_key": str(parent),
                "attempt_number": 1,
                "route_name": "primary",
                "runtime_kind": "codex_cli",
                "credential_mode": "native",
                "model": "test",
                "status": "running",
                "lease_owner": "native" if runtime_lease else "",
                "lease_expires_at": "2999-01-01 00:00:00" if runtime_lease else "",
            },
        )
    assert in_flight_work(store.path) == expected


def test_stalled_admitted_transaction_times_out_and_reopens_admission(tmp_path):
    from app.deployment_drain import (
        claim_connection,
        deployment_drain,
        DeploymentDrainTimeout,
    )

    store = AutoReplyStore(tmp_path / "worker.db")
    with claim_connection(store) as existing:
        assert existing is not None
        with pytest.raises(DeploymentDrainTimeout):
            with deployment_drain(store.path, timeout_seconds=0.02):
                pytest.fail("exclusive drain passed a still-admitted transaction")
        # The first transaction can still finish. A failed deploy released
        # its admission turnstile even before that transaction completes.
        with claim_connection(store) as resumed:
            assert resumed is not None
