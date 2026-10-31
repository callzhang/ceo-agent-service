from types import SimpleNamespace

from app.email_worker import (
    STALE_EMAIL_TASK_SECONDS,
    email_worker_components,
    run_email_task_recovery_watchdog_loop,
)
from app.store import AgentRole, AutoReplyStore


def _enqueue(store: AutoReplyStore, *, message_id: str, channel: str) -> int:
    store.enqueue_reply_task(
        conversation_id=f"conversation-{message_id}",
        conversation_title="Email task recovery",
        single_chat=True,
        trigger_message_id=message_id,
        trigger_create_time="2026-10-30 00:00:00",
        trigger_sender="sender@example.com",
        trigger_text="Please process",
        channel=channel,
    )
    task = store.get_reply_task_for_message(
        f"conversation-{message_id}", message_id, channel=channel
    )
    assert task is not None
    with store._connect() as db:
        db.execute(
            "update tasks set status='processing', locked_at=datetime('now','-45 minutes') where id=?",
            (task.id,),
        )
    return task.id


def test_watchdog_recovers_only_stale_email_tasks_without_live_runs(tmp_path):
    store = AutoReplyStore(tmp_path / "tasks.sqlite3")
    email_id = _enqueue(store, message_id="email-1", channel="email")
    dingtalk_id = _enqueue(store, message_id="dingtalk-1", channel="dingtalk")

    recovered = store.recover_stale_processing_reply_tasks(
        stale_after_seconds=STALE_EMAIL_TASK_SECONDS,
        channel="email",
    )

    assert [task.id for task in recovered] == [email_id]
    assert store.get_reply_task(email_id).status == "pending"
    assert store.get_reply_task(dingtalk_id).status == "processing"


def test_watchdog_does_not_recover_a_stale_task_with_a_live_run(tmp_path):
    store = AutoReplyStore(tmp_path / "tasks.sqlite3")
    task_id = _enqueue(store, message_id="email-live-run", channel="email")
    task = store.get_reply_task(task_id)
    assert task is not None
    store.claim_agent_run(
        task.id,
        task.execution_generation,
        role=AgentRole.CONSUMER,
        proposal_revision=0,
        turn_attempt=0,
        parent_agent_run_id=None,
        operation_id="",
        owner="watchdog-test",
    )

    assert store.recover_stale_processing_reply_tasks(
        stale_after_seconds=STALE_EMAIL_TASK_SECONDS,
        channel="email",
    ) == []
    assert store.get_reply_task(task_id).status == "processing"


def test_watchdog_does_not_recover_a_stale_task_with_a_running_runtime_attempt(
    tmp_path,
):
    store = AutoReplyStore(tmp_path / "tasks.sqlite3")
    task_id = _enqueue(store, message_id="email-live-runtime", channel="email")
    task = store.get_reply_task(task_id)
    assert task is not None
    run = store.claim_agent_run(
        task.id,
        task.execution_generation,
        role=AgentRole.CONSUMER,
        proposal_revision=0,
        turn_attempt=0,
        parent_agent_run_id=None,
        operation_id="",
        owner="watchdog-test",
    ).run
    store.claim_agent_runtime_attempt(
        run.id,
        "codex_oauth",
        "codex_cli",
        "local_oauth",
        "test-model",
    )
    with store._connect() as db:
        db.execute("update runs set status='failed' where id=?", (run.id,))

    assert store.recover_stale_processing_reply_tasks(
        stale_after_seconds=STALE_EMAIL_TASK_SECONDS,
        channel="email",
    ) == []
    assert store.get_reply_task(task_id).status == "processing"


def test_email_recovery_watchdog_is_an_independent_worker_component():
    store = SimpleNamespace()
    dependencies = SimpleNamespace(
        run_classification_once=lambda: None,
        run_direct_actions_once=lambda: None,
        task_store=store,
        orchestrator=SimpleNamespace(process=lambda *_args, **_kwargs: None),
        load_task_context=lambda _task: None,
        finalize_task=lambda *_args: None,
        record_health=lambda *_args: None,
        training_tick=lambda: None,
        publish_provider_observation_change=lambda: None,
    )

    components = email_worker_components(
        dependencies,
        accounts=(),
        active_model=None,
    )

    watchdog = dict(components)["email-task-recovery-watchdog"]
    assert watchdog.func is run_email_task_recovery_watchdog_loop
    assert watchdog.args == (store,)


def test_email_recovery_watchdog_records_success_and_keeps_running():
    recovered = [SimpleNamespace(id=417955)]
    calls = []

    class Store:
        def recover_stale_processing_reply_tasks(
            self, *, stale_after_seconds, limit, channel
        ):
            calls.append(("recover", stale_after_seconds, limit, channel))
            return recovered

    run_email_task_recovery_watchdog_loop(
        Store(),
        record_health=lambda scope, payload: calls.append((scope, payload)),
        component_ready=lambda name: calls.append(("ready", name)),
        sleep=lambda _seconds: None,
        max_cycles=1,
    )

    assert calls[0] == (
        "recover",
        STALE_EMAIL_TASK_SECONDS,
        100,
        "email",
    )
    assert calls[1] == (
        "component:email-task-recovery-watchdog",
        {"status": "ready", "recovered": 1},
    )
    assert calls[2] == ("ready", "email-task-recovery-watchdog")
