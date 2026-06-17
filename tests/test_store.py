import json
from pathlib import Path
import sqlite3

import pytest

from app.store import AutoReplyStore


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
