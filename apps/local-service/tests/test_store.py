from pathlib import Path
import sqlite3

from ceo_agent_service.store import AutoReplyStore


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


def test_reset_codex_sessions_clears_conversation_mapping_only(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    store.upsert_conversation("cid-1", "Friday", False, "session-1")
    attempt_id = store.record_reply_attempt(
        conversation_id="cid-1",
        conversation_title="Friday",
        trigger_message_id="msg-1",
        trigger_sender="Xiaomin",
        trigger_text="@Derek Zen 这个怎么处理？",
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
                '@Derek Zen 这个怎么处理？', 'send_reply', 'general', 'sent'
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
                '@Derek Zen 这个怎么处理？', 'send_reply', 'general',
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


def test_handoff_lifecycle(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    store.enter_handoff("cid-1", "msg-1", "分身答错了", handoff_message_create_time="2026-05-13 18:00:00")

    assert store.is_in_handoff("cid-1") is True
    assert store.get_handoff_message_create_time("cid-1") == "2026-05-13 18:00:00"

    store.clear_handoff("cid-1", "msg-2")

    assert store.is_in_handoff("cid-1") is False


def test_records_sent_reply_and_error(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")

    store.record_sent_reply(
        "cid-1",
        "msg-1",
        "收到（by磊哥分身）",
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
    store.record_sent_reply("cid-1", "msg-1", "收到（by磊哥分身）", recall_key="key-1")
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
        trigger_text="@Derek Zen 这个怎么处理？",
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
        final_reply_text="先收敛问题（by磊哥分身）",
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
    assert attempt.final_reply_text == "先收敛问题（by磊哥分身）"
    assert attempt.send_status == "sent"
    assert attempt.retry_count == 1
    assert attempt.reviewed_at is not None
    assert attempt.reviewer_feedback == "语气可以，但需要更具体"
    assert attempt.corrected_reply_text == "先明确负责人和时间点。"


def test_get_latest_reply_attempt_for_trigger(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    first_id = store.record_reply_attempt(
        conversation_id="cid-1",
        conversation_title="技术部",
        trigger_message_id="msg-1",
        trigger_sender="Xiaomin",
        trigger_text="@Derek Zen 这个怎么处理？",
        action="send_reply",
        sensitivity_kind="general",
        send_status="failed",
    )
    second_id = store.record_reply_attempt(
        conversation_id="cid-1",
        conversation_title="技术部",
        trigger_message_id="msg-1",
        trigger_sender="Xiaomin",
        trigger_text="@Derek Zen 这个怎么处理？",
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
        trigger_text="@Derek Zen 这个怎么处理？",
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

    assert [attempt.id for attempt in all_attempts] == [second_id, first_id]
    assert [attempt.id for attempt in attempts] == [second_id]
    assert attempts[0].conversation_title == "HR"
    assert attempts[0].send_status == "pending"
    assert first_id != second_id


def test_lists_reviewed_reply_attempts_for_optimization(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    unreviewed_id = store.record_reply_attempt(
        conversation_id="cid-1",
        conversation_title="技术部",
        trigger_message_id="msg-1",
        trigger_sender="Xiaomin",
        trigger_text="@Derek Zen 这个怎么处理？",
        action="send_reply",
        sensitivity_kind="general",
    )
    reviewed_id = store.record_reply_attempt(
        conversation_id="cid-2",
        conversation_title="Claire",
        trigger_message_id="msg-2",
        trigger_sender="Claire",
        trigger_text="磊哥上会啦",
        action="send_reply",
        sensitivity_kind="general",
        draft_reply_text="收到，我现在进会。",
    )
    store.update_reply_attempt(
        reviewed_id,
        final_reply_text="收到，我现在进会。（by磊哥分身）",
        send_status="sent",
    )
    store.record_reply_feedback(
        reviewed_id,
        feedback="不能代 Derek 声称正在进会",
        corrected_reply_text="我让磊哥本人看一下。（by磊哥分身）",
    )

    attempts = store.list_reviewed_reply_attempts()

    assert [attempt.id for attempt in attempts] == [reviewed_id]
    assert attempts[0].reviewer_feedback == "不能代 Derek 声称正在进会"
    assert attempts[0].corrected_reply_text == "我让磊哥本人看一下。（by磊哥分身）"
    assert unreviewed_id != reviewed_id


def test_lists_errors_newest_first_with_limit(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    store.record_error("cid-1", "msg-1", "codex", "invalid json")
    store.record_error("cid-2", "msg-2", "send", "authorization required")

    all_errors = store.list_errors()
    errors = store.list_errors(limit=1)

    assert [error.kind for error in all_errors] == ["send", "codex"]
    assert len(errors) == 1
    assert errors[0].conversation_id == "cid-2"
    assert errors[0].message_id == "msg-2"
    assert errors[0].kind == "send"
    assert errors[0].detail == "authorization required"
    assert errors[0].created_at


def test_lists_run_delta_records_after_ids(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    first_attempt_id = store.record_reply_attempt(
        conversation_id="cid-1",
        conversation_title="Friday",
        trigger_message_id="msg-1",
        trigger_sender="Mina",
        trigger_text="@Derek Zen 这个怎么处理？",
        action="no_reply",
        sensitivity_kind="general",
        send_status="skipped",
    )
    store.record_sent_reply("cid-1", "msg-1", "收到（by磊哥分身）")
    store.record_error("cid-1", "msg-1", "codex", "invalid json")

    baseline_attempt_id = store.max_reply_attempt_id()
    baseline_sent_reply_id = store.max_sent_reply_id()
    baseline_error_id = store.max_error_id()

    second_attempt_id = store.record_reply_attempt(
        conversation_id="cid-2",
        conversation_title="BA",
        trigger_message_id="msg-2",
        trigger_sender="Phina",
        trigger_text="@Derek Zen 需要看一下吗？",
        action="send_reply",
        sensitivity_kind="general",
        send_status="pending",
    )
    store.record_sent_reply("cid-2", "msg-2", "可以（by磊哥分身）")
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
    )

    profile = store.get_org_user_profile("user-1")

    assert profile is not None
    assert profile.user_id == "user-1"
    assert profile.name == "张三"
    assert profile.open_dingtalk_id == "open-1"
    assert profile.manager_user_id == "manager-1"
    assert profile.department_ids == {"dept-1", "dept-2"}
    assert store.find_org_user_by_open_dingtalk_id("open-1").user_id == "user-1"
    assert [user.user_id for user in store.find_org_users_by_name("张三")] == ["user-1"]
    assert store.list_org_user_ids() == ["user-1"]


def test_org_cache_metadata_round_trip(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")

    store.set_current_user_id("derek-user-1")
    store.set_hr_department_ids({"hr-dept-1"})

    assert store.get_current_user_id() == "derek-user-1"
    assert store.get_hr_department_ids() == {"hr-dept-1"}
