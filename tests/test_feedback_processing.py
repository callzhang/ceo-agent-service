import io
import json
import inspect
import sqlite3
import subprocess
import sys
import tarfile
import time
from functools import wraps
from tempfile import TemporaryDirectory
from pathlib import Path
from threading import Event, Thread

import pytest
from pydantic import ValidationError

import app.feedback_processing as feedback_processing_module
import app.task_entity_storage as task_entity_storage
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
from app.action_feedback_definitions import feedback_projection
from app.action_feedback_storage import insert_feedback_action, set_feedback_action
from tests.feedback_processing_fixtures import resolve_feedback_fixture


_NATIVE_FEEDBACK_PREDECESSOR = "1ab1ded2e548fb459afe15ebdf801ec5dc381995"
_PUBLISHED_SIGNAL_SOURCE = "d2dc7c6c31ac97bb2615da549ec7c82f83821df3"


def _feedback_owner_projection(kind: str, *, include_rowid: bool = False) -> str:
    """Project the current native owner of one feedback processing domain."""
    if kind == "feedback_processing_items":
        projection = store_module._native_feedback_item_projection()
        if not include_rowid:
            return projection
        return (
            "select owner.task_id as rowid, projected.* "
            "from feedback_processing_items owner join ("
            + projection
            + ") projected using(feedback_key)"
        )
    if kind == "feedback_processing_rounds":
        projection = store_module._native_feedback_round_projection()
    elif kind == "feedback_processing_batches":
        projection = store_module._native_feedback_batch_projection()
    else:
        raise ValueError(f"unsupported feedback owner: {kind}")
    return f"select id as rowid, owner.* from ({projection}) owner" if include_rowid else projection


def _insert_feedback_owner(
    db: sqlite3.Connection,
    kind: str,
    values: dict[str, object],
) -> sqlite3.Cursor:
    """Insert directly into a native feedback round or batch owner."""
    if kind not in {"feedback_processing_rounds", "feedback_processing_batches"}:
        raise ValueError(f"unsupported feedback owner insert: {kind}")
    columns = tuple(values)
    return db.execute(
        f"insert into {kind} ({','.join(columns)}) "
        f"values ({','.join('?' for _ in columns)})",
        tuple(values[column] for column in columns),
    )


def _update_feedback_owner(
    db: sqlite3.Connection,
    kind: str,
    owner_id: int,
    values: dict[str, object],
) -> int:
    """Mutate the exact current Task/domain owner selected by a fixture."""
    if kind == "feedback_processing_items":
        task_values = {
            key: value
            for key, value in values.items()
            if key in {"status", "created_at", "updated_at"}
        }
        item_values = {key: value for key, value in values.items() if key not in task_values}
        changed = 0
        if task_values:
            changed = int(task_entity_storage.update_task_cas(
                db,
                task_id=owner_id,
                expected={"kind": "feedback_processing"},
                changes=task_values,
            ) is not None)
        if item_values:
            changed = db.execute(
                "update feedback_processing_items set "
                + ",".join(f'"{key}"=?' for key in item_values)
                + " where task_id=?",
                (*item_values.values(), owner_id),
            ).rowcount
        return changed
    if kind not in {"feedback_processing_rounds", "feedback_processing_batches"}:
        raise ValueError(f"unsupported feedback owner update: {kind}")
    if kind == "feedback_processing_rounds" and "feedback_key" in values:
        db.execute("pragma foreign_keys=off")
    return db.execute(
        f"update {kind} set "
        + ",".join(f'"{key}"=?' for key in values)
        + " where id=?",
        (*values.values(), owner_id),
    ).rowcount


def _delete_feedback_owner(
    db: sqlite3.Connection,
    kind: str,
    owner_id: int,
) -> int:
    """Delete the selected native feedback domain row, leaving other owners intact."""
    if kind == "feedback_processing_items":
        db.execute("pragma foreign_keys=off")
        return db.execute(
            "delete from feedback_processing_items where task_id=?", (owner_id,)
        ).rowcount
    if kind not in {"feedback_processing_rounds", "feedback_processing_batches"}:
        raise ValueError(f"unsupported feedback owner delete: {kind}")
    return db.execute(f"delete from {kind} where id=?", (owner_id,)).rowcount


@pytest.fixture(scope="module")
def native_feedback_predecessor_tree():
    """Pin physical native-schema regressions to the last production predecessor."""
    with TemporaryDirectory(prefix="feedback-native-predecessor-") as root:
        tree = Path(root)
        archived = subprocess.check_output(
            ["git", "archive", _NATIVE_FEEDBACK_PREDECESSOR],
            cwd=Path(__file__).parents[1],
        )
        with tarfile.open(fileobj=io.BytesIO(archived)) as tar:
            tar.extractall(tree, filter="data")
        yield tree


@pytest.fixture(scope="module")
def published_signal_source_tree():
    """Materialize the exact Store25.2 source accepted by the current release."""
    with TemporaryDirectory(prefix="feedback-signal-source-") as root:
        tree = Path(root)
        archived = subprocess.check_output(
            ["git", "archive", _PUBLISHED_SIGNAL_SOURCE],
            cwd=Path(__file__).parents[1],
        )
        with tarfile.open(fileobj=io.BytesIO(archived)) as tar:
            tar.extractall(tree, filter="data")
        yield tree


def _assert_native_feedback_predecessor_case(tree: Path, nodeid: str) -> None:
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "--tb=short", nodeid],
        cwd=tree,
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def _native_predecessor_case(test):
    """Execute an unchanged physical-native regression in the pinned source tree."""
    @wraps(test)
    def run(*args, request, native_feedback_predecessor_tree, **kwargs):
        _assert_native_feedback_predecessor_case(
            native_feedback_predecessor_tree, request.node.nodeid,
        )

    original = inspect.signature(test)
    run.__signature__ = original.replace(parameters=[
        *original.parameters.values(),
        inspect.Parameter("request", inspect.Parameter.KEYWORD_ONLY),
        inspect.Parameter(
            "native_feedback_predecessor_tree", inspect.Parameter.KEYWORD_ONLY,
        ),
    ])
    return run


def test_native_feedback_receipts_survive_full_paired_selection(tmp_path: Path):
    """Current public receipts survive the exact immediately-previous release."""
    from app.email_field_retirement_release import prepare_email_field_retirement_release
    from tests.support.store28_5_source import create_store28_5_source
    db_path = tmp_path / "native-paired.sqlite3"
    with sqlite3.connect(db_path) as db:
        create_store28_5_source(db)
    initial = prepare_email_field_retirement_release(db_path)
    initial.publish()
    store = AutoReplyStore(db_path)
    store.upsert_feedback_event(key='feedback-native', feedback_token='token-native',
                                comment='source comment', original_text='source original')
    store.create_feedback_processing_batch(['feedback-native'], batch_id='batch-native')
    store.claim_feedback_processing_items('batch-native', ['feedback-native'])
    initial.rollback()
    with sqlite3.connect(db_path) as db:
        native_events = db.execute(
            "select key,comment,original_text,resolved_at from ("
            + feedback_projection("feedback_events")
            + ") order by key"
        ).fetchall()
        native_rounds = db.execute(
            "select feedback_key,round_number,batch_id,status,"
            "workbench_task_id,workbench_turn_id,attempt_id,agent_run_id,"
            "commit_sha,test_evidence_json,restart_evidence_json,"
            "health_evidence_json,note,started_at,resolved_at,created_at,updated_at "
            "from feedback_processing_rounds order by feedback_key"
        ).fetchall()
        native_pointers = db.execute(
            "select feedback_key,current_round_id from feedback_processing_items "
            "order by feedback_key"
        ).fetchall()
        assert len(native_events) == 1
        assert len(native_rounds) == 1
    from app.store_schema_release import prepare_database_schema_release

    release = prepare_database_schema_release(Path(__file__).parents[1], db_path)
    assert release is not None
    release.publish()
    release.finalize()
    selected = AutoReplyStore(db_path)
    with selected._connect() as db:
        tables = {row[0] for row in db.execute(
            "select name from sqlite_master where type='table'"
        )}
        assert "feedback_events" not in tables
        assert "feedback_processing_transitions" not in tables
        assert "feedback_iteration_decision_items" not in tables
        assert {
            "tasks", "feedback_processing_items", "feedback_processing_rounds",
            "feedback_processing_batches", "actions",
        } <= tables
        assert store_module.schema_is_current(db)
        assert [tuple(row) for row in db.execute(
            "select key,comment,original_text,resolved_at from ("
            + feedback_projection("feedback_events") + ") order by key"
        )] == native_events
        assert [tuple(row) for row in db.execute(
            "select feedback_key,round_number,batch_id,status,"
            "workbench_task_id,workbench_turn_id,attempt_id,agent_run_id,"
            "commit_sha,test_evidence_json,restart_evidence_json,"
            "health_evidence_json,note,started_at,resolved_at,created_at,updated_at "
            "from (" + _feedback_owner_projection("feedback_processing_rounds")
            + ") order by feedback_key"
        )] == native_rounds
        assert [tuple(row) for row in db.execute(
            "select feedback_key,current_round_id from ("
            + _feedback_owner_projection("feedback_processing_items")
            + ") order by feedback_key"
        )] == native_pointers


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
    store.upsert_conversation("cid-1", title="技术部", single_chat=False)
    store.upsert_conversation_runtime_session("cid-1", "codex_oauth", "session-1")
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
    assert {"actions", "tasks"} <= tables_before
    assert "feedback_events" not in tables_before
    assert {"feedback_processing_batches", "feedback_processing_items"} <= tables_before
    assert {"idx_actions_fb_event_key", "idx_actions_fb_event_token"} <= indexes_before

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
            row[1] for row in db.execute("pragma table_xinfo(feedback_processing_items)")
        }
        assert {"actions", "tasks"} <= tables
        assert "feedback_processing_rounds" in tables
        assert "feedback_processing_transitions" not in tables
        assert "current_round_id" in columns
        assert {
            "idx_actions_fb_transition_feedback",
            "idx_actions_fb_transition_batch_round",
            "idx_actions_fb_transition_round",
        } <= indexes

    store_module._INITIALIZED_STORE_PATHS.discard(db_path.resolve())
    AutoReplyStore(db_path)
    with sqlite3.connect(db_path) as db:
        assert db.execute("select count(*) from feedback_processing_rounds").fetchone()[0] == 0
        assert db.execute("select count(*) from actions where record_type='feedback_processing_transitions'").fetchone()[0] == 0


def test_feedback_transition_indexes_are_additive_manifested_and_idempotent(
    tmp_path: Path,
):
    db_path = tmp_path / "transition-indexes.sqlite3"
    store = AutoReplyStore(db_path)
    expected = {
        "idx_actions_fb_transition_batch_round": (
            "fb_transition_batch_id",
            "fb_transition_round_id",
            None,
        ),
        "idx_actions_fb_transition_round": ("fb_transition_round_id", None),
    }

    with store._connect() as db:
        first_indexes = {
            str(row["name"])
            for row in db.execute("select name from sqlite_master where type='index'")
        }
        for index_name, columns in expected.items():
            assert tuple(
                row["name"] for row in db.execute(f"pragma index_info({index_name})")
            ) == columns

    store_module._INITIALIZED_STORE_PATHS.discard(db_path.resolve())
    migrated = AutoReplyStore(db_path)
    with migrated._connect() as db:
        reopened_indexes = {
            str(row["name"])
            for row in db.execute(
                "select name from sqlite_master where type='index'"
            )
        }
        assert reopened_indexes == first_indexes
        for index_name, columns in expected.items():
            assert tuple(
                row["name"] for row in db.execute(f"pragma index_info({index_name})")
            ) == columns

    store_module._INITIALIZED_STORE_PATHS.discard(db_path.resolve())
    AutoReplyStore(db_path)
    with sqlite3.connect(db_path) as db:
        second_indexes = {
            str(row[0])
            for row in db.execute(
                "select name from sqlite_master where type='index'"
            )
        }
    assert second_indexes == first_indexes


