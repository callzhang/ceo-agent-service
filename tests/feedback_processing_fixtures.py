"""Synthetic resolved feedback through current public batch APIs; no external work."""
from app.feedback_processing import ResolutionEvidence
from app.store import AutoReplyStore


def resolve_feedback_fixture(
    store: AutoReplyStore, feedback_key: str, *, batch_id: str | None = None
) -> None:
    """Resolve an already seeded event without replacing its source content.

    Receipt values describe this synthetic database fixture. No tests, service
    restart, health request, commit lookup, Agent or provider is executed here.
    """
    selected_batch = batch_id or f"resolved-fixture:{feedback_key}"
    receipt = ResolutionEvidence(
        commit_sha="b" * 40,
        test_evidence={"pytest": {"exit_code": 0}},
        restart_evidence={
            "launchd_label": "com.ceo-agent-service.main",
            "before_pid": 10,
            "after_pid": 11,
        },
        health_evidence={
            "url": "http://127.0.0.1:8765/healthz",
            "status_code": 200,
            "ok": True,
        },
        backlog_evidence={"processing": 0, "failed": 0, "retryable": 0},
    )
    store.create_feedback_processing_batch([feedback_key], batch_id=selected_batch)
    assert store.claim_feedback_processing_items(selected_batch, [feedback_key])
    store.associate_feedback_processing_turn(
        feedback_key,
        workbench_task_id="task-fixture",
        workbench_turn_id="turn-fixture",
        attempt_id=71,
        agent_run_id=72,
    )
    store.patch_feedback_processing_item_evidence(
        feedback_key,
        commit_sha=receipt.commit_sha,
        test_evidence=receipt.test_evidence,
        restart_evidence=receipt.restart_evidence,
        health_evidence=receipt.health_evidence,
    )
    assert store.resolve_feedback_processing_batch(
        selected_batch, receipt, commit_is_ancestor=True
    )
