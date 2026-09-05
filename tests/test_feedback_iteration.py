from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.feedback_processing import (
    FeedbackIterationDecision,
    FeedbackIterationDisabledError,
    FeedbackImportItem,
    build_feedback_start_message,
)
from app.store import AutoReplyStore


def _decision(*, feedback_key: str) -> FeedbackIterationDecision:
    return FeedbackIterationDecision(
        scope="skill_only",
        root_cause="The loaded procedure omits the required existing tool use.",
        feedback_keys=[feedback_key],
        source_references=["attempt#1"],
        target_skill_revisions=[
            {"skill_id": 1, "from_revision": 1, "to_revision": 2}
        ],
        why_not_code="The existing route and tool already provide the needed capability.",
        acceptance={
            "scenario": "attempt#1",
            "expected_behavior": "The agent reads the persisted detail before acting.",
            "verification": ["focused regression", "startup load receipt"],
        },
    )


def test_disabled_feedback_iteration_rejects_batch_claim(tmp_path):
    store = AutoReplyStore(tmp_path / "feedback.sqlite3")
    store.upsert_feedback_event(key="manual:1", feedback_token="token")
    store.create_feedback_processing_batch(["manual:1"], batch_id="batch-1")
    store.set_feedback_iteration_enabled(False)

    with pytest.raises(FeedbackIterationDisabledError):
        store.claim_feedback_processing_items("batch-1", ["manual:1"])


def test_decision_context_uses_existing_summary_and_reference_only():
    message = build_feedback_start_message(
        "batch-1",
        [FeedbackImportItem(feedback_key="manual:1", summary="persisted summary", references=[{"label": "attempt#1", "route": "/attempts/1"}])],
        runtime_context={"config_id": 4, "loaded_revisions": [{"skill_id": 2, "revision_number": 3, "sha256": "abc"}]},
    )

    assert "persisted summary: persisted summary" in message
    assert "attempt#1 (/attempts/1)" in message
    assert "runtime config: 4" in message
    assert "managed Skill 2 revision 3 sha256: abc" in message
    assert "generated summary" not in message


def test_persisted_decision_is_strict_and_append_only(tmp_path):
    store = AutoReplyStore(tmp_path / "feedback.sqlite3")
    store.upsert_feedback_event(key="manual:1", feedback_token="token")
    store.create_feedback_processing_batch(["manual:1"], batch_id="batch-1")
    store.claim_feedback_processing_items("batch-1", ["manual:1"])

    recorded = store.record_feedback_iteration_decision(
        "batch-1", _decision(feedback_key="manual:1"), workbench_task_id="task-1", workbench_turn_id="turn-1"
    )

    assert store.list_feedback_iteration_decisions("batch-1") == (recorded,)
    with store._connect() as db:
        with pytest.raises(Exception, match="append-only"):
            db.execute("update feedback_iteration_decisions set decision_json='{}' where id=?", (recorded.id,))


def test_decision_rejects_missing_scope_specific_references():
    with pytest.raises(ValidationError):
        FeedbackIterationDecision(
            scope="skill_only",
            root_cause="x",
            feedback_keys=["manual:1"],
            source_references=["attempt#1"],
            target_skill_revisions=[],
            why_not_code="x",
            acceptance={"scenario": "attempt#1", "expected_behavior": "x", "verification": ["test"]},
        )