def test_feedback_transition_batch_queries_use_expected_indexes(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "transition-query-plan.sqlite3")
    _prepare_two_member_processing_batch(store)

    with store._connect() as db:
        round_ids = [
            int(row["id"])
            for row in db.execute(
                f"select id from ({_feedback_owner_projection('feedback_processing_rounds')}) "
                "where batch_id='batch-1' order by id"
            )
        ]
        batch_plan = " ".join(
            str(row["detail"])
            for row in db.execute(
                f"explain query plan select * from ({feedback_projection('feedback_processing_transitions')}) "
                "where batch_id=? order by round_id, id",
                ("batch-1",),
            )
        )
        round_plan = " ".join(
            str(row["detail"])
            for row in db.execute(
                f"explain query plan select * from ({feedback_projection('feedback_processing_transitions')}) "
                "where round_id in (?, ?) order by round_id, id",
                round_ids,
            )
        )

    assert "idx_actions_fb_transition_batch_round" in batch_plan
    assert "idx_actions_fb_transition_round" in round_plan


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
    db_path = tmp_path / f"positive-round-{round_number}.sqlite3"
    store = AutoReplyStore(db_path)
    stable_error = "schema_migration_invalid_feedback_processing_schema"
    store.upsert_feedback_event(
        key="feedback-valid-schema", feedback_token="token-valid-schema"
    )
    store.upsert_feedback_event(
        key=f"feedback-invalid-schema-{round_number}",
        feedback_token=f"token-invalid-schema-{round_number}",
    )
    with store._connect() as db:
        valid_round_id = _insert_feedback_owner(db, "feedback_processing_rounds", {
            "feedback_key": "feedback-valid-schema", "round_number": 1,
            "batch_id": "batch-valid-schema", "status": "processing",
        }).lastrowid
        assert valid_round_id is not None
        _insert_feedback_owner(db, "feedback_processing_rounds", {
            "feedback_key": f"feedback-invalid-schema-{round_number}",
            "round_number": round_number,
            "batch_id": f"batch-invalid-schema-{round_number}",
            "status": "processing",
        })
    with sqlite3.connect(db_path) as db:
        assert not AutoReplyStore._feedback_processing_round_storage_is_valid(db)
    with pytest.raises(store_module.FeedbackProcessingSchemaIntegrityError, match=stable_error):
        AutoReplyStore(db_path)
    assert db_path.resolve() not in store_module._INITIALIZED_STORE_PATHS
    with sqlite3.connect(db_path) as db:
        db.execute(
            "delete from feedback_processing_rounds where feedback_key=?",
            (f"feedback-invalid-schema-{round_number}",),
        )
        db.execute(
            "update feedback_processing_rounds set round_number=? where id=?",
            (round_number, valid_round_id),
        )
    with sqlite3.connect(db_path) as db:
        assert not AutoReplyStore._feedback_processing_round_storage_is_valid(db)
    with pytest.raises(store_module.FeedbackProcessingSchemaIntegrityError, match=stable_error):
        AutoReplyStore(db_path)
    assert db_path.resolve() not in store_module._INITIALIZED_STORE_PATHS


def test_feedback_round_schema_rejects_unknown_receipt_version(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "receipt-version-schema.sqlite3")
    store.upsert_feedback_event(
        key="feedback-invalid-receipt", feedback_token="token-invalid-receipt"
    )
    with store._connect() as db:
        _insert_feedback_owner(db, "feedback_processing_rounds", {
            "feedback_key": "feedback-invalid-receipt",
            "round_number": 1, "batch_id": "batch-invalid-receipt",
            "status": "processing", "receipt_version": 3,
        })
    with pytest.raises(ValueError, match="receipt version is invalid"):
        store.list_feedback_processing_rounds("feedback-invalid-receipt")


@pytest.mark.parametrize(
    ("trigger_name_stem", "clear_initialized_cache"),
    (
        ("trg_feedback_processing_round_number_positive", True),
        ("trg_feedback_processing_round_integer_v2", False),
    ),
)
@_native_predecessor_case
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
@_native_predecessor_case
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
@_native_predecessor_case
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


@_native_predecessor_case
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

    stable_error = "schema_migration_invalid_feedback_processing_schema"
    with sqlite3.connect(db_path) as db:
        assert not AutoReplyStore._feedback_processing_round_storage_is_valid(db)
    with pytest.raises(store_module.FeedbackProcessingSchemaIntegrityError, match=stable_error):
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


def test_current_cached_store_revalidates_invalid_round_payload_before_fast_return(
    tmp_path: Path,
):
    db_path = tmp_path / "cached-current-invalid-round.sqlite3"
    path_key = db_path.resolve()
    store_module._INITIALIZED_STORE_PATHS.discard(path_key)
    store = AutoReplyStore(db_path)
    store.upsert_feedback_event(
        key="feedback-cached-invalid", feedback_token="token-cached-invalid"
    )
    with store._connect() as db:
        round_id = _insert_feedback_owner(db, "feedback_processing_rounds", {
            "feedback_key": "feedback-cached-invalid", "round_number": 1,
            "batch_id": "batch-cached-invalid", "status": "processing",
            "note": "preserve cached invalid row",
        }).lastrowid
        assert round_id is not None
    assert path_key in store_module._INITIALIZED_STORE_PATHS

    with sqlite3.connect(db_path) as db:
        db.execute(
            "update feedback_processing_rounds set round_number=1.5 where id=?",
            (round_id,),
        )

    stable_error = "schema_migration_invalid_feedback_processing_schema"
    with sqlite3.connect(db_path) as db:
        assert not AutoReplyStore._feedback_processing_round_storage_is_valid(db)
    with pytest.raises(store_module.FeedbackProcessingSchemaIntegrityError, match=stable_error):
        AutoReplyStore(db_path)
    assert path_key not in store_module._INITIALIZED_STORE_PATHS
    with sqlite3.connect(db_path) as db:
        assert db.execute(
            "select round_number,typeof(round_number),note "
            "from feedback_processing_rounds where id=?",
            (round_id,),
        ).fetchone() == (1.5, "real", "preserve cached invalid row")


def test_current_cached_store_rejects_unexpected_round_index_manifest(tmp_path: Path):
    db_path = tmp_path / "cached-current-round-manifest.sqlite3"
    path_key = db_path.resolve()
    store_module._INITIALIZED_STORE_PATHS.discard(path_key)
    store = AutoReplyStore(db_path)
    assert path_key in store_module._INITIALIZED_STORE_PATHS
    with store._connect() as db:
        db.execute(
            "create index idx_feedback_processing_rounds_feedback "
            "on feedback_processing_rounds(batch_id)"
        )
    with store._connect() as db:
        assert not store_module.schema_is_current(db)
    with pytest.raises(sqlite3.IntegrityError, match="unsupported or malformed Store source schema"):
        AutoReplyStore(db_path)
    assert path_key in store_module._INITIALIZED_STORE_PATHS
    with pytest.raises(sqlite3.IntegrityError, match="unsupported or malformed Store source schema"):
        AutoReplyStore(db_path)
    with sqlite3.connect(db_path) as db:
        assert "batch_id" in db.execute(
            "select sql from sqlite_master "
            "where name='idx_feedback_processing_rounds_feedback'"
        ).fetchone()[0]


