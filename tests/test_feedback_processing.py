import json
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
    FeedbackProcessingRound,
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


@pytest.mark.parametrize("round_number", [0, -1])
def test_feedback_round_model_requires_positive_round_number(round_number: int):
    with pytest.raises(ValidationError):
        FeedbackProcessingRound(
            id=1,
            feedback_key="feedback-positive-model",
            round_number=round_number,
            batch_id="batch-positive-model",
            status="processing",
        )


@pytest.mark.parametrize("round_number", [0, -1, 1.5, "1.5", "abc"])
def test_feedback_round_schema_requires_positive_round_number(
    tmp_path: Path, round_number: object
):
    store = AutoReplyStore(tmp_path / f"positive-round-{round_number}.sqlite3")
    stable_error = "feedback_processing_round_number_must_be_positive"
    with store._connect() as db:
        db.execute(
            """
            insert into feedback_processing_rounds (
                feedback_key, round_number, batch_id, status
            ) values ('feedback-valid-schema', 1, 'batch-valid-schema',
                      'processing')
            """
        )
        with pytest.raises(sqlite3.IntegrityError, match=stable_error):
            db.execute(
                """
                insert into feedback_processing_rounds (
                    feedback_key, round_number, batch_id, status
                ) values (?, ?, ?, 'processing')
                """,
                (
                    f"feedback-invalid-schema-{round_number}",
                    round_number,
                    f"batch-invalid-schema-{round_number}",
                ),
            )
        with pytest.raises(sqlite3.IntegrityError, match=stable_error):
            db.execute(
                """
                update feedback_processing_rounds set round_number=?
                 where feedback_key='feedback-valid-schema'
                """,
                (round_number,),
            )
        db.execute(
            """
            update feedback_processing_rounds set round_number=2
             where feedback_key='feedback-valid-schema'
            """
        )
        assert tuple(
            db.execute(
                """
                select round_number, typeof(round_number)
                  from feedback_processing_rounds
                 where feedback_key='feedback-valid-schema'
                """
            ).fetchone()
        ) == (2, "integer")


def test_feedback_round_positive_invariant_upgrades_old_table_without_rebuild(
    tmp_path: Path,
):
    db_path = tmp_path / "upgrade-positive-round.sqlite3"
    store_module._INITIALIZED_STORE_PATHS.discard(db_path.resolve())
    store = AutoReplyStore(db_path)
    with store._connect() as db:
        db.execute("drop table feedback_processing_rounds")
        db.execute(
            """
            create table feedback_processing_rounds (
                id integer primary key autoincrement,
                feedback_key text not null,
                round_number integer not null,
                batch_id text not null default '',
                status text not null
                    check (status in ('processing', 'resolved')),
                workbench_task_id text not null default '',
                workbench_turn_id text not null default '',
                attempt_id integer not null default 0,
                agent_run_id integer not null default 0,
                commit_sha text not null default '',
                test_evidence_json text not null default '{}',
                restart_evidence_json text not null default '{}',
                health_evidence_json text not null default '{}',
                note text not null default '',
                started_at text not null default '',
                resolved_at text not null default '',
                reopened_at text not null default '',
                reopen_reason text not null default '',
                created_at text not null default current_timestamp,
                updated_at text not null default current_timestamp,
                unique (feedback_key, round_number),
                unique (feedback_key, batch_id)
            )
            """
        )
        db.execute(
            """
            create index idx_feedback_processing_rounds_feedback
                on feedback_processing_rounds(feedback_key, round_number desc)
            """
        )
        db.execute(
            """
            create index idx_feedback_processing_rounds_batch
                on feedback_processing_rounds(batch_id)
            """
        )
        db.execute(
            """
            create trigger
                trg_feedback_processing_round_number_positive_insert
            before insert on feedback_processing_rounds
            when new.round_number <= 0
                 or typeof(new.round_number) = 'blob'
            begin
                select raise(
                    abort,
                    'feedback_processing_round_number_must_be_positive'
                );
            end
            """
        )
        db.execute(
            """
            create trigger
                trg_feedback_processing_round_number_positive_update
            before update of round_number on feedback_processing_rounds
            when new.round_number <= 0
                 or typeof(new.round_number) = 'blob'
            begin
                select raise(
                    abort,
                    'feedback_processing_round_number_must_be_positive'
                );
            end
            """
        )
        db.execute(
            """
            insert into feedback_processing_rounds (
                feedback_key, round_number, batch_id, status, note
            ) values ('feedback-preserved', 1, 'batch-preserved',
                      'processing', 'preserve this row')
            """
        )

    assert store._schema_is_current() is False
    store_module._INITIALIZED_STORE_PATHS.discard(db_path.resolve())
    upgraded = AutoReplyStore(db_path)
    stable_error = "feedback_processing_round_number_must_be_positive"
    with upgraded._connect() as db:
        preserved = db.execute(
            """
            select feedback_key, round_number, batch_id, note
              from feedback_processing_rounds
             where feedback_key='feedback-preserved'
            """
        ).fetchone()
        assert tuple(preserved) == (
            "feedback-preserved",
            1,
            "batch-preserved",
            "preserve this row",
        )
        triggers = {
            row[0]
            for row in db.execute(
                """
                select name from sqlite_master
                 where type='trigger' and tbl_name='feedback_processing_rounds'
                """
            )
        }
        assert {
            "trg_feedback_processing_round_integer_v2_insert",
            "trg_feedback_processing_round_integer_v2_update",
        } <= triggers

        for round_number in (0, -1, 1.5, "1.5", "abc"):
            with pytest.raises(sqlite3.IntegrityError, match=stable_error):
                db.execute(
                    """
                    insert into feedback_processing_rounds (
                        feedback_key, round_number, batch_id, status
                    ) values (?, ?, ?, 'processing')
                    """,
                    (
                        f"feedback-invalid-{round_number}",
                        round_number,
                        f"batch-invalid-{round_number}",
                    ),
                )
            with pytest.raises(sqlite3.IntegrityError, match=stable_error):
                db.execute(
                    """
                    update feedback_processing_rounds set round_number=?
                     where feedback_key='feedback-preserved'
                    """,
                    (round_number,),
                )

        db.execute(
            """
            update feedback_processing_rounds set round_number=2
             where feedback_key='feedback-preserved'
            """
        )
        assert tuple(
            db.execute(
                """
                select round_number, typeof(round_number)
                  from feedback_processing_rounds
                 where feedback_key='feedback-preserved'
                """
            ).fetchone()
        ) == (2, "integer")


