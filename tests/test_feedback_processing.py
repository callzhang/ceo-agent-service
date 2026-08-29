import sqlite3
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.feedback_processing import (
    FeedbackImportItem,
    FeedbackProcessingBatchError,
    FeedbackProcessingClaimError,
    FeedbackProcessingItem,
    ResolutionEvidence,
    build_feedback_start_message,
    detail_references,
    persisted_feedback_summary,
    validate_resolution_evidence,
)
import app.store as store_module
from app.store import AutoReplyStore, UserFeedbackItem


def test_feedback_event_seeds_processing_item_without_changing_event(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "feedback.sqlite3")

    store.upsert_feedback_event(
        key="feedback-1",
        feedback_token="token-1",
        comment="原始反馈",
    )

    item = store.get_feedback_processing_item("feedback-1")
    event = store.get_feedback_event("feedback-1")

    assert item is not None
    assert item.status == "pending"
    assert item.feedback_key == "feedback-1"
    assert event is not None
    assert event.comment == "原始反馈"


def test_feedback_processing_schema_is_additive_and_reopen_is_idempotent(
    tmp_path: Path,
):
    db_path = tmp_path / "fresh.sqlite3"
    store_module._INITIALIZED_STORE_PATHS.discard(db_path.resolve())
    AutoReplyStore(db_path)

    def schema_snapshot() -> tuple[set[str], set[str]]:
        with sqlite3.connect(db_path) as db:
            tables = {
                row[0]
                for row in db.execute(
                    "select name from sqlite_master where type='table'"
                )
            }
            indexes = {
                row[0]
                for row in db.execute(
                    "select name from sqlite_master where type='index'"
                )
            }
        return tables, indexes

    tables_before, indexes_before = schema_snapshot()
    assert "feedback_events" in tables_before
    assert "feedback_processing_batches" in tables_before
    assert "feedback_processing_items" in tables_before
    assert "idx_feedback_processing_items_status" in indexes_before
    assert "idx_feedback_processing_items_batch" in indexes_before

    AutoReplyStore(db_path)
    tables_after, indexes_after = schema_snapshot()
    assert tables_after == tables_before
    assert indexes_after == indexes_before


def test_claim_associate_patch_and_resolve_feedback_batch(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "workflow.sqlite3")
    for key in ("feedback-1", "feedback-2"):
        store.upsert_feedback_event(
            key=key,
            feedback_token=f"token-{key}",
            comment=f"comment-{key}",
        )

    batch = store.create_feedback_processing_batch(
        ["feedback-1", "feedback-2"], batch_id="batch-1"
    )
    assert batch.batch_id == "batch-1"
    claimed = store.claim_feedback_processing_items(
        "batch-1", ["feedback-1", "feedback-2"]
    )
    assert {item.feedback_key for item in claimed} == {"feedback-1", "feedback-2"}
    assert all(item.status == "processing" for item in claimed)

    associated = store.associate_feedback_processing_turn(
        "feedback-1",
        workbench_task_id="task-1",
        workbench_turn_id="turn-1",
        attempt_id=12,
        agent_run_id=34,
    )
    assert associated is not None
    assert associated.workbench_turn_id == "turn-1"
    assert associated.attempt_id == 12
    assert associated.agent_run_id == 34

    store.patch_feedback_processing_item_evidence(
        "feedback-1",
        test_evidence={"passed": 3},
        restart_evidence={"process": "new"},
        health_evidence={"status": 200},
        commit_sha="abc123",
        status="resolved",
    )
    store.patch_feedback_processing_item_evidence("feedback-2", status="resolved")
    assert store.resolve_feedback_processing_batch("batch-1") is True
    assert store.get_feedback_processing_batch("batch-1").status == "resolved"
    assert store.resolve_feedback_processing_batch("batch-1") is True


def test_claim_rejects_unknown_or_resolved_keys_atomically(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "claim.sqlite3")
    store.upsert_feedback_event(
        key="feedback-1", feedback_token="token-1", comment="feedback"
    )
    store.upsert_feedback_event(
        key="feedback-resolved", feedback_token="token-2", comment="done"
    )
    assert store.resolve_feedback_event("feedback-resolved") is True

    with pytest.raises(FeedbackProcessingClaimError):
        store.claim_feedback_processing_items(
            "batch-invalid", ["feedback-1", "unknown-key"]
        )
    assert store.get_feedback_processing_batch("batch-invalid") is None
    assert store.get_feedback_processing_item("unknown-key") is None

    with pytest.raises(FeedbackProcessingClaimError):
        store.claim_feedback_processing_items(
            "batch-invalid", ["feedback-1", "feedback-resolved"]
        )
    assert store.get_feedback_processing_batch("batch-invalid") is None
    assert store.get_feedback_processing_item("feedback-1").status == "pending"
    assert store.get_feedback_processing_item("feedback-resolved").status == "resolved"


