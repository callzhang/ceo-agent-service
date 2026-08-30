import sqlite3
from pathlib import Path

import pytest
from pydantic import ValidationError

import app.feedback_processing as feedback_processing_module
from app.feedback_processing import (
    FeedbackImportItem,
    FEEDBACK_PROCESSING_ALREADY_PROCESSING_ERROR,
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


def test_manual_attempt_feedback_projection_keeps_context_and_pending_status(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "manual-feedback.sqlite3")
    store.upsert_conversation(
        "cid-1", title="技术部", single_chat=False, codex_session_id="session-1"
    )
    attempt_id = store.record_reply_attempt(
        conversation_id="cid-1",
        conversation_title="技术部",
        trigger_message_id="msg-1",
        trigger_sender="Xiaomin",
        trigger_text="请检查这个问题",
        action="send_reply",
        sensitivity_kind="general",
        codex_reason="direct ask",
        draft_reply_text="先按A方案走",
        audit_summary="查看材料后给出建议。",
    )
    store.update_reply_attempt(attempt_id, send_status="sent", final_reply_text="先按A方案走")
    store.record_reply_feedback(attempt_id, feedback="请通过处理反馈入口复核")
    store.upsert_feedback_event(
        key=f"manual:{attempt_id}",
        feedback_token=f"manual-attempt:{attempt_id}",
        rating_label="用户反馈",
        comment="请通过处理反馈入口复核",
        source="workbench",
    )

    row = store.list_user_feedback_items()[0]

    assert row.attempt_id == attempt_id
    assert row.conversation_title == "技术部"
    assert row.trigger_text == "请检查这个问题"
    assert row.reviewer_feedback == ""
    assert row.processing_status == "pending"


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


def test_feedback_round_schema_is_additive_and_idempotent(tmp_path: Path):
    db_path = tmp_path / "rounds.sqlite3"
    store_module._INITIALIZED_STORE_PATHS.discard(db_path.resolve())
    store = AutoReplyStore(db_path)

    with store._connect() as db:
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
        columns = {
            row[1]
            for row in db.execute("pragma table_info(feedback_processing_items)")
        }
        assert {
            "feedback_processing_rounds",
            "feedback_processing_transitions",
        } <= tables
        assert "current_round_id" in columns
        assert {
            "idx_feedback_processing_rounds_feedback",
            "idx_feedback_processing_rounds_batch",
            "idx_feedback_processing_transitions_feedback",
        } <= indexes

    store_module._INITIALIZED_STORE_PATHS.discard(db_path.resolve())
    AutoReplyStore(db_path)
    with sqlite3.connect(db_path) as db:
        assert db.execute(
            "select count(*) from feedback_processing_rounds"
        ).fetchone()[0] == 0
        assert db.execute(
            "select count(*) from feedback_processing_transitions"
        ).fetchone()[0] == 0


def test_feedback_round_models_are_strict():
    round_model = getattr(
        feedback_processing_module, "FeedbackProcessingRound"
    )
    transition_model = getattr(
        feedback_processing_module, "FeedbackProcessingTransition"
    )

    round_item = round_model(
        id=1,
        feedback_key="feedback-1",
        round_number=1,
        batch_id="batch-1",
        status="processing",
    )
    assert round_item.test_evidence == {}
    assert round_item.reopened_at == ""
    with pytest.raises(ValidationError):
        round_model(
            id=1,
            feedback_key="feedback-1",
            round_number="1",
            batch_id="batch-1",
            status="processing",
        )
    with pytest.raises(ValidationError):
        round_model(
            id=1,
            feedback_key="feedback-1",
            round_number=1,
            batch_id="batch-1",
            status="pending",
        )
    with pytest.raises(ValidationError):
        round_model(
            id=1,
            feedback_key="feedback-1",
            round_number=1,
            batch_id="batch-1",
            status="processing",
            unexpected=True,
        )

    transition = transition_model(
        id=1,
        feedback_key="feedback-1",
        from_status="",
        to_status="pending",
    )
    assert transition.round_id == 0
    assert transition.batch_id == ""
    with pytest.raises(ValidationError):
        transition_model(
            id=1,
            feedback_key="feedback-1",
            from_status="failed",
            to_status="pending",
        )
    assert FeedbackProcessingItem(feedback_key="feedback-1").current_round_id == 0


def test_feedback_round_backfill_preserves_legacy_receipts_and_source(
    tmp_path: Path,
):
    db_path = tmp_path / "legacy-rounds.sqlite3"
    store_module._INITIALIZED_STORE_PATHS.discard(db_path.resolve())
    store = AutoReplyStore(db_path)
    for key, comment in (
        ("feedback-pending", "pending original"),
        ("feedback-processing", "processing original"),
        ("feedback-resolved", "resolved original"),
    ):
        store.upsert_feedback_event(
            key=key,
            feedback_token=f"token-{key}",
            comment=comment,
            original_text=f"source-{key}",
        )

    with store._connect() as db:
        db.execute(
            """
            insert into feedback_processing_batches (
                batch_id, status, requested_count, created_at, updated_at, resolved_at
            ) values
                ('batch-processing', 'processing', 1, '2026-08-01 01:00:00',
                 '2026-08-01 02:00:00', ''),
                ('batch-resolved', 'resolved', 1, '2026-08-02 01:00:00',
                 '2026-08-02 03:00:00', '2026-08-02 03:00:00')
            """
        )
        db.execute(
            """
            update feedback_processing_items
               set batch_id='batch-processing', status='processing',
                   workbench_task_id='task-processing',
                   workbench_turn_id='turn-processing',
                   attempt_id=11, agent_run_id=21, commit_sha=?,
                   test_evidence_json='{"pytest":{"exit_code":0}}',
                   restart_evidence_json='{"before_pid":101,"after_pid":102}',
                   health_evidence_json='{"ok":true,"status_code":200}',
                   note='processing note', resolved_at='',
                   created_at='2026-08-01 01:05:00',
                   updated_at='2026-08-01 02:05:00'
             where feedback_key='feedback-processing'
            """,
            ("a" * 40,),
        )
        db.execute(
            """
            update feedback_processing_items
               set batch_id='batch-resolved', status='resolved',
                   workbench_task_id='task-resolved',
                   workbench_turn_id='turn-resolved',
                   attempt_id=12, agent_run_id=22, commit_sha=?,
                   test_evidence_json='{"pytest":{"exit_code":0}}',
                   restart_evidence_json='{"before_pid":201,"after_pid":202}',
                   health_evidence_json='{"ok":true,"status_code":200}',
                   note='resolved note',
                   resolved_at='2026-08-02 03:00:00',
                   created_at='2026-08-02 01:05:00',
                   updated_at='2026-08-02 03:05:00'
             where feedback_key='feedback-resolved'
            """,
            ("b" * 40,),
        )
        db.execute(
            """
            update feedback_events
               set resolved_at='2026-08-02 04:00:00'
             where key='feedback-resolved'
            """
        )
        source_before = [
            tuple(row)
            for row in db.execute(
                """
                select key, comment, original_text, resolved_at
                  from feedback_events order by key
                """
            )
        ]
        db.execute("drop table if exists feedback_processing_transitions")
        db.execute("drop table if exists feedback_processing_rounds")
        item_columns = {
            row[1]
            for row in db.execute("pragma table_info(feedback_processing_items)")
        }
        if "current_round_id" in item_columns:
            db.execute(
                "alter table feedback_processing_items drop column current_round_id"
            )

    store_module._INITIALIZED_STORE_PATHS.discard(db_path.resolve())
    migrated = AutoReplyStore(db_path)
    with migrated._connect() as db:
        tables = {
            row[0]
            for row in db.execute(
                "select name from sqlite_master where type='table'"
            )
        }
        assert "feedback_processing_rounds" in tables
        rounds = [
            dict(row)
            for row in db.execute(
                "select * from feedback_processing_rounds order by feedback_key"
            )
        ]
        pointers = {
            row["feedback_key"]: row["current_round_id"]
            for row in db.execute(
                """
                select feedback_key, current_round_id
                  from feedback_processing_items order by feedback_key
                """
            )
        }
        source_after = [
            tuple(row)
            for row in db.execute(
                """
                select key, comment, original_text, resolved_at
                  from feedback_events order by key
                """
            )
        ]
        assert len(rounds) == 2
        assert {row["feedback_key"] for row in rounds} == {
            "feedback-processing",
            "feedback-resolved",
        }
        by_key = {row["feedback_key"]: row for row in rounds}
        processing = by_key["feedback-processing"]
        assert processing["round_number"] == 1
        assert processing["batch_id"] == "batch-processing"
        assert processing["status"] == "processing"
        assert processing["workbench_task_id"] == "task-processing"
        assert processing["workbench_turn_id"] == "turn-processing"
        assert processing["attempt_id"] == 11
        assert processing["agent_run_id"] == 21
        assert processing["commit_sha"] == "a" * 40
        assert processing["test_evidence_json"] == '{"pytest":{"exit_code":0}}'
        assert processing["restart_evidence_json"] == (
            '{"before_pid":101,"after_pid":102}'
        )
        assert processing["health_evidence_json"] == (
            '{"ok":true,"status_code":200}'
        )
        assert processing["note"] == "processing note"
        assert processing["started_at"] == "2026-08-01 01:05:00"
        assert processing["resolved_at"] == ""
        assert processing["created_at"] == "2026-08-01 01:05:00"
        assert processing["updated_at"] == "2026-08-01 02:05:00"

        resolved = by_key["feedback-resolved"]
        assert resolved["round_number"] == 1
        assert resolved["batch_id"] == "batch-resolved"
        assert resolved["status"] == "resolved"
        assert resolved["resolved_at"] == "2026-08-02 03:00:00"
        assert resolved["created_at"] == "2026-08-02 01:05:00"
        assert resolved["updated_at"] == "2026-08-02 03:05:00"
        legacy_item = db.execute(
            """
            select resolved_at, updated_at
              from feedback_processing_items
             where feedback_key='feedback-resolved'
            """
        ).fetchone()
        assert legacy_item is not None
        assert legacy_item["resolved_at"] == "2026-08-02 03:00:00"
        assert legacy_item["updated_at"] == "2026-08-02 03:05:00"
        assert pointers["feedback-pending"] == 0
        assert pointers["feedback-processing"] == processing["id"]
        assert pointers["feedback-resolved"] == resolved["id"]
        assert db.execute(
            "select count(*) from feedback_processing_transitions"
        ).fetchone()[0] == 0
        assert source_after == source_before

        db.execute(
            "update service_state set value='legacy' where key=?",
            (store_module.STORE_SCHEMA_VERSION_KEY,),
        )

    store_module._INITIALIZED_STORE_PATHS.discard(db_path.resolve())
    reinitialized = AutoReplyStore(db_path)
    with reinitialized._connect() as db:
        assert db.execute(
            "select count(*) from feedback_processing_rounds"
        ).fetchone()[0] == 2
        assert db.execute(
            "select count(*) from feedback_processing_transitions"
        ).fetchone()[0] == 0
        assert [
            tuple(row)
            for row in db.execute(
                """
                select key, comment, original_text, resolved_at
                  from feedback_events order by key
                """
            )
        ] == source_before


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
        test_evidence={"passed": {"exit_code": 0}},
        restart_evidence={"process": "new", "launchd_label": "com.ceo-agent-service.main", "before_pid": 1, "after_pid": 2},
        health_evidence={"status_code": 200, "ok": True, "url": "http://127.0.0.1:8765/healthz"},
        commit_sha="a" * 40,
    )
    store.associate_feedback_processing_turn("feedback-2", workbench_task_id="task-1", workbench_turn_id="turn-1", attempt_id=13, agent_run_id=35)
    store.patch_feedback_processing_item_evidence("feedback-2", test_evidence={"passed": {"exit_code": 0}}, restart_evidence={"process": "new", "launchd_label": "com.ceo-agent-service.main", "before_pid": 1, "after_pid": 2}, health_evidence={"status_code": 200, "ok": True, "url": "http://127.0.0.1:8765/healthz"}, commit_sha="a" * 40)
    assert store.resolve_feedback_processing_batch("batch-1", {"commit_sha": "a" * 40, "test_evidence": {"passed": {"exit_code": 0}}, "restart_evidence": {"launchd_label": "com.ceo-agent-service.main", "before_pid": 1, "after_pid": 2}, "health_evidence": {"status_code": 200, "ok": True, "url": "http://127.0.0.1:8765/healthz"}}, current_head="a" * 40) is True
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

    with pytest.raises(FeedbackProcessingClaimError) as error:
        store.claim_feedback_processing_items("batch-2", ["feedback-1"])
    assert error.value.error_code == FEEDBACK_PROCESSING_ALREADY_PROCESSING_ERROR
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
        attempt_role="consumer",
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
        health_evidence={"url": "http://127.0.0.1:8765/healthz", "status_code": 200, "ok": True},
    )
    validate_resolution_evidence(complete, current_head=head)
    for bad in (
        complete.model_copy(update={"commit_sha": "b" * 40}),
        complete.model_copy(update={"test_evidence": {"pytest": {"exit_code": 1}}}),
        complete.model_copy(update={"restart_evidence": {"launchd_label": "x", "before_pid": 1}}),
        complete.model_copy(update={"health_evidence": {"status_code": 503, "ok": True, "url": "http://127.0.0.1:8765/healthz"}}),
        complete.model_copy(update={"test_evidence": {"pytest": {"exit_code": "0"}}}),
        complete.model_copy(update={"restart_evidence": {"launchd_label": "com.ceo-agent-service.main", "before_pid": True, "after_pid": 2}}),
        complete.model_copy(update={"health_evidence": {"status_code": 200, "ok": False, "url": "http://127.0.0.1:8765/healthz"}}),
        complete.model_copy(update={"health_evidence": {"status_code": 200, "ok": True, "url": "http://localhost.evil:8765/healthz"}}),
        complete.model_copy(update={"health_evidence": {"status_code": 200, "ok": True, "url": "http://127.0.0.1:8765/health"}}),
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
        store.patch_feedback_processing_item_evidence(key, commit_sha="a" * 40, test_evidence={"pytest": {"exit_code": 0}}, restart_evidence={"launchd_label": "com.ceo-agent-service.main", "before_pid": 1, "after_pid": 2}, health_evidence={"status_code": 200, "ok": True, "url": "http://127.0.0.1:8765/healthz"})
    head = "a" * 40
    evidence = ResolutionEvidence(
        commit_sha=head,
        test_evidence={"pytest": {"exit_code": 0}},
        restart_evidence={"launchd_label": "com.ceo-agent-service.main", "before_pid": 1, "after_pid": 2},
        health_evidence={"status_code": 200, "ok": True, "url": "http://127.0.0.1:8765/healthz"},
    )
    assert store.resolve_feedback_processing_batch("batch-1", evidence, current_head=head)
    assert {store.get_feedback_processing_item(key).status for key in ("feedback-1", "feedback-2")} == {"resolved"}
    assert store.get_feedback_processing_batch("batch-1").status == "resolved"
