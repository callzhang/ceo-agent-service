import json
from dataclasses import replace
from pathlib import Path
import sqlite3

import pytest

from app.store import AutoReplyStore
from app.universal_context import (
    UniversalContextMessage,
    UniversalTaskContext,
    canonical_universal_context_json,
    universal_context_sha256,
)
from app.universal_executor import (
    UniversalActionExecution,
    UniversalActionExecutionState,
    UniversalPlanExecution,
    build_universal_action_execution,
)
from app.universal_plan import (
    PlannedAction,
    PlannedActionKind,
    UniversalAudit,
    UniversalPlan,
)


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


def _universal_plan(*, reason: str = "Handle the task") -> UniversalPlan:
    return UniversalPlan(
        task_kind="message_handling",
        reason=reason,
        actions=[
            PlannedAction(
                kind=PlannedActionKind.MEMORY_WRITE,
                reason="Remember the result",
                target={"b": "2", "a": "1"},
                payload={"nested": {"z": 3, "a": 1}},
            )
        ],
        audit=UniversalAudit(summary="Persist execution", confidence=0.9),
    )


def _universal_context(
    task_id: int,
    *,
    execution_generation: str = "initial",
    trigger_text: str = "Handle this task",
    context_messages: tuple[UniversalContextMessage, ...] = (),
    required_dependencies: tuple[str, ...] = ("dws",),
    force_new_decision: bool = False,
    dry_run: bool = False,
) -> UniversalTaskContext:
    return UniversalTaskContext(
        task_id=task_id,
        conversation_id="cid-universal",
        conversation_title="Universal",
        single_chat=False,
        trigger_message_id="msg-universal",
        trigger_sender="Derek",
        trigger_text=trigger_text,
        context_messages=context_messages,
        required_dependencies=required_dependencies,
        force_new_decision=force_new_decision,
        dry_run=dry_run,
        execution_generation=execution_generation,
    )


def _universal_action_execution(
    store: AutoReplyStore,
    task_id: int,
    *,
    execution_generation: str = "initial",
) -> UniversalActionExecution:
    context = _universal_context(
        task_id,
        execution_generation=execution_generation,
    )
    plan_execution = store.create_universal_plan_execution(
        context,
        _universal_plan(),
    )
    return build_universal_action_execution(
        context,
        plan_execution,
        plan_execution.plan.actions[0],
        0,
    )


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

    store.requeue_reply_task(task_id, "retry")
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
    store.fail_reply_task(task.id, "old failure")

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
        attempt_id=42,
    )

    assert rerun.id == task.id
    assert rerun.status == "pending"
    assert rerun.locked_at is None
    assert rerun.force_new_decision is True
    assert rerun.oa_url == "https://oa.example/process"
    assert rerun.manual_rerun_attempt_id == 42
    assert rerun.error == "manual_rerun_from_attempt:42"
    assert rerun.trigger_text == "@Alex Chen 重新看"
    claimed = store.claim_reply_tasks(limit=1)
    assert [claimed_task.id for claimed_task in claimed] == [task.id]


def test_manual_rerun_always_allocates_a_new_execution_generation(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    _enqueue_universal_reply_task(store)

    rerun_args = {
        "conversation_id": "cid-universal",
        "conversation_title": "Universal",
        "single_chat": False,
        "trigger_message_id": "msg-universal",
        "trigger_create_time": "2026-07-20 10:01:00",
        "trigger_sender": "Derek",
        "trigger_text": "Run it again",
        "trigger_message_json": "{}",
        "attempt_id": 42,
    }
    first = store.enqueue_manual_rerun_reply_task(**rerun_args)
    second = store.enqueue_manual_rerun_reply_task(**rerun_args)

    assert first.execution_generation
    assert second.execution_generation
    assert first.execution_generation != "initial"
    assert second.execution_generation != "initial"
    assert first.execution_generation != second.execution_generation


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
        available_at="2026-05-13 17:01:00",
    )

    before = store.claim_reply_tasks(limit=1, now="2026-05-13 17:00:59")
    after = store.claim_reply_tasks(limit=1, now="2026-05-13 17:01:00")

    assert before == []
    assert len(after) == 1
    assert after[0].attempts == 2
    assert after[0].available_at == ""
    assert after[0].error == "temporary failure"


