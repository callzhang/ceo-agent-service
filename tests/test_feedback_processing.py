import json
import inspect
import sqlite3
import time
from pathlib import Path
from threading import Event, Thread

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
    FeedbackProcessingTransition,
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
    assert round_item.receipt_version == 1
    assert round_item.reopened_at == ""
    for receipt_version in (0, 3, "2"):
        with pytest.raises(ValidationError):
            round_model(
                id=1,
                feedback_key="feedback-1",
                round_number=1,
                batch_id="batch-1",
                status="processing",
                receipt_version=receipt_version,
            )
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


def test_feedback_round_schema_rejects_unknown_receipt_version(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "receipt-version-schema.sqlite3")
    with store._connect() as db:
        with pytest.raises(sqlite3.IntegrityError):
            db.execute(
                """
                insert into feedback_processing_rounds (
                    feedback_key, round_number, batch_id, status, receipt_version
                ) values ('feedback-invalid-receipt', 1, 'batch-invalid-receipt',
                          'processing', 3)
                """
            )


@pytest.mark.parametrize(
    ("trigger_name_stem", "clear_initialized_cache"),
    (
        ("trg_feedback_processing_round_number_positive", True),
        ("trg_feedback_processing_round_integer_v2", False),
    ),
)
def test_feedback_round_positive_invariant_upgrades_semantic_decoy_without_rebuild(
    tmp_path: Path,
    trigger_name_stem: str,
    clear_initialized_cache: bool,
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
            f"""
            create trigger {trigger_name_stem}_insert
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
            f"""
            create trigger {trigger_name_stem}_update
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
    if clear_initialized_cache:
        store_module._INITIALIZED_STORE_PATHS.discard(db_path.resolve())
    upgraded = AutoReplyStore(db_path)
    assert upgraded._schema_is_current() is True
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
    ("canonical_literal", "decoy_literal"),
    (
        ("'integer'", "'INTEGER'"),
        (
            "'feedback_processing_round_number_must_be_positive'",
            "'FEEDBACK_PROCESSING_ROUND_NUMBER_MUST_BE_POSITIVE'",
        ),
    ),
)
def test_cached_round_manifest_replaces_literal_case_decoys(
    tmp_path: Path,
    canonical_literal: str,
    decoy_literal: str,
):
    db_path = tmp_path / "cached-literal-case-decoy.sqlite3"
    path_key = db_path.resolve()
    store_module._INITIALIZED_STORE_PATHS.discard(path_key)
    store = AutoReplyStore(db_path)
    assert path_key in store_module._INITIALIZED_STORE_PATHS

    with store._connect() as db:
        db.execute(
            "drop trigger trg_feedback_processing_round_integer_v2_insert"
        )
        db.execute(
            "drop trigger trg_feedback_processing_round_integer_v2_update"
        )
        db.execute(
            store_module.FEEDBACK_PROCESSING_ROUND_INTEGER_INSERT_TRIGGER_SQL.replace(
                canonical_literal,
                decoy_literal,
            )
        )
        db.execute(
            store_module.FEEDBACK_PROCESSING_ROUND_INTEGER_UPDATE_TRIGGER_SQL.replace(
                canonical_literal,
                decoy_literal,
            )
        )

    repaired = AutoReplyStore(db_path)
    stable_error = "feedback_processing_round_number_must_be_positive"
    with repaired._connect() as db:
        db.execute(
            """
            insert into feedback_processing_rounds (
                feedback_key, round_number, batch_id, status
            ) values ('feedback-literal-case', 1,
                      'batch-literal-case', 'processing')
            """
        )
        db.execute(
            """
            update feedback_processing_rounds set round_number=2
             where feedback_key='feedback-literal-case'
            """
        )
        assert tuple(
            db.execute(
                """
                select round_number, typeof(round_number)
                  from feedback_processing_rounds
                 where feedback_key='feedback-literal-case'
                """
            ).fetchone()
        ) == (2, "integer")

        for round_number in (0, -1, 1.5, "1.5", "abc"):
            with pytest.raises(sqlite3.IntegrityError) as insert_error:
                db.execute(
                    """
                    insert into feedback_processing_rounds (
                        feedback_key, round_number, batch_id, status
                    ) values (?, ?, ?, 'processing')
                    """,
                    (
                        f"feedback-literal-invalid-{round_number}",
                        round_number,
                        f"batch-literal-invalid-{round_number}",
                    ),
                )
            assert str(insert_error.value) == stable_error
            with pytest.raises(sqlite3.IntegrityError) as update_error:
                db.execute(
                    """
                    update feedback_processing_rounds set round_number=?
                     where feedback_key='feedback-literal-case'
                    """,
                    (round_number,),
                )
            assert str(update_error.value) == stable_error

        trigger_sql = {
            row["name"]: row["sql"]
            for row in db.execute(
                """
                select name, sql from sqlite_master
                 where type='trigger'
                   and name in (
                       'trg_feedback_processing_round_integer_v2_insert',
                       'trg_feedback_processing_round_integer_v2_update'
                   )
                """
            )
        }
        assert decoy_literal not in trigger_sql[
            "trg_feedback_processing_round_integer_v2_insert"
        ]
        assert decoy_literal not in trigger_sql[
            "trg_feedback_processing_round_integer_v2_update"
        ]
        assert canonical_literal in trigger_sql[
            "trg_feedback_processing_round_integer_v2_insert"
        ]
        assert canonical_literal in trigger_sql[
            "trg_feedback_processing_round_integer_v2_update"
        ]


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
    assert triggers == {
        "trg_feedback_processing_round_number_positive_insert",
        "trg_feedback_processing_round_number_positive_update",
    }

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


def _rebuild_feedback_round_table_without_integer_check(
    db: sqlite3.Connection,
) -> None:
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


def test_cached_store_revalidates_invalid_round_storage_before_fast_return(
    tmp_path: Path,
):
    db_path = tmp_path / "cached-invalid-round.sqlite3"
    store_module._INITIALIZED_STORE_PATHS.discard(db_path.resolve())
    AutoReplyStore(db_path)
    assert db_path.resolve() in store_module._INITIALIZED_STORE_PATHS

    with sqlite3.connect(db_path) as db:
        _rebuild_feedback_round_table_without_integer_check(db)
        db.execute(
            """
            insert into feedback_processing_rounds (
                feedback_key, round_number, batch_id, status, note
            ) values ('feedback-cached-invalid', 1.5, 'batch-cached-invalid',
                      'processing', 'preserve cached invalid row')
            """
        )

    stable_error = "schema_migration_invalid_feedback_processing_round_number"
    with pytest.raises(
        store_module.FeedbackProcessingRoundMigrationIntegrityError,
        match=stable_error,
    ):
        AutoReplyStore(db_path)

    assert db_path.resolve() not in store_module._INITIALIZED_STORE_PATHS
    with sqlite3.connect(db_path) as db:
        assert db.execute(
            """
            select round_number, typeof(round_number), note
              from feedback_processing_rounds
             where feedback_key='feedback-cached-invalid'
            """
        ).fetchone() == (1.5, "real", "preserve cached invalid row")


