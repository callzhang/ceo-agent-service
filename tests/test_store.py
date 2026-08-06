import json
import importlib.util
from datetime import datetime, timezone
from multiprocessing import get_context
from pathlib import Path
from queue import Queue
import sqlite3
from threading import Barrier, Event, Thread
import time

import pytest

import app.store as store_module
from app.store import AgentRole, AgentRunLeaseLostError, AutoReplyStore


def _claim_audit_run(
    store: AutoReplyStore,
    reply_task_id: int,
    execution_generation: str,
    **kwargs,
):
    return store.claim_agent_run(
        reply_task_id,
        execution_generation,
        role=AgentRole.AUDIT,
        proposal_revision=0,
        turn_attempt=0,
        parent_agent_run_id=None,
        operation_id=f"direct-agent:{reply_task_id}:{execution_generation}",
        **kwargs,
    )


def _get_audit_run(
    store: AutoReplyStore,
    reply_task_id: int,
    execution_generation: str,
):
    return store.get_agent_run_for_turn(
        reply_task_id,
        execution_generation,
        role=AgentRole.AUDIT,
        proposal_revision=0,
        turn_attempt=0,
    )


def _enqueue_manual_rerun_in_process(
    db_path: str,
    attempt_id: int,
    barrier,
    results,
) -> None:
    store = AutoReplyStore(Path(db_path))
    barrier.wait(timeout=10)
    task = store.enqueue_manual_rerun_reply_task(
        conversation_id="cid-process-rerun",
        conversation_title="Process rerun",
        single_chat=False,
        trigger_message_id="msg-process-rerun",
        trigger_create_time="2026-07-29 11:00:00",
        trigger_sender="ET",
        trigger_text="请重新处理",
        trigger_message_json="{}",
        attempt_id=attempt_id,
    )
    results.put((task.id, task.execution_generation))


def _enqueue_universal_reply_task(
    store: AutoReplyStore,
    *,
    execution_generation: str = "initial",
) -> int:
    inserted = store.enqueue_reply_task(
        conversation_id="cid-universal",
        conversation_title="Universal",
        single_chat=False,
        trigger_message_id="msg-universal",
        trigger_create_time="2026-07-20 10:00:00",
        trigger_sender="Derek",
        trigger_text="Handle this task",
        execution_generation=execution_generation,
    )
    assert inserted is True
    return store.claim_reply_tasks(limit=1)[0].id


def test_store_indexes_and_searches_codex_sessions_with_fts_and_embeddings(
    tmp_path: Path,
):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")

    store.upsert_codex_session_search_index(
        session_id="session-risk-budget",
        source_type="meeting_alignment",
        source_id="10",
        title="上线评审",
        summary_text="话题：上线范围 风险预算。Derek 认为先定义可接受故障面。",
        fts_text="上线 上线范围 风险 风险预算 故障 故障面",
        embedding=[1.0, 0.0],
    )
    store.upsert_codex_session_search_index(
        session_id="session-customer-script",
        source_type="meeting_alignment",
        source_id="11",
        title="客服话术",
        summary_text="话题：客服解释口径。",
        fts_text="客服 话术 解释 口径",
        embedding=[0.0, 1.0],
    )

    results = store.search_codex_sessions(
        fts_query="上线 风险",
        query_embedding=[1.0, 0.0],
        limit=2,
    )

    assert [result.session_id for result in results] == [
        "session-risk-budget",
        "session-customer-script",
    ]
    assert results[0].embedding_score > results[1].embedding_score
    assert results[0].bm25_score is not None


