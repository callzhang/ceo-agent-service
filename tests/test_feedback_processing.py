import sqlite3
from pathlib import Path

from app.store import AutoReplyStore


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
        attempt_id="attempt-1",
        agent_run_id="run-1",
    )
    assert associated is not None
    assert associated.workbench_turn_id == "turn-1"

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