@pytest.mark.parametrize(
    ("round_number", "storage_type"),
    [(1.5, "real"), ("abc", "text"), (0, "integer"), (-1, "integer")],
)
def test_feedback_round_upgrade_rejects_existing_invalid_storage_without_mutation(
    tmp_path: Path,
    round_number: object,
    storage_type: str,
):
    db_path = tmp_path / f"invalid-legacy-round-{round_number}.sqlite3"
    store_module._INITIALIZED_STORE_PATHS.discard(db_path.resolve())
    store = AutoReplyStore(db_path)
    with store._connect() as db:
        db.execute("drop table feedback_processing_rounds")
        db.execute(
            """
            create table feedback_processing_rounds (
                id integer primary key autoincrement,
                feedback_key text not null,
                round_number integer not null,
                batch_id text not null default '',
                status text not null
                    check (status in ('processing', 'resolved')),
                workbench_task_id text not null default '',
                workbench_turn_id text not null default '',
                attempt_id integer not null default 0,
                agent_run_id integer not null default 0,
                commit_sha text not null default '',
                test_evidence_json text not null default '{}',
                restart_evidence_json text not null default '{}',
                health_evidence_json text not null default '{}',
                note text not null default '',
                started_at text not null default '',
                resolved_at text not null default '',
                reopened_at text not null default '',
                reopen_reason text not null default '',
                created_at text not null default current_timestamp,
                updated_at text not null default current_timestamp,
                unique (feedback_key, round_number),
                unique (feedback_key, batch_id)
            )
            """
        )
        db.execute(
            """
            create index idx_feedback_processing_rounds_feedback
                on feedback_processing_rounds(feedback_key, round_number desc)
            """
        )
        db.execute(
            """
            create index idx_feedback_processing_rounds_batch
                on feedback_processing_rounds(batch_id)
            """
        )
        db.execute(
            """
            insert into feedback_processing_rounds (
                feedback_key, round_number, batch_id, status, note
            ) values ('feedback-invalid-legacy', ?, 'batch-invalid-legacy',
                      'processing', 'must remain byte-for-byte equivalent')
            """,
            (round_number,),
        )
        for suffix, event in (
            ("insert", "insert"),
            ("update", "update of round_number"),
        ):
            db.execute(
                f"""
                create trigger
                    trg_feedback_processing_round_number_positive_{suffix}
                before {event} on feedback_processing_rounds
                when new.round_number <= 0
                     or typeof(new.round_number) = 'blob'
                begin
                    select raise(
                        abort,
                        'feedback_processing_round_number_must_be_positive'
                    );
                end
                """
            )

    stable_error = "schema_migration_invalid_feedback_processing_round_number"
    integrity_error_type = (
        store_module.FeedbackProcessingRoundMigrationIntegrityError
    )
    assert store._schema_is_current() is False
    store_module._INITIALIZED_STORE_PATHS.discard(db_path.resolve())
    with pytest.raises(integrity_error_type, match=stable_error) as first_error:
        AutoReplyStore(db_path)
    assert first_error.value.error_code == stable_error
    assert str(first_error.value) == stable_error

    def invalid_row_snapshot() -> tuple[object, str, str]:
        with sqlite3.connect(db_path) as db:
            return tuple(
                db.execute(
                    """
                    select round_number, typeof(round_number), note
                      from feedback_processing_rounds
                     where feedback_key='feedback-invalid-legacy'
                    """
                ).fetchone()
            )

    assert invalid_row_snapshot() == (
        round_number,
        storage_type,
        "must remain byte-for-byte equivalent",
    )
    with sqlite3.connect(db_path) as db:
        triggers = {
            row[0]
            for row in db.execute(
                """
                select name from sqlite_master
                 where type='trigger' and tbl_name='feedback_processing_rounds'
                """
            )
        }
    assert {
        "trg_feedback_processing_round_integer_v2_insert",
        "trg_feedback_processing_round_integer_v2_update",
    } <= triggers

    store_module._INITIALIZED_STORE_PATHS.discard(db_path.resolve())
    with pytest.raises(integrity_error_type, match=stable_error) as second_error:
        AutoReplyStore(db_path)
    assert second_error.value.error_code == stable_error
    assert str(second_error.value) == stable_error
    assert invalid_row_snapshot() == (
        round_number,
        storage_type,
        "must remain byte-for-byte equivalent",
    )


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