def test_claim_cannot_move_processing_item_to_another_batch(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "conflict.sqlite3")
    store.upsert_feedback_event(key="feedback-1", feedback_token="token-1")
    assert store.claim_feedback_processing_items("batch-1", ["feedback-1"])

    with pytest.raises(FeedbackProcessingClaimError):
        store.claim_feedback_processing_items("batch-2", ["feedback-1"])
    assert store.get_feedback_processing_item("feedback-1").batch_id == "batch-1"
    assert store.get_feedback_processing_batch("batch-2") is None


def test_claim_cannot_reassign_pending_item_seeded_by_another_batch(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "pending-conflict.sqlite3")
    store.upsert_feedback_event(key="feedback-1", feedback_token="token-1")
    store.create_feedback_processing_batch(["feedback-1"], batch_id="batch-1")

    with pytest.raises(FeedbackProcessingClaimError):
        store.claim_feedback_processing_items("batch-2", ["feedback-1"])
    item = store.get_feedback_processing_item("feedback-1")
    assert item is not None
    assert item.status == "pending"
    assert item.batch_id == "batch-1"
    assert store.get_feedback_processing_batch("batch-2") is None


def test_batch_reopen_requires_same_normalized_key_set(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "batch-reopen.sqlite3")
    for key in ("feedback-1", "feedback-2"):
        store.upsert_feedback_event(key=key, feedback_token=key)
    first = store.create_feedback_processing_batch(
        [" feedback-1", "feedback-2", "feedback-2"], batch_id="batch-1"
    )
    second = store.create_feedback_processing_batch(
        ["feedback-2", "feedback-1"], batch_id="batch-1"
    )
    assert second.requested_count == first.requested_count == 2
    with pytest.raises(FeedbackProcessingBatchError):
        store.create_feedback_processing_batch(["feedback-1"], batch_id="batch-1")
    assert store.get_feedback_processing_batch("batch-1").requested_count == 2

    with pytest.raises(FeedbackProcessingBatchError):
        store.claim_feedback_processing_items("batch-1", ["feedback-1"])
    assert store.get_feedback_processing_batch("batch-1").requested_count == 2


def test_create_batch_rejects_unknown_feedback_keys_atomically(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "batch-unknown.sqlite3")
    store.upsert_feedback_event(key="known", feedback_token="token-known")
    with pytest.raises(FeedbackProcessingBatchError):
        store.create_feedback_processing_batch(["known", "missing"], batch_id="batch-1")
    assert store.get_feedback_processing_batch("batch-1") is None
    assert store.get_feedback_processing_item("missing") is None


def test_reopen_existing_batch_is_idempotent_after_source_resolution(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "batch-resolved-reopen.sqlite3")
    store.upsert_feedback_event(key="feedback-1", feedback_token="token-1")
    original = store.create_feedback_processing_batch(["feedback-1"], batch_id="batch-1")
    assert store.resolve_feedback_event("feedback-1") is True
    reopened = store.create_feedback_processing_batch(["feedback-1"], batch_id="batch-1")
    assert reopened.batch_id == original.batch_id
    assert reopened.requested_count == original.requested_count


def test_legacy_text_processing_ids_are_read_as_integers(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "legacy-ids.sqlite3")
    store.upsert_feedback_event(key="feedback-1", feedback_token="token-1")
    assert store.claim_feedback_processing_items("batch-1", ["feedback-1"])
    with store._connect() as db:
        db.execute(
            "update feedback_processing_items set attempt_id='12', agent_run_id='34' where feedback_key=?",
            ("feedback-1",),
        )
    item = store.get_feedback_processing_item("feedback-1")
    assert item is not None
    assert item.attempt_id == 12
    assert item.agent_run_id == 34


def test_resolved_event_projection_and_status_transition_are_consistent(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "resolved.sqlite3")
    store.upsert_feedback_event(key="feedback-1", feedback_token="token-1")
    assert store.resolve_feedback_event("feedback-1") is True
    item = store.get_feedback_processing_item("feedback-1")
    assert item is not None
    assert item.status == "resolved"
    assert item.resolved_at

    with pytest.raises(ValueError):
        store.patch_feedback_processing_item_evidence("feedback-1", status="pending")

    with store._connect() as db:
        db.execute(
            "delete from feedback_processing_items where feedback_key=?",
            ("feedback-1",),
        )
    with pytest.raises(FeedbackProcessingBatchError):
        store.create_feedback_processing_batch(["feedback-1"], batch_id="batch-new")
    assert store.get_feedback_processing_batch("batch-new") is None