def test_store_rejects_post_migration_manifest_before_marker_or_cache(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    db_path = tmp_path / "post-migration-manifest-invalid.sqlite3"
    path_key = db_path.resolve()
    store_module._INITIALIZED_STORE_PATHS.discard(path_key)
    stable_error = "schema_migration_invalid_feedback_processing_schema"
    monkeypatch.setattr(AutoReplyStore, "_schema_manifest_is_current_in_connection",
                        lambda self, db, **options: False)
    schema_error_type = store_module.FeedbackProcessingSchemaIntegrityError

    with pytest.raises(schema_error_type, match=stable_error) as error:
        AutoReplyStore(db_path)
    assert error.value.error_code == stable_error
    assert str(error.value) == stable_error

    assert path_key not in store_module._INITIALIZED_STORE_PATHS
    with sqlite3.connect(db_path) as db:
        assert db.execute("select 1 from sqlite_schema where name='state'").fetchone()
        assert db.execute(
            f"select value from ({store_module.state_projection('service_state')}) "
            "where key='store_schema_version'"
        ).fetchone() == (store_module.STORE_SCHEMA_VERSION,)



@_native_predecessor_case
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


@_native_predecessor_case
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


def test_feedback_cold_reopen_preserves_current_batch_rounds_and_sources(tmp_path: Path):
    db_path = tmp_path / "current-rounds.sqlite3"
    store = AutoReplyStore(db_path)
    store.upsert_feedback_event(key="pending", feedback_token="token-pending")
    _prepare_processing_round(store, "processing", "batch-processing")
    store.upsert_feedback_event(
        key="resolved", feedback_token="token-resolved", comment="original source"
    )
    resolve_feedback_fixture(store, "resolved", batch_id="batch-resolved")

    def snapshot(current):
        return {
            key: (
                current.get_feedback_event(key),
                current.get_feedback_processing_item(key),
                current.list_feedback_processing_rounds(key),
                current.list_feedback_processing_transitions(key),
            )
            for key in ("pending", "processing", "resolved")
        }, [
            current.get_feedback_processing_batch(batch_id)
            for batch_id in ("batch-processing", "batch-resolved")
        ]

    before = snapshot(store)
    assert before[0]["pending"][1].current_round_id == 0
    assert before[0]["pending"][2] == []
    assert before[0]["processing"][1].status == "processing"
    assert before[0]["resolved"][1].status == "resolved"
    for key in ("processing", "resolved"):
        item = before[0][key][1]
        rounds = before[0][key][2]
        assert len(rounds) == 1
        assert item.current_round_id == rounds[0].id
        assert rounds[0].receipt_version == 2
    for _ in range(2):
        store_module._INITIALIZED_STORE_PATHS.discard(db_path.resolve())
        assert snapshot(AutoReplyStore(db_path)) == before






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
    resolve_feedback_fixture(store, "feedback-resolved")

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
    resolve_feedback_fixture(store, "feedback-1", batch_id="batch-1")
    reopened = store.create_feedback_processing_batch(["feedback-1"], batch_id="batch-1")
    assert reopened.batch_id == original.batch_id
    assert reopened.requested_count == original.requested_count


def test_legacy_text_processing_ids_are_read_as_integers(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "legacy-ids.sqlite3")
    store.upsert_feedback_event(key="feedback-1", feedback_token="token-1")
    assert store.claim_feedback_processing_items("batch-1", ["feedback-1"])
    with store._connect() as db:
        _update_feedback_owner(db, "feedback_processing_rounds",
                      store.get_feedback_processing_item("feedback-1").current_round_id,
                      {"attempt_id": "12", "agent_run_id": "34"})
    item = store.get_feedback_processing_item("feedback-1")
    assert item is not None
    assert item.attempt_id == 12
    assert item.agent_run_id == 34


def test_batch_resolved_event_projection_and_status_transition_are_consistent(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "resolved.sqlite3")
    store.upsert_feedback_event(key="feedback-1", feedback_token="token-1")
    resolve_feedback_fixture(store, "feedback-1", batch_id="batch-1")
    item = store.get_feedback_processing_item("feedback-1")
    event = store.get_feedback_event("feedback-1")
    batch = store.get_feedback_processing_batch("batch-1")
    rounds = store.list_feedback_processing_rounds("feedback-1")
    assert item is not None
    assert event is not None
    assert batch is not None
    assert item.status == batch.status == rounds[0].status == "resolved"
    assert item.current_round_id == rounds[0].id
    assert item.resolved_at == event.resolved_at == batch.resolved_at == rounds[0].resolved_at
    assert item.resolved_at

    with pytest.raises(ValueError):
        store.patch_feedback_processing_item_evidence("feedback-1", status="pending")

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
        assert db.execute(
            "select count(*) from feedback_processing_items"
        ).fetchone()[0] == 1
        assert db.execute(
            "select count(*) from feedback_processing_batches"
        ).fetchone()[0] == 1
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
    assert {ref["route"] for ref in refs if ref["label"] == "task#56"} == {
        "/tasks/legacy-project/56"
    }
    assert "/attempts/34/execution/run" not in {ref["route"] for ref in refs}


def test_missing_summary_is_empty_and_start_message_has_no_feedback_body():
    item = FeedbackImportItem(feedback_key="feedback-1", summary="", references=[])
    message = build_feedback_start_message("batch-1", [item])
    assert "batch-1" in message
    assert "Use managed system Skill: ceo-feedback-iteration" in message
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
    receipt_version: int = 1,
) -> int:
    store.upsert_feedback_event(
        key=feedback_key,
        feedback_token=f"token-{feedback_key}",
        comment=comment,
        raw_json='{"source":"preserved"}',
    )
    with store._connect() as db:
        _insert_feedback_owner(db, "feedback_processing_batches", {
            "batch_id": batch_id, "status": "resolved", "requested_count": 1,
            "resolved_at": "2026-08-30 01:02:03",
        })
        evidence = {
            "commit_sha": "a" * 40,
            "test_evidence_json": json.dumps({"pytest": {"exit_code": 0}}),
            "restart_evidence_json": json.dumps({
                "launchd_label": "com.ceo-agent-service.main",
                "before_pid": 1, "after_pid": 2,
            }),
            "health_evidence_json": json.dumps({
                "url": "http://127.0.0.1:8765/healthz",
                "status_code": 200, "ok": True,
            }),
        }
        round_id = _insert_feedback_owner(db, "feedback_processing_rounds", {
            "feedback_key": feedback_key, "round_number": round_number,
            "batch_id": batch_id, "status": "resolved",
            "workbench_task_id": "task-old", "workbench_turn_id": "turn-old",
            "attempt_id": 12, "agent_run_id": 34, **evidence,
            "backlog_evidence_json": json.dumps({
                "processing": 0, "failed": 0, "retryable": 0,
            }),
            "receipt_version": receipt_version, "note": "old note",
            "started_at": "2026-08-30 00:00:00",
            "resolved_at": "2026-08-30 01:02:03",
            "created_at": "2026-08-30 00:00:00",
            "updated_at": "2026-08-30 01:02:03",
        }).lastrowid
        assert round_id is not None
        if receipt_version == 2:
            insert_feedback_action(db, "feedback_processing_transitions", {
                "feedback_key": feedback_key, "round_id": round_id,
                "batch_id": batch_id, "from_status": "pending",
                "to_status": "processing", "created_at": "2026-08-30 00:00:00",
            })
            insert_feedback_action(db, "feedback_processing_transitions", {
                "feedback_key": feedback_key, "round_id": round_id,
                "batch_id": batch_id, "from_status": "processing",
                "to_status": "resolved", "workbench_task_id": "task-old",
                "workbench_turn_id": "turn-old",
                "created_at": "2026-08-30 01:02:03",
            })
        item_row = db.execute(
            f"select rowid from ({_feedback_owner_projection('feedback_processing_items', include_rowid=True)}) "
            "where feedback_key=?", (feedback_key,),
        ).fetchone()
        assert item_row is not None
        _update_feedback_owner(db, "feedback_processing_items", item_row[0], {
            "current_round_id": round_id, "batch_id": batch_id,
            "status": "resolved",
            "updated_at": "2026-08-30 01:02:03",
        })
        event_row = db.execute(
            f"select _source_rowid from ({feedback_projection('feedback_events', with_rowid=True)}) "
            "where key=?", (feedback_key,),
        ).fetchone()
        assert event_row is not None
        set_feedback_action(db, "feedback_events", event_row[0], {
            "resolved_at": "2026-08-30 01:02:03",
            "updated_at": "2026-08-30 01:02:03",
        })
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
                f"select * from ({_feedback_owner_projection('feedback_processing_rounds')}) where id=?",
                (round_id,),
            ).fetchone()
        )
        event_before = dict(
            db.execute(
                f"select * from ({feedback_projection('feedback_events')}) where key='feedback-1'"
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
                f"select * from ({_feedback_owner_projection('feedback_processing_rounds')}) where id=?",
                (round_id,),
            ).fetchone()
        )
        event_after = dict(
            db.execute(
                f"select * from ({feedback_projection('feedback_events')}) where key='feedback-1'"
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
        rounds_before = [dict(row) for row in db.execute(
            f"select * from ({_feedback_owner_projection('feedback_processing_rounds')})"
        )]
        transitions_before = [dict(row) for row in db.execute(
            f"select * from ({feedback_projection('feedback_processing_transitions')})"
        )]

    with pytest.raises(feedback_processing_module.FeedbackProcessingReopenError) as error:
        store.reopen_feedback_processing_item("feedback-1", reason="active claim")

    assert error.value.error_code == "feedback_reopen_processing"
    assert store.get_feedback_processing_item("feedback-1") == claimed
    with store._connect() as db:
        assert [dict(row) for row in db.execute(
            f"select * from ({_feedback_owner_projection('feedback_processing_rounds')})"
        )] == rounds_before
        assert [dict(row) for row in db.execute(
            f"select * from ({feedback_projection('feedback_processing_transitions')})"
        )] == transitions_before


@pytest.mark.parametrize("damage", ["missing", "wrong-status", "wrong-owner", "wrong-pointer"])
def test_reopen_incomplete_history_rolls_back_atomically(tmp_path: Path, damage: str):
    store = AutoReplyStore(tmp_path / f"reopen-incomplete-{damage}.sqlite3")
    round_id = _seed_resolved_feedback_round(store, "feedback-1")
    if damage == "wrong-owner":
        store.upsert_feedback_event(key="other", feedback_token="token-other")
    with store._connect() as db:
        if damage == "missing":
            _delete_feedback_owner(db, "feedback_processing_rounds", round_id)
        elif damage == "wrong-status":
            _update_feedback_owner(db, "feedback_processing_rounds", round_id,
                          {"status": "processing"})
        elif damage == "wrong-owner":
            _update_feedback_owner(db, "feedback_processing_rounds", round_id,
                          {"feedback_key": "other"})
        else:
            item_row = db.execute(
                f"select rowid from ({_feedback_owner_projection('feedback_processing_items', include_rowid=True)}) "
                "where feedback_key='feedback-1'"
            ).fetchone()
            assert item_row is not None
            _update_feedback_owner(db, "feedback_processing_items", item_row[0],
                          {"current_round_id": round_id + 100})
        item_before = dict(
            db.execute(
                f"select * from ({_feedback_owner_projection('feedback_processing_items')}) "
                "where feedback_key='feedback-1'"
            ).fetchone()
        )
        event_before = dict(
            db.execute(
                f"select * from ({feedback_projection('feedback_events')}) where key='feedback-1'"
            ).fetchone()
        )

    with pytest.raises(feedback_processing_module.FeedbackProcessingReopenError) as error:
        store.reopen_feedback_processing_item("feedback-1", reason="history is incomplete")

    assert error.value.error_code == "feedback_reopen_history_incomplete"
    with store._connect() as db:
        assert dict(db.execute(
            f"select * from ({_feedback_owner_projection('feedback_processing_items')}) "
            "where feedback_key='feedback-1'"
        ).fetchone()) == item_before
        assert dict(db.execute(
            f"select * from ({feedback_projection('feedback_events')}) where key='feedback-1'"
        ).fetchone()) == event_before
        assert db.execute(
            "select count(*) from actions where record_type='feedback_processing_transitions'"
        ).fetchone()[0] == 0


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
        assert db.execute(
            f"select count(*) from ({_feedback_owner_projection('feedback_processing_rounds')}) "
            "where feedback_key='feedback-1'"
        ).fetchone()[0] == 2
        assert db.execute(
            f"select count(*) from ({feedback_projection('feedback_processing_transitions')}) "
            "where feedback_key='feedback-1'"
        ).fetchone()[0] == 2


def test_batch_claim_rolls_back_when_one_pending_item_has_inconsistent_history(
    tmp_path: Path,
):
    store = AutoReplyStore(tmp_path / "claim-history-conflict.sqlite3")
    for key in ("good", "bad"):
        store.upsert_feedback_event(key=key, feedback_token=f"token-{key}")
    with store._connect() as db:
        _insert_feedback_owner(db, "feedback_processing_rounds", {
            "feedback_key": "bad", "round_number": 1,
            "batch_id": "old-batch", "status": "processing",
            "started_at": db.execute("select current_timestamp").fetchone()[0],
        })

    with pytest.raises(FeedbackProcessingClaimError):
        store.claim_feedback_processing_items("new-batch", ["good", "bad"])

    assert store.get_feedback_processing_batch("new-batch") is None
    assert {store.get_feedback_processing_item(key).status for key in ("good", "bad")} == {"pending"}
    with store._connect() as db:
        assert db.execute(
            f"select count(*) from ({_feedback_owner_projection('feedback_processing_rounds')}) "
            "where batch_id='new-batch'"
        ).fetchone()[0] == 0
        assert db.execute(
            "select count(*) from actions where record_type='feedback_processing_transitions'"
        ).fetchone()[0] == 0


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


def test_association_rejects_historical_expected_batch_without_current_round_mutation(
    tmp_path: Path,
):
    store = AutoReplyStore(tmp_path / "association-expected-batch.sqlite3")
    _seed_resolved_feedback_round(store, "feedback-1")
    store.reopen_feedback_processing_item("feedback-1", reason="premature")
    current = store.claim_feedback_processing_items("batch-2", ["feedback-1"])[0]

    with pytest.raises(ValueError, match="exact current processing batch"):
        store.associate_feedback_processing_turn(
            "feedback-1",
            expected_batch_id="batch-1",
            workbench_task_id="stale-task",
            workbench_turn_id="stale-turn",
            attempt_id=999,
            agent_run_id=1000,
        )

    assert store.get_feedback_processing_item("feedback-1") == current
    rounds = store.list_feedback_processing_rounds("feedback-1")
    assert rounds[0].batch_id == "batch-2"
    assert rounds[0].workbench_task_id == ""
    assert rounds[0].workbench_turn_id == ""
    assert rounds[0].attempt_id == 0
    assert rounds[0].agent_run_id == 0


@pytest.mark.parametrize("operation", ["associate", "patch"])
def test_stale_or_ambiguous_current_round_cannot_be_mutated(
    tmp_path: Path, operation: str
):
    store = AutoReplyStore(tmp_path / f"stale-round-{operation}.sqlite3")
    old_round_id = _seed_resolved_feedback_round(store, "feedback-1")
    store.reopen_feedback_processing_item("feedback-1", reason="premature")
    store.claim_feedback_processing_items("batch-2", ["feedback-1"])
    with store._connect() as db:
        round_rows_before = [dict(row) for row in db.execute(
            f"select * from ({_feedback_owner_projection('feedback_processing_rounds')}) order by id"
        )]
        item_before = dict(db.execute(
            f"select * from ({_feedback_owner_projection('feedback_processing_items')}) "
            "where feedback_key='feedback-1'"
        ).fetchone())
        _update_feedback_owner(db, "feedback_processing_items",
                      _feedback_item_source_rowid(db, "feedback-1"),
                      {"current_round_id": old_round_id})

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
        assert [dict(row) for row in db.execute(
            f"select * from ({_feedback_owner_projection('feedback_processing_rounds')}) order by id"
        )] == round_rows_before
        after = dict(db.execute(
            f"select * from ({_feedback_owner_projection('feedback_processing_items')}) "
            "where feedback_key='feedback-1'"
        ).fetchone())
    for key in ("feedback_key", "batch_id", "status", "created_at", "updated_at", "resolved_at"):
        assert after[key] == item_before[key]
    assert after["workbench_task_id"] is None  # stale pointer must not project old evidence


def test_resolution_evidence_records_backlog_without_gating_task_completion():
    complete = _complete_resolution_receipt()
    validate_resolution_evidence(complete, commit_is_ancestor=True)

    without_snapshot = complete.model_copy(update={"backlog_evidence": {}})
    validate_resolution_evidence(without_snapshot, commit_is_ancestor=True)
    for backlog in (
        {"processing": 1, "failed": 0, "retryable": 0},
        {"processing": 0, "failed": 1, "retryable": 0},
        {"processing": 0, "failed": 0, "retryable": 1},
    ):
        validate_resolution_evidence(
            complete.model_copy(update={"backlog_evidence": backlog}),
            commit_is_ancestor=True,
        )
    for backlog in (
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

    decision = feedback_processing_module.FeedbackIterationDecision(
        scope="runtime_config",
        root_cause="The approved runtime configuration was loaded.",
        feedback_keys=["feedback-1"],
        source_references=["runtime load receipt"],
        target_runtime_config_id=2,
        why_not_code="No code change was needed.",
        acceptance={
            "scenario": "Load the approved configuration",
            "expected_behavior": "The task uses the verified runtime configuration.",
            "verification": ["Matching startup load receipt exists."],
        },
    )
    scope_evidence = complete.model_copy(update={
        "backlog_evidence": {"processing": 1, "failed": 13, "retryable": 0},
        "previous_runtime_config_id": 1,
        "runtime_config_id": 2,
        "load_receipt_id": 3,
    })
    feedback_processing_module.validate_resolution_receipt(
        decision, scope_evidence, commit_is_ancestor=True
    )


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
    receipt = _complete_resolution_receipt()

    with pytest.raises(ValueError):
        store.resolve_feedback_processing_batch(
            "batch-2", stale_receipt, commit_is_ancestor=True
        )
    assert store.get_feedback_processing_item("feedback-1").status == "processing"
    assert store.get_feedback_processing_batch("batch-2").status == "processing"
    assert store.list_feedback_processing_rounds("feedback-1")[0].status == "processing"

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
        _update_feedback_owner(db, "feedback_processing_rounds", claimed[1].current_round_id,
                      {"health_evidence_json": "{}"})

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
        item_row = db.execute(
            f"select rowid from ({_feedback_owner_projection('feedback_processing_items', include_rowid=True)}) "
            "where feedback_key='feedback-1'"
        ).fetchone()
        assert item_row is not None
        _update_feedback_owner(db, "feedback_processing_items", item_row[0],
                      {"status": "resolved"})

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
    queries = {
        "feedback_events": (
            f"select * from ({feedback_projection('feedback_events', with_rowid=True)}) "
            "order by _source_rowid"
        ),
        "feedback_processing_transitions": (
            f"select * from ({feedback_projection('feedback_processing_transitions', with_rowid=True)}) "
            "order by _source_rowid"
        ),
        **{
            kind: f"select * from ({_feedback_owner_projection(kind, include_rowid=True)}) order by rowid"
            for kind in (
                "feedback_processing_items", "feedback_processing_rounds",
                "feedback_processing_batches",
            )
        },
    }
    with store._connect() as db:
        return {
            kind: [tuple(row) for row in db.execute(query)]
            for kind, query in queries.items()
        }


def _feedback_item_source_rowid(db: sqlite3.Connection, feedback_key: str) -> int:
    row = db.execute(
        f"select rowid from ({_feedback_owner_projection('feedback_processing_items', include_rowid=True)}) "
        "where feedback_key=?", (feedback_key,),
    ).fetchone()
    assert row is not None
    return int(row[0])


def _feedback_batch_source_rowid(db: sqlite3.Connection, batch_id: str) -> int:
    row = db.execute(
        f"select rowid from ({_feedback_owner_projection('feedback_processing_batches', include_rowid=True)}) "
        "where batch_id=?", (batch_id,),
    ).fetchone()
    assert row is not None
    return int(row[0])


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
    "corruption",
    (
        ("resolved_at", ""),
        ("workbench_task_id", ""),
        ("workbench_turn_id", ""),
        ("attempt_id", 0),
        ("agent_run_id", 0),
        ("commit_sha", ""),
        ("test_evidence_json", "{}"),
        ("restart_evidence_json", "{}"),
        ("health_evidence_json", "{}"),
        ("backlog_evidence_json", "{}"),
    ),
)
def test_reopen_rejects_incomplete_resolved_round_receipt_without_mutation(
    tmp_path: Path, corruption: tuple[str, object]
):
    store = AutoReplyStore(tmp_path / "reopen-complete-history.sqlite3")
    round_id = _seed_resolved_feedback_round(
        store,
        "feedback-1",
        receipt_version=2,
    )
    with store._connect() as db:
        _update_feedback_owner(db, "feedback_processing_rounds", round_id,
                      {corruption[0]: corruption[1]})
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
        batch = db.execute(
            f"select rowid from ({_feedback_owner_projection('feedback_processing_batches', include_rowid=True)}) "
            "where batch_id='batch-1'"
        ).fetchone()
        assert batch is not None
        _update_feedback_owner(db, "feedback_processing_batches", batch[0],
                      {"requested_count": 2})
    before = _feedback_processing_snapshot(store)

    with pytest.raises((FeedbackProcessingBatchError, FeedbackProcessingClaimError)):
        store.claim_feedback_processing_items("batch-1", ["feedback-1"])

    assert _feedback_processing_snapshot(store) == before


def test_resolve_rejects_missing_source_feedback_before_any_mutation(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "resolve-source-missing.sqlite3")
    _, receipt = _prepare_processing_round(store)
    with store._connect() as db:
        db.execute(
            "delete from actions where record_type='feedback_events' "
            "and fb_event_key='feedback-1'"
        )
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
            after update of payload_json on tasks
            when old.record_type='feedback_processing_rounds'
             and old."feedback_processing_rounds__status"='processing'
             and new."feedback_processing_rounds__status"='resolved'
            begin
                select feedback_resolution_delay();
            end
            """
        )

    assert store.resolve_feedback_processing_batch(
        "batch-1", receipt, commit_is_ancestor=True
    )
    with store._connect() as db:
        queries = (
            f"select resolved_at as timestamp from ({_feedback_owner_projection('feedback_processing_rounds')}) where batch_id='batch-1'",
            f"select updated_at as timestamp from ({_feedback_owner_projection('feedback_processing_rounds')}) where batch_id='batch-1'",
            f"select resolved_at as timestamp from ({_feedback_owner_projection('feedback_processing_items')}) where batch_id='batch-1'",
            f"select updated_at as timestamp from ({_feedback_owner_projection('feedback_processing_items')}) where batch_id='batch-1'",
            f"select resolved_at as timestamp from ({feedback_projection('feedback_events')}) where key in ('feedback-1', 'feedback-2')",
            f"select updated_at as timestamp from ({feedback_projection('feedback_events')}) where key in ('feedback-1', 'feedback-2')",
            f"select resolved_at as timestamp from ({_feedback_owner_projection('feedback_processing_batches')}) where batch_id='batch-1'",
            f"select updated_at as timestamp from ({_feedback_owner_projection('feedback_processing_batches')}) where batch_id='batch-1'",
            f"select created_at as timestamp from ({feedback_projection('feedback_processing_transitions')}) where batch_id='batch-1' and from_status='processing' and to_status='resolved'",
        )
        timestamps = {
            str(row["timestamp"])
            for query in queries
            for row in db.execute(query)
        }
    assert len(timestamps) == 1
    assert store.reopen_feedback_processing_item(
        "feedback-1", reason="delayed resolution remained atomic"
    ).status == "pending"


def test_resolve_rejects_orphan_round_in_batch_before_any_mutation(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "resolve-orphan-round.sqlite3")
    _, receipt = _prepare_processing_round(store)
    store.upsert_feedback_event(
        key="orphan-feedback", feedback_token="token-orphan-feedback"
    )
    with store._connect() as db:
        _insert_feedback_owner(db, "feedback_processing_rounds", {
            "feedback_key": "orphan-feedback", "round_number": 1,
            "batch_id": "batch-1", "status": "processing",
            "started_at": db.execute("select current_timestamp").fetchone()[0],
        })
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
        _delete_feedback_owner(db, "feedback_processing_items",
                      _feedback_item_source_rowid(db, "feedback-1"))
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
        item = db.execute(
            f"select rowid from ({_feedback_owner_projection('feedback_processing_items', include_rowid=True)}) "
            "where feedback_key='feedback-1'"
        ).fetchone()
        assert item is not None
        _update_feedback_owner(db, "feedback_processing_items", item[0],
                      {"current_round_id": malformed_pointer})
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
        item = db.execute(
            f"select rowid from ({_feedback_owner_projection('feedback_processing_items', include_rowid=True)}) "
            "where feedback_key='feedback-1'"
        ).fetchone()
        assert item is not None
        _update_feedback_owner(db, "feedback_processing_items", item[0],
                      {"current_round_id": malformed_pointer})

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
            item = db.execute(
                f"select rowid from ({_feedback_owner_projection('feedback_processing_items', include_rowid=True)}) "
                "where feedback_key='feedback-1'"
            ).fetchone()
            assert item is not None
            _update_feedback_owner(db, "feedback_processing_items", item[0],
                          {"current_round_id": "not-an-int"})
        before = _feedback_processing_snapshot(store)
        with pytest.raises(FeedbackProcessingClaimError):
            store.claim_feedback_processing_items("batch-1", ["feedback-1"])
    else:
        _, receipt = _prepare_processing_round(store)
        with store._connect() as db:
            item = db.execute(
                f"select rowid from ({_feedback_owner_projection('feedback_processing_items', include_rowid=True)}) "
                "where feedback_key='feedback-1'"
            ).fetchone()
            assert item is not None
            _update_feedback_owner(db, "feedback_processing_items", item[0],
                          {"current_round_id": 1.5})
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
        assert "receipt_version" in {
            column[0] for column in db.execute(
                f"select * from ({_feedback_owner_projection('feedback_processing_rounds')}) where 0"
            ).description
        }


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
        "feedback_keys",
    ]
    assert (
        store_signature.parameters["commit_is_ancestor"].default
        is inspect.Parameter.empty
    )
    assert store_signature.parameters["feedback_keys"].default is None
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
        _update_feedback_owner(db, "feedback_processing_rounds", round_id,
                      {"receipt_version": 1, "backlog_evidence_json": "{}"})

    reopened = store.reopen_feedback_processing_item(
        "feedback-1", reason="legacy receipt predates backlog evidence"
    )

    assert reopened is not None and reopened.status == "pending"
    with store._connect() as db:
        round_row = db.execute(
            "select receipt_version, backlog_evidence_json "
            f"from ({_feedback_owner_projection('feedback_processing_rounds')}) where id=?",
            (round_id,),
        ).fetchone()
    assert tuple(round_row) == (1, "{}")


@_native_predecessor_case
def test_pre_scope_receipt_schema_migrates_legacy_replay_and_reopen(tmp_path: Path):
    db_path = tmp_path / "pre-scope-receipt.sqlite3"
    store = AutoReplyStore(db_path)
    _seed_resolved_feedback_round(store, "feedback-1", receipt_version=1)
    with store._connect() as db:
        db.execute("alter table feedback_processing_rounds drop column scope_receipt_json")

    store_module._INITIALIZED_STORE_PATHS.discard(db_path.resolve())
    migrated = AutoReplyStore(db_path)
    with migrated._connect() as db:
        columns = {
            str(row["name"])
            for row in db.execute("pragma table_info(feedback_processing_rounds)")
        }
        stored_scope_receipt = db.execute(
            "select scope_receipt_json from feedback_processing_rounds"
        ).fetchone()[0]
    assert "scope_receipt_json" in columns
    assert stored_scope_receipt == "{}"
    assert migrated.resolve_feedback_processing_batch("batch-1", commit_is_ancestor=True) is True
    assert migrated.reopen_feedback_processing_item(
        "feedback-1", reason="legacy receipt remains valid"
    ).status == "pending"


@_native_predecessor_case
def test_receipt_version_migration_marks_only_valid_existing_backlog_v2(
    tmp_path: Path,
):
    db_path = tmp_path / "receipt-version-migration.sqlite3"
    store = AutoReplyStore(db_path)
    valid_id = _seed_resolved_feedback_round(
        store,
        "feedback-valid",
        batch_id="batch-valid",
        receipt_version=2,
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
        item = db.execute(
            f"select rowid from ({_feedback_owner_projection('feedback_processing_items', include_rowid=True)}) "
            "where feedback_key='feedback-1'"
        ).fetchone()
        assert item is not None
        _update_feedback_owner(db, "feedback_processing_items", item[0],
                      {"current_round_id": old_round_id, "batch_id": "batch-old"})
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




def test_resolved_batch_shortcut_rejects_corrupt_batch_only_state(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "resolved-batch-corrupt.sqlite3")
    store.upsert_feedback_event(key="feedback-1", feedback_token="token-1")
    with store._connect() as db:
        _insert_feedback_owner(db, "feedback_processing_batches", {
            "batch_id": "batch-1", "status": "resolved",
            "requested_count": 1,
            "resolved_at": db.execute("select current_timestamp").fetchone()[0],
        })
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
    "damage",
    (
        ("resolved_at", ""),
        ("updated_at", "1999-01-01 00:00:00"),
    ),
    ids=("missing-resolved-at", "updated-at-mismatch"),
)
def test_resolved_batch_idempotency_requires_shared_batch_completion_timestamp(
    tmp_path: Path,
    damage: tuple[str, str],
):
    store = AutoReplyStore(tmp_path / "resolved-batch-timestamp.sqlite3")
    receipt = _prepare_resolved_v2_batch(store)
    with store._connect() as db:
        _update_feedback_owner(db, "feedback_processing_batches",
                      _feedback_batch_source_rowid(db, "batch-1"),
                      {damage[0]: damage[1]})
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
        transition = db.execute(
            f"select * from ({feedback_projection('feedback_processing_transitions', with_rowid=True)}) "
            "where batch_id='batch-1' and from_status='processing' "
            "and to_status='resolved'"
        ).fetchone()
        assert transition is not None
        transition_rowid = int(transition["_source_rowid"])
        if damage == "missing":
            db.execute(
                "delete from actions where record_type='feedback_processing_transitions' "
                "and legacy_key=?", (str(transition_rowid),),
            )
        elif damage == "duplicate":
            insert_feedback_action(db, "feedback_processing_transitions", {
                field: transition[field] for field in (
                    "feedback_key", "round_id", "batch_id", "from_status",
                    "to_status", "reason", "workbench_task_id",
                    "workbench_turn_id", "created_at",
                )
            })
        elif damage == "mismatched":
            set_feedback_action(db, "feedback_processing_transitions",
                                transition_rowid, {"workbench_task_id": "wrong-task"})
        else:
            set_feedback_action(db, "feedback_processing_transitions",
                                transition_rowid,
                                {"created_at": "1999-01-01 00:00:00"})
    before = _feedback_processing_snapshot(store)

    with pytest.raises(ValueError):
        store.resolve_feedback_processing_batch(
            "batch-1", receipt, commit_is_ancestor=True
        )

    assert _feedback_processing_snapshot(store) == before


@pytest.mark.parametrize("damage", ("source-timestamp",))
def test_current_resolved_batch_idempotency_requires_exact_terminal_projection(
    tmp_path: Path,
    damage: str,
):
    store = AutoReplyStore(tmp_path / f"resolved-current-{damage}.sqlite3")
    receipt = _prepare_resolved_v2_batch(store)
    with store._connect() as db:
        if damage == "source-timestamp":
            event = db.execute(
                f"select _source_rowid from ({feedback_projection('feedback_events', with_rowid=True)}) "
                "where key='feedback-1'"
            ).fetchone()
            assert event is not None
            set_feedback_action(db, "feedback_events", event[0],
                                {"resolved_at": "1999-01-01 00:00:00"})
        else:
            _update_feedback_owner(db, "feedback_processing_items",
                          _feedback_item_source_rowid(db, "feedback-1"),
                          {"note": "wrong-note"})
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
        _update_feedback_owner(db, "feedback_processing_items",
                      _feedback_item_source_rowid(db, "feedback-1"),
                      {field: value})
    before = _feedback_processing_snapshot(store)

    with pytest.raises(ValueError):
        store.resolve_feedback_processing_batch(
            "batch-1", receipt, commit_is_ancestor=True
        )

    assert _feedback_processing_snapshot(store) == before


def _prepare_old_batch_with_newer_batch(
    store: AutoReplyStore,
    *,
    newer_status: str,
    include_second_member: bool = False,
) -> tuple[ResolutionEvidence, ResolutionEvidence]:
    old_receipt = _prepare_resolved_v2_batch(store)
    store.reopen_feedback_processing_item("feedback-1", reason="needs another round")
    feedback_keys = ["feedback-1"]
    if include_second_member:
        store.upsert_feedback_event(
            key="feedback-2",
            feedback_token="token-feedback-2",
        )
        feedback_keys.append("feedback-2")
    store.claim_feedback_processing_items("batch-2", feedback_keys)
    newer_receipt = _complete_resolution_receipt("c" * 40)
    for index, feedback_key in enumerate(feedback_keys, start=1):
        store.associate_feedback_processing_turn(
            feedback_key,
            workbench_task_id="task-newer",
            workbench_turn_id="turn-newer",
            attempt_id=100 + index,
            agent_run_id=200 + index,
        )
        store.patch_feedback_processing_item_evidence(
            feedback_key,
            commit_sha=newer_receipt.commit_sha,
            test_evidence=newer_receipt.test_evidence,
            restart_evidence=newer_receipt.restart_evidence,
            health_evidence=newer_receipt.health_evidence,
            note="newer receipt",
        )
    if newer_status == "resolved":
        assert store.resolve_feedback_processing_batch(
            "batch-2", newer_receipt, commit_is_ancestor=True
        )
    else:
        assert newer_status == "processing"
    return old_receipt, newer_receipt


@pytest.mark.parametrize("newer_status", ("processing", "resolved"))
def test_old_resolved_batch_idempotency_accepts_complete_newer_batch(
    tmp_path: Path,
    newer_status: str,
):
    store = AutoReplyStore(tmp_path / f"newer-valid-{newer_status}.sqlite3")
    old_receipt, _ = _prepare_old_batch_with_newer_batch(
        store,
        newer_status=newer_status,
        include_second_member=True,
    )

    assert store.resolve_feedback_processing_batch(
        "batch-1", old_receipt, commit_is_ancestor=True
    )


@pytest.mark.parametrize("newer_status", ("processing", "resolved"))
@pytest.mark.parametrize(
    "damage",
    ("missing-batch", "wrong-status", "requested-count"),
)
def test_old_resolved_batch_idempotency_requires_newer_batch_definition(
    tmp_path: Path,
    newer_status: str,
    damage: str,
):
    store = AutoReplyStore(
        tmp_path / f"newer-definition-{newer_status}-{damage}.sqlite3"
    )
    old_receipt, _ = _prepare_old_batch_with_newer_batch(
        store,
        newer_status=newer_status,
    )
    with store._connect() as db:
        batch_rowid = _feedback_batch_source_rowid(db, "batch-2")
        if damage == "missing-batch":
            _delete_feedback_owner(db, "feedback_processing_batches", batch_rowid)
        elif damage == "wrong-status":
            _update_feedback_owner(db, "feedback_processing_batches", batch_rowid,
                          {"status": "pending"})
        else:
            _update_feedback_owner(db, "feedback_processing_batches", batch_rowid,
                          {"requested_count": 2})
    before = _feedback_processing_snapshot(store)

    with pytest.raises(ValueError, match="resolved batch newer lineage is incomplete"):
        store.resolve_feedback_processing_batch(
            "batch-1", old_receipt, commit_is_ancestor=True
        )

    assert _feedback_processing_snapshot(store) == before


@pytest.mark.parametrize("damage", ("orphan-round", "missing-item", "extra-item"))
def test_old_resolved_batch_idempotency_requires_complete_newer_membership(
    tmp_path: Path,
    damage: str,
):
    store = AutoReplyStore(tmp_path / f"newer-membership-{damage}.sqlite3")
    old_receipt, _ = _prepare_old_batch_with_newer_batch(
        store,
        newer_status="processing",
        include_second_member=True,
    )
    if damage == "orphan-round":
        store.upsert_feedback_event(
            key="feedback-orphan", feedback_token="token-feedback-orphan"
        )
    with store._connect() as db:
        if damage == "orphan-round":
            _insert_feedback_owner(db, "feedback_processing_rounds", {
                "feedback_key": "feedback-orphan", "round_number": 1,
                "batch_id": "batch-2", "status": "processing",
                "receipt_version": 2,
                "started_at": db.execute("select current_timestamp").fetchone()[0],
            })
        elif damage == "missing-item":
            _delete_feedback_owner(db, "feedback_processing_items",
                          _feedback_item_source_rowid(db, "feedback-2"))
        else:
            store.upsert_feedback_event(
                key="feedback-extra",
                feedback_token="token-feedback-extra",
            )
            _update_feedback_owner(db, "feedback_processing_items",
                          _feedback_item_source_rowid(db, "feedback-extra"),
                          {"batch_id": "batch-2", "status": "processing"})
    before = _feedback_processing_snapshot(store)

    with pytest.raises(ValueError, match="resolved batch newer lineage is incomplete"):
        store.resolve_feedback_processing_batch(
            "batch-1", old_receipt, commit_is_ancestor=True
        )

    assert _feedback_processing_snapshot(store) == before


@pytest.mark.parametrize(
    ("field", "round_value", "item_value"),
    (
        ("commit_sha", "d" * 40, "d" * 40),
        (
            "test_evidence_json",
            '{"different":{"exit_code":0}}',
            '{"different":{"exit_code":0}}',
        ),
        (
            "restart_evidence_json",
            '{"launchd_label":"com.ceo-agent-service.main",'
            '"before_pid":20,"after_pid":21}',
            '{"launchd_label":"com.ceo-agent-service.main",'
            '"before_pid":20,"after_pid":21}',
        ),
        (
            "health_evidence_json",
            '{"url":"http://127.0.0.1:8765/healthz",'
            '"status_code":200,"ok":true,"probe":"different"}',
            '{"url":"http://127.0.0.1:8765/healthz",'
            '"status_code":200,"ok":true,"probe":"different"}',
        ),
        (
            "backlog_evidence_json",
            '{"processing":0,"failed":0,"retryable":0,"scope":"different"}',
            None,
        ),
    ),
)
def test_resolved_newer_batch_requires_one_common_member_receipt(
    tmp_path: Path,
    field: str,
    round_value: object,
    item_value: object | None,
):
    store = AutoReplyStore(tmp_path / f"newer-common-receipt-{field}.sqlite3")
    old_receipt, _ = _prepare_old_batch_with_newer_batch(
        store,
        newer_status="resolved",
        include_second_member=True,
    )
    with store._connect() as db:
        round_row = db.execute(
            f"select id from ({_feedback_owner_projection('feedback_processing_rounds')}) "
            "where feedback_key='feedback-2' and batch_id='batch-2'"
        ).fetchone()
        assert round_row is not None
        _update_feedback_owner(db, "feedback_processing_rounds", round_row[0],
                      {field: round_value})
    before = _feedback_processing_snapshot(store)

    with pytest.raises(ValueError, match="resolved batch newer lineage is incomplete"):
        store.resolve_feedback_processing_batch(
            "batch-1", old_receipt, commit_is_ancestor=True
        )

    assert _feedback_processing_snapshot(store) == before


@pytest.mark.parametrize("damage", ("legacy-version", "completed-backlog"))
def test_processing_newer_batch_requires_v2_round_with_empty_backlog(
    tmp_path: Path,
    damage: str,
):
    store = AutoReplyStore(tmp_path / f"newer-processing-contract-{damage}.sqlite3")
    old_receipt, _ = _prepare_old_batch_with_newer_batch(
        store,
        newer_status="processing",
        include_second_member=True,
    )
    with store._connect() as db:
        round_row = db.execute(
            f"select id from ({_feedback_owner_projection('feedback_processing_rounds')}) "
            "where feedback_key='feedback-2' and batch_id='batch-2'"
        ).fetchone()
        assert round_row is not None
        if damage == "legacy-version":
            _update_feedback_owner(db, "feedback_processing_rounds", round_row[0],
                          {"receipt_version": 1})
        else:
            _update_feedback_owner(db, "feedback_processing_rounds", round_row[0],
                          {"backlog_evidence_json":
                           '{"processing":0,"failed":0,"retryable":0}'})
    before = _feedback_processing_snapshot(store)

    with pytest.raises(ValueError, match="resolved batch newer lineage is incomplete"):
        store.resolve_feedback_processing_batch(
            "batch-1", old_receipt, commit_is_ancestor=True
        )

    assert _feedback_processing_snapshot(store) == before


@pytest.mark.parametrize("newer_status", ("processing", "resolved"))
def test_newer_batch_member_pointer_must_target_global_latest_round(
    tmp_path: Path,
    newer_status: str,
):
    store = AutoReplyStore(tmp_path / f"newer-global-latest-{newer_status}.sqlite3")
    old_receipt, _ = _prepare_old_batch_with_newer_batch(
        store,
        newer_status=newer_status,
        include_second_member=True,
    )
    with store._connect() as db:
        _insert_feedback_owner(db, "feedback_processing_rounds", {
            "feedback_key": "feedback-2", "round_number": 2,
            "batch_id": "batch-shadow", "status": newer_status,
            "receipt_version": 2,
            "started_at": db.execute("select current_timestamp").fetchone()[0],
        })
    before = _feedback_processing_snapshot(store)

    with pytest.raises(ValueError, match="resolved batch newer lineage is incomplete"):
        store.resolve_feedback_processing_batch(
            "batch-1", old_receipt, commit_is_ancestor=True
        )

    assert _feedback_processing_snapshot(store) == before


@pytest.mark.parametrize("newer_status", ("processing", "resolved"))
@pytest.mark.parametrize("damage", ("missing", "duplicate", "wrong-fields", "time"))
def test_v2_newer_round_requires_exact_claim_transition(
    tmp_path: Path,
    newer_status: str,
    damage: str,
):
    store = AutoReplyStore(
        tmp_path / f"newer-claim-transition-{newer_status}-{damage}.sqlite3"
    )
    old_receipt, _ = _prepare_old_batch_with_newer_batch(
        store,
        newer_status=newer_status,
        include_second_member=True,
    )
    with store._connect() as db:
        transition = db.execute(
            f"select * from ({feedback_projection('feedback_processing_transitions', with_rowid=True)}) "
            "where feedback_key='feedback-2' and batch_id='batch-2' "
            "and from_status='pending' and to_status='processing'"
        ).fetchone()
        assert transition is not None
        transition_rowid = int(transition["_source_rowid"])
        if damage == "missing":
            db.execute(
                "delete from actions where record_type='feedback_processing_transitions' "
                "and legacy_key=?", (str(transition_rowid),),
            )
        elif damage == "duplicate":
            insert_feedback_action(db, "feedback_processing_transitions", {
                field: transition[field] for field in (
                    "feedback_key", "round_id", "batch_id", "from_status",
                    "to_status", "reason", "workbench_task_id",
                    "workbench_turn_id", "created_at",
                )
            })
        elif damage == "wrong-fields":
            set_feedback_action(db, "feedback_processing_transitions",
                                transition_rowid, {"workbench_turn_id": "wrong-turn"})
        else:
            set_feedback_action(db, "feedback_processing_transitions",
                                transition_rowid,
                                {"created_at": "1999-01-01 00:00:00"})
    before = _feedback_processing_snapshot(store)

    with pytest.raises(ValueError, match="resolved batch newer lineage is incomplete"):
        store.resolve_feedback_processing_batch(
            "batch-1", old_receipt, commit_is_ancestor=True
        )

    assert _feedback_processing_snapshot(store) == before


def test_claim_uses_one_timestamp_for_rounds_and_claim_transitions(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "claim-shared-timestamp.sqlite3")
    for feedback_key in ("feedback-1", "feedback-2"):
        store.upsert_feedback_event(
            key=feedback_key,
            feedback_token=f"token-{feedback_key}",
        )

    original_open_connection = store._open_connection

    def delayed_open_connection() -> sqlite3.Connection:
        connection = original_open_connection()
        connection.create_function(
            "feedback_claim_delay",
            0,
            lambda: time.sleep(1.1),
        )
        return connection

    store._open_connection = delayed_open_connection  # type: ignore[method-assign]
    with store._connect() as db:
        db.execute(
            """
            create trigger delay_feedback_round_claim
            after insert on tasks
            when new.record_type='feedback_processing_rounds'
            begin
                select feedback_claim_delay();
            end
            """
        )

    store.claim_feedback_processing_items(
        "batch-1", ["feedback-1", "feedback-2"]
    )

    with store._connect() as db:
        timestamps = {
            str(row["timestamp"])
            for query in (
                f"select started_at as timestamp from ({_feedback_owner_projection('feedback_processing_rounds')}) where batch_id='batch-1'",
                f"select created_at as timestamp from ({feedback_projection('feedback_processing_transitions')}) where batch_id='batch-1' and from_status='pending' and to_status='processing'",
            )
            for row in db.execute(query)
        }
    assert len(timestamps) == 1


def test_resolved_v2_batch_rejects_empty_claim_timestamp_pair(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "resolved-empty-claim-time.sqlite3")
    receipt = _prepare_resolved_v2_batch(store)
    with store._connect() as db:
        round_rows = db.execute(
            f"select id from ({_feedback_owner_projection('feedback_processing_rounds')}) "
            "where batch_id='batch-1'"
        ).fetchall()
        for row in round_rows:
            _update_feedback_owner(db, "feedback_processing_rounds", row[0],
                          {"started_at": ""})
        claims = db.execute(
            f"select _source_rowid from ({feedback_projection('feedback_processing_transitions', with_rowid=True)}) "
            "where batch_id='batch-1' and from_status='pending' "
            "and to_status='processing'"
        ).fetchall()
        for row in claims:
            set_feedback_action(db, "feedback_processing_transitions", row[0],
                                {"created_at": ""})
    before = _feedback_processing_snapshot(store)

    with pytest.raises(ValueError):
        store.resolve_feedback_processing_batch(
            "batch-1", receipt, commit_is_ancestor=True
        )

    assert _feedback_processing_snapshot(store) == before


@pytest.mark.parametrize("newer_status", ("processing", "resolved"))
def test_v2_newer_batch_requires_one_shared_claim_timestamp(
    tmp_path: Path,
    newer_status: str,
):
    store = AutoReplyStore(tmp_path / f"newer-shared-claim-{newer_status}.sqlite3")
    old_receipt, _ = _prepare_old_batch_with_newer_batch(
        store,
        newer_status=newer_status,
        include_second_member=True,
    )
    with store._connect() as db:
        round_row = db.execute(
            f"select id from ({_feedback_owner_projection('feedback_processing_rounds')}) "
            "where feedback_key='feedback-2' and batch_id='batch-2'"
        ).fetchone()
        assert round_row is not None
        _update_feedback_owner(db, "feedback_processing_rounds", round_row[0],
                      {"started_at": "1999-01-01 00:00:00"})
        claim = db.execute(
            f"select _source_rowid from ({feedback_projection('feedback_processing_transitions', with_rowid=True)}) "
            "where feedback_key='feedback-2' and batch_id='batch-2' "
            "and from_status='pending' and to_status='processing'"
        ).fetchone()
        assert claim is not None
        set_feedback_action(db, "feedback_processing_transitions", claim[0],
                            {"created_at": "1999-01-01 00:00:00"})
    before = _feedback_processing_snapshot(store)

    with pytest.raises(ValueError, match="resolved batch newer lineage is incomplete"):
        store.resolve_feedback_processing_batch(
            "batch-1", old_receipt, commit_is_ancestor=True
        )

    assert _feedback_processing_snapshot(store) == before


def test_delayed_newer_batch_claim_remains_valid_for_old_batch_idempotency(
    tmp_path: Path,
):
    store = AutoReplyStore(tmp_path / "delayed-newer-claim-lineage.sqlite3")
    old_receipt = _prepare_resolved_v2_batch(store)
    store.reopen_feedback_processing_item("feedback-1", reason="needs another round")
    store.upsert_feedback_event(
        key="feedback-2",
        feedback_token="token-feedback-2",
    )
    original_open_connection = store._open_connection

    def delayed_open_connection() -> sqlite3.Connection:
        connection = original_open_connection()
        connection.create_function(
            "feedback_claim_lineage_delay",
            0,
            lambda: time.sleep(1.1),
        )
        return connection

    store._open_connection = delayed_open_connection  # type: ignore[method-assign]
    with store._connect() as db:
        db.execute(
            """
            create trigger delay_feedback_newer_round_claim
            after insert on tasks
            when new.record_type='feedback_processing_rounds'
            begin
                select feedback_claim_lineage_delay();
            end
            """
        )

    store.claim_feedback_processing_items(
        "batch-2", ["feedback-1", "feedback-2"]
    )

    assert store.resolve_feedback_processing_batch(
        "batch-1", old_receipt, commit_is_ancestor=True
    )


def _prepare_two_member_processing_batch(
    store: AutoReplyStore,
) -> ResolutionEvidence:
    receipt = _complete_resolution_receipt()
    for feedback_key in ("feedback-1", "feedback-2"):
        store.upsert_feedback_event(
            key=feedback_key,
            feedback_token=f"token-{feedback_key}",
        )
    store.claim_feedback_processing_items(
        "batch-1", ["feedback-1", "feedback-2"]
    )
    for index, feedback_key in enumerate(("feedback-1", "feedback-2"), start=1):
        store.associate_feedback_processing_turn(
            feedback_key,
            workbench_task_id="task-processing",
            workbench_turn_id="turn-processing",
            attempt_id=300 + index,
            agent_run_id=400 + index,
        )
        store.patch_feedback_processing_item_evidence(
            feedback_key,
            commit_sha=receipt.commit_sha,
            test_evidence=receipt.test_evidence,
            restart_evidence=receipt.restart_evidence,
            health_evidence=receipt.health_evidence,
            note="processing receipt",
        )
    return receipt


def _insert_orphan_feedback_batch_transition(
    store: AutoReplyStore,
    *,
    batch_id: str,
    damage: str,
    member_feedback_key: str = "feedback-1",
) -> None:
    if damage == "outside-batch-round":
        store.upsert_feedback_event(
            key="feedback-shadow", feedback_token="token-feedback-shadow"
        )
    with store._connect() as db:
        member_round = db.execute(
            f"select id from ({_feedback_owner_projection('feedback_processing_rounds')}) "
            "where batch_id=? and feedback_key=?",
            (batch_id, member_feedback_key),
        ).fetchone()
        assert member_round is not None
        if damage == "nonexistent-round":
            insert_feedback_action(db, "feedback_processing_transitions", {
                "feedback_key": "feedback-1", "round_id": 999999999,
                "batch_id": batch_id, "from_status": "pending",
                "to_status": "processing",
            })
            return
        if damage == "wrong-feedback-key":
            insert_feedback_action(db, "feedback_processing_transitions", {
                "feedback_key": "feedback-orphan",
                "round_id": int(member_round["id"]), "batch_id": batch_id,
                "from_status": "pending", "to_status": "processing",
            })
            return
        assert damage == "outside-batch-round"
        outside_round = _insert_feedback_owner(db, "feedback_processing_rounds", {
            "feedback_key": "feedback-shadow", "round_number": 1,
            "batch_id": "batch-shadow", "status": "processing",
            "receipt_version": 2,
            "started_at": db.execute("select current_timestamp").fetchone()[0],
        }).lastrowid
        assert outside_round is not None
        claim = db.execute(
            f"select * from ({feedback_projection('feedback_processing_transitions')}) "
            "where round_id=? and batch_id=? and from_status='pending' "
            "and to_status='processing'",
            (int(member_round["id"]), batch_id),
        ).fetchone()
        assert claim is not None
        insert_feedback_action(db, "feedback_processing_transitions", {
            "feedback_key": "feedback-shadow", "round_id": outside_round,
            "batch_id": batch_id, "from_status": claim["from_status"],
            "to_status": claim["to_status"], "reason": claim["reason"],
            "workbench_task_id": claim["workbench_task_id"],
            "workbench_turn_id": claim["workbench_turn_id"],
            "created_at": claim["created_at"],
        })


def _prepare_resolved_two_member_batch(
    store: AutoReplyStore,
) -> ResolutionEvidence:
    receipt = _prepare_two_member_processing_batch(store)
    assert store.resolve_feedback_processing_batch(
        "batch-1",
        receipt,
        commit_is_ancestor=True,
    )
    return receipt


@pytest.mark.parametrize(
    "damage",
    ("nonexistent-round", "wrong-feedback-key", "outside-batch-round"),
)
def test_resolved_batch_retry_rejects_orphan_batch_transitions(
    tmp_path: Path,
    damage: str,
):
    store = AutoReplyStore(tmp_path / f"resolved-orphan-{damage}.sqlite3")
    receipt = _prepare_resolved_two_member_batch(store)
    _insert_orphan_feedback_batch_transition(
        store,
        batch_id="batch-1",
        damage=damage,
    )
    before = _feedback_processing_snapshot(store)

    with pytest.raises(ValueError):
        store.resolve_feedback_processing_batch(
            "batch-1",
            receipt,
            commit_is_ancestor=True,
        )

    assert _feedback_processing_snapshot(store) == before


@pytest.mark.parametrize(
    "damage",
    ("nonexistent-round", "wrong-feedback-key", "outside-batch-round"),
)
def test_reopen_rejects_orphan_transitions_anywhere_in_resolved_batch(
    tmp_path: Path,
    damage: str,
):
    store = AutoReplyStore(tmp_path / f"reopen-orphan-{damage}.sqlite3")
    _prepare_resolved_two_member_batch(store)
    _insert_orphan_feedback_batch_transition(
        store,
        batch_id="batch-1",
        damage=damage,
        member_feedback_key="feedback-2",
    )
    before = _feedback_processing_snapshot(store)

    with pytest.raises(
        feedback_processing_module.FeedbackProcessingReopenError
    ) as error:
        store.reopen_feedback_processing_item(
            "feedback-1",
            reason="ownership history must be complete",
        )

    assert error.value.error_code == "feedback_reopen_history_incomplete"
    assert _feedback_processing_snapshot(store) == before


def test_multi_item_resolved_batch_can_reopen_one_member(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "multi-item-reopen.sqlite3")
    _prepare_resolved_two_member_batch(store)
    second_round_before = store.list_feedback_processing_rounds("feedback-2")

    reopened = store.reopen_feedback_processing_item(
        "feedback-1",
        reason="only the first member needs another round",
    )

    assert reopened is not None and reopened.status == "pending"
    assert store.get_feedback_processing_item("feedback-2").status == "resolved"
    assert store.get_feedback_processing_batch("batch-1").status == "resolved"
    assert store.get_feedback_event("feedback-1").resolved_at == ""
    assert store.get_feedback_event("feedback-2").resolved_at
    assert store.list_feedback_processing_rounds("feedback-2") == second_round_before


def _corrupt_resolved_batch_sibling(
    store: AutoReplyStore,
    *,
    damage: str,
) -> None:
    with store._connect() as db:
        sibling_round = db.execute(
            f"select id from ({_feedback_owner_projection('feedback_processing_rounds')}) "
            "where batch_id='batch-1' and feedback_key='feedback-2'"
        ).fetchone()
        assert sibling_round is not None
        completion = db.execute(
            f"select _source_rowid from ({feedback_projection('feedback_processing_transitions', with_rowid=True)}) "
            "where round_id=? and from_status='processing' and to_status='resolved'",
            (int(sibling_round["id"]),),
        ).fetchone()
        assert completion is not None
        if damage == "missing-completion":
            db.execute(
                "delete from actions where record_type='feedback_processing_transitions' "
                "and legacy_key=?", (str(completion[0]),),
            )
        elif damage == "wrong-completion":
            set_feedback_action(db, "feedback_processing_transitions",
                                completion[0], {"reason": "wrong"})
        elif damage == "source-timestamp":
            event = db.execute(
                f"select _source_rowid from ({feedback_projection('feedback_events', with_rowid=True)}) "
                "where key='feedback-2'"
            ).fetchone()
            assert event is not None
            set_feedback_action(db, "feedback_events", event[0],
                                {"resolved_at": "1999-01-01 00:00:00"})
        elif damage == "requested-count":
            _update_feedback_owner(db, "feedback_processing_batches",
                          _feedback_batch_source_rowid(db, "batch-1"),
                          {"requested_count": 3})
        else:
            assert damage == "receipt-mismatch"
            different_commit = "d" * 40
            _update_feedback_owner(db, "feedback_processing_rounds",
                          int(sibling_round["id"]),
                          {"commit_sha": different_commit})


@pytest.mark.parametrize(
    "damage",
    (
        "missing-completion",
        "wrong-completion",
        "source-timestamp",
        "requested-count",
        "receipt-mismatch",
    ),
)
def test_reopen_requires_complete_resolved_sibling_batch_closure(
    tmp_path: Path,
    damage: str,
):
    store = AutoReplyStore(tmp_path / f"reopen-sibling-{damage}.sqlite3")
    _prepare_resolved_two_member_batch(store)
    _corrupt_resolved_batch_sibling(store, damage=damage)
    before = _feedback_processing_snapshot(store)

    with pytest.raises(
        feedback_processing_module.FeedbackProcessingReopenError
    ) as error:
        store.reopen_feedback_processing_item(
            "feedback-1",
            reason="the full sibling batch must be intact",
        )

    assert error.value.error_code == "feedback_reopen_history_incomplete"
    assert _feedback_processing_snapshot(store) == before


@pytest.mark.parametrize(
    "receipt_field",
    ("commit", "test", "restart", "health", "backlog"),
)
def test_resolved_batch_no_evidence_retry_requires_common_persisted_receipt(
    tmp_path: Path,
    receipt_field: str,
):
    store = AutoReplyStore(
        tmp_path / f"no-evidence-common-receipt-{receipt_field}.sqlite3"
    )
    _prepare_resolved_two_member_batch(store)
    with store._connect() as db:
        if receipt_field == "commit":
            different_value: object = "d" * 40
            column = "commit_sha"
        else:
            different_receipts: dict[str, dict[str, object]] = {
                "test": {"pytest": {"exit_code": 0}, "variant": "different"},
                "restart": {
                    "launchd_label": "com.ceo-agent-service.main",
                    "before_pid": 10,
                    "after_pid": 11,
                    "variant": "different",
                },
                "health": {
                    "url": "http://127.0.0.1:8765/healthz",
                    "status_code": 200,
                    "ok": True,
                    "variant": "different",
                },
                "backlog": {
                    "processing": 0,
                    "failed": 0,
                    "retryable": 0,
                    "variant": "different",
                },
            }
            different_value = json.dumps(different_receipts[receipt_field])
            column = f"{receipt_field}_evidence_json"
        round_row = db.execute(
            f"select id from ({_feedback_owner_projection('feedback_processing_rounds')}) "
            "where feedback_key='feedback-2'"
        ).fetchone()
        assert round_row is not None
        _update_feedback_owner(db, "feedback_processing_rounds", round_row[0],
                      {column: different_value})
    before = _feedback_processing_snapshot(store)

    with pytest.raises(ValueError):
        store.resolve_feedback_processing_batch(
            "batch-1",
            commit_is_ancestor=True,
        )

    assert _feedback_processing_snapshot(store) == before


def test_resolved_batch_transition_validation_uses_two_bounded_queries(
    tmp_path: Path,
):
    store = AutoReplyStore(tmp_path / "bounded-transition-queries.sqlite3")
    _prepare_resolved_two_member_batch(store)
    statements: list[str] = []

    with store._connect() as db:
        batch = db.execute(
            f"select requested_count, resolved_at, updated_at "
            f"from ({_feedback_owner_projection('feedback_processing_batches')}) where batch_id='batch-1'"
        ).fetchone()
        assert batch is not None
        db.set_trace_callback(statements.append)
        try:
            AutoReplyStore._validate_resolved_feedback_processing_batch(
                db,
                batch_id="batch-1",
                requested_count=batch["requested_count"],
                batch_resolved_at=str(batch["resolved_at"]),
                batch_updated_at=str(batch["updated_at"]),
                evidence=None,
            )
        finally:
            db.set_trace_callback(None)

    transition_queries = [
        statement.lower()
        for statement in statements
        if "a.record_type='feedback_processing_transitions'" in statement.lower()
    ]
    assert len(transition_queries) == 2
    assert any("where batch_id=" in statement for statement in transition_queries)
    assert any("where round_id in (" in statement for statement in transition_queries)


@pytest.mark.parametrize("reopened_count", (1, 2))
def test_reopened_resolved_batch_reuses_two_grouped_transition_queries(
    tmp_path: Path,
    reopened_count: int,
):
    store = AutoReplyStore(tmp_path / f"reopened-query-count-{reopened_count}.sqlite3")
    _prepare_resolved_two_member_batch(store)
    for feedback_key in ("feedback-1", "feedback-2")[:reopened_count]:
        store.reopen_feedback_processing_item(
            feedback_key,
            reason=f"reopen {feedback_key}",
        )
    statements: list[str] = []

    with store._connect() as db:
        batch = db.execute(
            f"select requested_count, resolved_at, updated_at "
            f"from ({_feedback_owner_projection('feedback_processing_batches')}) where batch_id='batch-1'"
        ).fetchone()
        assert batch is not None
        db.set_trace_callback(statements.append)
        try:
            AutoReplyStore._validate_resolved_feedback_processing_batch(
                db,
                batch_id="batch-1",
                requested_count=batch["requested_count"],
                batch_resolved_at=str(batch["resolved_at"]),
                batch_updated_at=str(batch["updated_at"]),
                evidence=None,
            )
        finally:
            db.set_trace_callback(None)

    transition_queries = [
        statement.lower()
        for statement in statements
        if "a.record_type='feedback_processing_transitions'" in statement.lower()
    ]
    assert len(transition_queries) == 2
    assert any("where batch_id=" in statement for statement in transition_queries)
    assert any("where round_id in (" in statement for statement in transition_queries)


@pytest.mark.parametrize(
    "damage",
    (
        "missing",
        "duplicate",
        "reason",
        "workbench-task",
        "workbench-turn",
        "timestamp",
    ),
)
def test_grouped_reopen_transition_validation_remains_exact(
    tmp_path: Path,
    damage: str,
):
    store = AutoReplyStore(tmp_path / f"grouped-reopen-{damage}.sqlite3")
    receipt = _prepare_resolved_two_member_batch(store)
    store.reopen_feedback_processing_item(
        "feedback-1",
        reason="exact reopen reason",
    )
    with store._connect() as db:
        transition = db.execute(
            f"select * from ({feedback_projection('feedback_processing_transitions', with_rowid=True)}) "
            "where feedback_key='feedback-1' and batch_id='batch-1' "
            "and from_status='resolved' and to_status='pending'"
        ).fetchone()
        assert transition is not None
        transition_rowid = int(transition["_source_rowid"])
        if damage == "missing":
            db.execute(
                "delete from actions where record_type='feedback_processing_transitions' "
                "and legacy_key=?", (str(transition_rowid),),
            )
        elif damage == "duplicate":
            insert_feedback_action(db, "feedback_processing_transitions", {
                field: transition[field] for field in (
                    "feedback_key", "round_id", "batch_id", "from_status",
                    "to_status", "reason", "workbench_task_id",
                    "workbench_turn_id", "created_at",
                )
            })
        else:
            column, value = {
                "reason": ("reason", "different reason"),
                "workbench-task": ("workbench_task_id", "different-task"),
                "workbench-turn": ("workbench_turn_id", "different-turn"),
                "timestamp": ("created_at", "1999-01-01 00:00:00"),
            }[damage]
            set_feedback_action(db, "feedback_processing_transitions",
                                transition_rowid, {column: value})
    before = _feedback_processing_snapshot(store)

    with pytest.raises(ValueError):
        store.resolve_feedback_processing_batch(
            "batch-1",
            receipt,
            commit_is_ancestor=True,
        )

    assert _feedback_processing_snapshot(store) == before


def _prepare_old_members_in_same_newer_resolved_batch(
    store: AutoReplyStore,
    *,
    member_count: int,
) -> None:
    feedback_keys = [f"feedback-{index}" for index in range(1, member_count + 1)]
    old_receipt = _complete_resolution_receipt("b" * 40)
    for feedback_key in feedback_keys:
        store.upsert_feedback_event(
            key=feedback_key,
            feedback_token=f"token-{feedback_key}",
        )
    store.claim_feedback_processing_items("batch-1", feedback_keys)
    for index, feedback_key in enumerate(feedback_keys, start=1):
        store.associate_feedback_processing_turn(
            feedback_key,
            workbench_task_id="task-old",
            workbench_turn_id="turn-old",
            attempt_id=500 + index,
            agent_run_id=600 + index,
        )
        store.patch_feedback_processing_item_evidence(
            feedback_key,
            commit_sha=old_receipt.commit_sha,
            test_evidence=old_receipt.test_evidence,
            restart_evidence=old_receipt.restart_evidence,
            health_evidence=old_receipt.health_evidence,
            note="old receipt",
        )
    assert store.resolve_feedback_processing_batch(
        "batch-1",
        old_receipt,
        commit_is_ancestor=True,
    )
    for feedback_key in feedback_keys:
        store.reopen_feedback_processing_item(
            feedback_key,
            reason=f"reopen {feedback_key} into newer batch",
        )

    newer_receipt = _complete_resolution_receipt("c" * 40)
    store.claim_feedback_processing_items("batch-2", feedback_keys)
    for index, feedback_key in enumerate(feedback_keys, start=1):
        store.associate_feedback_processing_turn(
            feedback_key,
            workbench_task_id="task-newer",
            workbench_turn_id="turn-newer",
            attempt_id=700 + index,
            agent_run_id=800 + index,
        )
        store.patch_feedback_processing_item_evidence(
            feedback_key,
            commit_sha=newer_receipt.commit_sha,
            test_evidence=newer_receipt.test_evidence,
            restart_evidence=newer_receipt.restart_evidence,
            health_evidence=newer_receipt.health_evidence,
            note="newer receipt",
        )
    assert store.resolve_feedback_processing_batch(
        "batch-2",
        newer_receipt,
        commit_is_ancestor=True,
    )


@pytest.mark.parametrize("member_count", (1, 2))
def test_old_batch_reuses_grouped_newer_resolved_transition_validation(
    tmp_path: Path,
    member_count: int,
):
    store = AutoReplyStore(tmp_path / f"newer-resolved-query-{member_count}.sqlite3")
    _prepare_old_members_in_same_newer_resolved_batch(
        store,
        member_count=member_count,
    )
    statements: list[str] = []

    with store._connect() as db:
        batch = db.execute(
            f"select requested_count, resolved_at, updated_at "
            f"from ({_feedback_owner_projection('feedback_processing_batches')}) where batch_id='batch-1'"
        ).fetchone()
        assert batch is not None
        db.set_trace_callback(statements.append)
        try:
            AutoReplyStore._validate_resolved_feedback_processing_batch(
                db,
                batch_id="batch-1",
                requested_count=batch["requested_count"],
                batch_resolved_at=str(batch["resolved_at"]),
                batch_updated_at=str(batch["updated_at"]),
                evidence=None,
            )
        finally:
            db.set_trace_callback(None)

    transition_queries = [
        statement.lower()
        for statement in statements
        if "a.record_type='feedback_processing_transitions'" in statement.lower()
    ]
    assert len(transition_queries) == 4
    assert sum("where batch_id=" in query for query in transition_queries) == 2
    assert sum("where round_id in (" in query for query in transition_queries) == 2


@pytest.mark.parametrize("operation", ("first-resolve", "same-batch-retry"))
@pytest.mark.parametrize(
    "damage",
    ("nonexistent-round", "wrong-feedback-key", "outside-batch-round"),
)
def test_processing_batch_entrypoints_reject_orphan_batch_transitions(
    tmp_path: Path,
    operation: str,
    damage: str,
):
    store = AutoReplyStore(tmp_path / f"orphan-{operation}-{damage}.sqlite3")
    receipt = _prepare_two_member_processing_batch(store)
    _insert_orphan_feedback_batch_transition(
        store,
        batch_id="batch-1",
        damage=damage,
    )
    before = _feedback_processing_snapshot(store)

    with pytest.raises(ValueError):
        if operation == "first-resolve":
            store.resolve_feedback_processing_batch(
                "batch-1", receipt, commit_is_ancestor=True
            )
        else:
            store.claim_feedback_processing_items(
                "batch-1", ["feedback-1", "feedback-2"]
            )

    assert _feedback_processing_snapshot(store) == before


@pytest.mark.parametrize(
    "damage",
    ("nonexistent-round", "wrong-feedback-key", "outside-batch-round"),
)
def test_old_batch_retry_rejects_orphan_newer_batch_transitions(
    tmp_path: Path,
    damage: str,
):
    store = AutoReplyStore(tmp_path / f"newer-orphan-{damage}.sqlite3")
    old_receipt, _ = _prepare_old_batch_with_newer_batch(
        store,
        newer_status="processing",
        include_second_member=True,
    )
    _insert_orphan_feedback_batch_transition(
        store,
        batch_id="batch-2",
        damage=damage,
    )
    before = _feedback_processing_snapshot(store)

    with pytest.raises(ValueError, match="resolved batch newer lineage is incomplete"):
        store.resolve_feedback_processing_batch(
            "batch-1", old_receipt, commit_is_ancestor=True
        )

    assert _feedback_processing_snapshot(store) == before


@pytest.mark.parametrize("operation", ("first-resolve", "same-batch-retry"))
@pytest.mark.parametrize(
    "damage",
    (
        "missing-claim",
        "duplicate-claim",
        "wrong-claim",
        "illegal-transition",
        "nonempty-backlog",
        "stale-latest",
        "split-claim-time",
        "empty-claim-time",
        "missing-item",
        "missing-source",
    ),
)
def test_processing_batch_entrypoints_require_centralized_lineage(
    tmp_path: Path,
    operation: str,
    damage: str,
):
    store = AutoReplyStore(tmp_path / f"entry-{operation}-{damage}.sqlite3")
    receipt = _prepare_two_member_processing_batch(store)
    with store._connect() as db:
        claim = db.execute(
            f"select * from ({feedback_projection('feedback_processing_transitions', with_rowid=True)}) "
            "where feedback_key='feedback-2' and batch_id='batch-1' "
            "and from_status='pending' and to_status='processing'"
        ).fetchone()
        assert claim is not None
        claim_rowid = int(claim["_source_rowid"])
        if damage == "missing-claim":
            db.execute(
                "delete from actions where record_type='feedback_processing_transitions' "
                "and legacy_key=?", (str(claim_rowid),),
            )
        elif damage == "duplicate-claim":
            insert_feedback_action(db, "feedback_processing_transitions", {
                field: claim[field] for field in (
                    "feedback_key", "round_id", "batch_id", "from_status",
                    "to_status", "reason", "workbench_task_id",
                    "workbench_turn_id", "created_at",
                )
            })
        elif damage == "wrong-claim":
            set_feedback_action(db, "feedback_processing_transitions",
                                claim_rowid, {"reason": "wrong"})
        elif damage == "illegal-transition":
            insert_feedback_action(db, "feedback_processing_transitions", {
                "feedback_key": "feedback-2", "round_id": claim["round_id"],
                "batch_id": "batch-1", "from_status": "processing",
                "to_status": "pending",
            })
        elif damage == "nonempty-backlog":
            _update_feedback_owner(db, "feedback_processing_rounds", claim["round_id"],
                          {"backlog_evidence_json": json.dumps({"processing": 0})})
        elif damage == "stale-latest":
            _insert_feedback_owner(db, "feedback_processing_rounds", {
                "feedback_key": "feedback-2", "round_number": 2,
                "batch_id": "batch-shadow", "status": "processing",
                "receipt_version": 2,
                "started_at": db.execute("select current_timestamp").fetchone()[0],
            })
        elif damage in {"split-claim-time", "empty-claim-time"}:
            timestamp = "" if damage == "empty-claim-time" else "1999-01-01 00:00:00"
            _update_feedback_owner(db, "feedback_processing_rounds", claim["round_id"],
                          {"started_at": timestamp})
            set_feedback_action(db, "feedback_processing_transitions",
                                claim_rowid, {"created_at": timestamp})
        elif damage == "missing-item":
            _delete_feedback_owner(db, "feedback_processing_items",
                          _feedback_item_source_rowid(db, "feedback-2"))
        else:
            db.execute(
                "delete from actions where record_type='feedback_events' "
                "and fb_event_key='feedback-2'"
            )
    before = _feedback_processing_snapshot(store)

    with pytest.raises(ValueError):
        if operation == "first-resolve":
            store.resolve_feedback_processing_batch(
                "batch-1", receipt, commit_is_ancestor=True
            )
        else:
            store.claim_feedback_processing_items(
                "batch-1", ["feedback-1", "feedback-2"]
            )

    assert _feedback_processing_snapshot(store) == before


@_native_predecessor_case
def test_migrated_v1_processing_round_resolves_idempotently_and_reopens(
    tmp_path: Path,
):
    db_path = tmp_path / "migrated-v1-processing.sqlite3"
    store = AutoReplyStore(db_path)
    store.upsert_feedback_event(
        key="feedback-1",
        feedback_token="token-feedback-1",
    )
    receipt = _complete_resolution_receipt()
    with store._connect() as db:
        db.execute(
            "insert into feedback_processing_batches "
            "(batch_id, status, requested_count) "
            "values ('batch-1', 'processing', 1)"
        )
        db.execute(
            """
            update feedback_processing_items
               set current_round_id=0, batch_id='batch-1', status='processing',
                   workbench_task_id='task-legacy',
                   workbench_turn_id='turn-legacy', attempt_id=501,
                   agent_run_id=502, commit_sha=?, test_evidence_json=?,
                   restart_evidence_json=?, health_evidence_json=?,
                   note='legacy processing'
             where feedback_key='feedback-1'
            """,
            (
                receipt.commit_sha,
                json.dumps(receipt.test_evidence),
                json.dumps(receipt.restart_evidence),
                json.dumps(receipt.health_evidence),
            ),
        )
        db.execute("drop table feedback_processing_rounds")
        db.execute("drop table feedback_processing_transitions")

    store_module._INITIALIZED_STORE_PATHS.discard(db_path.resolve())
    migrated = AutoReplyStore(db_path)
    migrated_round = migrated.list_feedback_processing_rounds("feedback-1")[0]
    assert migrated_round.receipt_version == 1
    assert migrated.list_feedback_processing_transitions("feedback-1") == []

    migrated.patch_feedback_processing_item_evidence(
        "feedback-1",
        commit_sha=receipt.commit_sha,
        test_evidence=receipt.test_evidence,
        restart_evidence=receipt.restart_evidence,
        health_evidence=receipt.health_evidence,
        note="legacy processing",
    )
    assert migrated.resolve_feedback_processing_batch(
        "batch-1", receipt, commit_is_ancestor=True
    )
    resolved_round = migrated.list_feedback_processing_rounds("feedback-1")[0]
    assert resolved_round.receipt_version == 1
    assert resolved_round.backlog_evidence == receipt.backlog_evidence
    assert [
        (transition.from_status, transition.to_status)
        for transition in migrated.list_feedback_processing_transitions("feedback-1")
    ] == [("processing", "resolved")]
    assert migrated.resolve_feedback_processing_batch(
        "batch-1", receipt, commit_is_ancestor=True
    )
    assert migrated.reopen_feedback_processing_item(
        "feedback-1", reason="legacy receipt needs another round"
    ).status == "pending"
    assert [
        (transition.from_status, transition.to_status)
        for transition in migrated.list_feedback_processing_transitions("feedback-1")
    ] == [("resolved", "pending"), ("processing", "resolved")]


def test_resolved_transition_multiset_rejects_alternate_reopen_reason(
    tmp_path: Path,
):
    store = AutoReplyStore(tmp_path / "alternate-reopen-transition.sqlite3")
    receipt = _prepare_resolved_v2_batch(store)
    store.reopen_feedback_processing_item("feedback-1", reason="real reason")
    with store._connect() as db:
        round_row = db.execute(
            f"select * from ({_feedback_owner_projection('feedback_processing_rounds')}) "
            "where batch_id='batch-1'"
        ).fetchone()
        assert round_row is not None
        insert_feedback_action(db, "feedback_processing_transitions", {
            "feedback_key": "feedback-1", "round_id": int(round_row["id"]),
            "batch_id": "batch-1", "from_status": "resolved",
            "to_status": "pending", "reason": "alternate reason",
            "workbench_task_id": str(round_row["workbench_task_id"]),
            "workbench_turn_id": str(round_row["workbench_turn_id"]),
            "created_at": str(round_row["reopened_at"]),
        })
    before = _feedback_processing_snapshot(store)

    with pytest.raises(ValueError):
        store.resolve_feedback_processing_batch(
            "batch-1", receipt, commit_is_ancestor=True
        )

    assert _feedback_processing_snapshot(store) == before


def test_feedback_item_physical_schema_has_only_identity_and_current_pointer():
    from app.task_run_entity_definitions import _task_run_entity_objects_base

    ddl = _task_run_entity_objects_base()["feedback_processing_items"]
    db = sqlite3.connect(":memory:")
    db.execute(ddl)
    assert [row[1] for row in db.execute("pragma table_info(feedback_processing_items)")] == [
        "task_id", "feedback_key", "batch_id", "current_round_id"
    ]


def test_resolved_source_without_round_is_projected_from_exact_event(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "source-without-round.sqlite3")
    store.upsert_feedback_event(key="one", feedback_token="token-one")
    store.upsert_feedback_event(key="two", feedback_token="token-two")
    with store._connect() as db:
        event = db.execute(
            f"select _source_rowid from ({feedback_projection('feedback_events', with_rowid=True)}) where key=?",
            ("one",),
        ).fetchone()
        set_feedback_action(db, "feedback_events", event[0], {"resolved_at": "2026-01-02 03:04:05"})
    store.upsert_feedback_event(key="one", feedback_token="token-one")
    one = store.get_feedback_processing_item("one")
    two = store.get_feedback_processing_item("two")
    assert one.status == "resolved" and one.current_round_id == 0
    assert one.resolved_at == "2026-01-02 03:04:05"
    assert one.workbench_task_id == "" and one.test_evidence == {}
    assert two.resolved_at == "" and two.status == "pending"


@pytest.mark.parametrize("operation", ["associate", "patch"])
def test_round_write_rejects_pointer_changed_by_trigger(tmp_path: Path, operation: str):
    store = AutoReplyStore(tmp_path / f"round-pointer-{operation}.sqlite3")
    store.upsert_feedback_event(key="feedback-1", feedback_token="token-1")
    store.claim_feedback_processing_items("batch-1", ["feedback-1"])
    before = store.get_feedback_processing_item("feedback-1")
    with store._connect() as db:
        db.execute("""create trigger change_feedback_pointer after update on feedback_processing_rounds
                   begin update feedback_processing_items set current_round_id=0
                   where feedback_key=new.feedback_key; end""")
    with pytest.raises(ValueError, match="exact current processing round"):
        if operation == "associate":
            store.associate_feedback_processing_turn(
                "feedback-1", workbench_task_id="task", workbench_turn_id="turn",
                attempt_id=1, agent_run_id=2,
            )
        else:
            store.patch_feedback_processing_item_evidence("feedback-1", note="changed")
    assert store.get_feedback_processing_item("feedback-1") == before