def test_feedback_round_backfill_self_heals_current_schema_and_interruption(
    tmp_path: Path,
):
    db_path = tmp_path / "self-heal-rounds.sqlite3"
    store_module._INITIALIZED_STORE_PATHS.discard(db_path.resolve())
    store = AutoReplyStore(db_path)
    store.upsert_feedback_event(
        key="feedback-claimed",
        feedback_token="token-claimed",
        comment="claimed source",
        original_text="claimed original",
    )
    store.upsert_feedback_event(
        key="feedback-pending",
        feedback_token="token-pending",
        comment="pending source",
        original_text="pending original",
    )
    claimed = store.claim_feedback_processing_items(
        "batch-self-heal", ["feedback-claimed"]
    )
    assert len(claimed) == 1
    store.associate_feedback_processing_turn(
        "feedback-claimed",
        workbench_task_id="task-self-heal",
        workbench_turn_id="turn-self-heal",
        attempt_id=31,
        agent_run_id=41,
    )
    store.patch_feedback_processing_item_evidence(
        "feedback-claimed",
        commit_sha="c" * 40,
        test_evidence={"pytest": {"exit_code": 0}},
        restart_evidence={"before_pid": 301, "after_pid": 302},
        health_evidence={"ok": True, "status_code": 200},
        note="self-heal note",
    )

    expected_item = store.get_feedback_processing_item("feedback-claimed")
    expected_pending = store.get_feedback_processing_item("feedback-pending")
    expected_source = store.get_feedback_event("feedback-claimed")
    assert expected_item is not None
    assert expected_item.current_round_id == 0
    assert expected_pending is not None
    assert expected_pending.current_round_id == 0
    assert expected_source is not None
    with store._connect() as db:
        assert db.execute(
            "select count(*) from feedback_processing_rounds"
        ).fetchone()[0] == 0

    # A new process sees a structurally current schema. It must still repair
    # eligible legacy projections without forcing a schema-version downgrade.
    store_module._INITIALIZED_STORE_PATHS.discard(db_path.resolve())
    repaired = AutoReplyStore(db_path)
    repaired_item = repaired.get_feedback_processing_item("feedback-claimed")
    repaired_pending = repaired.get_feedback_processing_item("feedback-pending")
    repaired_source = repaired.get_feedback_event("feedback-claimed")
    assert repaired_item is not None
    assert repaired_pending is not None
    assert repaired_source is not None
    with repaired._connect() as db:
        rows = db.execute(
            "select * from feedback_processing_rounds order by id"
        ).fetchall()
        assert len(rows) == 1
        round_row = dict(rows[0])
        assert repaired_item.current_round_id == round_row["id"]
        assert round_row["feedback_key"] == expected_item.feedback_key
        assert round_row["round_number"] == 1
        assert round_row["batch_id"] == expected_item.batch_id
        assert round_row["status"] == expected_item.status
        assert round_row["workbench_task_id"] == expected_item.workbench_task_id
        assert round_row["workbench_turn_id"] == expected_item.workbench_turn_id
        assert round_row["attempt_id"] == expected_item.attempt_id
        assert round_row["agent_run_id"] == expected_item.agent_run_id
        assert round_row["commit_sha"] == expected_item.commit_sha
        assert json.loads(round_row["test_evidence_json"]) == (
            expected_item.test_evidence
        )
        assert json.loads(round_row["restart_evidence_json"]) == (
            expected_item.restart_evidence
        )
        assert json.loads(round_row["health_evidence_json"]) == (
            expected_item.health_evidence
        )
        assert round_row["note"] == expected_item.note
        assert round_row["started_at"] == expected_item.created_at
        assert round_row["resolved_at"] == expected_item.resolved_at
        assert round_row["created_at"] == expected_item.created_at
        assert round_row["updated_at"] == expected_item.updated_at
        assert repaired_pending == expected_pending
        assert repaired_pending.current_round_id == 0
        assert db.execute(
            """
            select count(*) from feedback_processing_rounds
             where feedback_key='feedback-pending'
            """
        ).fetchone()[0] == 0
        assert db.execute(
            "select count(*) from feedback_processing_transitions"
        ).fetchone()[0] == 0

        # Simulate an interrupted migration after the additive tables and
        # pointer column exist but before the eligible round is durable.
        db.execute(
            "delete from feedback_processing_rounds where feedback_key=?",
            ("feedback-claimed",),
        )
        db.execute(
            """
            update feedback_processing_items set current_round_id=0
             where feedback_key=?
            """,
            ("feedback-claimed",),
        )

    store_module._INITIALIZED_STORE_PATHS.discard(db_path.resolve())
    repaired_again = AutoReplyStore(db_path)
    repaired_again_item = repaired_again.get_feedback_processing_item(
        "feedback-claimed"
    )
    assert repaired_again_item is not None
    with repaired_again._connect() as db:
        rows = db.execute(
            """
            select * from feedback_processing_rounds
             where feedback_key='feedback-claimed'
            """
        ).fetchall()
        assert len(rows) == 1
        assert rows[0]["round_number"] == 1
        assert repaired_again_item.current_round_id == rows[0]["id"]
        repaired_round_id = rows[0]["id"]
        assert repaired_again_item.model_copy(
            update={"current_round_id": expected_item.current_round_id}
        ) == expected_item
        assert repaired_again.get_feedback_event("feedback-claimed") == expected_source
        assert db.execute(
            "select count(*) from feedback_processing_transitions"
        ).fetchone()[0] == 0

    store_module._INITIALIZED_STORE_PATHS.discard(db_path.resolve())
    idempotent = AutoReplyStore(db_path)
    with idempotent._connect() as db:
        rows = db.execute(
            """
            select id, round_number from feedback_processing_rounds
             where feedback_key='feedback-claimed'
            """
        ).fetchall()
        assert [(row["id"], row["round_number"]) for row in rows] == [
            (repaired_round_id, 1)
        ]
        assert db.execute(
            "select count(*) from feedback_processing_transitions"
        ).fetchone()[0] == 0