def test_complete_reply_task_for_message_marks_matching_task_done(tmp_path: Path):
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
    store.fail_reply_task(claimed.id, "old failure")

    updated = store.complete_reply_task_for_message("cid-1", "msg-1")

    tasks = store.list_reply_tasks(limit=1)
    assert updated == 1
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
    store.complete_reply_task(claimed[0].id)

    tasks = store.list_reply_tasks(statuses=("pending", "processing", "failed"))

    assert [task.trigger_message_id for task in tasks] == ["msg-2"]


def test_reset_stale_processing_reply_tasks_requeues_orphans(tmp_path: Path):
    db_path = tmp_path / "worker.sqlite3"
    store = AutoReplyStore(db_path)
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
    with sqlite3.connect(db_path) as db:
        db.execute(
            "update reply_tasks set locked_at=datetime('now', '-31 minutes') where id=?",
            (claimed[0].id,),
        )

    reset_count = store.reset_stale_processing_reply_tasks(30 * 60)
    reclaimed = store.claim_reply_tasks(limit=1)

    assert reset_count == 1
    assert reclaimed[0].id == claimed[0].id
    assert reclaimed[0].attempts == 2


def test_reset_processing_reply_tasks_requeues_all_processing_on_startup(
    tmp_path: Path,
):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    store.enqueue_reply_task(
        conversation_id="cid-1",
        conversation_title="Friday",
        single_chat=True,
        trigger_message_id="msg-1",
        trigger_create_time="2026-05-13 18:00:00",
        trigger_sender="Mina",
        trigger_text="第一条",
    )
    claimed = store.claim_reply_tasks(limit=1)

    recovered = store.reset_processing_reply_tasks()
    reclaimed = store.claim_reply_tasks(limit=1)

    assert [task.id for task in recovered] == [claimed[0].id]
    assert reclaimed[0].id == claimed[0].id
    assert reclaimed[0].status == "processing"
    assert reclaimed[0].attempts == 2


def test_complete_unfinished_reply_tasks_before_trigger_marks_older_tasks_done(
    tmp_path: Path,
):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    store.enqueue_reply_task(
        conversation_id="cid-1",
        conversation_title="Friday",
        single_chat=True,
        trigger_message_id="msg-old",
        trigger_create_time="2026-05-13 18:00:00",
        trigger_sender="Mina",
        trigger_text="第一条",
    )
    old_task = store.claim_reply_tasks(limit=1)[0]
    store.enqueue_reply_task(
        conversation_id="cid-1",
        conversation_title="Friday",
        single_chat=True,
        trigger_message_id="msg-new",
        trigger_create_time="2026-05-13 18:01:00",
        trigger_sender="Mina",
        trigger_text="第二条",
    )
    new_task = store.claim_reply_tasks(limit=1)[0]

    completed = store.complete_unfinished_reply_tasks_before_trigger(
        conversation_id="cid-1",
        trigger_create_time=new_task.trigger_create_time,
        exclude_task_id=new_task.id,
    )

    tasks = {task.trigger_message_id: task for task in store.list_reply_tasks()}
    assert [task.id for task in completed] == [old_task.id]
    assert tasks["msg-old"].status == "done"
    assert tasks["msg-old"].locked_at is None
    assert tasks["msg-new"].status == "processing"


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

    store.requeue_reply_task(claimed[0].id, "temporary dws auth failure")
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

    store.defer_reply_task_for_authorization(claimed[0].id, "authorization required")
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