def test_processing_model_rejects_string_attempt_ids():
    with pytest.raises(ValidationError):
        FeedbackProcessingItem(feedback_key="feedback-1", attempt_id="12")


def test_claim_retry_is_idempotent_without_duplicate_items(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "claim-retry.sqlite3")
    store.upsert_feedback_event(key="feedback-1", feedback_token="token-1")
    first = store.claim_feedback_processing_items("batch-1", ["feedback-1"])
    second = store.claim_feedback_processing_items("batch-1", ["feedback-1"])
    assert [item.feedback_key for item in second] == ["feedback-1"]
    with store._connect() as db:
        assert db.execute("select count(*) from feedback_processing_items").fetchone()[0] == 1
        assert db.execute("select count(*) from feedback_processing_batches").fetchone()[0] == 1
    assert second[0].updated_at == first[0].updated_at


def test_pending_count_excludes_processing_projection(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "pending-count.sqlite3")
    for key in ("feedback-1", "feedback-2"):
        store.upsert_feedback_event(key=key, feedback_token=key)
    assert store.count_pending_user_feedback_items() == 2
    store.claim_feedback_processing_items("batch-1", ["feedback-1"])
    assert store.count_pending_user_feedback_items() == 1


def test_summary_and_references_are_deterministic_and_persisted_only():
    item = UserFeedbackItem(
        key="feedback-1",
        feedback_token="token-1",
        reviewer_feedback=" reviewer ",
        corrected_reply_text=" corrected ",
        audit_summary=" audit ",
        codex_reason=" reason ",
        final_reply_text=" reply ",
        attempt_id=12,
        agent_run_id=34,
        codex_session_id="session-1",
        project_id=56,
    )
    assert persisted_feedback_summary(item) == "audit"
    refs = detail_references(item)
    assert {ref["label"] for ref in refs} == {"attempt#12", "run#34", "codex#session-1", "task#56"}
    assert all(ref["route"] == "" or ref["route"].startswith(("/attempts/", "/codex/", "/tasks/")) for ref in refs)
    assert "/attempts/34/execution/run" not in {ref["route"] for ref in refs}


def test_missing_summary_is_empty_and_start_message_has_no_feedback_body():
    item = FeedbackImportItem(feedback_key="feedback-1", summary="", references=[])
    message = build_feedback_start_message("batch-1", [item])
    assert "batch-1" in message
    assert "skills/ceo-feedback-processing/SKILL.md" in message
    assert "feedback-1" in message
    assert "persisted summary:" in message
    assert "原始反馈" not in message


def test_resolution_evidence_requires_current_head_and_success_receipts():
    head = "a" * 40
    complete = ResolutionEvidence(
        commit_sha=head,
        test_evidence={"pytest": {"exit_code": 0}},
        restart_evidence={"launchd_label": "com.ceo-agent-service.main", "before_pid": 1, "after_pid": 2},
        health_evidence={"url": "http://127.0.0.1:8765/health", "status_code": 200},
    )
    validate_resolution_evidence(complete, current_head=head)
    for bad in (
        complete.model_copy(update={"commit_sha": "b" * 40}),
        complete.model_copy(update={"test_evidence": {"pytest": {"exit_code": 1}}}),
        complete.model_copy(update={"restart_evidence": {"launchd_label": "x", "before_pid": 1}}),
        complete.model_copy(update={"health_evidence": {"status_code": 503}}),
    ):
        with pytest.raises(ValueError):
            validate_resolution_evidence(bad, current_head=head)


def test_resolve_evidence_marks_every_item_in_batch_atomically(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "resolve-evidence.sqlite3")
    for key in ("feedback-1", "feedback-2"):
        store.upsert_feedback_event(key=key, feedback_token=key)
    store.claim_feedback_processing_items("batch-1", ["feedback-1", "feedback-2"])
    for key in ("feedback-1", "feedback-2"):
        store.associate_feedback_processing_turn(key, workbench_task_id="task", workbench_turn_id="turn", attempt_id=1, agent_run_id=2)
    head = "a" * 40
    evidence = ResolutionEvidence(
        commit_sha=head,
        test_evidence={"pytest": {"exit_code": 0}},
        restart_evidence={"launchd_label": "com.ceo-agent-service.main", "before_pid": 1, "after_pid": 2},
        health_evidence={"status_code": 200, "url": "http://127.0.0.1:8765/health"},
    )
    assert store.resolve_feedback_processing_batch("batch-1", evidence, current_head=head)
    assert {store.get_feedback_processing_item(key).status for key in ("feedback-1", "feedback-2")} == {"resolved"}
    assert store.get_feedback_processing_batch("batch-1").status == "resolved"
