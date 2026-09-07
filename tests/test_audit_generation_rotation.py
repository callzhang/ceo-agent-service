from app.store import AgentRole, AutoReplyStore


def _enqueue_task(store: AutoReplyStore, *, message_id: str = "message"):
    store.enqueue_reply_task(
        conversation_id="conversation",
        conversation_title="Generation rotation",
        single_chat=False,
        trigger_message_id=message_id,
        trigger_create_time="2026-08-27 10:00:00",
        trigger_sender="Derek",
        trigger_text="Review this",
        trigger_message_json="{}",
        execution_generation="generation-old",
    )
    return store.claim_reply_tasks(limit=1)[0]


def _mark_running_audit(
    store: AutoReplyStore, task_id: int, generation: str
) -> int:
    run = store.claim_agent_run(
        task_id,
        generation,
        role=AgentRole.AUDIT,
        proposal_revision=0,
        turn_attempt=0,
        parent_agent_run_id=None,
        operation_id="audit-operation",
        owner="audit",
    ).run
    return run.id


def test_generation_rotation_supersedes_running_audit_run(tmp_path):
    store = AutoReplyStore(tmp_path / "rotation.sqlite3")
    task = _enqueue_task(store)
    run_id = _mark_running_audit(
        store, task.id, task.execution_generation
    )

    rotated_generation = store.rotate_reply_task_execution_generation(task.id)

    assert rotated_generation != task.execution_generation
    rotated_task = store.get_reply_task(task.id)
    assert rotated_task is not None
    assert rotated_task.execution_generation == rotated_generation
    assert rotated_task.status == "pending"
    superseded = store.get_agent_run(run_id)
    assert superseded is not None
    assert superseded.status == "failed"


def test_reviewed_feedback_rerun_supersedes_prior_audit_run(tmp_path):
    store = AutoReplyStore(tmp_path / "feedback.sqlite3")
    attempt_id, task = store.record_reviewed_reply_rerun(
        conversation_id="conversation",
        conversation_title="Feedback rerun",
        single_chat=False,
        trigger_message_id="feedback-message",
        trigger_create_time="2026-08-27 10:00:00",
        trigger_sender="Derek",
        trigger_text="Review this",
        trigger_message_json="{}",
        suggested_reply_text="Initial response",
    )
    claimed = store.claim_reply_task(task.id)
    assert claimed is not None
    run_id = _mark_running_audit(
        store, claimed.id, claimed.execution_generation
    )

    rerun_attempt_id, rerun_task = store.record_reviewed_reply_rerun(
        conversation_id="conversation",
        conversation_title="Feedback rerun",
        single_chat=False,
        trigger_message_id="feedback-message",
        trigger_create_time="2026-08-27 10:00:00",
        trigger_sender="Derek",
        trigger_text="Review this",
        trigger_message_json="{}",
        suggested_reply_text="Corrected response",
        reviewer_feedback="Please correct the response",
        source_attempt_id=attempt_id,
    )

    assert rerun_attempt_id == attempt_id
    assert rerun_task.execution_generation != claimed.execution_generation
    superseded = store.get_agent_run(run_id)
    assert superseded is not None
    assert superseded.status == "failed"


def test_store_does_not_expose_legacy_audit_reconciliation_queue(tmp_path):
    store = AutoReplyStore(tmp_path / "startup.sqlite3")

    assert not hasattr(store, "list_unknown_agent_runs")
    assert not hasattr(store, "claim_unknown_agent_run")
    assert not hasattr(store, "peek_pending_reconciliation_reply_tasks")
    assert not hasattr(store, "release_unknown_audit_reconciliation_leases_after_service_restart")
    assert not hasattr(store, "resolve_agent_run_manually")