def test_feedback_round_reconciliation_requires_pointer_ownership_and_batch(
    tmp_path: Path,
):
    db_path = tmp_path / "pointer-integrity.sqlite3"
    store_module._INITIALIZED_STORE_PATHS.discard(db_path.resolve())
    store = AutoReplyStore(db_path)

    def claim_with_evidence(feedback_key: str, batch_id: str, marker: str) -> None:
        store.upsert_feedback_event(
            key=feedback_key,
            feedback_token=f"token-{feedback_key}",
            comment=f"source-{marker}",
        )
        assert store.claim_feedback_processing_items(batch_id, [feedback_key])
        store.associate_feedback_processing_turn(
            feedback_key,
            workbench_task_id=f"task-{marker}",
            workbench_turn_id=f"turn-{marker}",
            attempt_id=51,
            agent_run_id=61,
        )
        store.patch_feedback_processing_item_evidence(
            feedback_key,
            commit_sha="d" * 40,
            test_evidence={"marker": marker, "exit_code": 0},
            restart_evidence={"marker": marker},
            health_evidence={"marker": marker},
            note=f"projection-{marker}",
        )

    claim_with_evidence("feedback-cross-a", "batch-cross-a", "cross-a")
    claim_with_evidence("feedback-cross-b", "batch-cross-b", "cross-b")
    claim_with_evidence("feedback-history", "batch-current", "history-current")
    claim_with_evidence(
        "feedback-unrepairable", "batch-unrepairable-current", "unrepairable"
    )
    items_before = {
        key: store.get_feedback_processing_item(key)
        for key in (
            "feedback-cross-a",
            "feedback-cross-b",
            "feedback-history",
            "feedback-unrepairable",
        )
    }
    assert all(item is not None for item in items_before.values())

    with store._connect() as db:
        def insert_round(
            feedback_key: str,
            round_number: int,
            batch_id: str,
            marker: str,
        ) -> int:
            cursor = db.execute(
                """
                insert into feedback_processing_rounds (
                    feedback_key, round_number, batch_id, status,
                    test_evidence_json, note
                ) values (?, ?, ?, 'processing', ?, ?)
                """,
                (
                    feedback_key,
                    round_number,
                    batch_id,
                    json.dumps({"round_marker": marker}, sort_keys=True),
                    f"round-{marker}",
                ),
            )
            return int(cursor.lastrowid)

        cross_a_round_id = insert_round(
            "feedback-cross-a", 1, "batch-cross-a", "cross-a"
        )
        cross_b_round_id = insert_round(
            "feedback-cross-b", 1, "batch-cross-b", "cross-b"
        )
        history_old_round_id = insert_round(
            "feedback-history", 1, "batch-old", "history-old"
        )
        history_current_round_id = insert_round(
            "feedback-history", 2, "batch-current", "history-current"
        )
        unrepairable_old_round_id = insert_round(
            "feedback-unrepairable", 1, "batch-unrepairable-old", "unrepairable-old"
        )
        db.execute(
            """
            update feedback_processing_items set current_round_id=?
             where feedback_key='feedback-cross-a'
            """,
            (cross_b_round_id,),
        )
        db.execute(
            """
            update feedback_processing_items set current_round_id=?
             where feedback_key='feedback-cross-b'
            """,
            (cross_b_round_id,),
        )
        db.execute(
            """
            update feedback_processing_items set current_round_id=?
             where feedback_key='feedback-history'
            """,
            (history_old_round_id,),
        )
        db.execute(
            """
            update feedback_processing_items set current_round_id=?
             where feedback_key='feedback-unrepairable'
            """,
            (unrepairable_old_round_id,),
        )
        rounds_before = [
            tuple(row)
            for row in db.execute(
                """
                select id, feedback_key, round_number, batch_id,
                       status, test_evidence_json, note
                  from feedback_processing_rounds order by id
                """
            )
        ]

    store_module._INITIALIZED_STORE_PATHS.discard(db_path.resolve())
    repaired = AutoReplyStore(db_path)
    repaired_items = {
        key: repaired.get_feedback_processing_item(key)
        for key in items_before
    }
    assert repaired_items["feedback-cross-a"].current_round_id == cross_a_round_id
    assert repaired_items["feedback-cross-b"].current_round_id == cross_b_round_id
    assert (
        repaired_items["feedback-history"].current_round_id
        == history_current_round_id
    )
    assert repaired_items["feedback-unrepairable"].current_round_id == 0
    for key, repaired_item in repaired_items.items():
        assert repaired_item is not None
        assert repaired_item.model_copy(update={"current_round_id": 0}) == (
            items_before[key].model_copy(update={"current_round_id": 0})
        )

    with repaired._connect() as db:
        rounds_after = [
            tuple(row)
            for row in db.execute(
                """
                select id, feedback_key, round_number, batch_id,
                       status, test_evidence_json, note
                  from feedback_processing_rounds order by id
                """
            )
        ]
        assert rounds_after == rounds_before
        assert len(rounds_after) == 5
        assert db.execute(
            "select count(*) from feedback_processing_transitions"
        ).fetchone()[0] == 0

    store_module._INITIALIZED_STORE_PATHS.discard(db_path.resolve())
    reopened = AutoReplyStore(db_path)
    assert {
        key: reopened.get_feedback_processing_item(key).current_round_id
        for key in repaired_items
    } == {
        "feedback-cross-a": cross_a_round_id,
        "feedback-cross-b": cross_b_round_id,
        "feedback-history": history_current_round_id,
        "feedback-unrepairable": 0,
    }
    with reopened._connect() as db:
        assert [
            tuple(row)
            for row in db.execute(
                """
                select id, feedback_key, round_number, batch_id,
                       status, test_evidence_json, note
                  from feedback_processing_rounds order by id
                """
            )
        ] == rounds_before


