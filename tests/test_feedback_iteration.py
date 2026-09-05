from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.feedback_processing import (
    FeedbackIterationDecision,
    FeedbackIterationDisabledError,
    FeedbackImportItem,
    ResolutionEvidence,
    build_feedback_start_message,
    validate_resolution_receipt,
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


def _resolution_receipt(**overrides: object) -> ResolutionEvidence:
    values: dict[str, object] = {
        "commit_sha": "a" * 40,
        "test_evidence": {"scenario": {"exit_code": 0}},
        "restart_evidence": {
            "launchd_label": "com.ceo-agent-service.main",
            "before_pid": 10,
            "after_pid": 11,
        },
        "health_evidence": {
            "url": "http://127.0.0.1:8765/healthz",
            "status_code": 200,
            "ok": True,
        },
        "backlog_evidence": {"processing": 0, "failed": 0, "retryable": 0},
        "runtime_config_id": 2,
        "previous_runtime_config_id": 1,
        "load_receipt_id": 3,
        "skill_revisions": [
            {"skill_id": 1, "revision_id": 2, "sha256": "b" * 64}
        ],
    }
    values.update(overrides)
    return ResolutionEvidence.model_validate(values)


@pytest.mark.parametrize(
    ("scope", "receipt", "error"),
    [
        ("skill_only", _resolution_receipt(commit_sha=""), None),
        ("skill_only", _resolution_receipt(load_receipt_id=0), "load receipt"),
        ("runtime_config", _resolution_receipt(commit_sha=""), None),
        ("code", _resolution_receipt(commit_sha=""), "commit"),
        ("mixed", _resolution_receipt(previous_runtime_config_id=0), "runtime configuration"),
    ],
)
def test_resolution_receipt_matches_decision_scope(scope, receipt, error):
    decision = _decision(feedback_key="manual:1").model_copy(
        update={
            "scope": scope,
            "target_runtime_config_id": 2 if scope in {"runtime_config", "mixed"} else None,
            "target_skill_revisions": (
                [{"skill_id": 1, "from_revision": 1, "to_revision": 2}]
                if scope in {"skill_only", "mixed"}
                else []
            ),
        }
    )
    if error is None:
        validate_resolution_receipt(decision, receipt, commit_is_ancestor=True)
    else:
        with pytest.raises(ValueError, match=error):
            validate_resolution_receipt(decision, receipt, commit_is_ancestor=True)


def test_skill_only_decision_resolves_with_loaded_revision_and_no_commit(tmp_path):
    store = AutoReplyStore(tmp_path / "feedback.sqlite3")
    skill = store.create_managed_skill("ceo-test", "Test Skill")
    revision_one = store.create_managed_skill_revision(
        skill.id,
        "---\nname: ceo-test\ndescription: Test managed Skill\nmetadata:\n  managed_by: ceo-agent-service\n---\n\n# One\n",
        source="settings",
    )
    first_config = store.create_runtime_skill_config(
        {skill.id: revision_one.id}, expected_parent_id=None
    )
    store.record_runtime_skill_load(
        first_config.id, pid=101, loaded={skill.id: revision_one.sha256}
    )
    revision_two = store.create_managed_skill_revision(
        skill.id,
        "---\nname: ceo-test\ndescription: Test managed Skill\nmetadata:\n  managed_by: ceo-agent-service\n---\n\n# Two\n",
        source="settings",
    )
    target_config = store.create_runtime_skill_config(
        {skill.id: revision_two.id}, expected_parent_id=first_config.id
    )
    load_receipt = store.record_runtime_skill_load(
        target_config.id, pid=102, loaded={skill.id: revision_two.sha256}
    )
    store.upsert_feedback_event(key="manual:1", feedback_token="token")
    store.create_feedback_processing_batch(["manual:1"], batch_id="batch-1")
    store.claim_feedback_processing_items("batch-1", ["manual:1"])
    store.associate_feedback_processing_turn(
        "manual:1", expected_batch_id="batch-1", workbench_task_id="task-1",
        workbench_turn_id="turn-1", attempt_id=1, agent_run_id=2,
    )
    store.patch_feedback_processing_item_evidence(
        "manual:1",
        test_evidence={"scenario": {"exit_code": 0}},
        restart_evidence={"launchd_label": "com.ceo-agent-service.main", "before_pid": 10, "after_pid": 11},
        health_evidence={"url": "http://127.0.0.1:8765/healthz", "status_code": 200, "ok": True},
    )
    decision = FeedbackIterationDecision.model_validate(
        {
            **_decision(feedback_key="manual:1").model_dump(),
            "target_skill_revisions": [
                {"skill_id": skill.id, "from_revision": revision_one.id, "to_revision": revision_two.id}
            ],
        }
    )
    store.record_feedback_iteration_decision(
        "batch-1", decision, workbench_task_id="task-1", workbench_turn_id="turn-1"
    )

    assert store.resolve_feedback_processing_batch(
        "batch-1",
        _resolution_receipt(
            commit_sha="",
            runtime_config_id=target_config.id,
            load_receipt_id=load_receipt.id,
            skill_revisions=[{"skill_id": skill.id, "revision_id": revision_two.id, "sha256": revision_two.sha256}],
        ),
        commit_is_ancestor=False,
    ) is True
    assert store.resolve_feedback_processing_batch(
        "batch-1", commit_is_ancestor=False
    ) is True


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
    store.associate_feedback_processing_turn(
        "manual:1", expected_batch_id="batch-1", workbench_task_id="task-1",
        workbench_turn_id="turn-1", attempt_id=1, agent_run_id=2,
    )

    recorded = store.record_feedback_iteration_decision(
        "batch-1", _decision(feedback_key="manual:1"), workbench_task_id="task-1", workbench_turn_id="turn-1"
    )

    assert store.list_feedback_iteration_decisions("batch-1") == (recorded,)
    with store._connect() as db:
        with pytest.raises(Exception, match="append-only"):
            db.execute("update feedback_iteration_decisions set decision_json='{}' where id=?", (recorded.id,))


def test_decision_rejects_forged_or_mixed_current_round_workbench_identity(tmp_path):
    store = AutoReplyStore(tmp_path / "feedback.sqlite3")
    for key in ("manual:1", "manual:2"):
        store.upsert_feedback_event(key=key, feedback_token=key)
    store.create_feedback_processing_batch(["manual:1", "manual:2"], batch_id="batch-1")
    store.claim_feedback_processing_items("batch-1", ["manual:1", "manual:2"])
    store.associate_feedback_processing_turn(
        "manual:1", expected_batch_id="batch-1", workbench_task_id="actual-task",
        workbench_turn_id="actual-turn", attempt_id=1, agent_run_id=2,
    )
    store.associate_feedback_processing_turn(
        "manual:2", expected_batch_id="batch-1", workbench_task_id="other-task",
        workbench_turn_id="other-turn", attempt_id=3, agent_run_id=4,
    )

    with pytest.raises(ValueError, match="current processing round association"):
        store.record_feedback_iteration_decision(
            "batch-1", _decision(feedback_key="manual:1"),
            workbench_task_id="forged-task", workbench_turn_id="forged-turn",
        )

    mixed = _decision(feedback_key="manual:1").model_copy(
        update={"feedback_keys": ["manual:1", "manual:2"]}
    )
    with pytest.raises(ValueError, match="current processing round association"):
        store.record_feedback_iteration_decision(
            "batch-1", mixed,
            workbench_task_id="actual-task", workbench_turn_id="actual-turn",
        )


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