def test_universal_plan_execution_get_or_create_keeps_first_snapshot(tmp_path: Path):
    db_path = tmp_path / "worker.sqlite3"
    store = AutoReplyStore(db_path)
    task_id = _enqueue_universal_reply_task(store)
    context = _universal_context(task_id)
    first_plan = _universal_plan(reason="First plan")

    first = store.create_universal_plan_execution(context, first_plan)
    repeated = store.create_universal_plan_execution(
        context,
        _universal_plan(reason="Replacement must not win"),
    )
    loaded = store.load_universal_plan_execution(context)

    assert repeated.execution_scope_id == first.execution_scope_id
    assert repeated.execution_generation == "initial"
    assert repeated.plan.reason == "First plan"
    assert loaded == first
    with sqlite3.connect(db_path) as db:
        row = db.execute(
            "select plan_json, context_hash, context_json from universal_plan_executions"
        ).fetchone()
    plan_json, context_hash, context_json = row
    assert plan_json == json.dumps(
        first_plan.model_dump(mode="json"),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    assert context_json == canonical_universal_context_json(context)
    assert context_hash == universal_context_sha256(context)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda context: replace(
            context,
            context_messages=tuple(reversed(context.context_messages)),
        ),
        lambda context: replace(
            context,
            context_messages=(
                replace(context.context_messages[0], content="Changed message"),
                *context.context_messages[1:],
            ),
        ),
        lambda context: replace(
            context,
            required_dependencies=("memory", "dws"),
        ),
        lambda context: replace(context, dry_run=True),
    ],
    ids=["message-order", "message-field", "dependencies", "dry-run"],
)
def test_universal_plan_execution_rejects_context_drift_on_load_and_create(
    tmp_path: Path,
    mutate,
):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    task_id = _enqueue_universal_reply_task(store)
    context = _universal_context(
        task_id,
        context_messages=(
            UniversalContextMessage("Alex", "msg-prior", "Earlier message"),
            UniversalContextMessage("Derek", "msg-universal", "Handle this task"),
        ),
        required_dependencies=("dws", "memory"),
    )
    store.create_universal_plan_execution(context, _universal_plan())
    drifted = mutate(context)

    with pytest.raises(ValueError, match="context identity mismatch"):
        store.load_universal_plan_execution(drifted)
    with pytest.raises(ValueError, match="context identity mismatch"):
        store.create_universal_plan_execution(
            drifted,
            _universal_plan(reason="Drifted context must not reuse scope"),
        )