def test_store_connections_enable_sqlite_concurrency_pragmas(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")

    with store._connect() as db:
        journal_mode = db.execute("pragma journal_mode").fetchone()[0]
        busy_timeout = db.execute("pragma busy_timeout").fetchone()[0]
        synchronous = db.execute("pragma synchronous").fetchone()[0]
        foreign_keys = db.execute("pragma foreign_keys").fetchone()[0]

    assert journal_mode == "wal"
    assert busy_timeout >= 30_000
    assert synchronous == 1
    assert foreign_keys == 1


def test_store_connections_can_use_short_busy_timeout(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3", busy_timeout_seconds=2)

    with store._connect() as db:
        busy_timeout = db.execute("pragma busy_timeout").fetchone()[0]

    assert busy_timeout == 2_000


def test_store_connections_close_after_context_exit(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")

    with store._connect() as db:
        db.execute("select 1").fetchone()

    with pytest.raises(sqlite3.ProgrammingError):
        db.execute("select 1").fetchone()


def test_store_initializes_same_path_once_per_process(tmp_path: Path, monkeypatch):
    calls: list[Path] = []
    original_initialize = AutoReplyStore._initialize

    def counted_initialize(self: AutoReplyStore) -> None:
        calls.append(self.path)
        original_initialize(self)

    monkeypatch.setattr(AutoReplyStore, "_initialize", counted_initialize)
    db_path = tmp_path / "worker.sqlite3"

    AutoReplyStore(db_path)
    AutoReplyStore(db_path)

    assert calls == [db_path]


def test_store_migrates_existing_follow_up_drafts_without_nonconstant_defaults(
    tmp_path: Path,
):
    db_path = tmp_path / "worker.sqlite3"
    db = sqlite3.connect(db_path)
    try:
        db.execute(
            """
            create table follow_up_drafts (
                id integer primary key autoincrement,
                project_id integer not null,
                todo_id integer not null default 0,
                owner_user_id text not null default '',
                owner_name text not null default '',
                target_conversation_id text not null default '',
                target_kind text not null default '',
                question_text text not null default '',
                risk_check_json text not null default '{}',
                status text not null default 'draft',
                send_result_json text not null default '{}',
                scheduled_at text not null default '',
                sent_at text not null default '',
                created_at text not null default current_timestamp
            )
            """
        )
        db.execute(
            """
            insert into follow_up_drafts (
                project_id, todo_id, owner_user_id, owner_name,
                target_conversation_id, target_kind, question_text,
                risk_check_json, status, send_result_json, scheduled_at, sent_at
            ) values (
                1, 1, 'owner-1', 'Alex',
                'cid-1', 'group', '请同步进展。',
                '{}', 'draft', '{}', '2026-06-26 09:00:00', ''
            )
            """
        )
        db.commit()
    finally:
        db.close()

    store = AutoReplyStore(db_path)

    with store._connect() as migrated:
        columns = {
            row["name"]
            for row in migrated.execute(
                "pragma table_info(follow_up_drafts)"
            ).fetchall()
        }
    assert "updated_at" in columns
    assert "evidence_check_json" in columns
    assert "title" in columns
    assert "description" in columns
    assert "owners_json" in columns
    assert "tags_json" in columns
    assert "participants_json" in columns
    assert "files_json" in columns


def test_store_writer_can_commit_while_reader_transaction_is_open(tmp_path: Path):
    db_path = tmp_path / "worker.sqlite3"
    store = AutoReplyStore(db_path)
    reader = sqlite3.connect(db_path)

    try:
        reader.execute("begin")
        reader.execute("select count(*) from errors").fetchone()

        store.record_error("cid-1", "msg-1", "producer", "database is locked")
    finally:
        reader.rollback()
        reader.close()

    assert store.list_errors(limit=1)[0].kind == "producer"


def test_conversation_session_persists(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    store.upsert_conversation(
        conversation_id="cid-1",
        title="Friday",
        single_chat=False,
        codex_session_id="session-1",
    )

    loaded = AutoReplyStore(tmp_path / "worker.sqlite3")

    assert loaded.get_codex_session_id("cid-1") == "session-1"


def test_codex_session_lock_is_exclusive(tmp_path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")

    assert store.acquire_codex_session_lock("cid-1", "okr:1") is True
    assert store.acquire_codex_session_lock("cid-1", "reply:msg-1") is False

    store.release_codex_session_lock("cid-1", "okr:1")
    assert store.acquire_codex_session_lock("cid-1", "reply:msg-1") is True


def test_codex_session_lock_replaces_stale_lock(tmp_path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")

    assert store.acquire_codex_session_lock("cid-1", "okr:1") is True
    with store._connect() as db:
        db.execute(
            """
            update codex_session_locks
            set locked_at=datetime('now', '-21 minutes')
            where conversation_id='cid-1'
            """
        )

    assert store.acquire_codex_session_lock("cid-1", "reply:msg-1") is True
    with store._connect() as db:
        rows = db.execute(
            "select owner from codex_session_locks where conversation_id='cid-1'"
        ).fetchall()
    assert [row["owner"] for row in rows] == ["reply:msg-1"]


def test_codex_session_lock_release_requires_owner(tmp_path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")

    assert store.acquire_codex_session_lock("cid-1", "okr:1") is True
    assert store.release_codex_session_lock("cid-1", "other") is False
    assert store.acquire_codex_session_lock("cid-1", "reply:msg-1") is False
    assert store.release_codex_session_lock("cid-1", "okr:1") is True


def test_codex_session_lock_context_manager_releases_without_swallowing(tmp_path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")

    with store.codex_session_lock("cid-1", "okr:1"):
        assert store.acquire_codex_session_lock("cid-1", "reply:msg-1") is False

    assert store.acquire_codex_session_lock("cid-1", "reply:msg-1") is True


def test_reply_task_queue_dedupes_by_conversation_and_message(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")

    first_inserted = store.enqueue_reply_task(
        conversation_id="cid-1",
        conversation_title="Friday",
        single_chat=False,
        trigger_message_id="msg-1",
        trigger_create_time="2026-05-13 18:00:00",
        trigger_sender="Mina",
        trigger_text="@Alex Chen 看一下",
    )
    second_inserted = store.enqueue_reply_task(
        conversation_id="cid-1",
        conversation_title="Friday",
        single_chat=False,
        trigger_message_id="msg-1",
        trigger_create_time="2026-05-13 18:00:00",
        trigger_sender="Mina",
        trigger_text="@Alex Chen 看一下",
    )

    assert first_inserted is True
    assert second_inserted is False
    assert store.count_reply_tasks(status="pending") == 1


def test_reply_task_execution_generation_defaults_and_survives_requeue(
    tmp_path: Path,
):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    task_id = _enqueue_universal_reply_task(store)
    claimed = store.list_reply_tasks(limit=1)[0]

    assert claimed.id == task_id
    assert claimed.execution_generation == "initial"

    store.requeue_reply_task(
        task_id,
        "retry",
        expected_execution_generation=claimed.execution_generation,
    )
    reclaimed = store.claim_reply_tasks(limit=1)[0]

    assert reclaimed.execution_generation == "initial"


def test_enqueue_reply_task_rejects_empty_execution_generation(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")

    with pytest.raises(ValueError, match="execution_generation must be non-empty"):
        store.enqueue_reply_task(
            conversation_id="cid-1",
            conversation_title="Friday",
            single_chat=False,
            trigger_message_id="msg-1",
            trigger_create_time="2026-07-20 10:00:00",
            trigger_sender="Derek",
            trigger_text="Handle this task",
            execution_generation="   ",
        )


def test_store_migrates_reply_tasks_with_initial_execution_generation(
    tmp_path: Path,
):
    db_path = tmp_path / "worker.sqlite3"
    with sqlite3.connect(db_path) as db:
        db.execute(
            """
            create table reply_tasks (
                id integer primary key autoincrement,
                conversation_id text not null,
                conversation_title text not null,
                single_chat integer not null,
                trigger_message_id text not null,
                trigger_create_time text not null,
                trigger_sender text not null,
                trigger_text text not null,
                status text not null default 'pending',
                attempts integer not null default 0,
                locked_at text,
                error text not null default '',
                created_at text not null default current_timestamp,
                updated_at text not null default current_timestamp,
                unique(conversation_id, trigger_message_id)
            )
            """
        )
        db.execute(
            """
            insert into reply_tasks (
                conversation_id, conversation_title, single_chat,
                trigger_message_id, trigger_create_time, trigger_sender, trigger_text
            ) values ('cid-legacy', 'Legacy', 0, 'msg-legacy',
                      '2026-07-20 09:00:00', 'Derek', 'Legacy task')
            """
        )

    store = AutoReplyStore(db_path)

    assert store.claim_reply_tasks(limit=1)[0].execution_generation == "initial"


def test_store_channel_identity_migration_preserves_active_execution_generation(
    tmp_path: Path,
):
    db_path = tmp_path / "worker.sqlite3"
    with sqlite3.connect(db_path) as db:
        db.executescript(
            """
            create table reply_tasks (
                id integer primary key autoincrement,
                channel text not null default 'dingtalk',
                conversation_id text not null,
                conversation_title text not null,
                single_chat integer not null,
                trigger_message_id text not null,
                trigger_create_time text not null,
                trigger_sender text not null,
                trigger_text text not null,
                trigger_message_json text not null default '{}',
                available_at text not null default '',
                force_new_decision integer not null default 0,
                oa_url text not null default '',
                manual_rerun_attempt_id integer not null default 0,
                manual_rerun_revision_key text not null default '',
                execution_generation text not null default 'initial',
                status text not null default 'pending',
                attempts integer not null default 0,
                locked_at text,
                error text not null default '',
                created_at text not null default current_timestamp,
                updated_at text not null default current_timestamp,
                unique(conversation_id, trigger_message_id)
            );
            insert into reply_tasks (
                conversation_id, conversation_title, single_chat,
                trigger_message_id, trigger_create_time, trigger_sender,
                trigger_text, execution_generation, status
            ) values (
                'cid-active', 'Active', 0, 'msg-active',
                '2026-07-20 09:00:00', 'Derek', 'Active task',
                'gen-active', 'processing'
            );
            """
        )

    store = AutoReplyStore(db_path)
    migrated = store.get_reply_task(1)
    assert migrated is not None
    assert migrated.status == "processing"
    assert migrated.execution_generation == "gen-active"

    AutoReplyStore(db_path)
    assert store.get_reply_task(1).execution_generation == "gen-active"


def test_reply_task_channel_identity_migration_rolls_back_on_rebuild_failure(
    tmp_path: Path,
):
    db_path = tmp_path / "worker.sqlite3"
    with sqlite3.connect(db_path) as db:
        db.row_factory = sqlite3.Row
        db.executescript(
            """
            create table reply_tasks (
                id integer primary key autoincrement,
                channel text not null default 'dingtalk',
                conversation_id text not null,
                conversation_title text not null,
                single_chat integer not null,
                trigger_message_id text not null,
                trigger_create_time text not null,
                trigger_sender text not null,
                trigger_text text not null,
                execution_generation text not null default 'initial',
                status text not null default 'pending',
                attempts integer not null default 0,
                locked_at text,
                error text not null default '',
                created_at text not null default current_timestamp,
                updated_at text not null default current_timestamp,
                unique(conversation_id, trigger_message_id)
            );
            insert into reply_tasks (
                conversation_id, conversation_title, single_chat,
                trigger_message_id, trigger_create_time, trigger_sender,
                trigger_text, execution_generation, status
            ) values (
                'cid-active', 'Active', 0, 'msg-active',
                '2026-07-20 09:00:00', 'Derek', 'Active task',
                'gen-active', 'processing'
            );
            create table reply_tasks_channel_migration (id integer primary key);
            """
        )

        with pytest.raises(sqlite3.OperationalError):
            AutoReplyStore._migrate_reply_task_channel_identity(db)

        row = db.execute(
            "select execution_generation, status from reply_tasks where id=1"
        ).fetchone()
        assert dict(row) == {
            "execution_generation": "gen-active",
            "status": "processing",
        }


def test_enqueue_manual_rerun_reply_task_requeues_existing_task(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    store.enqueue_reply_task(
        conversation_id="cid-1",
        conversation_title="Friday",
        single_chat=False,
        trigger_message_id="msg-1",
        trigger_create_time="2026-05-13 18:00:00",
        trigger_sender="Mina",
        trigger_text="@Alex Chen 看一下",
        trigger_message_json='{"open_message_id":"msg-1","content":"old"}',
    )
    task = store.claim_reply_tasks(limit=1)[0]
    store.fail_reply_task(
        task.id,
        "old failure",
        expected_execution_generation=task.execution_generation,
    )
    attempt_id = store.record_reply_attempt(
        conversation_id="cid-1",
        conversation_title="Friday",
        trigger_message_id="msg-1",
        trigger_sender="Mina",
        trigger_text="@Alex Chen 看一下",
        action="send_reply",
        sensitivity_kind="general",
        send_status="failed",
    )

    rerun = store.enqueue_manual_rerun_reply_task(
        conversation_id="cid-1",
        conversation_title="Friday",
        single_chat=False,
        trigger_message_id="msg-1",
        trigger_create_time="2026-05-13 18:01:00",
        trigger_sender="Mina",
        trigger_text="@Alex Chen 重新看",
        trigger_message_json='{"open_message_id":"msg-1","content":"new"}',
        oa_url="https://oa.example/process",
        attempt_id=attempt_id,
    )

    assert rerun.id == task.id
    assert rerun.status == "pending"
    assert rerun.locked_at is None
    assert rerun.force_new_decision is True
    assert rerun.oa_url == "https://oa.example/process"
    assert rerun.manual_rerun_attempt_id == attempt_id
    assert rerun.error == f"manual_rerun_from_attempt:{attempt_id}"
    assert rerun.trigger_text == "@Alex Chen 重新看"
    claimed = store.claim_reply_tasks(limit=1)
    assert [claimed_task.id for claimed_task in claimed] == [task.id]


def test_manual_rerun_dedupes_same_pending_source_attempt(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    _enqueue_universal_reply_task(store)
    attempt_id = store.record_reply_attempt(
        conversation_id="cid-universal",
        conversation_title="Universal",
        trigger_message_id="msg-universal",
        trigger_sender="Derek",
        trigger_text="Handle this task",
        action="send_reply",
        sensitivity_kind="general",
        send_status="failed",
    )

    rerun_args = {
        "conversation_id": "cid-universal",
        "conversation_title": "Universal",
        "single_chat": False,
        "trigger_message_id": "msg-universal",
        "trigger_create_time": "2026-07-20 10:01:00",
        "trigger_sender": "Derek",
        "trigger_text": "Run it again",
        "trigger_message_json": "{}",
        "attempt_id": attempt_id,
    }
    first = store.enqueue_manual_rerun_reply_task(**rerun_args)
    second = store.enqueue_manual_rerun_reply_task(**rerun_args)

    assert first.execution_generation
    assert second.execution_generation
    assert first.execution_generation != "initial"
    assert second.execution_generation != "initial"
    assert first.execution_generation == second.execution_generation
    assert first.id == second.id


def test_forced_manual_rerun_rotates_failed_pending_generation(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    task_id = _enqueue_universal_reply_task(store)
    original = store.get_reply_task(task_id)
    assert original is not None
    run = _claim_audit_run(store,
        task_id,
        original.execution_generation,
        owner="worker-1",
    ).run
    store.fail_agent_run(
        run.id,
        {"code": "codex_process_failed", "retryable": True},
        owner="worker-1",
    )
    store.defer_reply_task(
        task_id,
        "codex_process_failed",
        expected_execution_generation=original.execution_generation,
    )

    rerun = store.enqueue_manual_rerun_reply_task(
        conversation_id="cid-universal",
        conversation_title="Universal",
        single_chat=False,
        trigger_message_id="msg-universal",
        trigger_create_time="2026-07-20 10:01:00",
        trigger_sender="Derek",
        trigger_text="Run it again",
        trigger_message_json="{}",
        force_rotation=True,
    )

    assert rerun.id == task_id
    assert rerun.execution_generation != original.execution_generation
    assert rerun.status == "pending"


def test_forced_manual_rerun_does_not_supersede_active_agent_run(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    task_id = _enqueue_universal_reply_task(store)
    original = store.get_reply_task(task_id)
    assert original is not None
    _claim_audit_run(store,
        task_id,
        original.execution_generation,
        owner="worker-1",
    )

    with pytest.raises(ValueError, match="active agent run must finish"):
        store.enqueue_manual_rerun_reply_task(
            conversation_id="cid-universal",
            conversation_title="Universal",
            single_chat=False,
            trigger_message_id="msg-universal",
            trigger_create_time="2026-07-20 10:01:00",
            trigger_sender="Derek",
            trigger_text="Run it again",
            trigger_message_json="{}",
            force_rotation=True,
        )

    unchanged = store.get_reply_task(task_id)
    assert unchanged is not None
    assert unchanged.execution_generation == original.execution_generation


def test_manual_rerun_dedupes_same_attempt_across_processes(tmp_path: Path):
    db_path = tmp_path / "worker.sqlite3"
    store = AutoReplyStore(db_path)
    store.enqueue_reply_task(
        conversation_id="cid-process-rerun",
        conversation_title="Process rerun",
        single_chat=False,
        trigger_message_id="msg-process-rerun",
        trigger_create_time="2026-07-29 10:59:00",
        trigger_sender="ET",
        trigger_text="请处理",
        trigger_message_json="{}",
    )
    attempt_id = store.record_reply_attempt(
        conversation_id="cid-process-rerun",
        conversation_title="Process rerun",
        trigger_message_id="msg-process-rerun",
        trigger_sender="ET",
        trigger_text="请处理",
        action="send_reply",
        sensitivity_kind="general",
        send_status="failed",
    )
    context = get_context("spawn")
    barrier = context.Barrier(8)
    results = context.Queue()
    processes = [
        context.Process(
            target=_enqueue_manual_rerun_in_process,
                args=(str(db_path), attempt_id, barrier, results),
        )
        for _ in range(8)
    ]

    for process in processes:
        process.start()
    for process in processes:
        process.join(timeout=20)
        assert process.exitcode == 0

    outcomes = [results.get(timeout=2) for _ in processes]
    assert len({task_id for task_id, _ in outcomes}) == 1
    assert len({generation for _, generation in outcomes}) == 1


def test_manual_rerun_new_source_attempt_rotates_generation(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    common = {
        "conversation_id": "cid-corrected-rerun",
        "conversation_title": "Corrected rerun",
        "single_chat": False,
        "trigger_message_id": "msg-corrected-rerun",
        "trigger_create_time": "2026-07-29 11:00:00",
        "trigger_sender": "ET",
        "trigger_text": "请重新处理",
        "trigger_message_json": "{}",
    }
    attempt_ids = [
        store.record_reply_attempt(
            conversation_id=common["conversation_id"],
            conversation_title=common["conversation_title"],
            trigger_message_id=common["trigger_message_id"],
            trigger_sender=common["trigger_sender"],
            trigger_text=common["trigger_text"],
            action="send_reply",
            sensitivity_kind="general",
            send_status="failed",
        )
        for _ in range(2)
    ]

    first = store.enqueue_manual_rerun_reply_task(
        **common, attempt_id=attempt_ids[0]
    )
    corrected = store.enqueue_manual_rerun_reply_task(
        **common, attempt_id=attempt_ids[1]
    )

    assert corrected.id == first.id
    assert corrected.execution_generation != first.execution_generation
    assert corrected.manual_rerun_attempt_id == attempt_ids[1]


def test_manual_rerun_changed_attempt_revision_rotates_processing_generation(
    tmp_path: Path,
) -> None:
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    attempt_id = store.record_reply_attempt(
        conversation_id="cid-revised-attempt",
        conversation_title="Revised attempt",
        trigger_message_id="msg-revised-attempt",
        trigger_sender="ET",
        trigger_text="请重新处理",
        action="send_reply",
        sensitivity_kind="general",
        send_status="failed",
    )
    common = {
        "conversation_id": "cid-revised-attempt",
        "conversation_title": "Revised attempt",
        "single_chat": False,
        "trigger_message_id": "msg-revised-attempt",
        "trigger_create_time": "2026-07-29 11:00:00",
        "trigger_sender": "ET",
        "trigger_text": "请重新处理",
        "trigger_message_json": "{}",
        "attempt_id": attempt_id,
    }

    first = store.enqueue_manual_rerun_reply_task(**common)
    claimed = store.claim_reply_tasks(limit=1)
    assert claimed[0].execution_generation == first.execution_generation
    assert store.record_reply_feedback(
        attempt_id,
        feedback="请根据审核意见重新处理",
        corrected_reply_text="这是修正版回复。",
    )

    revised = store.enqueue_manual_rerun_reply_task(**common)
    repeated = store.enqueue_manual_rerun_reply_task(**common)

    assert revised.execution_generation != first.execution_generation
    assert revised.status == "pending"
    assert revised.execution_generation == repeated.execution_generation


def test_generation_rotation_waits_for_unknown_effect_reconciliation(
    tmp_path: Path,
) -> None:
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    task_id = _enqueue_universal_reply_task(store)
    run = _claim_audit_run(store,
        task_id,
        "initial",
        owner="worker-1",
        now="2026-07-29 09:00:00",
    ).run
    store.append_agent_run_event(
        run.id,
        {
            "type": "item.started",
            "item": {
                "id": "send-1",
                "type": "command_execution",
                "metadata": {"effect": "effectful"},
            },
        },
        owner="worker-1",
        now="2026-07-29 09:00:01",
    )

    with pytest.raises(ValueError, match="reconciliation required"):
        store.rotate_reply_task_execution_generation(task_id)

    task = store.get_reply_task(task_id)
    unresolved = store.get_agent_run(run.id)
    assert task is not None and task.execution_generation == "initial"
    assert task.status == "processing"
    assert unresolved is not None and unresolved.status == "unknown"
    assert [item.id for item in store.list_unknown_agent_runs()] == [run.id]
    assert _claim_audit_run(store,
        task_id,
        "initial",
        owner="new-worker",
    ).claimed is False


def test_reconciliation_defer_rejects_stale_generation_even_with_live_lease(
    tmp_path: Path,
) -> None:
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    task_id = _enqueue_universal_reply_task(store)
    run = _claim_audit_run(store,
        task_id,
        "initial",
        owner="worker-1",
        now="2026-07-29 09:00:00",
    ).run
    store.mark_agent_run_unknown(
        run.id,
        {"code": "effect_completion_missing"},
        owner="worker-1",
        now="2026-07-29 09:00:01",
    )
    claim = store.claim_unknown_agent_run(
        run.id,
        owner="reconciler-1",
        now="2026-07-29 09:00:02",
    )
    assert claim.claimed
    with store._connect() as db:
        db.execute(
            "update reply_tasks set execution_generation='new-generation' where id=?",
            (task_id,),
        )

    with pytest.raises(AgentRunLeaseLostError):
        store.defer_unknown_agent_run_reconciliation(
            run.id,
            {"code": "temporary_failure"},
            owner="reconciler-1",
            expected_execution_generation="initial",
            next_attempt_at="2026-07-29 09:10:00",
            now="2026-07-29 09:00:03",
        )

    unchanged = store.get_agent_run(run.id)
    assert unchanged is not None
    assert unchanged.reconciliation_next_attempt_at == ""
    assert unchanged.lease_owner == "reconciler-1"


def test_reviewed_reply_and_rerun_roll_back_together(tmp_path: Path) -> None:
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    store.enqueue_reply_task(
        conversation_id="cid-atomic-review",
        conversation_title="Review",
        single_chat=False,
        trigger_message_id="msg-atomic-review",
        trigger_create_time="2026-07-29 10:00:00",
        trigger_sender="ET",
        trigger_text="请处理",
        trigger_message_json="{}",
    )
    with store._connect() as db:
        db.executescript(
            """
            create trigger reject_review_rerun before update on reply_tasks
            when new.manual_rerun_attempt_id > 0
            begin
                select raise(abort, 'forced review rerun failure');
            end;
            """
        )

    with pytest.raises(sqlite3.IntegrityError, match="forced review rerun failure"):
        store.record_reviewed_reply_rerun(
            conversation_id="cid-atomic-review",
            conversation_title="Review",
            single_chat=False,
            trigger_message_id="msg-atomic-review",
            trigger_create_time="2026-07-29 10:00:00",
            trigger_sender="ET",
            trigger_text="请处理",
            trigger_message_json="{}",
            suggested_reply_text="建议内容",
            reviewer_feedback="审核意见",
        )

    attempts = store.list_reply_attempts(limit=20)
    task = store.get_reply_task_for_message(
        "cid-atomic-review", "msg-atomic-review"
    )
    assert attempts == []
    assert task is not None and task.manual_rerun_attempt_id == 0


def test_reviewed_reply_rerun_is_idempotent_across_concurrent_connections(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "worker.sqlite3"
    store = AutoReplyStore(db_path)
    store.enqueue_reply_task(
        conversation_id="cid-concurrent-review",
        conversation_title="Review",
        single_chat=False,
        trigger_message_id="msg-concurrent-review",
        trigger_create_time="2026-07-29 10:00:00",
        trigger_sender="ET",
        trigger_text="请处理",
        trigger_message_json="{}",
    )
    barrier = Barrier(12)
    results: Queue = Queue()

    def enqueue_review() -> None:
        thread_store = AutoReplyStore(db_path)
        try:
            barrier.wait(timeout=5)
            results.put(
                thread_store.record_reviewed_reply_rerun(
                    conversation_id="cid-concurrent-review",
                    conversation_title="Review",
                    single_chat=False,
                    trigger_message_id="msg-concurrent-review",
                    trigger_create_time="2026-07-29 10:00:00",
                    trigger_sender="ET",
                    trigger_text="请处理",
                    trigger_message_json="{}",
                    suggested_reply_text="建议内容",
                    reviewer_feedback="审核意见",
                )
            )
        except Exception as exc:  # pragma: no cover - surfaced below
            results.put(exc)

    threads = [Thread(target=enqueue_review) for _ in range(12)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)

    outcomes = [results.get_nowait() for _ in threads]
    errors = [outcome for outcome in outcomes if isinstance(outcome, Exception)]
    assert errors == []
    attempt_ids = {outcome[0] for outcome in outcomes}
    generations = {outcome[1].execution_generation for outcome in outcomes}
    assert len(attempt_ids) == 1
    assert len(generations) == 1

    matching_attempts = [
        attempt
        for attempt in store.list_reply_attempts(limit=20)
        if attempt.codex_reason == "reviewed_message_reply"
    ]
    assert [attempt.id for attempt in matching_attempts] == list(attempt_ids)


def test_reviewed_reply_rerun_allows_changed_feedback_to_rotate_generation(
    tmp_path: Path,
) -> None:
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    common = {
        "conversation_id": "cid-revised-review",
        "conversation_title": "Review",
        "single_chat": False,
        "trigger_message_id": "msg-revised-review",
        "trigger_create_time": "2026-07-29 10:00:00",
        "trigger_sender": "ET",
        "trigger_text": "请处理",
        "trigger_message_json": "{}",
        "suggested_reply_text": "建议内容",
    }

    first_attempt_id, first_task = store.record_reviewed_reply_rerun(
        **common,
        reviewer_feedback="审核意见",
    )
    revised_attempt_id, revised_task = store.record_reviewed_reply_rerun(
        **common,
        reviewer_feedback="补充后的审核意见",
    )

    assert revised_attempt_id != first_attempt_id
    assert revised_task.id == first_task.id
    assert revised_task.execution_generation != first_task.execution_generation


def test_agent_run_is_unique_per_task_generation(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    task_id = _enqueue_universal_reply_task(store)

    first = _claim_audit_run(store, task_id, "initial", owner="worker-1")
    second = _claim_audit_run(store, task_id, "initial", owner="worker-2")

    assert first.claimed is True
    assert second.claimed is False
    assert second.run.id == first.run.id
    assert second.run.lease_owner == "worker-1"


def test_agent_run_concurrent_claims_choose_exactly_one_owner(tmp_path: Path):
    db_path = tmp_path / "worker.sqlite3"
    first_store = AutoReplyStore(db_path)
    second_store = AutoReplyStore(db_path)
    task_id = _enqueue_universal_reply_task(first_store)
    barrier = Barrier(2)
    results: Queue = Queue()

    def claim(store: AutoReplyStore, owner: str) -> None:
        try:
            barrier.wait(timeout=5)
            results.put((_claim_audit_run(store, task_id, "initial", owner=owner), None))
        except BaseException as exc:
            results.put((None, exc))

    threads = [
        Thread(target=claim, args=(first_store, "worker-1")),
        Thread(target=claim, args=(second_store, "worker-2")),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)
        assert not thread.is_alive()

    outcomes = [results.get_nowait(), results.get_nowait()]
    errors = [error for _, error in outcomes if error is not None]
    assert errors == []
    claims = [claim for claim, _ in outcomes]
    assert sum(claim.claimed for claim in claims) == 1
    assert len({claim.run.id for claim in claims}) == 1
    winner = next(claim for claim in claims if claim.claimed)
    loser = next(claim for claim in claims if not claim.claimed)
    assert loser.run.lease_owner == winner.run.lease_owner


def test_agent_run_fresh_lease_cannot_be_stolen_but_expired_lease_recovers(
    tmp_path: Path,
):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    task_id = _enqueue_universal_reply_task(store)
    first = _claim_audit_run(store,
        task_id,
        "initial",
        owner="worker-1",
        lease_seconds=1800,
        now="2026-07-29 00:00:00",
    )
    store.set_agent_run_session(
        first.run.id,
        "session-1",
        owner="worker-1",
        transcript_start_line=8,
        now="2026-07-29 00:01:00",
    )

    fresh = _claim_audit_run(store,
        task_id,
        "initial",
        owner="worker-2",
        now="2026-07-29 00:29:59",
    )
    expired = _claim_audit_run(store,
        task_id,
        "initial",
        owner="worker-2",
        now="2026-07-29 00:30:01",
    )

    assert fresh.claimed is False
    assert expired.claimed is True
    assert expired.run.id == first.run.id
    assert expired.run.codex_session_id == "session-1"
    assert expired.run.transcript_start_line == 8
    assert expired.run.lease_owner == "worker-2"


def test_expired_sessionless_agent_run_cannot_be_reclaimed(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    task_id = _enqueue_universal_reply_task(store)
    first = _claim_audit_run(store,
        task_id,
        "initial",
        owner="worker-1",
        lease_seconds=1800,
        now="2026-07-29 00:00:00",
    )

    expired = _claim_audit_run(store,
        task_id,
        "initial",
        owner="worker-2",
        now="2026-07-29 00:30:01",
    )

    assert expired.claimed is False
    assert expired.run.id == first.run.id
    assert expired.run.codex_session_id == ""
    assert expired.run.lease_owner == "worker-1"
    assert expired.run.lease_expires_at == first.run.lease_expires_at


def test_agent_run_claim_rejects_generation_not_owned_by_task(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    task_id = _enqueue_universal_reply_task(store)

    with pytest.raises(ValueError, match="execution generation mismatch"):
        _claim_audit_run(store, task_id, "other-generation", owner="worker-1")


def test_agent_run_lease_renewal_requires_current_owner(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    task_id = _enqueue_universal_reply_task(store)
    claim = _claim_audit_run(store,
        task_id,
        "initial",
        owner="worker-1",
        now="2026-07-29 00:00:00",
    )

    renewed = store.renew_agent_run_lease(
        claim.run.id,
        owner="worker-1",
        lease_seconds=900,
        now="2026-07-29 00:10:00",
    )

    assert renewed.lease_expires_at == "2026-07-29 00:25:00"
    with pytest.raises(AgentRunLeaseLostError, match="agent run lease lost"):
        store.renew_agent_run_lease(
            claim.run.id,
            owner="worker-2",
            now="2026-07-29 00:11:00",
        )


def test_reclaimed_agent_run_rejects_every_stale_owner_mutation(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    task_id = _enqueue_universal_reply_task(store)
    first = _claim_audit_run(store,
        task_id,
        "initial",
        owner="worker-a",
        now="2026-07-29 00:00:00",
    )
    store.set_agent_run_session(
        first.run.id,
        "session-1",
        owner="worker-a",
        now="2026-07-29 00:01:00",
    )
    reclaimed = _claim_audit_run(store,
        task_id,
        "initial",
        owner="worker-b",
        now="2026-07-29 00:30:01",
    )
    assert reclaimed.claimed is True
    before = store.get_agent_run(first.run.id)
    assert before is not None

    stale_mutations = [
        lambda: store.set_agent_run_session(
            first.run.id,
            "session-1",
            owner="worker-a",
            now="2026-07-29 00:30:02",
        ),
        lambda: store.append_agent_run_event(
            first.run.id,
            {"type": "item.started", "call_id": "stale"},
            owner="worker-a",
            now="2026-07-29 00:30:02",
        ),
        lambda: store.complete_agent_run(
            first.run.id,
            {"outcome": "completed", "summary": "stale"},
            owner="worker-a",
            now="2026-07-29 00:30:02",
        ),
        lambda: store.fail_agent_run(
            first.run.id,
            {"code": "stale"},
            owner="worker-a",
            now="2026-07-29 00:30:02",
        ),
        lambda: store.mark_agent_run_unknown(
            first.run.id,
            {"code": "stale"},
            owner="worker-a",
            now="2026-07-29 00:30:02",
        ),
        lambda: store.renew_agent_run_lease(
            first.run.id,
            owner="worker-a",
            now="2026-07-29 00:30:02",
        ),
        lambda: store.record_agent_execution_receipt(
            first.run.id,
            receipt_id="stale-receipt",
            operation_id="stale-write",
            cli="dws",
            command_path="chat message send",
            command_digest="digest",
            exit_code=0,
            owner="worker-a",
            now="2026-07-29 00:30:02",
        ),
    ]
    for mutate in stale_mutations:
        with pytest.raises(AgentRunLeaseLostError, match="agent run lease lost"):
            mutate()
        assert store.get_agent_run(first.run.id) == before

    store.set_agent_run_session(
        first.run.id,
        "session-1",
        owner="worker-b",
        now="2026-07-29 00:30:02",
    )
    store.append_agent_run_event(
        first.run.id,
        {"type": "item.completed", "call_id": "owned"},
        owner="worker-b",
        now="2026-07-29 00:30:02",
    )
    renewed = store.renew_agent_run_lease(
        first.run.id,
        owner="worker-b",
        now="2026-07-29 00:31:00",
    )
    completed = store.complete_agent_run(
        first.run.id,
        {"outcome": "completed", "summary": "owned"},
        owner="worker-b",
        now="2026-07-29 00:31:01",
    )

    assert renewed.lease_owner == "worker-b"
    assert completed.status == "completed"
    assert [event["call_id"] for event in completed.tool_events] == ["owned"]


def test_expired_lease_blocks_writes_until_session_recovery(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    task_id = _enqueue_universal_reply_task(store)
    first = _claim_audit_run(store,
        task_id,
        "initial",
        owner="worker-a",
        now="2026-07-29 00:00:00",
    )
    store.set_agent_run_session(
        first.run.id,
        "session-1",
        owner="worker-a",
        now="2026-07-29 00:01:00",
    )

    before = store.get_agent_run(first.run.id)
    assert before is not None
    expired_mutations = [
        lambda: store.set_agent_run_session(
            first.run.id,
            "session-1",
            owner="worker-a",
            now="2026-07-29 00:30:01",
        ),
        lambda: store.append_agent_run_event(
            first.run.id,
            {"type": "item.started", "call_id": "blocked"},
            owner="worker-a",
            now="2026-07-29 00:30:01",
        ),
        lambda: store.complete_agent_run(
            first.run.id,
            {"outcome": "completed", "summary": "expired"},
            owner="worker-a",
            now="2026-07-29 00:30:01",
        ),
        lambda: store.fail_agent_run(
            first.run.id,
            {"code": "expired"},
            owner="worker-a",
            now="2026-07-29 00:30:01",
        ),
        lambda: store.mark_agent_run_unknown(
            first.run.id,
            {"code": "expired"},
            owner="worker-a",
            now="2026-07-29 00:30:01",
        ),
        lambda: store.renew_agent_run_lease(
            first.run.id,
            owner="worker-a",
            now="2026-07-29 00:30:01",
        ),
    ]
    for mutate in expired_mutations:
        with pytest.raises(AgentRunLeaseLostError, match="agent run lease lost"):
            mutate()
        assert store.get_agent_run(first.run.id) == before

    recovered = _claim_audit_run(store,
        task_id,
        "initial",
        owner="worker-b",
        now="2026-07-29 00:30:02",
    )
    appended = store.append_agent_run_event(
        first.run.id,
        {"type": "item.started", "call_id": "recovered"},
        owner="worker-b",
        now="2026-07-29 00:30:03",
    )

    assert recovered.claimed is True
    assert [event["call_id"] for event in appended.tool_events] == ["recovered"]


def test_expired_agent_run_with_incomplete_effect_cannot_be_reclaimed(
    tmp_path: Path,
):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    task_id = _enqueue_universal_reply_task(store)
    first = _claim_audit_run(store,
        task_id,
        "initial",
        owner="worker-a",
        lease_seconds=60,
        now="2026-07-29 00:00:00",
    )
    store.set_agent_run_session(
        first.run.id,
        "session-1",
        owner="worker-a",
        now="2026-07-29 00:00:01",
    )
    store.append_agent_run_event(
        first.run.id,
        {
            "type": "item.started",
            "item": {
                "id": "write-1",
                "type": "mcp_tool_call",
                "metadata": {"effect": "effectful"},
            },
        },
        owner="worker-a",
        now="2026-07-29 00:00:02",
    )

    reclaim = _claim_audit_run(store,
        task_id,
        "initial",
        owner="worker-b",
        now="2026-07-29 00:02:00",
    )

    assert reclaim.claimed is False
    assert reclaim.run.lease_owner == "worker-a"
    assert reclaim.run.side_effect_state == "unknown"


@pytest.mark.parametrize(
    ("cli", "command_path"),
    (
        ("dws", "chat message send"),
        ("mcp:xiaoqing_interview", "upload_interview_result"),
    ),
)
def test_expired_agent_run_with_confirmed_receipt_enters_reconciliation_without_replay(
    tmp_path: Path,
    cli: str,
    command_path: str,
):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    task_id = _enqueue_universal_reply_task(store)
    first = _claim_audit_run(store,
        task_id,
        "initial",
        owner="worker-a",
        lease_seconds=60,
        now="2026-07-29 00:00:00",
    )
    store.set_agent_run_session(
        first.run.id,
        "session-1",
        owner="worker-a",
        now="2026-07-29 00:00:01",
    )
    store.record_agent_execution_receipt(
        first.run.id,
        receipt_id=f"receipt-{cli}",
        operation_id="write-1",
        cli=cli,
        command_path=command_path,
        command_digest="digest",
        exit_code=0,
        owner="worker-a",
        now="2026-07-29 00:00:02",
    )

    reclaim = _claim_audit_run(store,
        task_id,
        "initial",
        owner="worker-b",
        now="2026-07-29 00:02:00",
    )

    assert reclaim.claimed is False
    assert reclaim.run.status == "unknown"
    assert reclaim.run.side_effect_state == "unknown"
    assert reclaim.run.lease_owner == ""
    assert store.list_agent_execution_receipts(first.run.id)[0].operation_id == "write-1"


def test_running_agent_events_are_persisted_incrementally(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    task_id = _enqueue_universal_reply_task(store)
    run = _claim_audit_run(store, task_id, "initial", owner="worker-1").run
    started = {
        "type": "item.started",
        "call_id": "c1",
        "effect": {"kind": "write", "provider": "dws"},
    }
    completed = {
        "type": "item.completed",
        "call_id": "c1",
        "receipt": {"accepted": True},
    }

    store.append_agent_run_event(run.id, started, owner="worker-1")
    store.append_agent_run_event(run.id, completed, owner="worker-1")

    loaded = store.get_agent_run(run.id)
    assert loaded is not None
    assert loaded.tool_events == [started, completed]
    assert loaded.transcript_end_line == 2


def test_agent_run_effect_state_tracks_structured_call_lifecycle(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    task_id = _enqueue_universal_reply_task(store)
    run = _claim_audit_run(store, task_id, "initial", owner="worker-1").run
    started = {
        "type": "item.started",
        "item": {
            "id": "write-1",
            "type": "mcp_tool_call",
            "metadata": {"effect": "effectful"},
        },
    }
    completed = {
        "type": "item.completed",
        "item": {
            "id": "write-1",
            "type": "mcp_tool_call",
            "metadata": {"effect": "effectful"},
        },
    }

    unknown = store.append_agent_run_event(run.id, started, owner="worker-1")
    confirmed = store.append_agent_run_event(run.id, completed, owner="worker-1")

    assert unknown.side_effect_state == "unknown"
    assert confirmed.side_effect_state == "confirmed"


def test_failed_agent_effect_is_terminal_and_not_unknown(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    task_id = _enqueue_universal_reply_task(store)
    run = _claim_audit_run(store, task_id, "initial", owner="worker-1").run
    started = {
        "type": "item.started",
        "item": {
            "id": "write-1",
            "type": "mcp_tool_call",
            "metadata": {"effect": "effectful"},
        },
    }
    failed = {
        "type": "item.failed",
        "item": {
            "id": "write-1",
            "type": "mcp_tool_call",
            "metadata": {"effect": "effectful"},
        },
    }

    store.append_agent_run_event(run.id, started, owner="worker-1")
    terminal = store.append_agent_run_event(run.id, failed, owner="worker-1")

    assert terminal.side_effect_state == "none"


def test_agent_run_events_use_append_only_rows_in_sequence(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    task_id = _enqueue_universal_reply_task(store)
    run = _claim_audit_run(store, task_id, "initial", owner="worker-1").run
    first = {"type": "item.started", "call_id": "c1"}
    second = {"type": "item.completed", "call_id": "c1"}

    store.append_agent_run_event(run.id, first, owner="worker-1")
    store.append_agent_run_event(run.id, second, owner="worker-1")

    with sqlite3.connect(store.path) as db:
        rows = db.execute(
            "select sequence, event_json from agent_run_events "
            "where agent_run_id=? order by sequence",
            (run.id,),
        ).fetchall()
        compact = db.execute(
            "select tool_events_json from agent_runs where id=?",
            (run.id,),
        ).fetchone()[0]
    assert [(row[0], json.loads(row[1])) for row in rows] == [
        (1, first),
        (2, second),
    ]
    assert compact == "[]"
    assert store.get_agent_run(run.id).tool_events == [first, second]


def test_agent_run_event_migration_backfills_legacy_json_once(tmp_path: Path):
    db_path = tmp_path / "worker.sqlite3"
    store = AutoReplyStore(db_path)
    task_id = _enqueue_universal_reply_task(store)
    run = _claim_audit_run(store, task_id, "initial", owner="worker-1").run
    legacy_events = [
        {"type": "item.started", "call_id": "legacy-1"},
        {"type": "item.failed", "call_id": "legacy-1"},
    ]
    with sqlite3.connect(db_path) as db:
        db.execute("drop table agent_run_events")
        db.execute(
            "update agent_runs set tool_events_json=? where id=?",
            (json.dumps(legacy_events), run.id),
        )
    store_module._INITIALIZED_STORE_PATHS.discard(db_path.resolve())

    migrated = AutoReplyStore(db_path)
    first_load = migrated.get_agent_run(run.id)
    store_module._INITIALIZED_STORE_PATHS.discard(db_path.resolve())
    second_load = AutoReplyStore(db_path).get_agent_run(run.id)

    assert first_load.tool_events == legacy_events
    assert second_load.tool_events == legacy_events
    with sqlite3.connect(db_path) as db:
        assert db.execute(
            "select count(*) from agent_run_events where agent_run_id=?",
            (run.id,),
        ).fetchone()[0] == 2
        assert db.execute(
            "select tool_events_json from agent_runs where id=?",
            (run.id,),
        ).fetchone()[0] == "[]"


def test_safe_persisted_receipt_closes_started_agent_effect(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    task_id = _enqueue_universal_reply_task(store)
    run = _claim_audit_run(store, task_id, "initial", owner="worker-1").run
    started = {
        "type": "item.started",
        "item": {
            "id": "write-1",
            "type": "mcp_tool_call",
            "metadata": {"effect": "effectful"},
        },
    }
    receipt = {
        "type": "item.completed",
        "item": {
            "id": "receipt-1",
            "type": "mcp_tool_call",
            "metadata": {"effect": "read_only"},
            "result": {
                "receipt_id": "receipt-1",
                "operation_id": "write-1",
                "completed": True,
                "persisted": True,
                "safe_to_confirm": True,
            },
        },
    }

    store.append_agent_run_event(run.id, started, owner="worker-1")
    confirmed = store.append_agent_run_event(run.id, receipt, owner="worker-1")

    assert confirmed.side_effect_state == "confirmed"


def test_execution_receipt_requires_current_unexpired_owner(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    task_id = _enqueue_universal_reply_task(store)
    run = _claim_audit_run(store,
        task_id,
        "initial",
        owner="worker-a",
        lease_seconds=60,
        now="2026-07-29 00:00:00",
    ).run

    with pytest.raises(AgentRunLeaseLostError, match="agent run lease lost"):
        store.record_agent_execution_receipt(
            run.id,
            receipt_id="receipt-stale-owner",
            operation_id="write-1",
            cli="dws",
            command_path="chat message send",
            command_digest="digest",
            exit_code=0,
            owner="worker-b",
            now="2026-07-29 00:00:30",
        )
    with pytest.raises(AgentRunLeaseLostError, match="agent run lease lost"):
        store.record_agent_execution_receipt(
            run.id,
            receipt_id="receipt-expired",
            operation_id="write-1",
            cli="dws",
            command_path="chat message send",
            command_digest="digest",
            exit_code=0,
            owner="worker-a",
            now="2026-07-29 00:01:01",
        )

    assert store.list_agent_execution_receipts(run.id) == []


def test_agent_run_concurrent_event_writers_do_not_drop_events(tmp_path: Path):
    db_path = tmp_path / "worker.sqlite3"
    first_store = AutoReplyStore(db_path)
    second_store = AutoReplyStore(db_path)
    task_id = _enqueue_universal_reply_task(first_store)
    run = _claim_audit_run(first_store, task_id, "initial", owner="worker-1").run
    barrier = Barrier(2)
    results: Queue = Queue()

    def append(store: AutoReplyStore, call_id: str) -> None:
        try:
            barrier.wait(timeout=5)
            store.append_agent_run_event(
                run.id,
                {"type": "item.completed", "call_id": call_id},
                owner="worker-1",
            )
            results.put(None)
        except BaseException as exc:
            results.put(exc)

    threads = [
        Thread(target=append, args=(first_store, "c1")),
        Thread(target=append, args=(second_store, "c2")),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)
        assert not thread.is_alive()

    assert [results.get_nowait(), results.get_nowait()] == [None, None]

    loaded = first_store.get_agent_run(run.id)
    assert loaded is not None
    assert len(loaded.tool_events) == 2
    assert {event["call_id"] for event in loaded.tool_events} == {"c1", "c2"}


def test_append_rechecks_default_time_after_waiting_for_write_lock(
    tmp_path: Path,
    monkeypatch,
):
    db_path = tmp_path / "worker.sqlite3"
    store = AutoReplyStore(db_path)
    task_id = _enqueue_universal_reply_task(store)
    run = _claim_audit_run(store,
        task_id,
        "initial",
        owner="worker-1",
        lease_seconds=2,
    ).run
    original_utc_store_time = store_module._utc_store_time
    clock_called = Event()

    def observed_utc_store_time(now=None):
        if now is None:
            clock_called.set()
        return original_utc_store_time(now)

    monkeypatch.setattr(store_module, "_utc_store_time", observed_utc_store_time)
    lock_db = sqlite3.connect(db_path, timeout=5)
    lock_db.execute("begin immediate")
    started = Event()
    outcomes: Queue = Queue()

    def append() -> None:
        started.set()
        try:
            result = store.append_agent_run_event(
                run.id,
                {"type": "item.started", "call_id": "expired-while-waiting"},
                owner="worker-1",
            )
            outcomes.put(result)
        except BaseException as exc:
            outcomes.put(exc)

    thread = Thread(target=append)
    thread.start()
    try:
        assert started.wait(timeout=2)
        clock_called_before_release = clock_called.wait(timeout=0.2)
        expires_at = datetime.strptime(
            run.lease_expires_at,
            "%Y-%m-%d %H:%M:%S",
        ).replace(tzinfo=timezone.utc)
        wait_seconds = max(
            0.0,
            (expires_at - datetime.now(timezone.utc)).total_seconds(),
        ) + 0.2
        assert wait_seconds < 3
        time.sleep(wait_seconds)
    finally:
        lock_db.rollback()
        lock_db.close()
        thread.join(timeout=10)

    assert not thread.is_alive()
    assert clock_called_before_release is False
    outcome = outcomes.get_nowait()
    assert isinstance(outcome, AgentRunLeaseLostError)
    loaded = store.get_agent_run(run.id)
    assert loaded is not None
    assert loaded.tool_events == []


@pytest.mark.parametrize("event", [[], "event", 1, None])
def test_agent_run_event_must_be_a_json_object(tmp_path: Path, event):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    task_id = _enqueue_universal_reply_task(store)
    run = _claim_audit_run(store, task_id, "initial", owner="worker-1").run

    with pytest.raises(ValueError, match="event must be a JSON object"):
        store.append_agent_run_event(run.id, event, owner="worker-1")


def test_agent_run_event_rejects_non_json_object_values(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    task_id = _enqueue_universal_reply_task(store)
    run = _claim_audit_run(store, task_id, "initial", owner="worker-1").run

    with pytest.raises(ValueError, match="event must be a JSON object"):
        store.append_agent_run_event(
            run.id,
            {"value": object()},
            owner="worker-1",
        )


def test_agent_run_terminal_transitions_are_strict_and_exactly_idempotent(
    tmp_path: Path,
):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    task_id = _enqueue_universal_reply_task(store)
    run = _claim_audit_run(store, task_id, "initial", owner="worker-1").run
    final_result = {"outcome": "completed", "summary": "sent"}

    completed = store.complete_agent_run(
        run.id,
        final_result,
        owner="worker-1",
        side_effect_state="confirmed",
        transcript_end_line=12,
    )
    repeated = store.complete_agent_run(
        run.id,
        final_result,
        owner="worker-1",
        side_effect_state="confirmed",
        transcript_end_line=12,
    )

    assert completed.status == "completed"
    assert repeated == completed
    assert completed.lease_owner == ""
    assert completed.lease_expires_at == ""
    with pytest.raises(ValueError, match="conflicting terminal rewrite"):
        store.complete_agent_run(
            run.id,
            {"outcome": "completed", "summary": "different"},
            owner="worker-1",
            side_effect_state="confirmed",
            transcript_end_line=12,
        )
    with pytest.raises(ValueError, match="transition from completed"):
        store.fail_agent_run(
            run.id,
            {"code": "late_failure"},
            owner="worker-1",
        )
    with pytest.raises(ValueError, match="terminal agent run"):
        store.append_agent_run_event(
            run.id,
            {"type": "late"},
            owner="worker-1",
        )


def test_unknown_agent_run_resolves_atomically_and_cannot_return_to_running(
    tmp_path: Path,
):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    task_id = _enqueue_universal_reply_task(store)
    run = _claim_audit_run(store, task_id, "initial", owner="worker-1").run
    unknown_error = {"code": "effect_completion_missing", "call_id": "c1"}

    unknown = store.mark_agent_run_unknown(
        run.id,
        unknown_error,
        owner="worker-1",
    )
    listed = store.list_unknown_agent_runs()
    reconciliation = store.claim_unknown_agent_run(
        run.id,
        owner="reconciler-1",
    )
    assert reconciliation.claimed
    completed = store.resolve_unknown_agent_run_confirmed(
        run.id,
        task_id,
        {"outcome": "completed", "summary": "effect confirmed"},
        owner="reconciler-1",
    )

    assert unknown.status == "unknown"
    assert unknown.side_effect_state == "unknown"
    assert [item.id for item in listed] == [run.id]
    assert completed.status == "completed"
    with pytest.raises(ValueError, match="transition from completed"):
        store.mark_agent_run_unknown(
            run.id,
            unknown_error,
            owner="worker-1",
        )


def test_unknown_agent_run_uses_explicit_reconciliation_event_path(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    task_id = _enqueue_universal_reply_task(store)
    run = _claim_audit_run(store, task_id, "initial", owner="worker-1").run
    store.mark_agent_run_unknown(
        run.id,
        {"code": "effect_completion_missing", "call_id": "c1"},
        owner="worker-1",
    )

    with pytest.raises(ValueError, match="terminal agent run"):
        store.append_agent_run_event(
            run.id,
            {"type": "reconciliation.completed", "call_id": "r1"},
            owner="worker-1",
        )
    claim = store.claim_unknown_agent_run(run.id, owner="reconciler-1")
    assert claim.claimed
    appended = store.append_unknown_agent_run_event(
        run.id,
        {"type": "reconciliation.completed", "call_id": "r1"},
        owner="reconciler-1",
    )

    assert appended is None
    persisted = store.get_agent_run(run.id)
    assert [event["call_id"] for event in persisted.tool_events] == ["r1"]


def test_failed_agent_run_rejects_conflicting_terminal_rewrite(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    task_id = _enqueue_universal_reply_task(store)
    run = _claim_audit_run(store, task_id, "initial", owner="worker-1").run
    error = {"code": "command_failed", "retryable": True}

    failed = store.fail_agent_run(
        run.id,
        error,
        owner="worker-1",
        transcript_end_line=5,
    )
    repeated = store.fail_agent_run(
        run.id,
        error,
        owner="worker-1",
        transcript_end_line=5,
    )

    assert failed == repeated
    with pytest.raises(ValueError, match="conflicting terminal rewrite"):
        store.fail_agent_run(
            run.id,
            {"code": "different_failure"},
            owner="worker-1",
            transcript_end_line=5,
        )


def test_unknown_agent_run_confirmed_absent_rotates_task(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    task_id = _enqueue_universal_reply_task(store)
    run = _claim_audit_run(store, task_id, "initial", owner="worker-1").run
    store.mark_agent_run_unknown(
        run.id,
        {"code": "effect_completion_missing", "call_id": "c1"},
        owner="worker-1",
    )
    claim = store.claim_unknown_agent_run(run.id, owner="reconciler-1")
    assert claim.claimed

    generation = store.resolve_unknown_agent_run_absent(
        run.id,
        task_id,
        code="reconciliation_confirmed_no_effect",
        owner="reconciler-1",
    )

    failed = store.get_agent_run(run.id)
    assert failed.status == "failed"
    assert failed.side_effect_state == "none"
    assert store.get_reply_task(task_id).execution_generation == generation


def test_unknown_reconciliation_claim_is_atomic_and_stale_owner_cannot_append(
    tmp_path: Path,
):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    task_id = _enqueue_universal_reply_task(store)
    run = _claim_audit_run(store,
        task_id,
        "initial",
        owner="worker-1",
        now="2026-07-29 09:00:00",
    ).run
    store.mark_agent_run_unknown(
        run.id,
        {"code": "effect_completion_missing", "call_id": "c1"},
        owner="worker-1",
        now="2026-07-29 09:00:01",
    )

    winner = store.claim_unknown_agent_run(
        run.id,
        owner="reconciler-a",
        lease_seconds=60,
        now="2026-07-29 09:00:02",
    )
    loser = store.claim_unknown_agent_run(
        run.id,
        owner="reconciler-b",
        lease_seconds=60,
        now="2026-07-29 09:00:02",
    )

    assert winner.claimed is True
    assert loser.claimed is False
    with pytest.raises(AgentRunLeaseLostError):
        store.append_unknown_agent_run_event(
            run.id,
            {"type": "item.completed", "item": {"id": "q1"}},
            owner="reconciler-b",
            now="2026-07-29 09:00:03",
        )


def test_confirmed_reconciliation_atomically_completes_run_and_reply_task(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    task_id = _enqueue_universal_reply_task(store)
    run = _claim_audit_run(store,
        task_id, "initial", owner="worker-1", now="2026-07-29 09:00:00"
    ).run
    store.mark_agent_run_unknown(
        run.id,
        {"code": "effect_completion_missing"},
        owner="worker-1",
        now="2026-07-29 09:00:01",
    )
    store.claim_unknown_agent_run(
        run.id,
        owner="reconciler-1",
        now="2026-07-29 09:00:02",
    )

    completed = store.resolve_unknown_agent_run_confirmed(
        run.id,
        task_id,
        {"outcome": "completed", "summary": "effect confirmed"},
        owner="reconciler-1",
        transcript_end_line=4,
        now="2026-07-29 09:00:03",
    )

    assert completed.status == "completed"
    assert completed.side_effect_state == "confirmed"
    assert store.get_reply_task(task_id).status == "done"


def test_absent_reconciliation_atomically_fails_run_and_rotates_pending_task(
    tmp_path: Path,
):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    task_id = _enqueue_universal_reply_task(store)
    original_generation = store.get_reply_task(task_id).execution_generation
    run = _claim_audit_run(store,
        task_id, original_generation, owner="worker-1", now="2026-07-29 09:00:00"
    ).run
    store.mark_agent_run_unknown(
        run.id,
        {"code": "effect_completion_missing"},
        owner="worker-1",
        now="2026-07-29 09:00:01",
    )
    store.claim_unknown_agent_run(
        run.id,
        owner="reconciler-1",
        now="2026-07-29 09:00:02",
    )

    generation = store.resolve_unknown_agent_run_absent(
        run.id,
        task_id,
        code="reconciliation_confirmed_no_effect",
        owner="reconciler-1",
        transcript_end_line=4,
        now="2026-07-29 09:00:03",
    )

    task = store.get_reply_task(task_id)
    assert store.get_agent_run(run.id).status == "failed"
    assert task.status == "pending"
    assert task.force_new_decision is True
    assert task.execution_generation == generation != original_generation
    assert task.error == "reconciliation_confirmed_no_effect"


@pytest.mark.parametrize(
    ("resolution", "run_status", "task_status", "send_status", "rotates"),
    [
        ("confirmed_occurred", "completed", "done", "completed", False),
        ("confirmed_not_occurred", "failed", "pending", "failed", True),
        ("terminate_unrecoverable", "failed", "failed", "blocked", False),
    ],
)
def test_suspended_unknown_run_requires_structured_manual_resolution(
    tmp_path: Path,
    resolution: str,
    run_status: str,
    task_status: str,
    send_status: str,
    rotates: bool,
):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    task_id = _enqueue_universal_reply_task(store)
    original_generation = store.get_reply_task(task_id).execution_generation
    run = _claim_audit_run(store,
        task_id, original_generation, owner="worker-1", now="2026-07-29 09:00:00"
    ).run
    store.mark_agent_run_unknown(
        run.id,
        {"code": "effect_completion_missing"},
        owner="worker-1",
        now="2026-07-29 09:00:01",
    )
    store.claim_unknown_agent_run(
        run.id,
        owner="reconciler-1",
        now="2026-07-29 09:00:02",
    )
    store.defer_unknown_agent_run_reconciliation(
        run.id,
        {"code": "reconciliation_needs_human", "retryable": False},
        owner="reconciler-1",
        expected_execution_generation=original_generation,
        next_attempt_at="",
        suspended=True,
        now="2026-07-29 09:00:03",
    )

    resolved = store.resolve_agent_run_manually(
        run.id,
        expected_execution_generation=original_generation,
        resolution=resolution,
        reason="人工核对外部系统后的结构化结论",
        actor="Derek",
        now="2026-07-29 09:00:04",
    )

    persisted_run = store.get_agent_run(run.id)
    task = store.get_reply_task(task_id)
    attempt = store.get_reply_attempt(resolved.attempt_id)
    assert persisted_run is not None and persisted_run.status == run_status
    assert task is not None and task.status == task_status
    assert (task.execution_generation != original_generation) is rotates
    assert attempt is not None and attempt.send_status == send_status
    assert attempt.send_error == f"manual_reconciliation_{resolution}"
    assert "Derek" in attempt.audit_summary
    assert store.list_suspended_unknown_agent_runs(limit=10) == []


def test_suspended_unknown_run_remains_visible_until_manual_resolution(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    task_id = _enqueue_universal_reply_task(store)
    run = _claim_audit_run(store,
        task_id, "initial", owner="worker-1", now="2026-07-29 09:00:00"
    ).run
    store.mark_agent_run_unknown(
        run.id,
        {"code": "effect_completion_missing"},
        owner="worker-1",
        now="2026-07-29 09:00:01",
    )
    store.claim_unknown_agent_run(
        run.id, owner="reconciler-1", now="2026-07-29 09:00:02"
    )
    store.defer_unknown_agent_run_reconciliation(
        run.id,
        {"code": "reconciliation_needs_human", "retryable": False},
        owner="reconciler-1",
        expected_execution_generation="initial",
        next_attempt_at="",
        suspended=True,
        now="2026-07-29 09:00:03",
    )

    assert [item.id for item in store.list_suspended_unknown_agent_runs(limit=10)] == [
        run.id
    ]
    assert store.list_unknown_agent_runs(now="2026-07-29 09:00:04") == []
    with pytest.raises(ValueError, match="reconciliation required"):
        store.rotate_reply_task_execution_generation(task_id)


def test_manual_reconciliation_closes_failed_run_after_external_effect_is_confirmed(
    tmp_path: Path,
):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    task_id = _enqueue_universal_reply_task(store)
    task = store.get_reply_task(task_id)
    assert task is not None
    run = _claim_audit_run(store, task_id, task.execution_generation, owner="worker").run
    store.fail_agent_run(
        run.id,
        {"code": "codex_result_invalid", "retryable": False},
        owner="worker",
    )
    store.finalize_reply_task_without_run(
        task_id=task_id,
        expected_execution_generation=task.execution_generation,
        task_status="failed",
        task_error="codex_result_invalid",
        available_at="",
        conversation_id=task.conversation_id,
        conversation_title=task.conversation_title,
        trigger_message_id=task.trigger_message_id,
        trigger_sender=task.trigger_sender,
        trigger_text=task.trigger_text,
        codex_reason="codex_result_invalid",
        audit_summary="codex_result_invalid",
        send_status="failed",
        send_error="codex_result_invalid",
        channel=task.channel,
    )

    resolved = store.resolve_agent_run_manually(
        run.id,
        expected_execution_generation=task.execution_generation,
        resolution="confirmed_occurred",
        reason="已从外部系统读回并确认动作完成",
        actor="Derek",
    )

    persisted_run = store.get_agent_run(run.id)
    persisted_task = store.get_reply_task(task_id)
    attempt = store.get_reply_attempt(resolved.attempt_id)
    assert persisted_run is not None and persisted_run.status == "completed"
    assert persisted_run.side_effect_state == "confirmed"
    assert persisted_task is not None and persisted_task.status == "done"
    assert attempt is not None and attempt.send_status == "completed"


def test_manual_reconciliation_cannot_mark_failed_run_without_effect_as_completed(
    tmp_path: Path,
):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    task_id = _enqueue_universal_reply_task(store)
    task = store.get_reply_task(task_id)
    assert task is not None
    run = _claim_audit_run(store, task_id, task.execution_generation, owner="worker").run
    store.fail_agent_run(
        run.id,
        {"code": "codex_result_invalid", "retryable": False},
        owner="worker",
    )
    store.finalize_reply_task_without_run(
        task_id=task_id,
        expected_execution_generation=task.execution_generation,
        task_status="failed",
        task_error="codex_result_invalid",
        available_at="",
        conversation_id=task.conversation_id,
        conversation_title=task.conversation_title,
        trigger_message_id=task.trigger_message_id,
        trigger_sender=task.trigger_sender,
        trigger_text=task.trigger_text,
        codex_reason="codex_result_invalid",
        audit_summary="codex_result_invalid",
        send_status="failed",
        send_error="codex_result_invalid",
        channel=task.channel,
    )

    with pytest.raises(AgentRunLeaseLostError, match="manual reconciliation target is stale"):
        store.resolve_agent_run_manually(
            run.id,
            expected_execution_generation=task.execution_generation,
            resolution="confirmed_not_occurred",
            reason="没有可读回的外部动作",
            actor="Derek",
        )


def test_manual_resolution_rolls_back_run_task_and_attempt_on_insert_failure(
    tmp_path: Path, monkeypatch
):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    task_id = _enqueue_universal_reply_task(store)
    run = _claim_audit_run(store,
        task_id, "initial", owner="worker-1", now="2026-07-29 09:00:00"
    ).run
    store.mark_agent_run_unknown(
        run.id,
        {"code": "effect_completion_missing"},
        owner="worker-1",
        now="2026-07-29 09:00:01",
    )
    store.claim_unknown_agent_run(
        run.id, owner="reconciler-1", now="2026-07-29 09:00:02"
    )
    store.defer_unknown_agent_run_reconciliation(
        run.id,
        {"code": "reconciliation_needs_human", "retryable": False},
        owner="reconciler-1",
        expected_execution_generation="initial",
        next_attempt_at="",
        suspended=True,
        now="2026-07-29 09:00:03",
    )
    before_attempts = store.count_reply_attempts()
    monkeypatch.setattr(
        store,
        "_insert_reconciliation_attempt_in_connection",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            sqlite3.IntegrityError("forced attempt failure")
        ),
    )

    with pytest.raises(sqlite3.IntegrityError, match="forced attempt failure"):
        store.resolve_agent_run_manually(
            run.id,
            expected_execution_generation="initial",
            resolution="confirmed_occurred",
            reason="人工确认",
            actor="Derek",
            now="2026-07-29 09:00:04",
        )

    unchanged_run = store.get_agent_run(run.id)
    unchanged_task = store.get_reply_task(task_id)
    assert unchanged_run is not None and unchanged_run.status == "unknown"
    assert unchanged_run.reconciliation_suspended is True
    assert unchanged_task is not None and unchanged_task.status == "processing"
    assert store.count_reply_attempts() == before_attempts


@pytest.mark.parametrize("outcome", ["confirmed", "absent"])
def test_automatic_reconciliation_rolls_back_terminal_state_when_attempt_fails(
    tmp_path: Path, monkeypatch, outcome: str
):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    task_id = _enqueue_universal_reply_task(store)
    run = _claim_audit_run(store,
        task_id, "initial", owner="worker-1", now="2026-07-29 09:00:00"
    ).run
    store.mark_agent_run_unknown(
        run.id,
        {"code": "effect_completion_missing"},
        owner="worker-1",
        now="2026-07-29 09:00:01",
    )
    store.claim_unknown_agent_run(
        run.id, owner="reconciler-1", now="2026-07-29 09:00:02"
    )
    monkeypatch.setattr(
        store,
        "_insert_reconciliation_attempt_in_connection",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            sqlite3.IntegrityError("forced attempt failure")
        ),
    )

    with pytest.raises(sqlite3.IntegrityError, match="forced attempt failure"):
        if outcome == "confirmed":
            store.resolve_unknown_agent_run_confirmed(
                run.id,
                task_id,
                {"outcome": "completed", "summary": "confirmed"},
                owner="reconciler-1",
                now="2026-07-29 09:00:03",
            )
        else:
            store.resolve_unknown_agent_run_absent(
                run.id,
                task_id,
                code="reconciliation_confirmed_no_effect",
                owner="reconciler-1",
                now="2026-07-29 09:00:03",
            )

    unchanged_run = store.get_agent_run(run.id)
    unchanged_task = store.get_reply_task(task_id)
    assert unchanged_run is not None and unchanged_run.status == "unknown"
    assert unchanged_run.lease_owner == "reconciler-1"
    assert unchanged_task is not None and unchanged_task.status == "processing"
    assert unchanged_task.execution_generation == "initial"
    assert store.count_reply_attempts() == 0


def test_reply_task_state_writes_reject_stale_generation_before_run_creation(
    tmp_path: Path,
):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    task_id = _enqueue_universal_reply_task(store)
    new_generation = store.rotate_reply_task_execution_generation(task_id)

    for operation in (
        lambda: store.requeue_reply_task(
            task_id, "old context failure", expected_execution_generation="initial"
        ),
        lambda: store.fail_reply_task(
            task_id, "old authorization failure", expected_execution_generation="initial"
        ),
        lambda: store.defer_reply_task(
            task_id, "old active run", expected_execution_generation="initial"
        ),
        lambda: store.complete_reply_task(
            task_id, expected_execution_generation="initial"
        ),
    ):
        with pytest.raises(AgentRunLeaseLostError):
            operation()

    task = store.get_reply_task(task_id)
    assert task is not None
    assert task.execution_generation == new_generation
    assert task.status == "pending"
    assert task.locked_at is None
    assert task.error == "execution_generation_rotated"


def test_completed_reconciliation_atomically_finishes_processing_task(
    tmp_path: Path,
):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    task_id = _enqueue_universal_reply_task(store)
    run = _claim_audit_run(store,
        task_id, "initial", owner="worker-1", now="2026-07-29 09:00:00"
    ).run
    store.mark_agent_run_unknown(
        run.id,
        {"code": "effect_completion_missing"},
        owner="worker-1",
        now="2026-07-29 09:00:01",
    )
    store.claim_unknown_agent_run(
        run.id,
        owner="reconciler-1",
        now="2026-07-29 09:00:02",
    )
    store.resolve_unknown_agent_run_confirmed(
        run.id,
        task_id,
        {
            "outcome": "completed",
            "summary": "effect confirmed",
            "proof": {"observed_state": "completed"},
        },
        owner="reconciler-1",
        now="2026-07-29 09:00:03",
    )

    assert store.get_reply_task(task_id).status == "done"


def test_unknown_reconciliation_must_finish_before_generation_rotation(
    tmp_path: Path,
):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    task_id = _enqueue_universal_reply_task(store)
    run = _claim_audit_run(store,
        task_id, "initial", owner="worker-1", now="2026-07-29 09:00:00"
    ).run
    store.mark_agent_run_unknown(
        run.id,
        {"code": "effect_completion_missing"},
        owner="worker-1",
        now="2026-07-29 09:00:01",
    )
    store.claim_unknown_agent_run(
        run.id,
        owner="reconciler-1",
        now="2026-07-29 09:00:02",
    )
    with pytest.raises(ValueError, match="reconciliation required"):
        store.rotate_reply_task_execution_generation(task_id)

    store.resolve_unknown_agent_run_absent(
        run.id,
        task_id,
        code="reconciliation_confirmed_no_effect",
        owner="reconciler-1",
        now="2026-07-29 09:00:03",
    )

    assert store.get_agent_run(run.id).status == "failed"
    task = store.get_reply_task(task_id)
    assert task.status == "pending"
    assert task.execution_generation != "initial"


def test_generation_switch_revokes_old_run_write_access_and_only_new_run_claims(
    tmp_path: Path,
):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    task_id = _enqueue_universal_reply_task(store)
    old = _claim_audit_run(store,
        task_id,
        "initial",
        owner="old-worker",
        now="2026-07-29 09:00:00",
    ).run

    new_generation = store.rotate_reply_task_execution_generation(task_id)

    with pytest.raises(AgentRunLeaseLostError):
        store.append_agent_run_event(
            old.id,
            {
                "type": "item.completed",
                "item": {
                    "id": "send-1",
                    "type": "command_execution",
                    "metadata": {"effect": "effectful"},
                },
            },
            owner="old-worker",
            now="2026-07-29 09:00:01",
        )
    with pytest.raises(AgentRunLeaseLostError):
        store.record_agent_execution_receipt(
            old.id,
            receipt_id="receipt-old",
            operation_id="send-1",
            cli="dws",
            command_path="chat message send",
            command_digest="digest-old",
            exit_code=0,
            owner="old-worker",
            now="2026-07-29 09:00:01",
        )
    with pytest.raises(AgentRunLeaseLostError):
        store.complete_agent_run(
            old.id,
            {"outcome": "completed"},
            owner="old-worker",
            side_effect_state="confirmed",
            now="2026-07-29 09:00:01",
        )

    superseded = store.get_agent_run(old.id)
    assert superseded is not None
    assert superseded.status == "failed"
    assert "superseded" in superseded.structured_error_json
    assert superseded.lease_owner == ""
    assert superseded.lease_expires_at == ""

    claimed_task = store.claim_reply_task(
        task_id,
        now="2026-07-29 09:00:01",
    )
    assert claimed_task is not None
    new_claim = _claim_audit_run(store,
        task_id,
        new_generation,
        owner="new-worker",
        now="2026-07-29 09:00:01",
    )
    assert new_claim.claimed is True
    assert new_claim.run.execution_generation == new_generation


def test_rotation_request_keeps_unknown_run_due_and_claimable(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    task_id = _enqueue_universal_reply_task(store)
    run = _claim_audit_run(store,
        task_id, "initial", owner="worker-1", now="2026-07-29 09:00:00"
    ).run
    store.mark_agent_run_unknown(
        run.id,
        {"code": "effect_completion_missing"},
        owner="worker-1",
        now="2026-07-29 09:00:01",
    )
    with pytest.raises(ValueError, match="reconciliation required"):
        store.rotate_reply_task_execution_generation(task_id)
    before = store.get_agent_run(run.id)

    due = store.list_unknown_agent_runs(now="2026-07-29 09:00:02")
    claim = store.claim_unknown_agent_run(
        run.id,
        owner="reconciler-1",
        now="2026-07-29 09:00:02",
    )

    assert [item.id for item in due] == [run.id]
    assert claim.claimed is True
    assert claim.run.execution_generation == "initial"
    assert before is not None and before.execution_generation == "initial"


def test_manual_rerun_waits_for_running_unknown_effect(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    task_id = _enqueue_universal_reply_task(store)
    attempt_id = store.record_reply_attempt(
        conversation_id="cid-universal",
        conversation_title="Universal",
        trigger_message_id="msg-universal",
        trigger_sender="ET",
        trigger_text="请处理",
        action="agent_run",
        sensitivity_kind="general",
        send_status="failed",
    )
    run = _claim_audit_run(store,
        task_id,
        "initial",
        owner="worker-1",
        now="2026-07-29 09:00:00",
    ).run
    store.append_agent_run_event(
        run.id,
        {
            "type": "item.started",
            "item": {
                "id": "send-1",
                "type": "command_execution",
                "metadata": {"effect": "effectful"},
            },
        },
        owner="worker-1",
        now="2026-07-29 09:00:01",
    )

    with pytest.raises(ValueError, match="reconciliation required"):
        store.enqueue_manual_rerun_reply_task(
            conversation_id="cid-universal",
            conversation_title="Universal",
            single_chat=False,
            trigger_message_id="msg-universal",
            trigger_create_time="2026-07-29 09:00:00",
            trigger_sender="ET",
            trigger_text="请处理",
            trigger_message_json="{}",
            attempt_id=attempt_id,
        )

    task = store.get_reply_task(task_id)
    assert task is not None and task.execution_generation == "initial"
    assert store.get_agent_run(run.id).status == "unknown"


def test_reviewed_rerun_does_not_persist_instruction_before_reconciliation(
    tmp_path: Path,
):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    task_id = _enqueue_universal_reply_task(store)
    run = _claim_audit_run(store,
        task_id,
        "initial",
        owner="worker-1",
        now="2026-07-29 09:00:00",
    ).run
    store.append_agent_run_event(
        run.id,
        {
            "type": "item.started",
            "item": {
                "id": "send-1",
                "type": "command_execution",
                "metadata": {"effect": "effectful"},
            },
        },
        owner="worker-1",
        now="2026-07-29 09:00:01",
    )

    with pytest.raises(ValueError, match="reconciliation required"):
        store.record_reviewed_reply_rerun(
            conversation_id="cid-universal",
            conversation_title="Universal",
            single_chat=False,
            trigger_message_id="msg-universal",
            trigger_create_time="2026-07-29 09:00:00",
            trigger_sender="ET",
            trigger_text="请处理",
            trigger_message_json="{}",
            suggested_reply_text="修正版",
            reviewer_feedback="请修正",
        )

    assert store.count_reply_attempts() == 0
    assert store.get_agent_run(run.id).status == "unknown"


def test_unknown_event_append_is_bounded_and_does_not_reload_agent_run(
    tmp_path: Path, monkeypatch
):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    task_id = _enqueue_universal_reply_task(store)
    run = _claim_audit_run(store, task_id, "initial", owner="worker-1").run
    store.mark_agent_run_unknown(
        run.id,
        {"code": "effect_completion_missing"},
        owner="worker-1",
    )
    store.claim_unknown_agent_run(run.id, owner="reconciler-1")

    assert (
        store.append_unknown_agent_run_event(
            run.id,
            {"type": "item.completed", "item": {"id": "q1"}},
            owner="reconciler-1",
        )
        is None
    )
    monkeypatch.setattr("app.store.MAX_RECONCILIATION_EVENTS", 1)
    with pytest.raises(ValueError, match="reconciliation event limit exceeded"):
        store.append_unknown_agent_run_event(
            run.id,
            {"type": "item.completed", "item": {"id": "q2"}},
            owner="reconciler-1",
        )
    monkeypatch.setattr("app.store.MAX_RECONCILIATION_EVENTS", 256)
    with pytest.raises(ValueError, match="agent run event exceeds size limit"):
        store.append_unknown_agent_run_event(
            run.id,
            {"type": "item.completed", "item": {"output": "x" * (256 * 1024)}},
            owner="reconciler-1",
        )


def test_reconciliation_event_limit_excludes_direct_run_history(
    tmp_path: Path, monkeypatch
):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    task_id = _enqueue_universal_reply_task(store)
    run = _claim_audit_run(store, task_id, "initial", owner="worker-1").run
    for index in range(256):
        store.append_agent_run_event(
            run.id,
            {
                "type": "item.completed",
                "item": {"id": f"direct-{index}"},
                "_ceo_event_scope": "reconciliation",
            },
            owner="worker-1",
        )
    store.mark_agent_run_unknown(
        run.id,
        {"code": "effect_completion_missing"},
        owner="worker-1",
    )
    store.claim_unknown_agent_run(run.id, owner="reconciler-1")
    monkeypatch.setattr("app.store.MAX_RECONCILIATION_EVENTS", 1)

    store.append_unknown_agent_run_event(
        run.id,
        {"type": "item.completed", "item": {"id": "reconcile-1"}},
        owner="reconciler-1",
    )
    with pytest.raises(ValueError, match="reconciliation event limit exceeded"):
        store.append_unknown_agent_run_event(
            run.id,
            {"type": "item.completed", "item": {"id": "reconcile-2"}},
            owner="reconciler-1",
        )


def test_reconciliation_event_count_uses_run_scope_composite_index(tmp_path: Path):
    db_path = tmp_path / "worker.sqlite3"
    AutoReplyStore(db_path)

    with sqlite3.connect(db_path) as db:
        indexes = {
            row[1]
            for row in db.execute("pragma index_list(agent_run_events)").fetchall()
        }
        plan = db.execute(
            "explain query plan select count(*) from agent_run_events "
            "where agent_run_id=? and event_scope='reconciliation'",
            (1,),
        ).fetchall()

    assert "idx_agent_run_events_run_scope" in indexes
    assert any(
        "USING COVERING INDEX idx_agent_run_events_run_scope" in row[3]
        for row in plan
    ), plan


def test_legacy_agent_run_events_adds_scope_before_index_and_is_idempotent(
    tmp_path: Path,
):
    db_path = tmp_path / "worker.sqlite3"
    with sqlite3.connect(db_path) as db:
        db.executescript(
            """
            create table agent_run_events (
                id integer primary key autoincrement,
                agent_run_id integer not null,
                sequence integer not null,
                event_json text not null,
                event_type text not null default '',
                call_id text not null default '',
                effect_kind text not null default '',
                receipt_operation_id text not null default '',
                created_at text not null default current_timestamp,
                unique(agent_run_id, sequence)
            );
            insert into agent_run_events (
                agent_run_id, sequence, event_json, event_type
            ) values (7, 1, '{"type":"item.completed"}', 'item.completed');
            """
        )

    store = AutoReplyStore(db_path)
    store._initialize()

    with sqlite3.connect(db_path) as db:
        columns = {
            row[1] for row in db.execute("pragma table_info(agent_run_events)")
        }
        indexes = {
            row[1] for row in db.execute("pragma index_list(agent_run_events)")
        }
        preserved = db.execute(
            "select agent_run_id, sequence, event_json, event_scope "
            "from agent_run_events"
        ).fetchall()
        plan = db.execute(
            "explain query plan select count(*) from agent_run_events "
            "where agent_run_id=? and event_scope='reconciliation'",
            (7,),
        ).fetchall()

    assert "event_scope" in columns
    assert "idx_agent_run_events_run_scope" in indexes
    assert preserved == [(7, 1, '{"type":"item.completed"}', "direct")]
    assert any(
        "USING COVERING INDEX idx_agent_run_events_run_scope" in row[3]
        for row in plan
    ), plan


def test_get_agent_run_for_turn_returns_exact_row(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    task_id = _enqueue_universal_reply_task(store)
    claimed = _claim_audit_run(store, task_id, "initial", owner="worker-1")

    loaded = _get_audit_run(store, task_id, "initial")

    assert loaded == claimed.run
    assert _get_audit_run(store, task_id, "missing") is None


def test_claim_reply_tasks_marks_tasks_processing_atomically(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    store.enqueue_reply_task(
        conversation_id="cid-1",
        conversation_title="Friday",
        single_chat=False,
        trigger_message_id="msg-1",
        trigger_create_time="2026-05-13 18:00:00",
        trigger_sender="Mina",
        trigger_text="@Alex Chen 看一下",
    )

    claimed = store.claim_reply_tasks(limit=1)
    second_claim = store.claim_reply_tasks(limit=1)

    assert len(claimed) == 1
    assert claimed[0].conversation_id == "cid-1"
    assert claimed[0].trigger_message_id == "msg-1"
    assert claimed[0].status == "processing"
    assert claimed[0].attempts == 1
    assert second_claim == []
    assert store.count_reply_tasks(status="pending") == 0
    assert store.count_reply_tasks(status="processing") == 1


def test_peek_reply_tasks_does_not_claim_or_increment_attempts(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    store.enqueue_reply_task(
        conversation_id="cid-1",
        conversation_title="Friday",
        single_chat=False,
        trigger_message_id="msg-1",
        trigger_create_time="2026-05-13 18:00:00",
        trigger_sender="Derek",
        trigger_text="read this",
        trigger_message_json="{}",
    )

    peeked = store.peek_reply_tasks(limit=1, now="2026-05-13 18:01:00")
    task_id = peeked[0].id

    task = store.get_reply_task(task_id)
    assert task is not None
    assert task.status == "pending"
    assert task.attempts == 0


def test_peek_reply_tasks_pages_after_id_without_claiming(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    for index in range(3):
        store.enqueue_reply_task(
            conversation_id=f"cid-{index}",
            conversation_title="Friday",
            single_chat=False,
            trigger_message_id=f"msg-{index}",
            trigger_create_time=f"2026-05-13 18:00:0{index}",
            trigger_sender="Derek",
            trigger_text=str(index),
            trigger_message_json="{}",
        )

    first_page = store.peek_reply_tasks(
        limit=2, now="2026-05-13 18:01:00"
    )
    second_page = store.peek_reply_tasks(
        limit=2,
        now="2026-05-13 18:01:00",
        after_id=first_page[-1].id,
    )

    assert [task.trigger_message_id for task in first_page] == ["msg-0", "msg-1"]
    assert [task.trigger_message_id for task in second_page] == ["msg-2"]
    assert store.count_reply_tasks(status="pending") == 3


def test_peek_reply_tasks_respects_pending_snapshot_upper_bound(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    for index in range(3):
        store.enqueue_reply_task(
            conversation_id=f"cid-{index}",
            conversation_title="Friday",
            single_chat=False,
            trigger_message_id=f"msg-{index}",
            trigger_create_time=f"2026-05-13 18:00:0{index}",
            trigger_sender="Derek",
            trigger_text=str(index),
            trigger_message_json="{}",
        )
    max_id = store.max_pending_reply_task_id(
        now="2026-05-13 18:01:00",
        channel="dingtalk",
    )
    assert max_id is not None
    store.enqueue_reply_task(
        conversation_id="cid-new",
        conversation_title="Friday",
        single_chat=False,
        trigger_message_id="msg-new",
        trigger_create_time="2026-05-13 18:00:03",
        trigger_sender="Derek",
        trigger_text="new",
        trigger_message_json="{}",
    )

    snapshot = store.peek_reply_tasks(
        limit=10,
        now="2026-05-13 18:01:00",
        channel="dingtalk",
        max_id=max_id,
    )

    assert [task.trigger_message_id for task in snapshot] == [
        "msg-0",
        "msg-1",
        "msg-2",
    ]


def test_claim_reply_task_claims_only_requested_pending_task(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    for index in (1, 2):
        store.enqueue_reply_task(
            conversation_id=f"cid-{index}",
            conversation_title="Friday",
            single_chat=False,
            trigger_message_id=f"msg-{index}",
            trigger_create_time=f"2026-05-13 18:00:0{index}",
            trigger_sender="Derek",
            trigger_text=str(index),
            trigger_message_json="{}",
        )
    first, second = store.peek_reply_tasks(limit=2, now="2026-05-13 18:01:00")

    claimed = store.claim_reply_task(second.id, now="2026-05-13 18:01:00")

    assert claimed is not None
    assert claimed.id == second.id
    assert claimed.status == "processing"
    assert claimed.attempts == 1
    unchanged = store.get_reply_task(first.id)
    assert unchanged is not None
    assert unchanged.status == "pending"
    assert unchanged.attempts == 0


def test_claim_reply_tasks_waits_until_available_at(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    store.enqueue_reply_task(
        conversation_id="cid-1",
        conversation_title="Friday",
        single_chat=False,
        trigger_message_id="msg-1",
        trigger_create_time="2026-05-13 18:00:00",
        trigger_sender="Mina",
        trigger_text="@Alex Chen 看一下",
        available_at="2026-05-13 17:05:00",
        error="waiting_fast_path_unread_backoff",
    )

    before = store.claim_reply_tasks(limit=1, now="2026-05-13 17:04:59")
    after = store.claim_reply_tasks(limit=1, now="2026-05-13 17:05:00")

    assert before == []
    assert len(after) == 1
    assert after[0].status == "processing"
    assert after[0].available_at == ""
    assert after[0].error == "waiting_fast_path_unread_backoff"


def test_requeue_reply_task_can_delay_next_claim(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    store.enqueue_reply_task(
        conversation_id="cid-1",
        conversation_title="Friday",
        single_chat=False,
        trigger_message_id="msg-1",
        trigger_create_time="2026-05-13 18:00:00",
        trigger_sender="Mina",
        trigger_text="@Alex Chen 看一下",
    )
    claimed = store.claim_reply_tasks(limit=1, now="2026-05-13 17:00:00")

    store.requeue_reply_task(
        claimed[0].id,
        "temporary failure",
        expected_execution_generation=claimed[0].execution_generation,
        available_at="2026-05-13 17:01:00",
    )

    before = store.claim_reply_tasks(limit=1, now="2026-05-13 17:00:59")
    after = store.claim_reply_tasks(limit=1, now="2026-05-13 17:01:00")

    assert before == []
    assert len(after) == 1
    assert after[0].attempts == 2
    assert after[0].available_at == ""
    assert after[0].error == "temporary failure"


def test_complete_reply_task_marks_generation_bound_task_done(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    store.enqueue_reply_task(
        conversation_id="cid-1",
        conversation_title="Friday",
        single_chat=False,
        trigger_message_id="msg-1",
        trigger_create_time="2026-05-13 18:00:00",
        trigger_sender="Mina",
        trigger_text="@Alex Chen 看一下",
    )
    claimed = store.claim_reply_tasks(limit=1)[0]
    store.complete_reply_task(
        claimed.id,
        expected_execution_generation=claimed.execution_generation,
    )

    tasks = store.list_reply_tasks(limit=1)
    assert tasks[0].status == "done"
    assert tasks[0].error == ""


def test_list_reply_tasks_filters_statuses_newest_first(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    store.enqueue_reply_task(
        conversation_id="cid-1",
        conversation_title="Friday",
        single_chat=False,
        trigger_message_id="msg-1",
        trigger_create_time="2026-05-13 18:00:00",
        trigger_sender="Mina",
        trigger_text="@Alex Chen 看一下",
    )
    store.enqueue_reply_task(
        conversation_id="cid-2",
        conversation_title="HR管理",
        single_chat=False,
        trigger_message_id="msg-2",
        trigger_create_time="2026-05-13 18:01:00",
        trigger_sender="Phina",
        trigger_text="@Alex Chen 再看一下",
    )
    claimed = store.claim_reply_tasks(limit=1)
    store.complete_reply_task(
        claimed[0].id,
        expected_execution_generation=claimed[0].execution_generation,
    )

    tasks = store.list_reply_tasks(statuses=("pending", "processing", "failed"))

    assert [task.trigger_message_id for task in tasks] == ["msg-2"]


def test_requeue_reply_task_keeps_attempt_count_for_retry(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    store.enqueue_reply_task(
        conversation_id="cid-1",
        conversation_title="Friday",
        single_chat=False,
        trigger_message_id="msg-1",
        trigger_create_time="2026-05-13 18:00:00",
        trigger_sender="Mina",
        trigger_text="@Alex Chen 看一下",
    )
    claimed = store.claim_reply_tasks(limit=1)

    store.requeue_reply_task(
        claimed[0].id,
        "temporary dws auth failure",
        expected_execution_generation=claimed[0].execution_generation,
    )
    reclaimed = store.claim_reply_tasks(limit=1)

    assert reclaimed[0].id == claimed[0].id
    assert reclaimed[0].attempts == 2
    assert reclaimed[0].error == "temporary dws auth failure"


def test_defer_reply_task_for_authorization_refunds_claim_attempt(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    store.enqueue_reply_task(
        conversation_id="cid-1",
        conversation_title="Friday",
        single_chat=False,
        trigger_message_id="msg-1",
        trigger_create_time="2026-05-13 18:00:00",
        trigger_sender="Mina",
        trigger_text="@Alex Chen 看一下",
    )
    claimed = store.claim_reply_tasks(limit=1)

    store.defer_reply_task_for_authorization(
        claimed[0].id,
        "authorization required",
        expected_execution_generation=claimed[0].execution_generation,
    )
    reclaimed = store.claim_reply_tasks(limit=1)

    assert reclaimed[0].id == claimed[0].id
    assert reclaimed[0].attempts == 1
    assert reclaimed[0].error == "authorization required"


def test_create_and_claim_okr_review_request(tmp_path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    request_id = store.create_okr_review_request(
        conversation_id="cid-1",
        conversation_title="韩露",
        trigger_message_id="msg-1",
        trigger_sender="韩露",
        trigger_sender_user_id="user-1",
        trigger_text="帮我审核 OKR",
        period_label="2026 Q2",
        period_start="2026-04-01",
        period_end="2026-06-30",
        okr_source_json='{"objectives":[]}',
    )

    claimed = store.claim_okr_review_requests(limit=1)

    assert [item.id for item in claimed] == [request_id]
    assert claimed[0].status == "processing"


def test_recreating_okr_review_request_requeues_failed_request(tmp_path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    request_id = store.create_okr_review_request(
        conversation_id="cid-1",
        conversation_title="韩露",
        trigger_message_id="msg-1",
        trigger_sender="韩露",
        trigger_sender_user_id="user-1",
        trigger_text="帮我审核 OKR",
        period_label="2026 Q2",
        period_start="2026-04-01",
        period_end="2026-06-30",
        okr_source_json='{"objectives":[]}',
    )
    store.mark_okr_review_request_failed(request_id, "source unavailable")

    recreated_id = store.create_okr_review_request(
        conversation_id="cid-1",
        conversation_title="韩露",
        trigger_message_id="msg-1",
        trigger_sender="韩露",
        trigger_sender_user_id="user-1",
        trigger_text="帮我审核 OKR",
        period_label="2026 Q2",
        period_start="2026-04-01",
        period_end="2026-06-30",
        okr_source_json='{"processed":{"okrRows":[]}}',
    )

    assert recreated_id == request_id
    loaded = store.get_okr_review_request(request_id)
    assert loaded.status == "pending"
    assert loaded.error == ""
    assert loaded.codex_session_id == ""
    assert json.loads(loaded.okr_source_json)["processed"]["okrRows"] == []


def test_recreating_okr_review_request_does_not_requeue_done_request(tmp_path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    request_id = store.create_okr_review_request(
        conversation_id="cid-1",
        conversation_title="韩露",
        trigger_message_id="msg-1",
        trigger_sender="韩露",
        trigger_sender_user_id="user-1",
        trigger_text="帮我审核 OKR",
        period_label="2026 Q2",
        period_start="2026-04-01",
        period_end="2026-06-30",
        okr_source_json='{"objectives":[]}',
    )
    store.mark_okr_review_request_done(request_id, codex_session_id="session-1")

    recreated_id = store.create_okr_review_request(
        conversation_id="cid-1",
        conversation_title="韩露",
        trigger_message_id="msg-1",
        trigger_sender="韩露",
        trigger_sender_user_id="user-1",
        trigger_text="帮我审核 OKR",
        period_label="2026 Q2",
        period_start="2026-04-01",
        period_end="2026-06-30",
        okr_source_json='{"processed":{"okrRows":[]}}',
    )

    assert recreated_id == request_id
    loaded = store.get_okr_review_request(request_id)
    assert loaded.status == "done"
    assert loaded.codex_session_id == "session-1"
    assert json.loads(loaded.okr_source_json)["objectives"] == []


def test_recreating_okr_review_request_does_not_reset_processing_request(tmp_path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    request_id = store.create_okr_review_request(
        conversation_id="cid-1",
        conversation_title="韩露",
        trigger_message_id="msg-1",
        trigger_sender="韩露",
        trigger_sender_user_id="user-1",
        trigger_text="帮我审核 OKR",
        period_label="2026 Q2",
        period_start="2026-04-01",
        period_end="2026-06-30",
        okr_source_json='{"objectives":[]}',
    )
    claimed = store.claim_okr_review_requests(limit=1)

    recreated_id = store.create_okr_review_request(
        conversation_id="cid-1",
        conversation_title="韩露",
        trigger_message_id="msg-1",
        trigger_sender="韩露",
        trigger_sender_user_id="user-1",
        trigger_text="帮我审核 OKR",
        period_label="2026 Q2",
        period_start="2026-04-01",
        period_end="2026-06-30",
        okr_source_json='{"processed":{"okrRows":[]}}',
    )

    assert [item.id for item in claimed] == [request_id]
    assert recreated_id == request_id
    loaded = store.get_okr_review_request(request_id)
    assert loaded.status == "processing"
    assert json.loads(loaded.okr_source_json)["objectives"] == []


def test_reset_recoverable_okr_review_requests_requeues_stale_processing(
    tmp_path: Path,
):
    db_path = tmp_path / "worker.sqlite3"
    store = AutoReplyStore(db_path)
    request_id = store.create_okr_review_request(
        conversation_id="cid-1",
        conversation_title="卢鑫",
        trigger_message_id="msg-1",
        trigger_sender="卢鑫",
        trigger_sender_user_id="user-1",
        trigger_text="查一下我的评分",
        period_label="2026 Q3",
        period_start="2026-07-01",
        period_end="2026-09-30",
        okr_source_json='{"objectives":[]}',
    )
    claimed = store.claim_okr_review_requests(limit=1)[0]
    assert store.acquire_codex_session_lock("cid-1", f"okr_review:{request_id}")
    with sqlite3.connect(db_path) as db:
        db.execute(
            "update okr_review_requests set updated_at=datetime('now', '-31 minutes') where id=?",
            (request_id,),
        )

    recovered = store.reset_recoverable_okr_review_requests(
        processing_max_age_seconds=30 * 60
    )

    assert [request.id for request in recovered] == [claimed.id]
    loaded = store.get_okr_review_request(request_id)
    assert loaded.status == "pending"
    assert loaded.error == ""
    assert store.acquire_codex_session_lock("cid-1", "reply:msg-1")


def test_reset_recoverable_okr_review_requests_keeps_fresh_processing(
    tmp_path: Path,
):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    request_id = store.create_okr_review_request(
        conversation_id="cid-1",
        conversation_title="卢鑫",
        trigger_message_id="msg-1",
        trigger_sender="卢鑫",
        trigger_sender_user_id="user-1",
        trigger_text="查一下我的评分",
        period_label="2026 Q3",
        period_start="2026-07-01",
        period_end="2026-09-30",
        okr_source_json='{"objectives":[]}',
    )
    store.claim_okr_review_requests(limit=1)

    recovered = store.reset_recoverable_okr_review_requests(
        processing_max_age_seconds=30 * 60
    )

    assert recovered == []
    assert store.get_okr_review_request(request_id).status == "processing"


def test_reset_recoverable_okr_review_requests_requeues_stale_lock_failure(
    tmp_path: Path,
):
    db_path = tmp_path / "worker.sqlite3"
    store = AutoReplyStore(db_path)
    request_id = store.create_okr_review_request(
        conversation_id="cid-1",
        conversation_title="卢鑫",
        trigger_message_id="msg-1",
        trigger_sender="卢鑫",
        trigger_sender_user_id="user-1",
        trigger_text="再查一下我的评分",
        period_label="2026 Q3",
        period_start="2026-07-01",
        period_end="2026-09-30",
        okr_source_json='{"objectives":[]}',
    )
    store.mark_okr_review_request_failed(request_id, "codex session locked: cid-1")

    recovered = store.reset_recoverable_okr_review_requests(
        processing_max_age_seconds=30 * 60
    )

    assert [request.id for request in recovered] == [request_id]
    loaded = store.get_okr_review_request(request_id)
    assert loaded.status == "pending"
    assert loaded.error == ""


def test_reset_recoverable_okr_review_requests_keeps_fresh_lock_failure(
    tmp_path: Path,
):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    request_id = store.create_okr_review_request(
        conversation_id="cid-1",
        conversation_title="卢鑫",
        trigger_message_id="msg-1",
        trigger_sender="卢鑫",
        trigger_sender_user_id="user-1",
        trigger_text="再查一下我的评分",
        period_label="2026 Q3",
        period_start="2026-07-01",
        period_end="2026-09-30",
        okr_source_json='{"objectives":[]}',
    )
    store.mark_okr_review_request_failed(request_id, "codex session locked: cid-1")
    assert store.acquire_codex_session_lock("cid-1", "okr_review:other")

    recovered = store.reset_recoverable_okr_review_requests(
        processing_max_age_seconds=30 * 60
    )

    assert recovered == []
    assert store.get_okr_review_request(request_id).status == "failed"


def test_record_okr_review_run_and_items(tmp_path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    request_id = store.create_okr_review_request(
        conversation_id="cid-1",
        conversation_title="韩露",
        trigger_message_id="msg-1",
        trigger_sender="韩露",
        trigger_sender_user_id="user-1",
        trigger_text="帮我审核 OKR",
        period_label="2026 Q2",
        period_start="2026-04-01",
        period_end="2026-06-30",
        okr_source_json='{"objectives":[]}',
    )
    run_id = store.record_okr_review_run(
        request_id=request_id,
        codex_session_id="session-1",
        codex_transcript_start_line=1,
        codex_transcript_end_line=10,
        envelope_json='{"kind":"okr_review"}',
        audit_tool_events_json='[]',
        audit_summary="审核完成。",
    )
    item_id = store.record_okr_review_item(
        request_id=request_id,
        objective_title="O",
        objective_weight=1.0,
        kr_title="KR",
        kr_weight=0.5,
        item_json='{"kr_title":"KR"}',
    )
    store.mark_okr_review_request_done(request_id, codex_session_id="session-1")

    loaded = store.get_okr_review_request(request_id)
    assert loaded.status == "done"
    assert run_id > 0
    assert item_id > 0


def test_create_okr_review_request_requires_source_json(tmp_path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")

    with pytest.raises(TypeError):
        store.create_okr_review_request(
            conversation_id="cid-1",
            conversation_title="韩露",
            trigger_message_id="msg-1",
            trigger_sender="韩露",
            trigger_sender_user_id="user-1",
            trigger_text="帮我审核 OKR",
            period_label="2026 Q2",
            period_start="2026-04-01",
            period_end="2026-06-30",
        )


def test_record_okr_review_run_requires_audit_fields(tmp_path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")

    with pytest.raises(TypeError):
        store.record_okr_review_run(
            request_id=1,
            codex_session_id="session-1",
            codex_transcript_start_line=1,
            codex_transcript_end_line=10,
            audit_tool_events_json="[]",
        )


def test_record_okr_review_item_requires_item_json(tmp_path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")

    with pytest.raises(TypeError):
        store.record_okr_review_item(
            request_id=1,
            objective_title="O",
            objective_weight=1.0,
            kr_title="KR",
            kr_weight=0.5,
        )


def test_reset_codex_sessions_clears_conversation_mapping_only(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    store.upsert_conversation("cid-1", "Friday", False, "session-1")
    attempt_id = store.record_reply_attempt(
        conversation_id="cid-1",
        conversation_title="Friday",
        trigger_message_id="msg-1",
        trigger_sender="Xiaomin",
        trigger_text="@Alex Chen 这个怎么处理？",
        action="send_reply",
        sensitivity_kind="general",
        codex_session_id="session-1",
        codex_transcript_start_line=3,
        codex_transcript_end_line=9,
    )

    cleared = store.reset_codex_sessions()

    assert cleared == 1
    assert store.get_codex_session_id("cid-1") is None
    attempt = store.get_reply_attempt(attempt_id)
    assert attempt is not None
    assert attempt.codex_session_id == "session-1"
    assert attempt.codex_transcript_start_line == 3
    assert attempt.codex_transcript_end_line == 9


def test_record_reply_attempt_extracts_memory_write_events(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    memory_output = {
        "structured_content": {
            "result": json.dumps(
                {
                    "ok": True,
                    "episode_uuid": "episode-1",
                    "processing_status": "completed",
                }
            )
        }
    }

    attempt_id = store.record_reply_attempt(
        conversation_id="cid-1",
        conversation_title="Friday",
        trigger_message_id="msg-1",
        trigger_sender="Xiaomin",
        trigger_text="记一下这个项目口径",
        action="send_reply",
        sensitivity_kind="general",
        audit_tool_events_json=json.dumps(
            [
                {
                    "event_type": "response_item",
                    "tool": "memory_write",
                    "call_id": "call-1",
                    "input": json.dumps({"data": "stable fact"}),
                    "output": json.dumps(memory_output),
                }
            ]
        ),
    )

    events = store.list_memory_write_events_for_attempt(attempt_id)

    assert len(events) == 1
    assert events[0].status == "written"
    assert events[0].memory_episode_id == "episode-1"


def test_record_reply_attempt_extracts_memory_write_output_from_tool_output(
    tmp_path: Path,
):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    memory_output = {
        "result": json.dumps(
            {
                "ok": True,
                "episode_uuid": "episode-2",
                "processing_status": "pending",
            }
        )
    }

    attempt_id = store.record_reply_attempt(
        conversation_id="cid-1",
        conversation_title="Friday",
        trigger_message_id="msg-1",
        trigger_sender="Xiaomin",
        trigger_text="记一下这个项目口径",
        action="send_reply",
        sensitivity_kind="general",
        audit_tool_events_json=json.dumps(
            [
                {
                    "event_type": "response_item",
                    "tool": "memory_write",
                    "call_id": "call-1",
                    "input": json.dumps({"data": "stable fact"}),
                },
                {
                    "event_type": "response_item",
                    "tool": "tool_output",
                    "call_id": "call-1",
                    "output": "Wall time: 1.1 seconds\nOutput:\n"
                    + json.dumps(memory_output),
                },
            ]
        ),
    )

    events = store.list_memory_write_events_for_attempt(attempt_id)

    assert len(events) == 1
    assert events[0].status == "written"
    assert events[0].memory_episode_id == "episode-2"


def test_record_reply_attempt_ignores_tool_search_memory_write_mentions(
    tmp_path: Path,
):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")

    attempt_id = store.record_reply_attempt(
        conversation_id="cid-1",
        conversation_title="Friday",
        trigger_message_id="msg-1",
        trigger_sender="Xiaomin",
        trigger_text="查一下记忆",
        action="send_reply",
        sensitivity_kind="general",
        audit_tool_events_json=json.dumps(
            [
                {
                    "event_type": "response_item",
                    "tool": "tool_search_call",
                    "call_id": "call-1",
                    "input": json.dumps({"query": "memory_connector memory_write"}),
                }
            ]
        ),
    )

    assert store.list_memory_write_events_for_attempt(attempt_id) == []


def test_reply_attempt_migration_backfills_codex_session_from_conversation(tmp_path: Path):
    db_path = tmp_path / "worker.sqlite3"
    with sqlite3.connect(db_path) as db:
        db.executescript(
            """
            create table conversations (
                conversation_id text primary key,
                title text not null,
                single_chat integer not null,
                codex_session_id text
            );
            create table reply_attempts (
                id integer primary key autoincrement,
                conversation_id text not null,
                conversation_title text not null,
                trigger_message_id text not null,
                trigger_sender text not null,
                trigger_text text not null,
                action text not null,
                sensitivity_kind text not null,
                codex_reason text not null default '',
                draft_reply_text text not null default '',
                audit_documents_json text not null default '[]',
                audit_tool_events_json text not null default '[]',
                audit_summary text not null default '',
                final_reply_text text not null default '',
                permission_action text not null default '',
                permission_reason text not null default '',
                send_status text not null,
                send_error text not null default '',
                retry_count integer not null default 0,
                reviewed_at text,
                reviewer_feedback text not null default '',
                corrected_reply_text text not null default '',
                created_at text not null default current_timestamp,
                updated_at text not null default current_timestamp
            );
            insert into conversations (
                conversation_id, title, single_chat, codex_session_id
            ) values ('cid-1', 'Friday', 0, 'session-1');
            insert into reply_attempts (
                conversation_id, conversation_title, trigger_message_id,
                trigger_sender, trigger_text, action, sensitivity_kind, send_status
            ) values (
                'cid-1', 'Friday', 'msg-1', 'Xiaomin',
                '@Alex Chen 这个怎么处理？', 'send_reply', 'general', 'sent'
            );
            """
        )

    store = AutoReplyStore(db_path)
    attempt = store.get_reply_attempt(1)

    assert attempt is not None
    assert attempt.codex_session_id == "session-1"
    assert attempt.codex_transcript_start_line == 0
    assert attempt.codex_transcript_end_line == 0


def test_reply_attempt_migration_normalizes_authorization_status_to_failed(
    tmp_path: Path,
):
    db_path = tmp_path / "worker.sqlite3"
    with sqlite3.connect(db_path) as db:
        db.executescript(
            """
            create table reply_attempts (
                id integer primary key autoincrement,
                conversation_id text not null,
                conversation_title text not null,
                trigger_message_id text not null,
                trigger_sender text not null,
                trigger_text text not null,
                action text not null,
                sensitivity_kind text not null,
                codex_reason text not null default '',
                draft_reply_text text not null default '',
                audit_documents_json text not null default '[]',
                audit_tool_events_json text not null default '[]',
                audit_summary text not null default '',
                final_reply_text text not null default '',
                permission_action text not null default '',
                permission_reason text not null default '',
                send_status text not null,
                send_error text not null default '',
                retry_count integer not null default 0,
                reviewed_at text,
                reviewer_feedback text not null default '',
                corrected_reply_text text not null default '',
                created_at text not null default current_timestamp,
                updated_at text not null default current_timestamp
            );
            insert into reply_attempts (
                conversation_id, conversation_title, trigger_message_id,
                trigger_sender, trigger_text, action, sensitivity_kind, send_status
            ) values (
                'cid-1', 'Friday', 'msg-1', 'Xiaomin',
                '@Alex Chen 这个怎么处理？', 'send_reply', 'general',
                'needs_authorization'
            );
            """
        )

    store = AutoReplyStore(db_path)
    attempt = store.get_reply_attempt(1)

    assert attempt is not None
    assert attempt.send_status == "failed"


def test_seen_messages_are_deduplicated(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")

    assert store.has_seen("msg-1") is False
    assert store.mark_seen("msg-1", "cid-1") is True
    assert store.has_seen("msg-1") is True
    assert store.mark_seen("msg-1", "cid-1") is False


def test_records_sent_reply_and_error(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")

    store.record_sent_reply(
        "cid-1",
        "msg-1",
        "收到（by明哥分身）",
        send_result_json='{"result":{"processQueryKey":"key-1"}}',
        recall_key="key-1",
    )
    store.record_error("cid-1", "msg-2", "codex_json", "invalid json")
    sent_reply = store.get_sent_reply("cid-1", "msg-1")

    assert store.count_sent_replies() == 1
    assert sent_reply is not None
    assert sent_reply.recall_key == "key-1"
    assert sent_reply.recall_status == ""
    assert store.count_errors() == 1


def test_records_sent_reply_recall_result(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    store.record_sent_reply("cid-1", "msg-1", "收到（by明哥分身）", recall_key="key-1")
    sent_reply = store.get_sent_reply("cid-1", "msg-1")

    assert sent_reply is not None

    store.update_sent_reply_recall(
        sent_reply.id,
        recall_status="recalled",
        recall_error="",
    )
    updated = store.get_sent_reply("cid-1", "msg-1")

    assert updated is not None
    assert updated.recall_status == "recalled"
    assert updated.recalled_at is not None


def test_feedback_pressure_counts_unanswered_replies_since_last_feedback(
    tmp_path: Path,
):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    store.record_sent_reply(
        "cid-1",
        "old-before-feedback",
        "旧回复",
        feedback_token="token-old",
    )
    store.record_sent_reply(
        "cid-1",
        "old-unanswered",
        "旧回复",
        feedback_token="token-1",
    )
    store.record_sent_reply(
        "cid-1",
        "recent-unanswered",
        "近回复",
        feedback_token="token-2",
    )
    store.record_sent_reply(
        "cid-2",
        "other-conversation",
        "其他会话",
        feedback_token="token-3",
    )
    store.upsert_feedback_event(
        key="event-old",
        feedback_token="token-old",
        rating="up",
        received_at="2026-06-01 12:00:00",
    )
    with sqlite3.connect(store.path) as db:
        db.execute(
            "update sent_replies set sent_at=? where trigger_message_id=?",
            ("2026-05-30 12:00:00", "old-before-feedback"),
        )
        db.execute(
            "update sent_replies set sent_at=? where trigger_message_id=?",
            ("2026-06-02 12:00:00", "old-unanswered"),
        )
        db.execute(
            "update sent_replies set sent_at=? where trigger_message_id=?",
            ("2026-06-09 12:00:00", "recent-unanswered"),
        )
        db.execute(
            "update sent_replies set sent_at=? where trigger_message_id=?",
            ("2026-06-02 12:00:00", "other-conversation"),
        )

    stats = store.feedback_pressure_stats(
        "cid-1",
        now_utc="2026-06-12 12:00:00",
    )

    assert stats.unanswered_since_last_feedback == 2
    assert stats.unanswered_older_than_7_days == 1
    assert stats.unanswered_older_than_10_days == 1


def test_list_sent_replies_with_feedback_tokens_for_conversation(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    store.record_sent_reply("cid-1", "msg-1", "无反馈")
    store.record_sent_reply("cid-1", "msg-2", "旧回复", feedback_token="token-1")
    store.record_sent_reply("cid-2", "msg-3", "其他会话", feedback_token="token-2")
    store.record_sent_reply("cid-1", "msg-4", "新回复", feedback_token="token-3")

    replies = store.list_sent_replies_with_feedback_tokens_for_conversation(
        "cid-1",
        limit=10,
    )

    assert [reply.trigger_message_id for reply in replies] == ["msg-4", "msg-2"]


def test_list_sent_replies_waiting_for_feedback_events_filters_answered_tokens(
    tmp_path: Path,
):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    store.record_sent_reply("cid-1", "msg-1", "无反馈")
    store.record_sent_reply("cid-1", "msg-2", "已有本地反馈", feedback_token="token-1")
    store.record_sent_reply("cid-1", "msg-3", "等待反馈同步", feedback_token="token-2")
    store.upsert_feedback_event(
        key="event-1",
        feedback_token="token-1",
        rating="useful",
        received_at="2026-06-18T08:00:00.000Z",
    )

    replies = store.list_sent_replies_waiting_for_feedback_events(limit=10)

    assert [reply.trigger_message_id for reply in replies] == ["msg-3"]


def test_reply_attempt_tracing_and_feedback_round_trip(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")

    attempt_id = store.record_reply_attempt(
        conversation_id="cid-1",
        conversation_title="技术部",
        trigger_message_id="msg-1",
        trigger_sender="Xiaomin",
        trigger_text="@Alex Chen 这个怎么处理？",
        action="send_reply",
        sensitivity_kind="general",
        codex_reason="direct ask",
        draft_reply_text="先收敛问题",
        codex_session_id="session-1",
        codex_transcript_start_line=2,
        codex_transcript_end_line=7,
        audit_documents_json='[{"path":"面试/岗位画像.md"}]',
        audit_tool_events_json='[{"tool":"exec_command","command":"rg 岗位"}]',
        audit_summary="查看岗位画像后判断需要先收敛问题。",
    )
    store.update_reply_attempt(
        attempt_id,
        final_reply_text="先收敛问题（by明哥分身）",
        permission_action="allow",
        permission_reason="",
        send_status="sent",
        retry_count=1,
    )
    store.record_reply_feedback(
        attempt_id,
        feedback="语气可以，但需要更具体",
        corrected_reply_text="先明确负责人和时间点。",
    )

    attempt = store.get_reply_attempt(attempt_id)

    assert store.count_reply_attempts() == 1
    assert attempt is not None
    assert attempt.conversation_title == "技术部"
    assert attempt.trigger_message_id == "msg-1"
    assert attempt.action == "send_reply"
    assert attempt.audit_documents_json == '[{"path":"面试/岗位画像.md"}]'
    assert attempt.audit_tool_events_json == '[{"tool":"exec_command","command":"rg 岗位"}]'
    assert attempt.audit_summary == "查看岗位画像后判断需要先收敛问题。"
    assert attempt.codex_session_id == "session-1"
    assert attempt.codex_transcript_start_line == 2
    assert attempt.codex_transcript_end_line == 7
    assert attempt.final_reply_text == "先收敛问题（by明哥分身）"
    assert attempt.send_status == "sent"
    assert attempt.retry_count == 1
    assert attempt.reviewed_at is not None
    assert attempt.reviewer_feedback == "语气可以，但需要更具体"
    assert attempt.corrected_reply_text == "先明确负责人和时间点。"


def test_reply_attempt_records_oa_metadata(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")

    attempt_id = store.record_reply_attempt(
        conversation_id="cid-1",
        conversation_title="审批通知",
        trigger_message_id="msg-1",
        trigger_sender="工作通知",
        trigger_text="[Ding]张静提醒您审批他的录用申请",
        action="oa_approval",
        sensitivity_kind="internal_personnel",
        codex_reason="oa approval handled by dingtalk-oa-approval skill",
        codex_session_id="session-1",
        oa_process_instance_id="proc-1",
        oa_task_id="task-1",
        oa_url="https://aflow.dingtalk.com/dingtalk/mobile/query/formService#/detail?procInstId=proc-1",
        oa_action="退回",
        oa_remark="请补充试用期考核标准和完整面试记录后再提交。",
        oa_action_result_json='{"errcode":0,"errmsg":"ok"}',
        send_status="skipped",
    )

    loaded = store.get_reply_attempt(attempt_id)

    assert loaded is not None
    assert loaded.action == "oa_approval"
    assert loaded.oa_process_instance_id == "proc-1"
    assert loaded.oa_task_id == "task-1"
    assert loaded.oa_url.startswith("https://aflow.dingtalk.com/")
    assert loaded.oa_action == "退回"
    assert loaded.oa_remark == "请补充试用期考核标准和完整面试记录后再提交。"
    assert loaded.oa_action_result_json == '{"errcode":0,"errmsg":"ok"}'


def test_reply_attempt_records_calendar_response_metadata(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")

    attempt_id = store.record_reply_attempt(
        conversation_id="cid-1",
        conversation_title="Mina",
        trigger_message_id="msg-1",
        trigger_sender="Mina",
        trigger_text="[日程]",
        action="no_reply",
        sensitivity_kind="general",
        codex_reason="calendar invite handled",
        calendar_event_id="event-1",
        calendar_response_status="accepted",
        calendar_response_result_json='{"success":true}',
        send_status="skipped",
    )

    loaded = store.get_reply_attempt(attempt_id)

    assert loaded is not None
    assert loaded.calendar_event_id == "event-1"
    assert loaded.calendar_response_status == "accepted"
    assert loaded.calendar_response_result_json == '{"success":true}'


def test_record_reply_attempt_for_trigger_reuses_existing_attempt_id(
    tmp_path: Path,
):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")

    first_id = store.record_reply_attempt_for_trigger(
        conversation_id="cid-1",
        conversation_title="技术部",
        trigger_message_id="msg-1",
        trigger_sender="Xiaomin",
        trigger_text="@Alex Chen 这个怎么处理？",
        action="no_reply",
        sensitivity_kind="general",
        codex_reason="system_or_notification_message",
        send_status="skipped",
    )
    store.update_reply_attempt(
        first_id,
        final_reply_text="旧回复",
        send_error="no_reply",
        retry_count=2,
    )

    second_id = store.record_reply_attempt_for_trigger(
        conversation_id="cid-1",
        conversation_title="技术部",
        trigger_message_id="msg-1",
        trigger_sender="Xiaomin",
        trigger_text="@Alex Chen 这个怎么处理？",
        action="send_reply",
        sensitivity_kind="general",
        codex_reason="direct ask",
        draft_reply_text="先按A方案走",
        codex_session_id="session-1",
        audit_documents_json='[{"title":"chat"}]',
        audit_tool_events_json='[{"tool":"dws"}]',
        audit_summary="已重新判断，需要回复。",
        send_status="pending",
    )

    attempt = store.get_reply_attempt(first_id)

    assert second_id == first_id
    assert store.count_reply_attempts() == 1
    assert attempt is not None
    assert attempt.action == "send_reply"
    assert attempt.codex_reason == "direct ask"
    assert attempt.draft_reply_text == "先按A方案走"
    assert attempt.codex_session_id == "session-1"
    assert attempt.audit_documents_json == '[{"title":"chat"}]'
    assert attempt.audit_tool_events_json == '[{"tool":"dws"}]'
    assert attempt.audit_summary == "已重新判断，需要回复。"
    assert attempt.final_reply_text == ""
    assert attempt.send_status == "pending"
    assert attempt.send_error == ""
    assert attempt.retry_count == 0


def test_record_reply_attempt_for_trigger_does_not_overwrite_sent_reply_attempt(
    tmp_path: Path,
):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")

    first_id = store.record_reply_attempt_for_trigger(
        conversation_id="cid-1",
        conversation_title="技术部",
        trigger_message_id="msg-1",
        trigger_sender="Xiaomin",
        trigger_text="@Alex Chen 这个怎么处理？",
        action="send_reply",
        sensitivity_kind="general",
        codex_reason="direct ask",
        draft_reply_text="先按A方案走",
        send_status="pending",
    )
    store.update_reply_attempt(
        first_id,
        final_reply_text="先按A方案走",
        send_status="sent",
    )
    store.record_sent_reply(
        "cid-1",
        "msg-1",
        "先按A方案走",
        send_result_json='{"success":true}',
        feedback_token="token-1",
    )

    second_id = store.record_reply_attempt_for_trigger(
        conversation_id="cid-1",
        conversation_title="技术部",
        trigger_message_id="msg-1",
        trigger_sender="Xiaomin",
        trigger_text="@Alex Chen 这个怎么处理？",
        action="stop_with_error",
        sensitivity_kind="general",
        codex_reason="provider failed",
        send_status="pending",
    )

    first_attempt = store.get_reply_attempt(first_id)
    second_attempt = store.get_reply_attempt(second_id)

    assert second_id != first_id
    assert store.count_reply_attempts() == 2
    assert first_attempt is not None
    assert first_attempt.action == "send_reply"
    assert first_attempt.send_status == "sent"
    assert first_attempt.final_reply_text == "先按A方案走"
    assert second_attempt is not None
    assert second_attempt.action == "stop_with_error"
    assert second_attempt.send_status == "pending"


def test_get_latest_reply_attempt_for_trigger(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    first_id = store.record_reply_attempt(
        conversation_id="cid-1",
        conversation_title="技术部",
        trigger_message_id="msg-1",
        trigger_sender="Xiaomin",
        trigger_text="@Alex Chen 这个怎么处理？",
        action="send_reply",
        sensitivity_kind="general",
        send_status="failed",
    )
    second_id = store.record_reply_attempt(
        conversation_id="cid-1",
        conversation_title="技术部",
        trigger_message_id="msg-1",
        trigger_sender="Xiaomin",
        trigger_text="@Alex Chen 这个怎么处理？",
        action="send_reply",
        sensitivity_kind="general",
        send_status="dry_run",
    )

    attempt = store.get_latest_reply_attempt_for_trigger("cid-1", "msg-1")

    assert first_id != second_id
    assert attempt is not None
    assert attempt.id == second_id
    assert store.get_latest_reply_attempt_for_trigger("cid-1", "missing") is None


def test_history_query_skips_search_text_materialization_without_search():
    query, args = AutoReplyStore._history_items_query(
        send_statuses=None,
        query_text="",
        kinds=None,
        reply_channels=None,
        object_types=("reply", "meeting", "task"),
        created_since="",
    )

    assert query.count("iif(?1,") == 4
    assert args == [False, "reply", "meeting", "task"]


def test_history_treats_superseded_blocked_reply_as_skipped(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    blocked_id = store.record_reply_attempt(
        conversation_id="cid-1",
        conversation_title="技术部",
        trigger_message_id="msg-1",
        trigger_sender="Xiaomin",
        trigger_text="@Alex Chen 这个怎么处理？",
        action="stop_with_error",
        sensitivity_kind="general",
        send_status="blocked",
    )
    sent_id = store.record_reply_attempt(
        conversation_id="cid-1",
        conversation_title="技术部",
        trigger_message_id="msg-1",
        trigger_sender="Xiaomin",
        trigger_text="@Alex Chen 这个怎么处理？",
        action="send_reply",
        sensitivity_kind="general",
        send_status="sent",
    )

    blocked_items = store.list_history_items(send_statuses=("blocked",))
    skipped_items = store.list_history_items(send_statuses=("skipped",))

    assert blocked_id != sent_id
    assert [item.source_id for item in blocked_items] == []
    assert [item.source_id for item in skipped_items] == [blocked_id]


def test_history_groups_approval_retries_under_latest_meaningful_review(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    reviewed_id = store.record_reply_attempt(
        conversation_id="oa_pending_scan",
        conversation_title="审批待办",
        trigger_message_id="oa-pending:proc-1:first",
        trigger_sender="Derek OA",
        trigger_text="付款申请",
        action="agent_run",
        sensitivity_kind="general",
        oa_process_instance_id="proc-1",
        oa_task_id="task-1",
        oa_action="review",
        send_status="needs_human",
    )
    failed_retry_id = store.record_reply_attempt(
        conversation_id="oa_pending_scan",
        conversation_title="审批待办",
        trigger_message_id="oa-pending:proc-1:own-remark",
        trigger_sender="Derek OA",
        trigger_text="付款申请",
        action="agent_run",
        sensitivity_kind="general",
        oa_process_instance_id="proc-1",
        oa_task_id="task-1",
        oa_action="review",
        send_status="failed",
    )

    items = store.list_history_items(object_types=("approval",))

    assert failed_retry_id != reviewed_id
    assert [item.source_id for item in items] == [reviewed_id]
    assert items[0].status == "needs_human"


def test_history_keeps_blocked_side_effects_visible_after_terminal_reply(
    tmp_path: Path,
):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    store.record_sent_reply("cid-memory", "msg-memory", "reply delivered")
    memory_id = store.record_reply_attempt(
        conversation_id="cid-memory",
        conversation_title="Strategy",
        trigger_message_id="msg-memory",
        trigger_sender="Derek",
        trigger_text="Remember this",
        action="memory_write",
        sensitivity_kind="general",
        send_status="blocked",
    )
    store.update_reply_attempt(memory_id, send_error="memory backend unavailable")
    oa_id = store.record_reply_attempt(
        conversation_id="cid-oa",
        conversation_title="Approvals",
        trigger_message_id="msg-oa",
        trigger_sender="System",
        trigger_text="Pending approval",
        action="oa_approval",
        sensitivity_kind="general",
        send_status="blocked",
    )
    store.update_reply_attempt(oa_id, send_error="oa_task_not_current_user")
    store.record_reply_attempt(
        conversation_id="cid-oa",
        conversation_title="Approvals",
        trigger_message_id="msg-oa",
        trigger_sender="System",
        trigger_text="Pending approval",
        action="no_reply",
        sensitivity_kind="general",
        send_status="skipped",
    )

    blocked_items = store.list_history_items(send_statuses=("blocked",))

    assert {item.source_id for item in blocked_items} == {memory_id, oa_id}
    assert store.count_recoverable_blocked_reply_attempts() == 2


def test_history_preserves_superseded_meeting_failure(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    job_id = store.upsert_meeting_alignment_job(
        meeting_id="meeting-1",
        title="招聘站会",
        source_json="{}",
        participants_json="[]",
        ended_at="2026-07-22T10:12:44+00:00",
        eligible_at="2026-07-22T10:22:44+00:00",
        status="pending",
    )
    failed_run_id = store.record_meeting_alignment_run(
        job_id=job_id,
        codex_session_id="meeting-session-failed",
        decision_json="{}",
        audit_summary="首次生成失败",
        status="retry",
        error="Codex did not return a valid MeetingAlignmentDecision",
    )
    sent_run_id = store.record_meeting_alignment_run(
        job_id=job_id,
        codex_session_id="meeting-session-sent",
        decision_json='{"action":"send"}',
        audit_summary="再次生成完成",
        status="ready_to_send",
        error="",
    )
    store.update_meeting_alignment_job(
        job_id,
        status="sent",
        target_kind="group",
        target_title="HR",
        final_message="会后对齐已发送。",
        send_result_json='{"status":"sent"}',
    )

    failed_items = store.list_history_items(send_statuses=("failed",))
    skipped_items = store.list_history_items(send_statuses=("skipped",))
    sent_items = store.list_history_items(send_statuses=("sent",))

    assert failed_run_id != sent_run_id
    assert [item.source_id for item in failed_items] == [failed_run_id]
    assert [item.source_id for item in skipped_items] == []
    assert [item.source_id for item in sent_items] == [sent_run_id]


def test_lists_reply_attempts_newest_first_with_limit(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    first_id = store.record_reply_attempt(
        conversation_id="cid-1",
        conversation_title="技术部",
        trigger_message_id="msg-1",
        trigger_sender="Xiaomin",
        trigger_text="@Alex Chen 这个怎么处理？",
        action="send_reply",
        sensitivity_kind="general",
        codex_reason="direct ask",
    )
    second_id = store.record_reply_attempt(
        conversation_id="cid-2",
        conversation_title="HR",
        trigger_message_id="msg-2",
        trigger_sender="HR",
        trigger_text="张三转正怎么看？",
        action="no_reply",
        sensitivity_kind="internal_personnel",
        codex_reason="privacy",
    )

    all_attempts = store.list_reply_attempts()
    attempts = store.list_reply_attempts(limit=1)
    offset_attempts = store.list_reply_attempts(limit=1, offset=1)

    assert [attempt.id for attempt in all_attempts] == [second_id, first_id]
    assert [attempt.id for attempt in attempts] == [second_id]
    assert [attempt.id for attempt in offset_attempts] == [first_id]
    assert attempts[0].conversation_title == "HR"
    assert attempts[0].send_status == "pending"
    assert first_id != second_id


def test_lists_reply_attempts_since_timestamp(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    old_id = store.record_reply_attempt(
        conversation_id="cid-old",
        conversation_title="Old",
        trigger_message_id="msg-old",
        trigger_sender="Old",
        trigger_text="old",
        action="send_reply",
        sensitivity_kind="general",
    )
    new_id = store.record_reply_attempt(
        conversation_id="cid-new",
        conversation_title="New",
        trigger_message_id="msg-new",
        trigger_sender="New",
        trigger_text="new",
        action="send_reply",
        sensitivity_kind="general",
    )
    with store._connect() as db:
        db.execute(
            "update reply_attempts set created_at=? where id=?",
            ("2026-06-04 00:00:00", old_id),
        )
        db.execute(
            "update reply_attempts set created_at=? where id=?",
            ("2026-06-05 00:00:00", new_id),
        )

    attempts = store.list_reply_attempts_since("2026-06-04 12:00:00")

    assert [attempt.id for attempt in attempts] == [new_id]


def test_lists_reviewed_reply_attempts_for_optimization(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    unreviewed_id = store.record_reply_attempt(
        conversation_id="cid-1",
        conversation_title="技术部",
        trigger_message_id="msg-1",
        trigger_sender="Xiaomin",
        trigger_text="@Alex Chen 这个怎么处理？",
        action="send_reply",
        sensitivity_kind="general",
    )
    reviewed_id = store.record_reply_attempt(
        conversation_id="cid-2",
        conversation_title="Claire",
        trigger_message_id="msg-2",
        trigger_sender="Claire",
        trigger_text="明哥上会啦",
        action="send_reply",
        sensitivity_kind="general",
        draft_reply_text="收到，我现在进会。",
    )
    store.update_reply_attempt(
        reviewed_id,
        final_reply_text="收到，我现在进会。（by明哥分身）",
        send_status="sent",
    )
    store.record_reply_feedback(
        reviewed_id,
        feedback="不能代 Alex 声称正在进会",
        corrected_reply_text="我让明哥本人看一下。（by明哥分身）",
    )

    attempts = store.list_reviewed_reply_attempts()

    assert [attempt.id for attempt in attempts] == [reviewed_id]
    assert attempts[0].reviewer_feedback == "不能代 Alex 声称正在进会"
    assert attempts[0].corrected_reply_text == "我让明哥本人看一下。（by明哥分身）"
    assert unreviewed_id != reviewed_id


def test_lists_errors_newest_first_with_limit(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    store.record_error("cid-1", "msg-1", "codex", "invalid json")
    store.record_error("cid-2", "msg-2", "send", "authorization required")

    all_errors = store.list_errors()
    errors = store.list_errors(limit=1)
    offset_errors = store.list_errors(limit=1, offset=1)

    assert [error.kind for error in all_errors] == ["send", "codex"]
    assert len(errors) == 1
    assert errors[0].conversation_id == "cid-2"
    assert errors[0].message_id == "msg-2"
    assert errors[0].kind == "send"
    assert errors[0].detail == "authorization required"
    assert errors[0].created_at
    assert len(offset_errors) == 1
    assert offset_errors[0].kind == "codex"


def test_lists_run_delta_records_after_ids(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    first_attempt_id = store.record_reply_attempt(
        conversation_id="cid-1",
        conversation_title="Friday",
        trigger_message_id="msg-1",
        trigger_sender="Mina",
        trigger_text="@Alex Chen 这个怎么处理？",
        action="no_reply",
        sensitivity_kind="general",
        send_status="skipped",
    )
    store.record_sent_reply("cid-1", "msg-1", "收到（by明哥分身）")
    store.record_error("cid-1", "msg-1", "codex", "invalid json")

    baseline_attempt_id = store.max_reply_attempt_id()
    baseline_sent_reply_id = store.max_sent_reply_id()
    baseline_error_id = store.max_error_id()

    second_attempt_id = store.record_reply_attempt(
        conversation_id="cid-2",
        conversation_title="BA",
        trigger_message_id="msg-2",
        trigger_sender="Phina",
        trigger_text="@Alex Chen 需要看一下吗？",
        action="send_reply",
        sensitivity_kind="general",
        send_status="pending",
    )
    store.record_sent_reply("cid-2", "msg-2", "可以（by明哥分身）")
    store.record_error("cid-2", "msg-2", "read_messages", "dws timeout")

    assert baseline_attempt_id == first_attempt_id
    assert baseline_sent_reply_id == 1
    assert baseline_error_id == 1
    assert [attempt.id for attempt in store.list_reply_attempts_after(baseline_attempt_id)] == [
        second_attempt_id
    ]
    assert [
        sent.trigger_message_id for sent in store.list_sent_replies_after(baseline_sent_reply_id)
    ] == ["msg-2"]
    assert [error.kind for error in store.list_errors_after(baseline_error_id)] == [
        "read_messages"
    ]


def test_org_user_profile_cache_round_trip(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")

    store.upsert_org_user_profile(
        user_id="user-1",
        name="张三",
        open_dingtalk_id="open-1",
        manager_user_id="manager-1",
        department_ids={"dept-1", "dept-2"},
        title="产品负责人",
        manager_name="李四",
        department_names={"产品部", "售前解决方案部"},
        org_labels=["职务: 产品负责人", "岗位: 管理层"],
        has_subordinate=True,
    )

    profile = store.get_org_user_profile("user-1")

    assert profile is not None
    assert profile.user_id == "user-1"
    assert profile.name == "张三"
    assert profile.open_dingtalk_id == "open-1"
    assert profile.manager_user_id == "manager-1"
    assert profile.manager_name == "李四"
    assert profile.department_ids == {"dept-1", "dept-2"}
    assert profile.department_names == {"产品部", "售前解决方案部"}
    assert profile.title == "产品负责人"
    assert profile.org_labels == ["职务: 产品负责人", "岗位: 管理层"]
    assert profile.has_subordinate is True
    assert store.find_org_user_by_open_dingtalk_id("open-1").user_id == "user-1"
    assert [user.user_id for user in store.find_org_users_by_name("张三")] == ["user-1"]
    assert store.list_org_user_ids() == ["user-1"]


def test_org_cache_metadata_round_trip(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")

    store.set_current_user_id("principal-user-1")
    store.set_hr_department_ids({"hr-dept-1"})

    assert store.get_current_user_id() == "principal-user-1"
    assert store.get_hr_department_ids() == {"hr-dept-1"}


def test_service_state_round_trip(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")

    store.set_service_state("dws_upgrade_checked_date", "2026-05-25")
    loaded = AutoReplyStore(tmp_path / "worker.sqlite3")

    assert loaded.get_service_state("dws_upgrade_checked_date") == "2026-05-25"


def test_missing_service_state_returns_none(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")

    assert store.get_service_state("missing") is None


def test_list_oa_attempt_history_returns_newest_first(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    first_id = store.record_reply_attempt(
        conversation_id="cid-oa",
        conversation_title="OA 群",
        trigger_message_id="msg-1",
        trigger_sender="Derek",
        trigger_text="审批 1",
        action="oa_approval",
        sensitivity_kind="internal_personnel",
        codex_reason="退回",
        draft_reply_text="请补材料",
        oa_process_instance_id="proc-1",
        oa_task_id="task-1",
        oa_action="退回",
        oa_remark="请补材料",
        send_status="commented",
    )
    second_id = store.record_reply_attempt(
        conversation_id="cid-oa",
        conversation_title="OA 群",
        trigger_message_id="msg-2",
        trigger_sender="Derek",
        trigger_text="审批 2",
        action="oa_approval",
        sensitivity_kind="internal_personnel",
        codex_reason="同意",
        draft_reply_text="同意",
        oa_process_instance_id="proc-1",
        oa_task_id="task-2",
        oa_action="同意",
        oa_remark="同意",
        send_status="skipped",
    )
    store.record_reply_attempt(
        conversation_id="cid-other",
        conversation_title="其他",
        trigger_message_id="msg-3",
        trigger_sender="Derek",
        trigger_text="审批 3",
        action="oa_approval",
        sensitivity_kind="internal_personnel",
        codex_reason="同意",
        draft_reply_text="同意",
        oa_process_instance_id="proc-2",
        send_status="skipped",
    )

    history = store.list_oa_attempt_history("proc-1")

    assert [attempt.id for attempt in history] == [second_id, first_id]
    assert store.list_oa_attempt_history("") == []


def test_backfill_oa_audit_metadata_recovers_completed_agent_scan_attempt(
    tmp_path: Path,
):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    trigger_message_id = "oa-pending:proc-1:revision-1"
    assert store.enqueue_reply_task(
        conversation_id="oa_pending_scan",
        conversation_title="审批待办",
        single_chat=True,
        trigger_message_id=trigger_message_id,
        trigger_create_time="2026-08-06T05:41:00+00:00",
        trigger_sender="Derek OA",
        trigger_text="审批待办扫描",
        oa_url="https://aflow.dingtalk.com/detail?procInstId=proc-1&taskId=task-1",
    )
    attempt_id = store.record_reply_attempt(
        conversation_id="oa_pending_scan",
        conversation_title="审批待办",
        trigger_message_id=trigger_message_id,
        trigger_sender="Derek OA",
        trigger_text="审批待办扫描",
        action="agent_run",
        sensitivity_kind="general",
        audit_summary="已审阅并评论要求补充材料。",
        send_status="needs_human",
    )

    assert store.backfill_oa_audit_metadata() == 1

    attempt = store.get_reply_attempt(attempt_id)
    assert attempt is not None
    assert attempt.oa_process_instance_id == "proc-1"
    assert attempt.oa_task_id == "task-1"
    assert attempt.oa_url.endswith("procInstId=proc-1&taskId=task-1")
    assert attempt.oa_action == "review"


def test_setup_wizard_step_state_round_trips(tmp_path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")

    store.upsert_setup_wizard_step(
        step_id="mcp",
        status="done",
        summary="Codex config contains memory_connector",
        manual_confirmed_by="",
    )
    row = store.get_setup_wizard_step("mcp")

    assert row["step_id"] == "mcp"
    assert row["status"] == "done"
    assert row["summary"] == "Codex config contains memory_connector"
    assert row["manual_confirmed_by"] == ""
    assert row["updated_at"]


def test_setup_wizard_event_history_round_trips(tmp_path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")

    event_id = store.record_setup_wizard_event(
        step_id="mcp",
        action_id="setup_mcp",
        status="done",
        summary="wrote config",
        evidence_json='{"codex_config": "/tmp/config.toml"}',
        stdout_excerpt="setup-memory-connector codex_config=/tmp/config.toml",
        stderr_excerpt="",
    )
    events = store.list_setup_wizard_events("mcp")

    assert event_id > 0
    assert len(events) == 1
    assert events[0]["step_id"] == "mcp"
    assert events[0]["action_id"] == "setup_mcp"
    assert events[0]["evidence_json"] == '{"codex_config": "/tmp/config.toml"}'


def test_setup_wizard_running_event_is_not_finished(tmp_path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")

    store.record_setup_wizard_event(
        step_id="mcp",
        action_id="setup_mcp",
        status="running",
    )
    events = store.list_setup_wizard_events("mcp")

    assert events[0]["started_at"]
    assert events[0]["finished_at"] == ""


def test_setup_wizard_running_event_ignores_legacy_finished_default(tmp_path):
    db_path = tmp_path / "worker.sqlite3"
    with sqlite3.connect(db_path) as db:
        db.executescript(
            """
            create table setup_wizard_events (
                id integer primary key autoincrement,
                step_id text not null,
                action_id text not null,
                status text not null,
                summary text not null default '',
                evidence_json text not null default '{}',
                stdout_excerpt text not null default '',
                stderr_excerpt text not null default '',
                started_at text not null default current_timestamp,
                finished_at text not null default current_timestamp
            );
            """
        )
    store = AutoReplyStore(db_path)

    store.record_setup_wizard_event(
        step_id="mcp",
        action_id="setup_mcp",
        status="running",
    )

    events = store.list_setup_wizard_events("mcp")
    assert events[0]["finished_at"] == ""


def test_setup_wizard_steps_list_has_stable_tie_breaker(tmp_path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    store.upsert_setup_wizard_step(step_id="mcp", status="done", summary="ok")
    store.upsert_setup_wizard_step(step_id="preflight", status="done", summary="ok")
    with sqlite3.connect(tmp_path / "worker.sqlite3") as db:
        db.execute("update setup_wizard_steps set updated_at='2026-06-12 12:00:00'")

    rows = store.list_setup_wizard_steps()

    assert [row["step_id"] for row in rows] == ["mcp", "preflight"]


def test_reply_attempt_round_trips_mail_action_state(tmp_path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")

    attempt_id = store.record_reply_attempt(
        conversation_id="cid-1",
        conversation_title="HR",
        trigger_message_id="msg-1",
        trigger_sender="Alan",
        trigger_text="审批并回复邮件",
        action="send_reply",
        sensitivity_kind="general",
        mail_mailbox="derek@example.com",
        mail_message_id="mail-1",
        mail_subject="Re: 评奖结果",
        mail_reply_text="确认无误，可以发布。",
    )
    store.update_reply_attempt(
        attempt_id,
        mail_action_result_json='{"success": true}',
    )

    attempt = store.get_reply_attempt(attempt_id)
    assert attempt is not None
    assert attempt.mail_mailbox == "derek@example.com"
    assert attempt.mail_message_id == "mail-1"
    assert attempt.mail_subject == "Re: 评奖结果"
    assert attempt.mail_reply_text == "确认无误，可以发布。"
    assert attempt.mail_action_result_json == '{"success": true}'


def test_sent_reply_exists_matches_exact_conversation_and_trigger(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    store.record_sent_reply("cid-1", "msg-1", "Sent")

    assert store.sent_reply_exists("cid-1", "msg-1") is True
    assert store.sent_reply_exists("cid-1", "msg-other") is False
    assert store.sent_reply_exists("cid-other", "msg-1") is False


def test_channel_login_claim_requires_owner_to_finalize_and_persists_safe_state(
    tmp_path: Path,
):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    now = datetime(2026, 7, 28, 12, tzinfo=timezone.utc)

    claimed, reserved = store.claim_channel_login_request(
        channel="dingtalk",
        reason_code="live_probe_auth_failed",
        now=now,
        suppression_seconds=3600,
        reservation_owner="owner-1",
    )

    assert claimed is True
    assert reserved["status"] == "starting"
    assert (
        store.update_claimed_channel_login_request(
            channel="dingtalk",
            reservation_owner="owner-2",
            state={"status": "running", "pid": 99},
        )
        is False
    )
    assert store.update_claimed_channel_login_request(
        channel="dingtalk",
        reservation_owner="owner-1",
        state={"status": "failed", "exited_at": now.isoformat()},
    )
    state = json.loads(
        store.get_service_state("channel_login_request:dingtalk") or "{}"
    )
    assert state == {
        "checked_at": now.isoformat(),
        "exited_at": now.isoformat(),
        "reason_code": "live_probe_auth_failed",
        "started_at": now.isoformat(),
        "status": "failed",
    }


def test_removed_runtime_tables_modules_and_apis_are_absent(tmp_path: Path) -> None:
    from app.worker import DingTalkAutoReplyWorker

    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    with store._connect() as db:
        tables = {
            str(row[0])
            for row in db.execute(
                "select name from sqlite_master where type='table'"
            ).fetchall()
        }

    assert "agent_runs" in tables
    assert "universal_plan_executions" not in tables
    assert "universal_action_executions" not in tables
    assert store.get_service_state("dws_auth_backup") is None
    assert not hasattr(store, "create_universal_plan_execution")
    assert not hasattr(store, "claim_universal_action_execution")
    assert not hasattr(DingTalkAutoReplyWorker, "execute_universal_send_reply")
    assert importlib.util.find_spec("app.universal_consumer") is None
    assert importlib.util.find_spec("app.universal_plan") is None


def test_removed_runtime_migrates_unreferenced_history_before_drop(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "worker.sqlite3"
    store = AutoReplyStore(db_path)
    assert store.enqueue_reply_task(
        conversation_id="cid-legacy",
        conversation_title="Legacy history",
        single_chat=False,
        trigger_message_id="msg-legacy",
        trigger_create_time="2026-07-20 10:00:00",
        trigger_sender="Derek",
        trigger_text="Handle this task",
    )
    task_id = store.claim_reply_tasks(limit=1)[0].id
    existing_attempt_id = store.record_reply_attempt(
        conversation_id="cid-legacy",
        conversation_title="Legacy history",
        trigger_message_id="msg-legacy",
        trigger_sender="Derek",
        trigger_text="Handle this task",
        action="send_reply",
        sensitivity_kind="general",
        send_status="sent",
    )
    with store._connect() as db:
        db.executescript(
            """
            create table universal_plan_executions (
                execution_scope_id text primary key,
                reply_task_id integer not null
            );
            create table universal_action_executions (
                execution_id text primary key,
                execution_scope_id text not null,
                attempt_id integer,
                action_kind text not null,
                status text not null,
                error text not null default '',
                result_json text not null default '',
                created_at text not null,
                updated_at text not null
            );
            """
        )
        db.execute(
            "insert into universal_plan_executions values (?, ?)",
            ("scope-1", task_id),
        )
        db.executemany(
            """
            insert into universal_action_executions values (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                (
                    "action-existing",
                    "scope-1",
                    existing_attempt_id,
                    "send_reply",
                    "succeeded",
                    "",
                    '{"receipt":"existing"}',
                    "2026-07-20 10:01:00",
                    "2026-07-20 10:02:00",
                ),
                (
                    "action-missing",
                    "scope-1",
                    None,
                    "oa_approval",
                    "failed",
                    "legacy failure",
                    '{"outcome":"failed"}',
                    "2026-07-20 10:03:00",
                    "2026-07-20 10:04:00",
                ),
            ),
        )
        db.execute(
            "insert or replace into service_state (key, value) values (?, ?)",
            ("dws_auth_backup", '{"archive":"removed"}'),
        )

    store_module._INITIALIZED_STORE_PATHS.discard(db_path.resolve())
    migrated = AutoReplyStore(db_path)

    attempts = migrated.list_reply_attempts(limit=20)
    assert len(attempts) == 2
    historical = next(attempt for attempt in attempts if attempt.id != existing_attempt_id)
    assert historical.action == "oa_approval"
    assert historical.send_status == "failed"
    assert historical.send_error == "legacy failure"
    assert historical.audit_summary == '{"outcome":"failed"}'
    with migrated._connect() as db:
        tables = {
            str(row[0])
            for row in db.execute(
                "select name from sqlite_master where type='table'"
            ).fetchall()
        }
    assert "universal_plan_executions" not in tables
    assert "universal_action_executions" not in tables
    assert migrated.get_service_state("dws_auth_backup") is None


_LEGACY_EFFECT_SUCCESS_CASES: dict[str, tuple[str, dict[str, object]]] = {
    "send_reply": (
        "sent",
        {"action_kind": "send_reply", "outcome": "delivered"},
    ),
    "ask_clarifying_question": (
        "sent",
        {"action_kind": "ask_clarifying_question", "outcome": "delivered"},
    ),
    "oa_approval": (
        "completed",
        {
            "action": "同意",
            "outcome": "applied",
            "process_instance_id": "process-1",
            "task_id": "task-1",
        },
    ),
    "mail_reply": ("sent", {"success": True}),
    "calendar_response": ("calendar", {"success": True}),
    "dws_markdown_document_reply": (
        "document",
        {
            "node_id": "node-1",
            "url": "https://alidocs.dingtalk.com/i/nodes/node-1",
            "delivery": {"messageId": "message-1"},
        },
    ),
    "dws_message_reaction": ("reacted", {"reactionId": "reaction-1"}),
    "queue_okr_review": (
        "completed",
        {
            "action_kind": "queue_okr_review",
            "outcome": "okr_review_queued_and_acknowledged",
        },
    ),
    "memory_write": (
        "completed",
        {
            "episode_uuid": "episode-1",
            "processing_status": "completed",
            "duplicate": False,
        },
    ),
}


def test_removed_runtime_migrates_every_action_status_with_terminal_semantics(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "worker.sqlite3"
    store = AutoReplyStore(db_path)
    actions = (
        "send_reply",
        "ask_clarifying_question",
        "oa_approval",
        "mail_reply",
        "calendar_response",
        "dws_markdown_document_reply",
        "dws_message_reaction",
        "queue_okr_review",
        "memory_write",
        "no_reply",
        "handoff_to_human",
        "blocked",
        "stop_with_error",
    )
    legacy_statuses = ("succeeded", "failed", "blocked", "unknown", "not_started")
    succeeded_status = {
        **{
            action: status
            for action, (status, _) in _LEGACY_EFFECT_SUCCESS_CASES.items()
        },
        "no_reply": "skipped",
        "handoff_to_human": "blocked",
        "blocked": "blocked",
        "stop_with_error": "failed",
    }
    expected: dict[str, str] = {}
    with store._connect() as db:
        db.executescript(
            """
            create table universal_plan_executions (
                execution_scope_id text primary key,
                reply_task_id integer not null
            );
            create table universal_action_executions (
                execution_id text primary key,
                execution_scope_id text not null,
                attempt_id integer,
                action_kind text not null,
                status text not null,
                error text not null default '',
                result_json text not null default '',
                created_at text not null,
                updated_at text not null
            );
            """
        )
        for action in actions:
            for legacy_status in legacy_statuses:
                key = f"{action}-{legacy_status}"
                db.execute(
                    """
                    insert into reply_tasks (
                        conversation_id, conversation_title, single_chat,
                        trigger_message_id, trigger_create_time, trigger_sender,
                        trigger_text
                    ) values ('cid-migration', 'Migration', 0, ?,
                              '2026-07-20 09:00:00', 'Derek', ?)
                    """,
                    (key, key),
                )
                task_id = int(db.execute("select last_insert_rowid()").fetchone()[0])
                scope = f"scope-{key}"
                db.execute(
                    "insert into universal_plan_executions values (?, ?)",
                    (scope, task_id),
                )
                db.execute(
                    """
                    insert into universal_action_executions
                    values (?, ?, null, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        f"execution-{key}",
                        scope,
                        action,
                        legacy_status,
                        f"legacy-{legacy_status}" if legacy_status != "succeeded" else "",
                            json.dumps(
                                _LEGACY_EFFECT_SUCCESS_CASES.get(action, ("", {}))[1]
                            )
                            if legacy_status == "succeeded"
                            else "",
                        "2026-07-20 10:00:00",
                        "2026-07-20 10:01:00",
                    ),
                )
                expected[key] = (
                    succeeded_status[action]
                    if legacy_status == "succeeded"
                    else "blocked"
                    if legacy_status in {"blocked", "unknown"}
                    else "failed"
                )

    store_module._INITIALIZED_STORE_PATHS.discard(db_path.resolve())
    migrated = AutoReplyStore(db_path)
    actual = {
        attempt.trigger_message_id: attempt.send_status
        for attempt in migrated.list_reply_attempts(limit=200)
    }

    assert actual == expected
    store_module._INITIALIZED_STORE_PATHS.discard(db_path.resolve())
    AutoReplyStore(db_path)
    assert len(migrated.list_reply_attempts(limit=200)) == len(expected)


@pytest.mark.parametrize(
    ("action", "success_receipt", "expected_status"),
    [
        (action, receipt, status)
        for action, (status, receipt) in _LEGACY_EFFECT_SUCCESS_CASES.items()
    ],
)
def test_removed_runtime_requires_action_specific_success_receipt(
    action: str,
    success_receipt: dict[str, object],
    expected_status: str,
) -> None:
    success = AutoReplyStore._removed_runtime_attempt_status(
        action=action,
        legacy_status="succeeded",
        result_json=json.dumps(success_receipt, ensure_ascii=False),
    )
    unknown = AutoReplyStore._removed_runtime_attempt_status(
        action=action,
        legacy_status="succeeded",
        result_json='{"unexpected":"value"}',
    )

    assert success == (expected_status, "")
    for error_receipt in (
        {"error": "failed"},
        {"error": {"code": "failed"}},
        {"success": False},
    ):
        assert AutoReplyStore._removed_runtime_attempt_status(
            action=action,
            legacy_status="succeeded",
            result_json=json.dumps(error_receipt),
        ) == ("failed", "migrated_explicit_execution_failure")
    assert unknown == ("failed", "migrated_unverified_execution_receipt")


@pytest.mark.parametrize(
    "receipt",
    [
        {
            "tool_events": [
                {
                    "type": "item.completed",
                    "item": {
                        "call_id": "call-1",
                        "metadata": {"effect": "effectful"},
                    },
                }
            ]
        },
        {
            "receipt": {
                "receipt_id": "receipt-1",
                "operation_id": "operation-1",
                "completed": True,
                "persisted": True,
                "safe_to_confirm": True,
            }
        },
    ],
)
def test_removed_runtime_accepts_completed_effect_evidence(
    receipt: dict[str, object],
) -> None:
    assert AutoReplyStore._removed_runtime_attempt_status(
        action="agent_action",
        legacy_status="succeeded",
        result_json=json.dumps(receipt),
    ) == ("completed", "")


def test_removed_runtime_structured_block_is_not_completed() -> None:
    assert AutoReplyStore._removed_runtime_attempt_status(
        action="oa_approval",
        legacy_status="succeeded",
        result_json=json.dumps(
            {
                "action": "同意",
                "outcome": "blocked",
                "process_instance_id": "process-1",
                "task_id": "task-1",
            },
            ensure_ascii=False,
        ),
    ) == ("blocked", "migrated_structured_execution_block")


def test_removed_runtime_migration_starts_immediate_transaction(tmp_path: Path) -> None:
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    statements: list[str] = []
    with store._connect() as db:
        db.set_trace_callback(statements.append)
        AutoReplyStore._migrate_removed_runtime(db)

    assert any(statement.strip().upper() == "BEGIN IMMEDIATE" for statement in statements)


def test_removed_runtime_migration_rolls_back_every_change_on_failure(
    tmp_path: Path,
) -> None:
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    assert store.enqueue_reply_task(
        conversation_id="cid-rollback",
        conversation_title="Rollback",
        single_chat=False,
        trigger_message_id="msg-rollback",
        trigger_create_time="2026-07-20 10:00:00",
        trigger_sender="Derek",
        trigger_text="rollback",
    )
    task = store.get_reply_task_for_message("cid-rollback", "msg-rollback")
    assert task is not None
    with store._connect() as db:
        db.executescript(
            """
            create table universal_plan_executions (
                execution_scope_id text primary key,
                reply_task_id integer not null
            );
            create table universal_action_executions (
                execution_id text primary key,
                execution_scope_id text not null,
                attempt_id integer,
                action_kind text not null,
                status text not null,
                error text not null default '',
                result_json text not null default '',
                created_at text not null,
                updated_at text not null
            );
            create trigger reject_auth_cleanup before delete on service_state
            when old.key='dws_auth_backup'
            begin
                select raise(abort, 'forced migration failure');
            end;
            """
        )
        db.execute(
            "insert into universal_plan_executions values ('scope-rollback', ?)",
            (task.id,),
        )
        db.execute(
            """
            insert into universal_action_executions values (
                'action-rollback', 'scope-rollback', null, 'send_reply',
                'succeeded', '', '{"receipt":{"completed":true}}',
                '2026-07-20 10:01:00', '2026-07-20 10:02:00'
            )
            """
        )
        db.execute(
            "insert or replace into service_state (key, value) values (?, ?)",
            ("dws_auth_backup", "present"),
        )

    with sqlite3.connect(store.path) as db:
        db.row_factory = sqlite3.Row
        with pytest.raises(sqlite3.IntegrityError, match="forced migration failure"):
            AutoReplyStore._migrate_removed_runtime(db)
        tables = {
            row["name"]
            for row in db.execute(
                "select name from sqlite_master where type='table'"
            ).fetchall()
        }
        attempts = db.execute(
            "select count(*) from reply_attempts where trigger_message_id='msg-rollback'"
        ).fetchone()[0]
        auth_state = db.execute(
            "select value from service_state where key='dws_auth_backup'"
        ).fetchone()

    assert "universal_plan_executions" in tables
    assert "universal_action_executions" in tables
    assert attempts == 0
    assert auth_state is not None


def test_recover_orphaned_processing_reply_tasks_is_generation_aware(
    tmp_path: Path,
) -> None:
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    task_ids = []
    for index in range(3):
        store.enqueue_reply_task(
            conversation_id=f"cid-{index}",
            conversation_title=f"Conversation {index}",
            single_chat=False,
            trigger_message_id=f"msg-{index}",
            trigger_create_time="2026-07-30 09:00:00",
            trigger_sender="Derek",
            trigger_text="handle this",
        )
        task_ids.append(store.claim_reply_tasks(1)[0].id)

    running_task = store.get_reply_task(task_ids[1])
    unknown_task = store.get_reply_task(task_ids[2])
    assert running_task is not None and unknown_task is not None
    _claim_audit_run(store,
        running_task.id,
        running_task.execution_generation,
        owner="running-worker",
    )
    unknown_run = _claim_audit_run(store,
        unknown_task.id,
        unknown_task.execution_generation,
        owner="unknown-worker",
    ).run
    store.mark_agent_run_unknown(
        unknown_run.id,
        {"code": "effect_completion_missing"},
        owner="unknown-worker",
    )

    recovered = store.recover_orphaned_processing_reply_tasks(limit=10)

    assert [task.id for task in recovered] == [task_ids[0]]
    assert store.get_reply_task(task_ids[0]).status == "pending"
    assert store.get_reply_task(task_ids[0]).attempts == 0
    assert store.get_reply_task(task_ids[1]).status == "processing"
    assert store.get_reply_task(task_ids[2]).status == "processing"


def test_recover_interrupted_wechat_read_only_decision_has_precise_reason(
    tmp_path: Path,
) -> None:
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    store.enqueue_reply_task(
        channel="wechat",
        conversation_id="wechat-conversation",
        conversation_title="Wechat contact",
        single_chat=True,
        trigger_message_id="wechat-message",
        trigger_create_time="2026-08-07 01:00:00",
        trigger_sender="contact",
        trigger_text="Can you reply?",
    )
    [task] = store.claim_reply_tasks(1, channel="wechat")

    store.mark_wechat_read_only_decision_started(
        task.id,
        expected_execution_generation=task.execution_generation,
    )
    recovered = store.recover_orphaned_processing_reply_tasks(limit=10)

    assert [item.id for item in recovered] == [task.id]
    recovered_task = store.get_reply_task(task.id)
    assert recovered_task is not None
    assert recovered_task.status == "pending"
    assert recovered_task.error == "interrupted_read_only_decision"