def test_feedback_round_reconciliation_clears_ambiguous_matching_history(
    tmp_path: Path,
):
    db_path = tmp_path / "ambiguous-pointer.sqlite3"
    store_module._INITIALIZED_STORE_PATHS.discard(db_path.resolve())
    store = AutoReplyStore(db_path)
    store.upsert_feedback_event(
        key="feedback-ambiguous",
        feedback_token="token-ambiguous",
        comment="ambiguous source",
    )
    assert store.claim_feedback_processing_items(
        "batch-ambiguous", ["feedback-ambiguous"]
    )
    store.patch_feedback_processing_item_evidence(
        "feedback-ambiguous",
        test_evidence={"exit_code": 0},
        note="ambiguous projection",
    )

    with store._connect() as db:
        db.execute("drop table feedback_processing_rounds")
        db.execute(
            """
            create table feedback_processing_rounds (
                id integer primary key autoincrement,
                feedback_key text not null,
                round_number integer not null check (round_number > 0),
                batch_id text not null default '',
                status text not null
                    check (status in ('processing', 'resolved')),
                workbench_task_id text not null default '',
                workbench_turn_id text not null default '',
                attempt_id integer not null default 0,
                agent_run_id integer not null default 0,
                commit_sha text not null default '',
                test_evidence_json text not null default '{}',
                restart_evidence_json text not null default '{}',
                health_evidence_json text not null default '{}',
                note text not null default '',
                started_at text not null default '',
                resolved_at text not null default '',
                reopened_at text not null default '',
                reopen_reason text not null default '',
                created_at text not null default current_timestamp,
                updated_at text not null default current_timestamp
            )
            """
        )
        db.execute(
            """
            create index idx_feedback_processing_rounds_feedback
                on feedback_processing_rounds(feedback_key, round_number desc)
            """
        )
        db.execute(
            """
            create index idx_feedback_processing_rounds_batch
                on feedback_processing_rounds(batch_id)
            """
        )
        db.execute(
            """
            insert into feedback_processing_rounds (
                feedback_key, round_number, batch_id, status,
                test_evidence_json, note
            ) values
                ('feedback-ambiguous', 1, 'batch-ambiguous', 'processing',
                 '{"round":"one"}', 'ambiguous-one'),
                ('feedback-ambiguous', 2, 'batch-ambiguous', 'processing',
                 '{"round":"two"}', 'ambiguous-two')
            """
        )
        db.execute(
            """
            update feedback_processing_items set current_round_id=999999
             where feedback_key='feedback-ambiguous'
            """
        )
        rounds_before = [
            tuple(row)
            for row in db.execute(
                """
                select id, round_number, test_evidence_json, note
                  from feedback_processing_rounds order by id
                """
            )
        ]

    store_module._INITIALIZED_STORE_PATHS.discard(db_path.resolve())
    repaired = AutoReplyStore(db_path)
    repaired_item = repaired.get_feedback_processing_item("feedback-ambiguous")
    assert repaired_item is not None
    assert repaired_item.current_round_id == 0
    assert repaired_item.test_evidence == {"exit_code": 0}
    assert repaired_item.note == "ambiguous projection"
    with repaired._connect() as db:
        assert [
            tuple(row)
            for row in db.execute(
                """
                select id, round_number, test_evidence_json, note
                  from feedback_processing_rounds order by id
                """
            )
        ] == rounds_before
        assert db.execute(
            "select count(*) from feedback_processing_transitions"
        ).fetchone()[0] == 0

    store_module._INITIALIZED_STORE_PATHS.discard(db_path.resolve())
    reopened = AutoReplyStore(db_path)
    assert reopened.get_feedback_processing_item(
        "feedback-ambiguous"
    ).current_round_id == 0


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