def test_universal_plan_execution_round_trip_is_deep_copied(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    task_id = _enqueue_universal_reply_task(store)
    context = _universal_context(task_id)
    source = _universal_plan()

    created = store.create_universal_plan_execution(context, source)
    source.reason = "Mutated source"
    source.actions[0].payload["nested"]["a"] = 99
    created.plan.reason = "Mutated returned snapshot"
    created.plan.actions[0].payload["nested"]["a"] = 88
    loaded = store.load_universal_plan_execution(context)

    assert loaded is not None
    assert loaded.plan.reason == "Handle the task"
    assert loaded.plan.actions[0].payload["nested"]["a"] == 1


def test_universal_plan_execution_uses_new_scope_for_new_generation(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    task_id = _enqueue_universal_reply_task(store)
    initial = store.create_universal_plan_execution(
        _universal_context(task_id), _universal_plan()
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
        attempt_id=7,
    )

    current = store.create_universal_plan_execution(
        _universal_context(
            task_id,
            execution_generation=rerun.execution_generation,
            trigger_text="Run it again",
            force_new_decision=True,
        ),
        _universal_plan(reason="Rerun plan"),
    )

    assert current.execution_scope_id != initial.execution_scope_id
    assert current.execution_generation == rerun.execution_generation


def test_universal_plan_execution_rejects_stale_task_generation(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    task_id = _enqueue_universal_reply_task(store)
    stale_context = _universal_context(task_id)
    store.create_universal_plan_execution(stale_context, _universal_plan())
    store.enqueue_manual_rerun_reply_task(
        conversation_id="cid-universal",
        conversation_title="Universal",
        single_chat=False,
        trigger_message_id="msg-universal",
        trigger_create_time="2026-07-20 10:01:00",
        trigger_sender="Derek",
        trigger_text="Run it again",
        trigger_message_json="{}",
    )

    with pytest.raises(ValueError, match="execution generation mismatch"):
        store.load_universal_plan_execution(stale_context)
    with pytest.raises(ValueError, match="execution generation mismatch"):
        store.create_universal_plan_execution(
            stale_context,
            _universal_plan(reason="Stale overwrite"),
        )


def test_universal_plan_execution_is_atomic_across_store_instances(tmp_path: Path):
    db_path = tmp_path / "worker.sqlite3"
    first_store = AutoReplyStore(db_path)
    task_id = _enqueue_universal_reply_task(first_store)
    second_store = AutoReplyStore(db_path)
    context = _universal_context(task_id)

    first = first_store.create_universal_plan_execution(
        context, _universal_plan(reason="First writer")
    )
    second = second_store.create_universal_plan_execution(
        context, _universal_plan(reason="Second writer")
    )

    assert second.execution_scope_id == first.execution_scope_id
    assert second.plan.reason == "First writer"


def test_universal_plan_execution_database_uniqueness_is_enforced(tmp_path: Path):
    db_path = tmp_path / "worker.sqlite3"
    store = AutoReplyStore(db_path)
    task_id = _enqueue_universal_reply_task(store)
    created = store.create_universal_plan_execution(
        _universal_context(task_id), _universal_plan()
    )

    with sqlite3.connect(db_path) as db:
        unique_column_sets = {
            tuple(
                row[2]
                for row in db.execute(f"pragma index_info('{index[1]}')").fetchall()
            )
            for index in db.execute(
                "pragma index_list('universal_plan_executions')"
            ).fetchall()
            if index[2]
        }
        assert ("reply_task_id", "execution_generation") in unique_column_sets
        plan_json = db.execute(
            "select plan_json from universal_plan_executions where execution_scope_id=?",
            (created.execution_scope_id,),
        ).fetchone()[0]
        with pytest.raises(sqlite3.IntegrityError):
            db.execute(
                """
                insert into universal_plan_executions (
                    execution_scope_id, reply_task_id, execution_generation, plan_json
                ) values (?, ?, ?, ?)
                """,
                ("conflicting-scope", task_id, "initial", plan_json),
            )
        with pytest.raises(sqlite3.IntegrityError):
            db.execute(
                """
                insert into universal_plan_executions (
                    execution_scope_id, reply_task_id, execution_generation, plan_json
                ) values (?, ?, ?, ?)
                """,
                (created.execution_scope_id, task_id, "other-generation", plan_json),
            )


def test_universal_plan_execution_strictly_parses_persisted_plan(tmp_path: Path):
    db_path = tmp_path / "worker.sqlite3"
    store = AutoReplyStore(db_path)
    task_id = _enqueue_universal_reply_task(store)
    context = _universal_context(task_id)
    created = store.create_universal_plan_execution(
        context, _universal_plan()
    )
    with sqlite3.connect(db_path) as db:
        db.execute(
            "update universal_plan_executions set plan_json='{}' where execution_scope_id=?",
            (created.execution_scope_id,),
        )

    with pytest.raises(ValueError):
        store.load_universal_plan_execution(context)


def test_store_migrates_legacy_plan_context_columns_and_fails_closed(
    tmp_path: Path,
):
    db_path = tmp_path / "worker.sqlite3"
    plan = _universal_plan()
    plan_json = json.dumps(
        plan.model_dump(mode="json"),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    with sqlite3.connect(db_path) as db:
        db.executescript(
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
                trigger_message_json text not null default '{}',
                available_at text not null default '',
                force_new_decision integer not null default 0,
                oa_url text not null default '',
                manual_rerun_attempt_id integer not null default 0,
                execution_generation text not null default 'initial',
                status text not null default 'pending',
                attempts integer not null default 0,
                locked_at text,
                error text not null default '',
                created_at text not null default current_timestamp,
                updated_at text not null default current_timestamp,
                unique(conversation_id, trigger_message_id)
            );
            create table universal_plan_executions (
                execution_scope_id text primary key,
                reply_task_id integer not null,
                execution_generation text not null,
                plan_json text not null,
                status text not null default 'active',
                created_at text not null default current_timestamp,
                updated_at text not null default current_timestamp,
                unique(reply_task_id, execution_generation),
                foreign key(reply_task_id) references reply_tasks(id)
            );
            insert into reply_tasks (
                conversation_id, conversation_title, single_chat,
                trigger_message_id, trigger_create_time, trigger_sender, trigger_text
            ) values (
                'cid-universal', 'Universal', 0, 'msg-universal',
                '2026-07-20 10:00:00', 'Derek', 'Handle this task'
            );
            """
        )
        db.execute(
            """
            insert into universal_plan_executions (
                execution_scope_id, reply_task_id, execution_generation, plan_json
            ) values ('legacy-scope', 1, 'initial', ?)
            """,
            (plan_json,),
        )

    store = AutoReplyStore(db_path)
    context = _universal_context(1)
    with store._connect() as db:
        columns = {
            column["name"]: column
            for column in db.execute(
                "pragma table_info(universal_plan_executions)"
            ).fetchall()
        }
        row = db.execute(
            """
            select context_hash, context_json
            from universal_plan_executions where execution_scope_id='legacy-scope'
            """
        ).fetchone()
    assert columns["context_hash"]["notnull"] == 1
    assert columns["context_hash"]["dflt_value"] == "''"
    assert columns["context_json"]["notnull"] == 1
    assert columns["context_json"]["dflt_value"] == "''"
    assert dict(row) == {"context_hash": "", "context_json": ""}

    with pytest.raises(ValueError, match="legacy plan context missing"):
        store.load_universal_plan_execution(context)

    plan_execution = UniversalPlanExecution("legacy-scope", "initial", plan)
    execution = build_universal_action_execution(
        context,
        plan_execution,
        plan_execution.plan.actions[0],
        0,
    )
    with pytest.raises(ValueError, match="legacy plan context missing"):
        store.claim_universal_action_execution(execution)


def test_universal_action_started_survives_restart_as_unknown_and_failed_reclaims(
    tmp_path: Path,
):
    db_path = tmp_path / "worker.sqlite3"
    store = AutoReplyStore(db_path)
    task_id = _enqueue_universal_reply_task(store)
    execution = _universal_action_execution(store, task_id)

    assert (
        store.get_universal_action_execution_state(execution)
        is UniversalActionExecutionState.NOT_STARTED
    )
    assert (
        store.claim_universal_action_execution(execution)
        is UniversalActionExecutionState.NOT_STARTED
    )
    assert (
        store.get_universal_action_execution_state(execution)
        is UniversalActionExecutionState.UNKNOWN
    )
    assert (
        store.claim_universal_action_execution(execution)
        is UniversalActionExecutionState.UNKNOWN
    )

    reopened = AutoReplyStore(db_path)
    assert (
        reopened.get_universal_action_execution_state(execution)
        is UniversalActionExecutionState.UNKNOWN
    )
    reopened.mark_universal_action_execution_failed(execution, "no side effect")
    assert (
        reopened.get_universal_action_execution_state(execution)
        is UniversalActionExecutionState.NOT_STARTED
    )
    assert (
        reopened.claim_universal_action_execution(execution)
        is UniversalActionExecutionState.NOT_STARTED
    )
    reopened.mark_universal_action_execution_unknown(execution, "outcome unavailable")
    assert (
        reopened.get_universal_action_execution_state(execution)
        is UniversalActionExecutionState.UNKNOWN
    )
    assert (
        reopened.claim_universal_action_execution(execution)
        is UniversalActionExecutionState.UNKNOWN
    )
    with reopened._connect() as db:
        row = db.execute(
            "select status, error from universal_action_executions where execution_id=?",
            (execution.execution_id,),
        ).fetchone()
    assert dict(row) == {"status": "unknown", "error": "outcome unavailable"}


def test_universal_action_success_is_persistent_and_idempotent(tmp_path: Path):
    db_path = tmp_path / "worker.sqlite3"
    store = AutoReplyStore(db_path)
    task_id = _enqueue_universal_reply_task(store)
    execution = _universal_action_execution(store, task_id)

    store.claim_universal_action_execution(execution)
    store.complete_universal_action_execution(
        execution,
        attempt_id=17,
        result_json='{"ok":true}',
    )

    reopened = AutoReplyStore(db_path)
    assert (
        reopened.get_universal_action_execution_state(execution)
        is UniversalActionExecutionState.SUCCEEDED
    )
    assert (
        reopened.claim_universal_action_execution(execution)
        is UniversalActionExecutionState.SUCCEEDED
    )
    with reopened._connect() as db:
        row = db.execute(
            """
            select status, attempt_id, result_json, error, completed_at
            from universal_action_executions where execution_id=?
            """,
            (execution.execution_id,),
        ).fetchone()
    assert row["status"] == "succeeded"
    assert row["attempt_id"] == 17
    assert row["result_json"] == '{"ok":true}'
    assert row["error"] == ""
    assert row["completed_at"]


def test_universal_action_complete_and_marks_require_started_claim(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    task_id = _enqueue_universal_reply_task(store)
    execution = _universal_action_execution(store, task_id)

    with pytest.raises(ValueError, match="must be started"):
        store.complete_universal_action_execution(execution)
    with pytest.raises(ValueError, match="must be started"):
        store.mark_universal_action_execution_unknown(execution, "unknown")
    with pytest.raises(ValueError, match="must be started"):
        store.mark_universal_action_execution_failed(execution, "failed")


@pytest.mark.parametrize(
    "mutate",
    [
        lambda execution: replace(execution, execution_id="wrong-id"),
        lambda execution: replace(execution, execution_scope_id="wrong-scope"),
        lambda execution: replace(execution, action_index=1),
        lambda execution: replace(execution, action_hash="0" * 64),
        lambda execution: replace(
            execution,
            context=replace(execution.context, execution_generation="stale-generation"),
        ),
        lambda execution: replace(
            execution,
            action=execution.action.model_copy(
                update={"reason": "Changed persisted action"}, deep=True
            ),
        ),
    ],
    ids=["execution-id", "scope", "index", "hash", "generation", "action-json"],
)
def test_universal_action_identity_drift_fails_closed(tmp_path: Path, mutate):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    task_id = _enqueue_universal_reply_task(store)
    execution = _universal_action_execution(store, task_id)
    store.claim_universal_action_execution(execution)

    with pytest.raises(ValueError):
        store.get_universal_action_execution_state(mutate(execution))


def test_universal_action_rejects_persisted_action_json_drift(tmp_path: Path):
    db_path = tmp_path / "worker.sqlite3"
    store = AutoReplyStore(db_path)
    task_id = _enqueue_universal_reply_task(store)
    execution = _universal_action_execution(store, task_id)
    store.claim_universal_action_execution(execution)
    with sqlite3.connect(db_path) as db:
        db.execute(
            "update universal_action_executions set action_json='{}' where execution_id=?",
            (execution.execution_id,),
        )

    with pytest.raises(ValueError, match="action identity mismatch"):
        store.get_universal_action_execution_state(execution)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda context: replace(
            context,
            context_messages=tuple(reversed(context.context_messages)),
        ),
        lambda context: replace(
            context,
            context_messages=(
                replace(context.context_messages[0], sender_name="Changed sender"),
                *context.context_messages[1:],
            ),
        ),
        lambda context: replace(
            context,
            required_dependencies=("memory", "dws"),
        ),
        lambda context: replace(context, dry_run=True),
    ],
    ids=["message-order", "message-field", "dependencies", "dry-run"],
)
def test_universal_action_rejects_bound_context_drift(tmp_path: Path, mutate):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    task_id = _enqueue_universal_reply_task(store)
    context = _universal_context(
        task_id,
        context_messages=(
            UniversalContextMessage("Alex", "msg-prior", "Earlier message"),
            UniversalContextMessage("Derek", "msg-universal", "Handle this task"),
        ),
        required_dependencies=("dws", "memory"),
    )
    plan_execution = store.create_universal_plan_execution(
        context,
        _universal_plan(),
    )
    execution = build_universal_action_execution(
        context,
        plan_execution,
        plan_execution.plan.actions[0],
        0,
    )

    with pytest.raises(ValueError, match="context identity mismatch"):
        store.get_universal_action_execution_state(
            replace(execution, context=mutate(context))
        )


def test_universal_action_rejects_inactive_plan_scope(tmp_path: Path):
    db_path = tmp_path / "worker.sqlite3"
    store = AutoReplyStore(db_path)
    task_id = _enqueue_universal_reply_task(store)
    execution = _universal_action_execution(store, task_id)
    with sqlite3.connect(db_path) as db:
        db.execute(
            "update universal_plan_executions set status='closed' where execution_scope_id=?",
            (execution.execution_scope_id,),
        )

    with pytest.raises(ValueError, match="plan execution is not active"):
        store.claim_universal_action_execution(execution)


def test_universal_action_rejects_stale_task_generation(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    task_id = _enqueue_universal_reply_task(store)
    execution = _universal_action_execution(store, task_id)
    store.enqueue_manual_rerun_reply_task(
        conversation_id="cid-universal",
        conversation_title="Universal",
        single_chat=False,
        trigger_message_id="msg-universal",
        trigger_create_time="2026-07-20 10:01:00",
        trigger_sender="Derek",
        trigger_text="Run it again",
        trigger_message_json="{}",
    )

    with pytest.raises(ValueError, match="execution generation mismatch"):
        store.get_universal_action_execution_state(execution)
    with pytest.raises(ValueError, match="execution generation mismatch"):
        store.claim_universal_action_execution(execution)


def test_universal_action_database_uniqueness_and_canonical_json(tmp_path: Path):
    db_path = tmp_path / "worker.sqlite3"
    store = AutoReplyStore(db_path)
    task_id = _enqueue_universal_reply_task(store)
    execution = _universal_action_execution(store, task_id)
    store.claim_universal_action_execution(execution)

    with sqlite3.connect(db_path) as db:
        db.row_factory = sqlite3.Row
        row = db.execute(
            "select * from universal_action_executions where execution_id=?",
            (execution.execution_id,),
        ).fetchone()
        assert row["action_json"] == json.dumps(
            execution.action.model_dump(mode="json"),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        with pytest.raises(sqlite3.IntegrityError):
            db.execute(
                """
                insert into universal_action_executions (
                    execution_id, execution_scope_id, action_index, action_kind,
                    action_hash, action_json, status
                ) values (?, ?, ?, ?, ?, ?, 'started')
                """,
                (
                    "different-execution-id",
                    execution.execution_scope_id,
                    execution.action_index,
                    execution.action.kind.value,
                    execution.action_hash,
                    row["action_json"],
                ),
            )


def test_sent_reply_exists_matches_exact_conversation_and_trigger(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    store.record_sent_reply("cid-1", "msg-1", "Sent")

    assert store.sent_reply_exists("cid-1", "msg-1") is True
    assert store.sent_reply_exists("cid-1", "msg-other") is False
    assert store.sent_reply_exists("cid-other", "msg-1") is False