def test_store_rejects_post_migration_manifest_before_marker_or_cache(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    db_path = tmp_path / "post-migration-manifest-invalid.sqlite3"
    path_key = db_path.resolve()
    store_module._INITIALIZED_STORE_PATHS.discard(path_key)
    stable_error = "schema_migration_invalid_feedback_processing_schema"
    monkeypatch.setitem(
        store_module.STORE_SCHEMA_REQUIRED_TRIGGER_DEFINITIONS,
        "trg_feedback_processing_round_integer_v2_insert",
        "canonical definition deliberately unavailable",
    )
    schema_error_type = store_module.FeedbackProcessingSchemaIntegrityError

    with pytest.raises(schema_error_type, match=stable_error) as error:
        AutoReplyStore(db_path)
    assert error.value.error_code == stable_error
    assert str(error.value) == stable_error

    assert path_key not in store_module._INITIALIZED_STORE_PATHS
    with sqlite3.connect(db_path) as db:
        marker = db.execute(
            "select value from service_state where key=?",
            (store_module.STORE_SCHEMA_VERSION_KEY,),
        ).fetchone()
    assert marker is None


def test_feedback_round_guard_installation_blocks_concurrent_invalid_writer(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    db_path = tmp_path / "atomic-round-guard.sqlite3"
    store_module._INITIALIZED_STORE_PATHS.discard(db_path.resolve())
    store = AutoReplyStore(db_path)
    with store._connect() as db:
        _rebuild_feedback_round_table_without_integer_check(db)
        db.execute(
            """
            insert into feedback_processing_rounds (
                feedback_key, round_number, batch_id, status
            ) values ('feedback-concurrent-existing', 1,
                      'batch-concurrent-existing', 'processing')
            """
        )

    guard_window = Event()
    writer_attempted = Event()
    writer_finished = Event()
    original_open_connection = AutoReplyStore._open_connection

    def traced_open_connection(self: AutoReplyStore) -> sqlite3.Connection:
        db = original_open_connection(self)

        def trace(sql: str) -> None:
            normalized = " ".join(sql.casefold().split())
            if (
                "create trigger" in normalized
                and "trg_feedback_processing_round_integer_v2_update"
                in normalized
            ):
                guard_window.set()
                writer_attempted.wait(timeout=5)
                writer_finished.wait(timeout=0.25)

        db.set_trace_callback(trace)
        return db

    monkeypatch.setattr(
        AutoReplyStore,
        "_open_connection",
        traced_open_connection,
    )
    writer_results: dict[str, str] = {}
    migration_errors: list[BaseException] = []

    def concurrent_writer() -> None:
        if not guard_window.wait(timeout=5):
            writer_results["coordination"] = "guard window not observed"
            writer_finished.set()
            return
        with sqlite3.connect(db_path, timeout=5) as db:
            writer_attempted.set()
            operations = {
                "insert": (
                    """
                    insert into feedback_processing_rounds (
                        feedback_key, round_number, batch_id, status
                    ) values ('feedback-concurrent-new', 1.5,
                              'batch-concurrent-new', 'processing')
                    """,
                    (),
                ),
                "update": (
                    """
                    update feedback_processing_rounds
                       set round_number='abc'
                     where feedback_key='feedback-concurrent-existing'
                    """,
                    (),
                ),
            }
            for operation, (sql, parameters) in operations.items():
                try:
                    db.execute(sql, parameters)
                except sqlite3.IntegrityError as exc:
                    writer_results[operation] = str(exc)
                else:
                    writer_results[operation] = "succeeded"
            db.commit()
        writer_finished.set()

    def migrate() -> None:
        try:
            AutoReplyStore(db_path)
        except BaseException as exc:  # pragma: no cover - asserted below
            migration_errors.append(exc)

    store_module._INITIALIZED_STORE_PATHS.discard(db_path.resolve())
    writer = Thread(target=concurrent_writer)
    migrator = Thread(target=migrate)
    writer.start()
    migrator.start()
    migrator.join(timeout=10)
    writer.join(timeout=10)

    assert not migrator.is_alive()
    assert not writer.is_alive()
    assert migration_errors == []
    stable_error = "feedback_processing_round_number_must_be_positive"
    assert writer_results == {
        "insert": stable_error,
        "update": stable_error,
    }
    with sqlite3.connect(db_path) as db:
        assert db.execute(
            """
            select round_number, typeof(round_number)
              from feedback_processing_rounds
             where feedback_key='feedback-concurrent-existing'
            """
        ).fetchone() == (1, "integer")
        assert db.execute(
            """
            select count(*) from feedback_processing_rounds
             where feedback_key='feedback-concurrent-new'
            """
        ).fetchone()[0] == 0


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
    # Simulate a Task 1-era interrupted migration after the projection was
    # written but before its round and transition became durable.
    with store._connect() as db:
        db.execute(
            "delete from feedback_processing_transitions where feedback_key=?",
            ("feedback-claimed",),
        )
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
        db.execute("delete from feedback_processing_transitions")
        db.execute("delete from feedback_processing_rounds")
        db.execute("update feedback_processing_items set current_round_id=0")

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
        ).fetchone()[0] == 1

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
    assert store.resolve_feedback_processing_batch("batch-1", {"commit_sha": "a" * 40, "test_evidence": {"passed": {"exit_code": 0}}, "restart_evidence": {"process": "new", "launchd_label": "com.ceo-agent-service.main", "before_pid": 1, "after_pid": 2}, "health_evidence": {"status_code": 200, "ok": True, "url": "http://127.0.0.1:8765/healthz"}, "backlog_evidence": {"processing": 0, "failed": 0, "retryable": 0}}, commit_is_ancestor=True) is True
    assert store.get_feedback_processing_batch("batch-1").status == "resolved"
    assert store.resolve_feedback_processing_batch(
        "batch-1", commit_is_ancestor=True
    ) is True


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


def test_resolution_evidence_requires_prevalidated_ancestry_and_success_receipts():
    head = "a" * 40
    complete = ResolutionEvidence(
        commit_sha=head,
        test_evidence={"pytest": {"exit_code": 0}},
        restart_evidence={"launchd_label": "com.ceo-agent-service.main", "before_pid": 1, "after_pid": 2},
        health_evidence={"url": "http://127.0.0.1:8765/healthz", "status_code": 200, "ok": True},
        backlog_evidence={"processing": 0, "failed": 0, "retryable": 0},
    )
    validate_resolution_evidence(complete, commit_is_ancestor=True)
    for bad in (
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
            validate_resolution_evidence(bad, commit_is_ancestor=True)


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
        backlog_evidence={"processing": 0, "failed": 0, "retryable": 0},
    )
    assert store.resolve_feedback_processing_batch(
        "batch-1", evidence, commit_is_ancestor=True
    )
    assert {store.get_feedback_processing_item(key).status for key in ("feedback-1", "feedback-2")} == {"resolved"}
    assert store.get_feedback_processing_batch("batch-1").status == "resolved"


def _seed_resolved_feedback_round(
    store: AutoReplyStore,
    feedback_key: str,
    *,
    batch_id: str = "batch-1",
    round_number: int = 1,
    comment: str = "original feedback",
) -> int:
    store.upsert_feedback_event(
        key=feedback_key,
        feedback_token=f"token-{feedback_key}",
        comment=comment,
        raw_json='{"source":"preserved"}',
    )
    with store._connect() as db:
        db.execute(
            """
            insert into feedback_processing_batches (
                batch_id, status, requested_count, resolved_at
            ) values (?, 'resolved', 1, '2026-08-30 01:02:03')
            """,
            (batch_id,),
        )
        cursor = db.execute(
            """
            insert into feedback_processing_rounds (
                feedback_key, round_number, batch_id, status,
                workbench_task_id, workbench_turn_id, attempt_id,
                agent_run_id, commit_sha, test_evidence_json,
                restart_evidence_json, health_evidence_json,
                backlog_evidence_json, receipt_version, note,
                started_at, resolved_at, created_at, updated_at
            ) values (
                ?, ?, ?, 'resolved', 'task-old', 'turn-old', 12, 34, ?,
                ?, ?, ?, ?, 2, 'old note', '2026-08-30 00:00:00',
                '2026-08-30 01:02:03', '2026-08-30 00:00:00',
                '2026-08-30 01:02:03'
            )
            """,
            (
                feedback_key,
                round_number,
                batch_id,
                "a" * 40,
                json.dumps({"pytest": {"exit_code": 0}}),
                json.dumps(
                    {
                        "launchd_label": "com.ceo-agent-service.main",
                        "before_pid": 1,
                        "after_pid": 2,
                    }
                ),
                json.dumps(
                    {
                        "url": "http://127.0.0.1:8765/healthz",
                        "status_code": 200,
                        "ok": True,
                    }
                ),
                json.dumps({"processing": 0, "failed": 0, "retryable": 0}),
            ),
        )
        round_id = int(cursor.lastrowid)
        db.execute(
            """
            update feedback_processing_items
               set current_round_id=?, batch_id=?, status='resolved',
                   workbench_task_id='task-old', workbench_turn_id='turn-old',
                   attempt_id=12, agent_run_id=34, commit_sha=?,
                   test_evidence_json=?, restart_evidence_json=?,
                   health_evidence_json=?, note='old note',
                   resolved_at='2026-08-30 01:02:03',
                   updated_at='2026-08-30 01:02:03'
             where feedback_key=?
            """,
            (
                round_id,
                batch_id,
                "a" * 40,
                json.dumps({"pytest": {"exit_code": 0}}),
                json.dumps(
                    {
                        "launchd_label": "com.ceo-agent-service.main",
                        "before_pid": 1,
                        "after_pid": 2,
                    }
                ),
                json.dumps(
                    {
                        "url": "http://127.0.0.1:8765/healthz",
                        "status_code": 200,
                        "ok": True,
                    }
                ),
                feedback_key,
            ),
        )
        db.execute(
            """
            update feedback_events
               set resolved_at='2026-08-30 01:02:03',
                   updated_at='2026-08-30 01:02:03'
             where key=?
            """,
            (feedback_key,),
        )
    return round_id


def _complete_resolution_receipt(commit_sha: str = "b" * 40) -> ResolutionEvidence:
    return ResolutionEvidence(
        commit_sha=commit_sha,
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


def test_feedback_reopen_public_signature_and_typed_validation(tmp_path: Path):
    signature = inspect.signature(AutoReplyStore.reopen_feedback_processing_item)
    assert list(signature.parameters) == ["self", "feedback_key", "reason"]
    assert signature.parameters["reason"].kind is inspect.Parameter.KEYWORD_ONLY

    store = AutoReplyStore(tmp_path / "reopen-validation.sqlite3")
    store.upsert_feedback_event(key="pending", feedback_token="token-pending")
    for reason in ("", "   ", "\t\n"):
        with pytest.raises(
            feedback_processing_module.FeedbackProcessingReopenError
        ) as error:
            store.reopen_feedback_processing_item("pending", reason=reason)
        assert error.value.error_code == "feedback_reopen_invalid"
    assert store.reopen_feedback_processing_item("missing", reason="factual reason") is None


def test_reopen_resolved_item_preserves_history_and_clears_projection_atomically(
    tmp_path: Path,
):
    store = AutoReplyStore(tmp_path / "reopen-resolved.sqlite3")
    round_id = _seed_resolved_feedback_round(store, "feedback-1")
    with store._connect() as db:
        round_before = dict(
            db.execute(
                "select * from feedback_processing_rounds where id=?", (round_id,)
            ).fetchone()
        )
        event_before = dict(
            db.execute(
                "select * from feedback_events where key='feedback-1'"
            ).fetchone()
        )

    reason = "  The stored receipt predates the completed repair.  "
    reopened = store.reopen_feedback_processing_item("feedback-1", reason=reason)

    assert reopened is not None
    assert reopened.status == "pending"
    assert reopened.current_round_id == 0
    assert reopened.batch_id == ""
    assert reopened.workbench_task_id == ""
    assert reopened.workbench_turn_id == ""
    assert reopened.attempt_id == 0
    assert reopened.agent_run_id == 0
    assert reopened.commit_sha == ""
    assert reopened.test_evidence == {}
    assert reopened.restart_evidence == {}
    assert reopened.health_evidence == {}
    assert reopened.note == ""
    assert reopened.resolved_at == ""

    rounds = store.list_feedback_processing_rounds("feedback-1")
    assert len(rounds) == 1
    assert rounds[0].reopen_reason == reason
    assert rounds[0].reopened_at
    with store._connect() as db:
        round_after = dict(
            db.execute(
                "select * from feedback_processing_rounds where id=?", (round_id,)
            ).fetchone()
        )
        event_after = dict(
            db.execute(
                "select * from feedback_events where key='feedback-1'"
            ).fetchone()
        )
    for field, value in round_before.items():
        if field not in {"reopened_at", "reopen_reason"}:
            assert round_after[field] == value
    assert event_after["resolved_at"] == ""
    for field in ("comment", "original_text", "reply_text", "raw_json", "created_at"):
        assert event_after[field] == event_before[field]

    transitions = store.list_feedback_processing_transitions("feedback-1")
    assert len(transitions) == 1
    assert transitions[0].round_id == round_id
    assert transitions[0].batch_id == "batch-1"
    assert transitions[0].from_status == "resolved"
    assert transitions[0].to_status == "pending"
    assert transitions[0].reason == reason
    assert transitions[0].workbench_task_id == "task-old"
    assert transitions[0].workbench_turn_id == "turn-old"


def test_reopen_pending_is_idempotent_without_history_mutation(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "reopen-pending.sqlite3")
    store.upsert_feedback_event(key="feedback-1", feedback_token="token-1")
    before = store.get_feedback_processing_item("feedback-1")

    first = store.reopen_feedback_processing_item("feedback-1", reason="first reason")
    second = store.reopen_feedback_processing_item("feedback-1", reason="second reason")

    assert first == before
    assert second == before
    assert store.list_feedback_processing_rounds("feedback-1") == []
    assert store.list_feedback_processing_transitions("feedback-1") == []


def test_reopen_processing_raises_typed_error_without_mutation(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "reopen-processing.sqlite3")
    store.upsert_feedback_event(key="feedback-1", feedback_token="token-1")
    claimed = store.claim_feedback_processing_items("batch-1", ["feedback-1"])[0]
    with store._connect() as db:
        rounds_before = [dict(row) for row in db.execute("select * from feedback_processing_rounds")]
        transitions_before = [dict(row) for row in db.execute("select * from feedback_processing_transitions")]

    with pytest.raises(feedback_processing_module.FeedbackProcessingReopenError) as error:
        store.reopen_feedback_processing_item("feedback-1", reason="active claim")

    assert error.value.error_code == "feedback_reopen_processing"
    assert store.get_feedback_processing_item("feedback-1") == claimed
    with store._connect() as db:
        assert [dict(row) for row in db.execute("select * from feedback_processing_rounds")] == rounds_before
        assert [dict(row) for row in db.execute("select * from feedback_processing_transitions")] == transitions_before


@pytest.mark.parametrize("damage", ["missing", "wrong-status", "wrong-owner", "wrong-pointer"])
def test_reopen_incomplete_history_rolls_back_atomically(tmp_path: Path, damage: str):
    store = AutoReplyStore(tmp_path / f"reopen-incomplete-{damage}.sqlite3")
    round_id = _seed_resolved_feedback_round(store, "feedback-1")
    with store._connect() as db:
        if damage == "missing":
            db.execute("delete from feedback_processing_rounds where id=?", (round_id,))
        elif damage == "wrong-status":
            db.execute(
                "update feedback_processing_rounds set status='processing' where id=?",
                (round_id,),
            )
        elif damage == "wrong-owner":
            db.execute(
                "update feedback_processing_rounds set feedback_key='other' where id=?",
                (round_id,),
            )
        else:
            db.execute(
                "update feedback_processing_items set current_round_id=? where feedback_key='feedback-1'",
                (round_id + 100,),
            )
        item_before = dict(
            db.execute(
                "select * from feedback_processing_items where feedback_key='feedback-1'"
            ).fetchone()
        )
        event_before = dict(
            db.execute(
                "select * from feedback_events where key='feedback-1'"
            ).fetchone()
        )

    with pytest.raises(feedback_processing_module.FeedbackProcessingReopenError) as error:
        store.reopen_feedback_processing_item("feedback-1", reason="history is incomplete")

    assert error.value.error_code == "feedback_reopen_history_incomplete"
    with store._connect() as db:
        assert dict(db.execute("select * from feedback_processing_items where feedback_key='feedback-1'").fetchone()) == item_before
        assert dict(db.execute("select * from feedback_events where key='feedback-1'").fetchone()) == event_before
        assert db.execute("select count(*) from feedback_processing_transitions").fetchone()[0] == 0


def test_reopened_claim_creates_empty_next_round_and_transitions_newest_first(
    tmp_path: Path,
):
    store = AutoReplyStore(tmp_path / "claim-round-two.sqlite3")
    old_round_id = _seed_resolved_feedback_round(store, "feedback-1")
    store.reopen_feedback_processing_item("feedback-1", reason="premature")

    claimed = store.claim_feedback_processing_items("batch-2", ["feedback-1"])
    retried = store.claim_feedback_processing_items("batch-2", ["feedback-1"])

    assert len(claimed) == len(retried) == 1
    current = claimed[0]
    assert current.current_round_id > old_round_id
    assert retried[0].current_round_id == current.current_round_id
    assert current.batch_id == "batch-2"
    assert current.status == "processing"
    assert current.workbench_task_id == current.workbench_turn_id == ""
    assert current.attempt_id == current.agent_run_id == 0
    assert current.commit_sha == current.note == ""
    assert current.test_evidence == current.restart_evidence == current.health_evidence == {}
    rounds = store.list_feedback_processing_rounds("feedback-1")
    assert [round_item.round_number for round_item in rounds] == [2, 1]
    assert rounds[0].id == current.current_round_id
    assert rounds[0].batch_id == "batch-2"
    assert rounds[0].status == "processing"
    assert rounds[0].workbench_task_id == rounds[0].workbench_turn_id == ""
    assert rounds[0].test_evidence == rounds[0].restart_evidence == rounds[0].health_evidence == {}
    transitions = store.list_feedback_processing_transitions("feedback-1")
    assert [(item.from_status, item.to_status) for item in transitions] == [
        ("pending", "processing"),
        ("resolved", "pending"),
    ]
    with store._connect() as db:
        assert db.execute("select count(*) from feedback_processing_rounds where feedback_key='feedback-1'").fetchone()[0] == 2
        assert db.execute("select count(*) from feedback_processing_transitions where feedback_key='feedback-1'").fetchone()[0] == 2


def test_batch_claim_rolls_back_when_one_pending_item_has_inconsistent_history(
    tmp_path: Path,
):
    store = AutoReplyStore(tmp_path / "claim-history-conflict.sqlite3")
    for key in ("good", "bad"):
        store.upsert_feedback_event(key=key, feedback_token=f"token-{key}")
    with store._connect() as db:
        db.execute(
            """
            insert into feedback_processing_rounds (
                feedback_key, round_number, batch_id, status, started_at
            ) values ('bad', 1, 'old-batch', 'processing', current_timestamp)
            """
        )

    with pytest.raises(FeedbackProcessingClaimError):
        store.claim_feedback_processing_items("new-batch", ["good", "bad"])

    assert store.get_feedback_processing_batch("new-batch") is None
    assert {store.get_feedback_processing_item(key).status for key in ("good", "bad")} == {"pending"}
    with store._connect() as db:
        assert db.execute("select count(*) from feedback_processing_rounds where batch_id='new-batch'").fetchone()[0] == 0
        assert db.execute("select count(*) from feedback_processing_transitions").fetchone()[0] == 0


def test_association_and_evidence_patch_update_only_exact_current_round(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "round-patch.sqlite3")
    _seed_resolved_feedback_round(store, "feedback-1")
    store.reopen_feedback_processing_item("feedback-1", reason="premature")
    item = store.claim_feedback_processing_items("batch-2", ["feedback-1"])[0]

    associated = store.associate_feedback_processing_turn(
        "feedback-1",
        workbench_task_id="task-new",
        workbench_turn_id="turn-new",
        attempt_id=56,
        agent_run_id=78,
    )
    patched = store.patch_feedback_processing_item_evidence(
        "feedback-1",
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
        note="new note",
    )

    assert associated is not None and patched is not None
    assert associated.current_round_id == patched.current_round_id == item.current_round_id
    rounds = store.list_feedback_processing_rounds("feedback-1")
    current_round, old_round = rounds
    assert current_round.workbench_task_id == "task-new"
    assert current_round.workbench_turn_id == "turn-new"
    assert current_round.attempt_id == 56
    assert current_round.agent_run_id == 78
    assert current_round.commit_sha == "b" * 40
    assert current_round.test_evidence == {"pytest": {"exit_code": 0}}
    assert current_round.note == "new note"
    assert old_round.workbench_task_id == "task-old"
    assert old_round.commit_sha == "a" * 40
    assert old_round.note == "old note"


@pytest.mark.parametrize("operation", ["associate", "patch"])
def test_stale_or_ambiguous_current_round_cannot_be_mutated(
    tmp_path: Path, operation: str
):
    store = AutoReplyStore(tmp_path / f"stale-round-{operation}.sqlite3")
    old_round_id = _seed_resolved_feedback_round(store, "feedback-1")
    store.reopen_feedback_processing_item("feedback-1", reason="premature")
    store.claim_feedback_processing_items("batch-2", ["feedback-1"])
    with store._connect() as db:
        round_rows_before = [dict(row) for row in db.execute("select * from feedback_processing_rounds order by id")]
        item_before = dict(db.execute("select * from feedback_processing_items where feedback_key='feedback-1'").fetchone())
        db.execute(
            "update feedback_processing_items set current_round_id=? where feedback_key='feedback-1'",
            (old_round_id,),
        )

    with pytest.raises(ValueError):
        if operation == "associate":
            store.associate_feedback_processing_turn(
                "feedback-1",
                workbench_task_id="wrong-task",
                workbench_turn_id="wrong-turn",
                attempt_id=90,
                agent_run_id=91,
            )
        else:
            store.patch_feedback_processing_item_evidence(
                "feedback-1", commit_sha="c" * 40
            )

    with store._connect() as db:
        assert [dict(row) for row in db.execute("select * from feedback_processing_rounds order by id")] == round_rows_before
        after = dict(db.execute("select * from feedback_processing_items where feedback_key='feedback-1'").fetchone())
    assert {key: value for key, value in after.items() if key != "current_round_id"} == {
        key: value for key, value in item_before.items() if key != "current_round_id"
    }


def test_resolution_evidence_requires_zero_backlog_and_prevalidated_commit():
    complete = _complete_resolution_receipt()
    validate_resolution_evidence(complete, commit_is_ancestor=True)

    with pytest.raises(ValidationError):
        ResolutionEvidence(
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
        )
    for backlog in (
        {"processing": 1, "failed": 0, "retryable": 0},
        {"processing": 0, "failed": 1, "retryable": 0},
        {"processing": 0, "failed": 0, "retryable": 1},
        {"processing": 0, "failed": 0},
        {"processing": False, "failed": 0, "retryable": 0},
    ):
        with pytest.raises(ValueError):
            validate_resolution_evidence(
                complete.model_copy(update={"backlog_evidence": backlog}),
                commit_is_ancestor=True,
            )
    with pytest.raises(ValueError):
        validate_resolution_evidence(complete, commit_is_ancestor=False)


def test_resolve_uses_only_current_round_receipt_and_updates_batch_atomically(
    tmp_path: Path,
):
    store = AutoReplyStore(tmp_path / "resolve-current-round.sqlite3")
    old_round_id = _seed_resolved_feedback_round(store, "feedback-1")
    store.reopen_feedback_processing_item("feedback-1", reason="premature")
    claimed = store.claim_feedback_processing_items("batch-2", ["feedback-1"])[0]
    store.associate_feedback_processing_turn(
        "feedback-1",
        workbench_task_id="task-new",
        workbench_turn_id="turn-new",
        attempt_id=56,
        agent_run_id=78,
    )
    stale_receipt = _complete_resolution_receipt("a" * 40)

    with pytest.raises(ValueError):
        store.resolve_feedback_processing_batch(
            "batch-2", stale_receipt, commit_is_ancestor=True
        )
    assert store.get_feedback_processing_item("feedback-1").status == "processing"
    assert store.get_feedback_processing_batch("batch-2").status == "processing"
    assert store.list_feedback_processing_rounds("feedback-1")[0].status == "processing"

    receipt = _complete_resolution_receipt()
    store.patch_feedback_processing_item_evidence(
        "feedback-1",
        commit_sha=receipt.commit_sha,
        test_evidence=receipt.test_evidence,
        restart_evidence=receipt.restart_evidence,
        health_evidence=receipt.health_evidence,
    )
    assert store.resolve_feedback_processing_batch(
        "batch-2", receipt, commit_is_ancestor=True
    )

    item = store.get_feedback_processing_item("feedback-1")
    rounds = store.list_feedback_processing_rounds("feedback-1")
    assert item is not None
    assert item.status == "resolved"
    assert item.current_round_id == claimed.current_round_id
    assert rounds[0].id == claimed.current_round_id
    assert rounds[0].status == "resolved"
    assert rounds[0].resolved_at
    assert rounds[1].id == old_round_id
    assert rounds[1].commit_sha == "a" * 40
    assert store.get_feedback_processing_batch("batch-2").status == "resolved"
    assert store.get_feedback_event("feedback-1").resolved_at
    assert [
        (transition.from_status, transition.to_status)
        for transition in store.list_feedback_processing_transitions("feedback-1")
    ] == [
        ("processing", "resolved"),
        ("pending", "processing"),
        ("resolved", "pending"),
    ]


def test_resolve_rolls_back_entire_batch_on_one_invalid_current_round(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "resolve-batch-rollback.sqlite3")
    for key in ("feedback-1", "feedback-2"):
        store.upsert_feedback_event(key=key, feedback_token=f"token-{key}")
    claimed = store.claim_feedback_processing_items(
        "batch-1", ["feedback-1", "feedback-2"]
    )
    receipt = _complete_resolution_receipt()
    for key in ("feedback-1", "feedback-2"):
        store.associate_feedback_processing_turn(
            key,
            workbench_task_id="task",
            workbench_turn_id="turn",
            attempt_id=1,
            agent_run_id=2,
        )
        store.patch_feedback_processing_item_evidence(
            key,
            commit_sha=receipt.commit_sha,
            test_evidence=receipt.test_evidence,
            restart_evidence=receipt.restart_evidence,
            health_evidence=receipt.health_evidence,
        )
    with store._connect() as db:
        db.execute(
            "update feedback_processing_rounds set health_evidence_json='{}' where id=?",
            (claimed[1].current_round_id,),
        )

    with pytest.raises(ValueError):
        store.resolve_feedback_processing_batch(
            "batch-1", receipt, commit_is_ancestor=True
        )

    assert {store.get_feedback_processing_item(key).status for key in ("feedback-1", "feedback-2")} == {"processing"}
    assert store.get_feedback_processing_batch("batch-1").status == "processing"
    assert {round_item.status for key in ("feedback-1", "feedback-2") for round_item in store.list_feedback_processing_rounds(key)} == {"processing"}
    assert all(not store.get_feedback_event(key).resolved_at for key in ("feedback-1", "feedback-2"))
    assert all(store.list_feedback_processing_transitions(key)[0].to_status == "processing" for key in ("feedback-1", "feedback-2"))


def test_unresolved_batch_cannot_bypass_current_round_receipt(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "resolve-receipt-required.sqlite3")
    store.upsert_feedback_event(key="feedback-1", feedback_token="token-1")
    item = store.claim_feedback_processing_items("batch-1", ["feedback-1"])[0]
    with store._connect() as db:
        db.execute(
            "update feedback_processing_items set status='resolved' where feedback_key='feedback-1'"
        )

    assert store.resolve_feedback_processing_batch(
        "batch-1", commit_is_ancestor=True
    ) is False
    assert store.get_feedback_processing_batch("batch-1").status == "processing"
    assert store.list_feedback_processing_rounds("feedback-1")[0].id == item.current_round_id
    assert store.list_feedback_processing_rounds("feedback-1")[0].status == "processing"


def test_round_and_transition_models_are_strict_read_contracts():
    with pytest.raises(ValidationError):
        FeedbackProcessingTransition(
            id=1,
            feedback_key="feedback-1",
            from_status="resolved",
            to_status="pending",
            unexpected=True,
        )


def _feedback_processing_snapshot(store: AutoReplyStore) -> dict[str, list[tuple[object, ...]]]:
    with store._connect() as db:
        return {
            table: [
                tuple(row)
                for row in db.execute(
                    f"select * from {table} order by rowid"
                ).fetchall()
            ]
            for table in (
                "feedback_events",
                "feedback_processing_items",
                "feedback_processing_rounds",
                "feedback_processing_transitions",
                "feedback_processing_batches",
            )
        }


def _prepare_processing_round(
    store: AutoReplyStore,
    feedback_key: str = "feedback-1",
    batch_id: str = "batch-1",
) -> tuple[FeedbackProcessingItem, ResolutionEvidence]:
    store.upsert_feedback_event(
        key=feedback_key,
        feedback_token=f"token-{feedback_key}",
    )
    item = store.claim_feedback_processing_items(batch_id, [feedback_key])[0]
    receipt = _complete_resolution_receipt()
    store.associate_feedback_processing_turn(
        feedback_key,
        workbench_task_id="task-current",
        workbench_turn_id="turn-current",
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
    return item, receipt


@pytest.mark.parametrize(
    "corruption_sql",
    (
        "update feedback_processing_rounds set resolved_at='' where id=?",
        "update feedback_processing_rounds set workbench_task_id='' where id=?",
        "update feedback_processing_rounds set workbench_turn_id='' where id=?",
        "update feedback_processing_rounds set attempt_id=0 where id=?",
        "update feedback_processing_rounds set agent_run_id=0 where id=?",
        "update feedback_processing_rounds set commit_sha='' where id=?",
        "update feedback_processing_rounds set test_evidence_json='{}' where id=?",
        "update feedback_processing_rounds set restart_evidence_json='{}' where id=?",
        "update feedback_processing_rounds set health_evidence_json='{}' where id=?",
        "update feedback_processing_rounds set backlog_evidence_json='{}' where id=?",
    ),
)
def test_reopen_rejects_incomplete_resolved_round_receipt_without_mutation(
    tmp_path: Path, corruption_sql: str
):
    store = AutoReplyStore(tmp_path / "reopen-complete-history.sqlite3")
    round_id = _seed_resolved_feedback_round(store, "feedback-1")
    with store._connect() as db:
        columns = {
            str(row["name"])
            for row in db.execute("pragma table_info(feedback_processing_rounds)")
        }
        if "backlog_evidence_json" not in columns:
            db.execute(
                "alter table feedback_processing_rounds add column "
                "backlog_evidence_json text not null default '{}'"
            )
        db.execute(
            "update feedback_processing_rounds set backlog_evidence_json=? where id=?",
            (
                json.dumps({"processing": 0, "failed": 0, "retryable": 0}),
                round_id,
            ),
        )
        db.execute(corruption_sql, (round_id,))
    before = _feedback_processing_snapshot(store)

    with pytest.raises(feedback_processing_module.FeedbackProcessingReopenError) as error:
        store.reopen_feedback_processing_item("feedback-1", reason="receipt incomplete")

    assert error.value.error_code == "feedback_reopen_history_incomplete"
    assert _feedback_processing_snapshot(store) == before


def test_claim_rejects_existing_batch_requested_count_mismatch_without_mutation(
    tmp_path: Path,
):
    store = AutoReplyStore(tmp_path / "claim-batch-count.sqlite3")
    store.upsert_feedback_event(key="feedback-1", feedback_token="token-1")
    store.create_feedback_processing_batch(["feedback-1"], batch_id="batch-1")
    with store._connect() as db:
        db.execute(
            "update feedback_processing_batches set requested_count=2 where batch_id='batch-1'"
        )
    before = _feedback_processing_snapshot(store)

    with pytest.raises((FeedbackProcessingBatchError, FeedbackProcessingClaimError)):
        store.claim_feedback_processing_items("batch-1", ["feedback-1"])

    assert _feedback_processing_snapshot(store) == before


def test_resolve_rejects_missing_source_feedback_before_any_mutation(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "resolve-source-missing.sqlite3")
    _, receipt = _prepare_processing_round(store)
    with store._connect() as db:
        db.execute("delete from feedback_events where key='feedback-1'")
    before = _feedback_processing_snapshot(store)

    with pytest.raises(ValueError):
        store.resolve_feedback_processing_batch(
            "batch-1", receipt, commit_is_ancestor=True
        )

    assert _feedback_processing_snapshot(store) == before


def test_resolve_persists_backlog_receipt_for_later_reopen(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "resolve-backlog-history.sqlite3")
    _, receipt = _prepare_processing_round(store)

    assert store.resolve_feedback_processing_batch(
        "batch-1", receipt, commit_is_ancestor=True
    )
    resolved_round = store.list_feedback_processing_rounds("feedback-1")[0]
    assert resolved_round.backlog_evidence == receipt.backlog_evidence
    assert store.reopen_feedback_processing_item(
        "feedback-1", reason="validated historical receipt"
    ).status == "pending"


def test_resolve_shares_one_timestamp_across_delayed_multi_item_batch(
    tmp_path: Path,
):
    store = AutoReplyStore(tmp_path / "resolve-shared-timestamp.sqlite3")
    receipt = _complete_resolution_receipt()
    for key in ("feedback-1", "feedback-2"):
        store.upsert_feedback_event(key=key, feedback_token=f"token-{key}")
    store.claim_feedback_processing_items(
        "batch-1", ["feedback-1", "feedback-2"]
    )
    for index, key in enumerate(("feedback-1", "feedback-2"), start=1):
        store.associate_feedback_processing_turn(
            key,
            workbench_task_id="task-current",
            workbench_turn_id="turn-current",
            attempt_id=70 + index,
            agent_run_id=80 + index,
        )
        store.patch_feedback_processing_item_evidence(
            key,
            commit_sha=receipt.commit_sha,
            test_evidence=receipt.test_evidence,
            restart_evidence=receipt.restart_evidence,
            health_evidence=receipt.health_evidence,
        )

    original_open_connection = store._open_connection

    def delayed_open_connection() -> sqlite3.Connection:
        connection = original_open_connection()
        connection.create_function(
            "feedback_resolution_delay",
            0,
            lambda: time.sleep(1.1),
        )
        return connection

    store._open_connection = delayed_open_connection  # type: ignore[method-assign]
    with store._connect() as db:
        db.execute(
            """
            create trigger delay_feedback_round_resolution
            after update of status on feedback_processing_rounds
            when old.status='processing' and new.status='resolved'
            begin
                select feedback_resolution_delay();
            end
            """
        )

    assert store.resolve_feedback_processing_batch(
        "batch-1", receipt, commit_is_ancestor=True
    )
    with store._connect() as db:
        timestamps = {
            str(row["timestamp"])
            for row in db.execute(
                """
                select resolved_at as timestamp
                  from feedback_processing_rounds where batch_id='batch-1'
                union all
                select updated_at
                  from feedback_processing_rounds where batch_id='batch-1'
                union all
                select resolved_at
                  from feedback_processing_items where batch_id='batch-1'
                union all
                select updated_at
                  from feedback_processing_items where batch_id='batch-1'
                union all
                select resolved_at
                  from feedback_events where key in ('feedback-1', 'feedback-2')
                union all
                select updated_at
                  from feedback_events where key in ('feedback-1', 'feedback-2')
                union all
                select resolved_at
                  from feedback_processing_batches where batch_id='batch-1'
                union all
                select updated_at
                  from feedback_processing_batches where batch_id='batch-1'
                union all
                select created_at
                  from feedback_processing_transitions
                 where batch_id='batch-1' and from_status='processing'
                   and to_status='resolved'
                """
            )
        }
    assert len(timestamps) == 1
    assert store.reopen_feedback_processing_item(
        "feedback-1", reason="delayed resolution remained atomic"
    ).status == "pending"


def test_resolve_rejects_orphan_round_in_batch_before_any_mutation(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "resolve-orphan-round.sqlite3")
    _, receipt = _prepare_processing_round(store)
    with store._connect() as db:
        db.execute(
            """
            insert into feedback_processing_rounds (
                feedback_key, round_number, batch_id, status, started_at
            ) values ('orphan-feedback', 1, 'batch-1', 'processing', current_timestamp)
            """
        )
    before = _feedback_processing_snapshot(store)

    with pytest.raises(ValueError):
        store.resolve_feedback_processing_batch(
            "batch-1", receipt, commit_is_ancestor=True
        )

    assert _feedback_processing_snapshot(store) == before


def test_resolve_rejects_missing_item_for_batch_round_before_any_mutation(
    tmp_path: Path,
):
    store = AutoReplyStore(tmp_path / "resolve-item-missing.sqlite3")
    _, receipt = _prepare_processing_round(store)
    with store._connect() as db:
        db.execute(
            "delete from feedback_processing_items where feedback_key='feedback-1'"
        )
    before = _feedback_processing_snapshot(store)

    with pytest.raises(ValueError):
        store.resolve_feedback_processing_batch(
            "batch-1", receipt, commit_is_ancestor=True
        )

    assert _feedback_processing_snapshot(store) == before


@pytest.mark.parametrize("malformed_pointer", ("not-an-int", 1.5, -1, 0))
def test_reopen_malformed_round_pointer_is_typed_and_atomic(
    tmp_path: Path, malformed_pointer: object
):
    store = AutoReplyStore(tmp_path / "reopen-malformed-pointer.sqlite3")
    _seed_resolved_feedback_round(store, "feedback-1")
    with store._connect() as db:
        db.execute(
            "update feedback_processing_items set current_round_id=? where feedback_key='feedback-1'",
            (malformed_pointer,),
        )
    before = _feedback_processing_snapshot(store)

    with pytest.raises(feedback_processing_module.FeedbackProcessingReopenError) as error:
        store.reopen_feedback_processing_item("feedback-1", reason="bad pointer")

    assert error.value.error_code == "feedback_reopen_history_incomplete"
    assert _feedback_processing_snapshot(store) == before


@pytest.mark.parametrize("malformed_pointer", ("not-an-int", 1.5, -1))
def test_feedback_item_reader_rejects_malformed_round_pointer(
    tmp_path: Path, malformed_pointer: object
):
    store = AutoReplyStore(tmp_path / "read-malformed-pointer.sqlite3")
    store.upsert_feedback_event(key="feedback-1", feedback_token="token-1")
    with store._connect() as db:
        db.execute(
            "update feedback_processing_items set current_round_id=? where feedback_key='feedback-1'",
            (malformed_pointer,),
        )

    with pytest.raises(ValueError, match="feedback_processing_current_round_id_invalid"):
        store.get_feedback_processing_item("feedback-1")


@pytest.mark.parametrize("operation", ("claim", "patch", "resolve"))
def test_task2_writes_reject_malformed_round_pointer_with_domain_error(
    tmp_path: Path, operation: str
):
    store = AutoReplyStore(tmp_path / f"{operation}-malformed-pointer.sqlite3")
    if operation == "claim":
        store.upsert_feedback_event(key="feedback-1", feedback_token="token-1")
        with store._connect() as db:
            db.execute(
                "update feedback_processing_items set current_round_id='not-an-int' "
                "where feedback_key='feedback-1'"
            )
        before = _feedback_processing_snapshot(store)
        with pytest.raises(FeedbackProcessingClaimError):
            store.claim_feedback_processing_items("batch-1", ["feedback-1"])
    else:
        _, receipt = _prepare_processing_round(store)
        with store._connect() as db:
            db.execute(
                "update feedback_processing_items set current_round_id=1.5 "
                "where feedback_key='feedback-1'"
            )
        before = _feedback_processing_snapshot(store)
        if operation == "patch":
            with pytest.raises(
                ValueError, match="feedback_processing_current_round_id_invalid"
            ):
                store.patch_feedback_processing_item_evidence(
                    "feedback-1", note="must not write"
                )
        else:
            with pytest.raises(
                ValueError, match="feedback_processing_current_round_id_invalid"
            ):
                store.resolve_feedback_processing_batch(
                    "batch-1", receipt, commit_is_ancestor=True
                )
    assert _feedback_processing_snapshot(store) == before


def _ensure_receipt_version_column(store: AutoReplyStore) -> None:
    with store._connect() as db:
        columns = {
            str(row["name"])
            for row in db.execute("pragma table_info(feedback_processing_rounds)")
        }
        if "receipt_version" not in columns:
            db.execute(
                "alter table feedback_processing_rounds add column "
                "receipt_version integer not null default 1"
            )


def test_resolution_requires_explicit_strict_commit_ancestry_signatures():
    validation_signature = inspect.signature(validate_resolution_evidence)
    assert list(validation_signature.parameters) == [
        "evidence",
        "commit_is_ancestor",
    ]
    assert (
        validation_signature.parameters["commit_is_ancestor"].default
        is inspect.Parameter.empty
    )
    store_signature = inspect.signature(
        AutoReplyStore.resolve_feedback_processing_batch
    )
    assert list(store_signature.parameters) == [
        "self",
        "batch_id",
        "evidence",
        "commit_is_ancestor",
    ]
    assert (
        store_signature.parameters["commit_is_ancestor"].default
        is inspect.Parameter.empty
    )
    receipt = _complete_resolution_receipt()
    validate_resolution_evidence(receipt, commit_is_ancestor=True)
    for invalid in (False, None, 1, "true"):
        with pytest.raises((TypeError, ValueError)):
            validate_resolution_evidence(
                receipt,
                commit_is_ancestor=invalid,  # type: ignore[arg-type]
            )


def test_legacy_v1_complete_receipt_reopens_without_synthetic_backlog(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "legacy-v1-reopen.sqlite3")
    round_id = _seed_resolved_feedback_round(store, "feedback-1")
    _ensure_receipt_version_column(store)
    with store._connect() as db:
        db.execute(
            """
            update feedback_processing_rounds
               set receipt_version=1, backlog_evidence_json='{}'
             where id=?
            """,
            (round_id,),
        )

    reopened = store.reopen_feedback_processing_item(
        "feedback-1", reason="legacy receipt predates backlog evidence"
    )

    assert reopened is not None and reopened.status == "pending"
    with store._connect() as db:
        round_row = db.execute(
            "select receipt_version, backlog_evidence_json "
            "from feedback_processing_rounds where id=?",
            (round_id,),
        ).fetchone()
    assert tuple(round_row) == (1, "{}")


def test_receipt_version_migration_marks_only_valid_existing_backlog_v2(
    tmp_path: Path,
):
    db_path = tmp_path / "receipt-version-migration.sqlite3"
    store = AutoReplyStore(db_path)
    valid_id = _seed_resolved_feedback_round(
        store,
        "feedback-valid",
        batch_id="batch-valid",
    )
    legacy_id = _seed_resolved_feedback_round(
        store,
        "feedback-legacy",
        batch_id="batch-legacy",
    )
    with store._connect() as db:
        db.execute(
            "update feedback_processing_rounds set backlog_evidence_json='{}' where id=?",
            (legacy_id,),
        )
        db.execute(
            "alter table feedback_processing_rounds drop column receipt_version"
        )

    store_module._INITIALIZED_STORE_PATHS.discard(db_path.resolve())
    migrated = AutoReplyStore(db_path)
    with migrated._connect() as db:
        versions = {
            int(row["id"]): int(row["receipt_version"])
            for row in db.execute(
                "select id, receipt_version from feedback_processing_rounds"
            )
        }
    assert versions == {valid_id: 2, legacy_id: 1}


def test_new_claim_and_resolution_persist_receipt_version_two(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "receipt-version-two.sqlite3")
    _ensure_receipt_version_column(store)
    item, receipt = _prepare_processing_round(store)
    assert store.list_feedback_processing_rounds("feedback-1")[0].receipt_version == 2

    assert store.resolve_feedback_processing_batch(
        "batch-1", receipt, commit_is_ancestor=True
    )
    resolved_round = store.list_feedback_processing_rounds("feedback-1")[0]
    assert resolved_round.id == item.current_round_id
    assert resolved_round.receipt_version == 2
    assert resolved_round.backlog_evidence == receipt.backlog_evidence


def _make_stale_resolved_round_pointer(store: AutoReplyStore) -> tuple[int, int]:
    old_round_id = _seed_resolved_feedback_round(
        store,
        "feedback-1",
        batch_id="batch-old",
        round_number=1,
    )
    new_round_id = _seed_resolved_feedback_round(
        store,
        "feedback-1",
        batch_id="batch-new",
        round_number=2,
    )
    with store._connect() as db:
        db.execute(
            """
            update feedback_processing_items
               set current_round_id=?, batch_id='batch-old'
             where feedback_key='feedback-1'
            """,
            (old_round_id,),
        )
    return old_round_id, new_round_id


def test_reopen_rejects_stale_non_latest_round_pointer_without_mutation(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "reopen-stale-latest.sqlite3")
    old_round_id, new_round_id = _make_stale_resolved_round_pointer(store)
    before = _feedback_processing_snapshot(store)

    with pytest.raises(feedback_processing_module.FeedbackProcessingReopenError) as error:
        store.reopen_feedback_processing_item("feedback-1", reason="stale pointer")

    assert error.value.error_code == "feedback_reopen_history_incomplete"
    assert _feedback_processing_snapshot(store) == before
    assert [
        round_item.id for round_item in store.list_feedback_processing_rounds("feedback-1")
    ] == [new_round_id, old_round_id]


def test_round_reconciliation_clears_stale_non_latest_matching_pointer(
    tmp_path: Path,
):
    db_path = tmp_path / "reconcile-stale-latest.sqlite3"
    store = AutoReplyStore(db_path)
    old_round_id, new_round_id = _make_stale_resolved_round_pointer(store)
    rounds_before = store.list_feedback_processing_rounds("feedback-1")

    store_module._INITIALIZED_STORE_PATHS.discard(db_path.resolve())
    reconciled = AutoReplyStore(db_path)

    assert reconciled.get_feedback_processing_item("feedback-1").current_round_id == 0
    assert [
        round_item.id
        for round_item in reconciled.list_feedback_processing_rounds("feedback-1")
    ] == [new_round_id, old_round_id]
    assert reconciled.list_feedback_processing_rounds("feedback-1") == rounds_before


def test_resolved_batch_shortcut_rejects_corrupt_batch_only_state(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "resolved-batch-corrupt.sqlite3")
    store.upsert_feedback_event(key="feedback-1", feedback_token="token-1")
    with store._connect() as db:
        db.execute(
            """
            insert into feedback_processing_batches (
                batch_id, status, requested_count, resolved_at
            ) values ('batch-1', 'resolved', 1, current_timestamp)
            """
        )
    before = _feedback_processing_snapshot(store)

    with pytest.raises(ValueError):
        store.resolve_feedback_processing_batch(
            "batch-1",
            _complete_resolution_receipt(),
            commit_is_ancestor=True,
        )

    assert _feedback_processing_snapshot(store) == before


def test_old_resolved_batch_remains_idempotent_after_feedback_reopen(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "resolved-batch-reopened.sqlite3")
    _, receipt = _prepare_processing_round(store)
    assert store.resolve_feedback_processing_batch(
        "batch-1", receipt, commit_is_ancestor=True
    )
    store.reopen_feedback_processing_item("feedback-1", reason="premature")
    store.claim_feedback_processing_items("batch-2", ["feedback-1"])

    assert store.resolve_feedback_processing_batch(
        "batch-1", receipt, commit_is_ancestor=True
    )
    assert store.get_feedback_processing_batch("batch-1").status == "resolved"
    assert store.get_feedback_processing_item("feedback-1").batch_id == "batch-2"


def test_resolved_batch_idempotency_exact_matches_supplied_receipt(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "resolved-batch-receipt-match.sqlite3")
    _, receipt = _prepare_processing_round(store)
    assert store.resolve_feedback_processing_batch(
        "batch-1", receipt, commit_is_ancestor=True
    )
    different = receipt.model_copy(
        update={"test_evidence": {"different": {"exit_code": 0}}}
    )

    with pytest.raises(ValueError):
        store.resolve_feedback_processing_batch(
            "batch-1", different, commit_is_ancestor=True
        )


def _association_payload(
    feedback_key: str,
    *,
    attempt_id: int,
    agent_run_id: int,
) -> dict[str, object]:
    return {
        feedback_key: {
            "workbench_task_id": "task-current",
            "workbench_turn_id": "turn-current",
            "attempt_id": attempt_id,
            "agent_run_id": agent_run_id,
        }
    }


@pytest.mark.parametrize("mutation", ("missing", "extra"))
def test_supplied_association_keys_must_exactly_close_batch(
    tmp_path: Path,
    mutation: str,
):
    store = AutoReplyStore(tmp_path / "association-closure.sqlite3")
    receipt = _complete_resolution_receipt()
    for key in ("feedback-1", "feedback-2"):
        store.upsert_feedback_event(key=key, feedback_token=f"token-{key}")
    store.claim_feedback_processing_items(
        "batch-1", ["feedback-1", "feedback-2"]
    )
    associations: dict[str, object] = {}
    for index, key in enumerate(("feedback-1", "feedback-2"), start=1):
        attempt_id = 70 + index
        agent_run_id = 80 + index
        store.associate_feedback_processing_turn(
            key,
            workbench_task_id="task-current",
            workbench_turn_id="turn-current",
            attempt_id=attempt_id,
            agent_run_id=agent_run_id,
        )
        store.patch_feedback_processing_item_evidence(
            key,
            commit_sha=receipt.commit_sha,
            test_evidence=receipt.test_evidence,
            restart_evidence=receipt.restart_evidence,
            health_evidence=receipt.health_evidence,
        )
        associations.update(
            _association_payload(
                key,
                attempt_id=attempt_id,
                agent_run_id=agent_run_id,
            )
        )
    if mutation == "missing":
        associations.pop("feedback-2")
    else:
        associations["extra-feedback"] = {
            "workbench_task_id": "task-current",
            "workbench_turn_id": "turn-current",
            "attempt_id": 99,
            "agent_run_id": 100,
        }
    supplied = ResolutionEvidence.model_validate(
        {**receipt.model_dump(), "associations": associations}
    )

    with pytest.raises(ValueError):
        store.resolve_feedback_processing_batch(
            "batch-1", supplied, commit_is_ancestor=True
        )


@pytest.mark.parametrize(
    "association",
    (
        {
            "workbench_task_id": "task",
            "workbench_turn_id": "turn",
            "attempt_id": 1,
            "agent_run_id": 2,
            "extra": True,
        },
        {
            "workbench_task_id": "task",
            "workbench_turn_id": "turn",
            "attempt_id": True,
            "agent_run_id": 2,
        },
        {
            "workbench_task_id": "task",
            "workbench_turn_id": "turn",
            "attempt_id": 1,
        },
    ),
)
def test_supplied_association_objects_are_strict(association: dict[str, object]):
    receipt = _complete_resolution_receipt()
    with pytest.raises(ValidationError):
        ResolutionEvidence.model_validate(
            {
                **receipt.model_dump(),
                "associations": {"feedback-1": association},
            }
        )


def _prepare_resolved_v2_batch(
    store: AutoReplyStore,
) -> ResolutionEvidence:
    _, receipt = _prepare_processing_round(store)
    assert store.resolve_feedback_processing_batch(
        "batch-1", receipt, commit_is_ancestor=True
    )
    return receipt


@pytest.mark.parametrize(
    "damage_sql",
    (
        "update feedback_processing_batches set resolved_at='' "
        "where batch_id='batch-1'",
        "update feedback_processing_batches set updated_at='1999-01-01 00:00:00' "
        "where batch_id='batch-1'",
    ),
    ids=("missing-resolved-at", "updated-at-mismatch"),
)
def test_resolved_batch_idempotency_requires_shared_batch_completion_timestamp(
    tmp_path: Path,
    damage_sql: str,
):
    store = AutoReplyStore(tmp_path / "resolved-batch-timestamp.sqlite3")
    receipt = _prepare_resolved_v2_batch(store)
    with store._connect() as db:
        db.execute(damage_sql)
    before = _feedback_processing_snapshot(store)

    with pytest.raises(ValueError):
        store.resolve_feedback_processing_batch(
            "batch-1", receipt, commit_is_ancestor=True
        )

    assert _feedback_processing_snapshot(store) == before


@pytest.mark.parametrize(
    "damage",
    ("missing", "duplicate", "mismatched", "timestamp"),
)
def test_resolved_v2_batch_idempotency_requires_exact_completion_transition(
    tmp_path: Path,
    damage: str,
):
    store = AutoReplyStore(tmp_path / f"resolved-transition-{damage}.sqlite3")
    receipt = _prepare_resolved_v2_batch(store)
    with store._connect() as db:
        if damage == "missing":
            db.execute(
                "delete from feedback_processing_transitions "
                "where batch_id='batch-1' and from_status='processing' "
                "and to_status='resolved'"
            )
        elif damage == "duplicate":
            db.execute(
                """
                insert into feedback_processing_transitions (
                    feedback_key, round_id, batch_id, from_status, to_status,
                    reason, workbench_task_id, workbench_turn_id, created_at
                )
                select feedback_key, round_id, batch_id, from_status, to_status,
                       reason, workbench_task_id, workbench_turn_id, created_at
                  from feedback_processing_transitions
                 where batch_id='batch-1' and from_status='processing'
                   and to_status='resolved'
                """
            )
        elif damage == "mismatched":
            db.execute(
                """
                update feedback_processing_transitions
                   set workbench_task_id='wrong-task'
                 where batch_id='batch-1' and from_status='processing'
                   and to_status='resolved'
                """
            )
        else:
            db.execute(
                """
                update feedback_processing_transitions
                   set created_at='1999-01-01 00:00:00'
                 where batch_id='batch-1' and from_status='processing'
                   and to_status='resolved'
                """
            )
    before = _feedback_processing_snapshot(store)

    with pytest.raises(ValueError):
        store.resolve_feedback_processing_batch(
            "batch-1", receipt, commit_is_ancestor=True
        )

    assert _feedback_processing_snapshot(store) == before


@pytest.mark.parametrize("damage", ("source-timestamp", "item-note"))
def test_current_resolved_batch_idempotency_requires_exact_terminal_projection(
    tmp_path: Path,
    damage: str,
):
    store = AutoReplyStore(tmp_path / f"resolved-current-{damage}.sqlite3")
    receipt = _prepare_resolved_v2_batch(store)
    with store._connect() as db:
        if damage == "source-timestamp":
            db.execute(
                "update feedback_events set resolved_at='1999-01-01 00:00:00' "
                "where key='feedback-1'"
            )
        else:
            db.execute(
                "update feedback_processing_items set note='wrong-note' "
                "where feedback_key='feedback-1'"
            )
    before = _feedback_processing_snapshot(store)

    with pytest.raises(ValueError):
        store.resolve_feedback_processing_batch(
            "batch-1", receipt, commit_is_ancestor=True
        )

    assert _feedback_processing_snapshot(store) == before


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("batch_id", "old-batch"),
        ("workbench_task_id", "old-task"),
        ("workbench_turn_id", "old-turn"),
        ("attempt_id", 12),
        ("agent_run_id", 34),
        ("commit_sha", "a" * 40),
        ("test_evidence_json", '{"pytest":{"exit_code":0}}'),
        ("restart_evidence_json", '{"launchd_label":"old"}'),
        ("health_evidence_json", '{"status_code":200}'),
        ("note", "old-note"),
        ("resolved_at", "1999-01-01 00:00:00"),
    ),
)
def test_reopened_resolved_batch_idempotency_requires_cleared_pending_projection(
    tmp_path: Path,
    field: str,
    value: object,
):
    store = AutoReplyStore(tmp_path / f"reopened-clear-{field}.sqlite3")
    receipt = _prepare_resolved_v2_batch(store)
    reopened = store.reopen_feedback_processing_item(
        "feedback-1", reason="needs another round"
    )
    assert reopened is not None and reopened.current_round_id == 0
    with store._connect() as db:
        db.execute(
            f"update feedback_processing_items set {field}=? "
            "where feedback_key='feedback-1'",
            (value,),
        )
    before = _feedback_processing_snapshot(store)

    with pytest.raises(ValueError):
        store.resolve_feedback_processing_batch(
            "batch-1", receipt, commit_is_ancestor=True
        )

    assert _feedback_processing_snapshot(store) == before


def test_old_resolved_batch_idempotency_validates_newer_processing_lineage(
    tmp_path: Path,
):
    store = AutoReplyStore(tmp_path / "resolved-newer-processing-lineage.sqlite3")
    receipt = _prepare_resolved_v2_batch(store)
    store.reopen_feedback_processing_item("feedback-1", reason="needs another round")
    store.claim_feedback_processing_items("batch-2", ["feedback-1"])
    with store._connect() as db:
        db.execute(
            "update feedback_processing_items set note='projection-only-corruption' "
            "where feedback_key='feedback-1'"
        )
    before = _feedback_processing_snapshot(store)

    with pytest.raises(ValueError):
        store.resolve_feedback_processing_batch(
            "batch-1", receipt, commit_is_ancestor=True
        )

    assert _feedback_processing_snapshot(store) == before
