from pathlib import Path

from fastapi.testclient import TestClient

from ceo_agent_service.audit_web import (
    create_audit_app,
    handle_feedback_post,
    handle_recall_post,
    handle_reviewed_message_reply,
    render_attempt_detail,
    render_attempt_list,
    render_codex_session_detail,
    render_codex_session_list,
    render_error_list,
    run_audit_web,
)
from ceo_agent_service.dingtalk_models import DingTalkConversation, DingTalkMessage
from ceo_agent_service.store import AutoReplyStore


def seed_attempt(store: AutoReplyStore) -> int:
    store.upsert_conversation(
        "cid-1",
        title="技术部",
        single_chat=False,
        codex_session_id="session-1",
    )
    attempt_id = store.record_reply_attempt(
        conversation_id="cid-1",
        conversation_title="技术部",
        trigger_message_id="msg-1",
        trigger_sender="Xiaomin",
        trigger_text="@Derek Zen 这个怎么处理？",
        action="send_reply",
        sensitivity_kind="general",
        codex_reason="direct ask",
        draft_reply_text="先按A方案走",
        codex_session_id="session-1",
        codex_transcript_start_line=2,
        codex_transcript_end_line=8,
        audit_documents_json='[{"path":"面试/岗位画像.md","relevance":"判断岗位要求"}]',
        audit_tool_events_json='[{"tool":"exec_command","command":"rg 岗位"}]',
        audit_summary="查看岗位画像后建议先按A方案走。",
    )
    store.update_reply_attempt(
        attempt_id,
        final_reply_text="> Xiaomin: 这个怎么处理？\n\n先按A方案走（by磊哥分身）",
        permission_action="allow",
        send_status="sent",
    )
    return attempt_id


def test_render_attempt_list_shows_history_rows(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    attempt_id = seed_attempt(store)

    html = render_attempt_list(store)

    assert "CEO Agent Audit" in html
    assert f"/attempts/{attempt_id}" in html
    assert "技术部" in html
    assert "Xiaomin" in html
    assert "send_reply" in html
    assert "sent" in html
    assert "attempt-feed" in html
    assert "attempt-item" in html
    assert "attempt-line" in html
    assert "问" in html
    assert "答" in html
    assert "attempt-body" not in html
    assert "&gt; Xiaomin:" not in html
    assert f"/attempts/{attempt_id}" in html
    assert "查看/反馈" in html
    assert "Codex" in html
    assert "/codex/session-1" in html


def test_render_page_includes_favicon_for_browser_tabs(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    seed_attempt(store)

    html = render_attempt_list(store)

    assert 'rel="icon"' in html
    assert 'href="data:image/svg+xml,' in html
    assert "%2300d4a4" in html


def test_empty_attempt_list_shows_db_path(tmp_path: Path):
    db_path = tmp_path / "worker.sqlite3"
    store = AutoReplyStore(db_path)

    html = render_attempt_list(store)

    assert "No reply attempts recorded." in html
    assert str(db_path) in html


def test_render_attempt_list_shows_pending_reply_tasks(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    store.enqueue_reply_task(
        conversation_id="cid-1",
        conversation_title="HR管理",
        single_chat=False,
        trigger_message_id="msg-queued",
        trigger_create_time="2026-05-28 18:00:00",
        trigger_sender="Mina",
        trigger_text="@Derek Zen(磊哥) 这个候选人怎么看？",
    )

    html = render_attempt_list(store)

    assert "Queued / processing" in html
    assert "#task-1" in html
    assert "HR管理" in html
    assert "Mina" in html
    assert "pending" in html
    assert "@Derek Zen(磊哥) 这个候选人怎么看？" in html


def test_render_attempt_list_shows_processing_reply_tasks(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    store.enqueue_reply_task(
        conversation_id="cid-1",
        conversation_title="HR管理",
        single_chat=False,
        trigger_message_id="msg-queued",
        trigger_create_time="2026-05-28 18:00:00",
        trigger_sender="Mina",
        trigger_text="@Derek Zen(磊哥) 这个候选人怎么看？",
    )
    store.claim_reply_tasks(limit=1)

    html = render_attempt_list(store)

    assert "#task-1" in html
    assert "processing" in html


def test_render_attempt_list_uses_attempt_codex_session_over_conversation(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    attempt_id = seed_attempt(store)
    store.upsert_conversation(
        "cid-1",
        title="技术部",
        single_chat=False,
        codex_session_id="new-session",
    )

    html = render_attempt_list(store)
    status, detail = render_attempt_detail(store, attempt_id)

    assert "/codex/session-1" in html
    assert "/codex/new-session" not in html
    assert status == 200
    assert "/codex/session-1" in detail
    assert "lines 2-8" in detail


def test_render_attempt_detail_shows_quality_warnings(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    attempt_id = store.record_reply_attempt(
        conversation_id="cid-1",
        conversation_title="技术部",
        trigger_message_id="msg-1",
        trigger_sender="Xiaomin",
        trigger_text="@Derek Zen 这个怎么处理？",
        action="send_reply",
        sensitivity_kind="general",
        draft_reply_text="先按A方案走",
        audit_documents_json="[]",
        audit_tool_events_json="[]",
        audit_summary="",
    )

    status, html = render_attempt_detail(store, attempt_id)

    assert status == 200
    assert "Audit quality warnings" in html
    assert "missing audit_summary" in html
    assert "missing codex_session_id" in html
    assert "send_reply has no audit tool events" in html
    assert "send_reply has no audit documents" in html


def test_render_attempt_detail_suppresses_quality_warnings_for_skipped_attempts(
    tmp_path: Path,
):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    attempt_id = store.record_reply_attempt(
        conversation_id="cid-1",
        conversation_title="张毅倜(ET)",
        trigger_message_id="msg-1",
        trigger_sender="张毅倜(ET)",
        trigger_text="[dingtalk://dingtalkclient/page/flash_minutes_detail]",
        action="no_reply",
        sensitivity_kind="general",
        audit_summary="系统类或通知类消息，无需自动回复。",
    )
    store.update_reply_attempt(attempt_id, send_status="skipped", send_error="no_reply")

    list_html = render_attempt_list(store)
    status, detail_html = render_attempt_detail(store, attempt_id)

    assert status == 200
    assert "Quality warning" not in list_html
    assert "Audit quality warnings" not in detail_html
    assert "missing codex_session_id" not in detail_html


def test_attempt_detail_renders_oa_metadata(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    attempt_id = store.record_reply_attempt(
        conversation_id="cid-1",
        conversation_title="审批通知",
        trigger_message_id="msg-1",
        trigger_sender="工作通知",
        trigger_text="[Ding]审批提醒",
        action="oa_approval",
        sensitivity_kind="internal_personnel",
        codex_reason="oa approval handled by dingtalk-oa-approval skill",
        oa_process_instance_id="proc-1",
        oa_task_id="task-1",
        oa_url="https://aflow.dingtalk.com/detail?procInstId=proc-1",
        oa_action="通过",
        oa_remark="材料完整，同意。",
        oa_action_result_json='{"errcode":0,"errmsg":"ok"}',
        send_status="skipped",
    )

    status, html = render_attempt_detail(store, attempt_id)

    assert status == 200
    assert "OA approval" in html
    assert "proc-1" in html
    assert "task-1" in html
    assert "通过" in html
    assert "材料完整，同意。" in html
    assert "https://aflow.dingtalk.com/detail?procInstId=proc-1" in html


def test_render_attempt_list_uses_distinct_action_and_status_pill_classes(
    tmp_path: Path,
):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    attempt_id = store.record_reply_attempt(
        conversation_id="cid-1",
        conversation_title="张毅倜(ET)",
        trigger_message_id="msg-1",
        trigger_sender="张毅倜(ET)",
        trigger_text="[dingtalk://dingtalkclient/page/flash_minutes_detail]",
        action="no_reply",
        sensitivity_kind="general",
        audit_summary="系统类或通知类消息，无需自动回复。",
    )
    store.update_reply_attempt(attempt_id, send_status="skipped", send_error="no_reply")

    html = render_attempt_list(store)

    assert 'class="pill action-no_reply"' in html
    assert 'class="pill status-skipped"' in html
    assert ".action-no_reply" in html


def test_render_attempt_detail_allows_explained_empty_documents(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    attempt_id = store.record_reply_attempt(
        conversation_id="cid-1",
        conversation_title="技术部",
        trigger_message_id="msg-1",
        trigger_sender="Xiaomin",
        trigger_text="@Derek Zen 这个怎么处理？",
        action="send_reply",
        sensitivity_kind="general",
        draft_reply_text="先按A方案走",
        codex_session_id="session-1",
        audit_documents_json="[]",
        audit_tool_events_json='[{"tool":"exec_command","command":"rg 上下文"}]',
        audit_summary="只需上下文判断，当前消息已经足够确认处理方式。",
    )

    status, html = render_attempt_detail(store, attempt_id)

    assert status == 200
    assert "send_reply has no audit documents" not in html


def test_render_attempt_detail_allows_explained_empty_tool_events(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    attempt_id = store.record_reply_attempt(
        conversation_id="cid-1",
        conversation_title="技术部",
        trigger_message_id="msg-1",
        trigger_sender="Xiaomin",
        trigger_text="@Derek Zen 这个怎么处理？",
        action="send_reply",
        sensitivity_kind="general",
        draft_reply_text="先按A方案走",
        codex_session_id="session-1",
        audit_documents_json="[]",
        audit_tool_events_json="[]",
        audit_summary="只需上下文判断，当前消息已经足够确认处理方式。",
    )

    status, html = render_attempt_detail(store, attempt_id)

    assert status == 200
    assert "send_reply has no audit tool events" not in html


def test_fastapi_app_serves_history_routes(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    attempt_id = seed_attempt(store)
    app = create_audit_app(store.path)
    client = TestClient(app)

    response = client.get("/")
    detail_response = client.get(f"/attempts/{attempt_id}")

    assert response.status_code == 200
    assert "CEO Agent Audit" in response.text
    assert "技术部" in response.text
    assert detail_response.status_code == 200
    assert "Codex local history" in detail_response.text


def test_fastapi_app_records_feedback_and_redirects(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    attempt_id = seed_attempt(store)
    app = create_audit_app(store.path)
    client = TestClient(app)

    response = client.post(
        f"/attempts/{attempt_id}/feedback",
        data={"feedback": "需要更严谨", "corrected_reply": "先看材料"},
        follow_redirects=False,
    )

    attempt = store.get_reply_attempt(attempt_id)
    assert response.status_code == 303
    assert response.headers["location"] == f"/attempts/{attempt_id}"
    assert attempt is not None
    assert attempt.reviewer_feedback == "需要更严谨"
    assert attempt.corrected_reply_text == "先看材料"


def test_render_attempt_detail_shows_full_decision_and_feedback_form(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    attempt_id = seed_attempt(store)
    store.record_sent_reply("cid-1", "msg-1", "先按A方案走（by磊哥分身）")

    status, html = render_attempt_detail(store, attempt_id)

    assert status == 200
    assert html.index("生成回复") < html.index("Trigger")
    assert html.index("先按A方案走（by磊哥分身）") < html.index("Trigger")
    assert "review-grid" in html
    assert "reply-pre" in html
    assert "@Derek Zen 这个怎么处理？" in html
    assert "direct ask" in html
    assert "Audit summary" in html
    assert "查看岗位画像后建议先按A方案走" in html
    assert "Audit documents" in html
    assert "面试/岗位画像.md" in html
    assert "Audit tool events" in html
    assert "rg 岗位" in html
    assert "json-pre" in html
    assert "json-key" in html
    assert "json-string" in html
    assert "\n  " in html
    assert "先按A方案走" in html
    assert "permission" in html
    assert "记录反馈 / 修改意见" in html
    assert "反馈意见" in html
    assert "建议回复" in html
    assert f'action="/attempts/{attempt_id}/feedback"' in html
    assert "textarea" in html
    assert "Codex local history" in html
    assert "/codex/session-1" in html
    assert "撤销发送" in html
    assert "撤销不可用" in html
    assert "当前发送方式不支持" in html


def test_attempt_list_uses_single_review_feedback_entrypoint(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    attempt_id = seed_attempt(store)

    html = render_attempt_list(store)

    assert f'href="/attempts/{attempt_id}"' in html
    assert f'href="/attempts/{attempt_id}#feedback"' not in html
    assert "查看/反馈" in html
    assert ">Codex</a>" in html


def test_render_codex_session_list_shows_conversation_sessions(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    attempt_id = seed_attempt(store)

    html = render_codex_session_list(store)

    assert "Codex Sessions" in html
    assert "技术部" in html
    assert "cid-1" in html
    assert "/codex/session-1" in html
    assert "History" in html
    assert f"/attempts/{attempt_id}" in html
    assert "send_reply" in html
    assert "sent" in html


def test_render_codex_session_detail_uses_local_rendered_history(
    tmp_path: Path,
):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    attempt_id = seed_attempt(store)
    codex_home = tmp_path / ".codex"
    session_path = (
        codex_home
        / "sessions"
        / "2026"
        / "05"
        / "14"
        / "rollout-2026-05-14T12-00-00-session-1.jsonl"
    )
    session_path.parent.mkdir(parents=True)
    session_path.write_text(
        "\n".join(
            [
                '{"timestamp":"2026-05-14T12:00:00Z","type":"session_meta","payload":{"id":"session-1","cwd":"/Users/derek/Documents/memory"}}',
                '{"timestamp":"2026-05-14T12:00:01Z","type":"response_item","payload":{"type":"message","role":"assistant","content":[{"type":"output_text","text":"已查看岗位画像"}]}}',
            ]
        ),
        encoding="utf-8",
    )

    status, html = render_codex_session_detail(
        "session-1",
        codex_home=codex_home,
        store=store,
    )

    assert status == 200
    assert "Codex Session session-1" in html
    assert str(session_path) in html
    assert "已查看岗位画像" in html
    assert "Related history" in html
    assert f"/attempts/{attempt_id}" in html
    assert "@Derek Zen 这个怎么处理？" in html
    assert '<details class="event event-assistant" open>' in html
    assert '<details class="event event-session">' in html
    assert '<time>2026-05-14T12:00:01Z</time>' in html


def test_render_codex_session_detail_returns_404_when_missing(tmp_path: Path):
    status, html = render_codex_session_detail("missing", codex_home=tmp_path)

    assert status == 404
    assert "Codex session not found" in html


def test_render_attempt_detail_shows_recall_button_when_recall_key_exists(
    tmp_path: Path,
):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    attempt_id = seed_attempt(store)
    store.record_sent_reply(
        "cid-1",
        "msg-1",
        "先按A方案走（by磊哥分身）",
        recall_key="key-1",
    )

    status, html = render_attempt_detail(store, attempt_id)

    assert status == 200
    assert "撤销发送" in html
    assert f'action="/attempts/{attempt_id}/recall"' in html
    assert "确认撤销这条已发送消息？" in html
    assert "撤销这条消息" in html


def test_render_attempt_detail_returns_404_when_missing(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")

    status, html = render_attempt_detail(store, 99)

    assert status == 404
    assert "Attempt not found" in html


def test_handle_feedback_post_updates_attempt_and_redirects(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    attempt_id = seed_attempt(store)
    body = (
        "feedback=%E9%9C%80%E8%A6%81%E6%9B%B4%E4%B8%A5%E8%B0%A8"
        "&corrected_reply=%E5%85%88%E7%9C%8B%E6%9D%90%E6%96%99"
    ).encode()

    status, headers, html = handle_feedback_post(store, attempt_id, body)

    attempt = store.get_reply_attempt(attempt_id)
    assert status == 303
    assert headers["Location"] == f"/attempts/{attempt_id}"
    assert html == ""
    assert attempt is not None
    assert attempt.reviewer_feedback == "需要更严谨"
    assert attempt.corrected_reply_text == "先看材料"


def test_handle_recall_post_calls_dws_and_records_success(tmp_path: Path):
    class FakeDws:
        def __init__(self):
            self.calls = []

        def recall_bot_message(self, conversation_id, process_query_key):
            self.calls.append((conversation_id, process_query_key))
            return {"success": True}

    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    attempt_id = seed_attempt(store)
    store.record_sent_reply(
        "cid-1",
        "msg-1",
        "先按A方案走（by磊哥分身）",
        recall_key="key-1",
    )
    dws = FakeDws()

    status, headers, html = handle_recall_post(store, dws, attempt_id)

    sent_reply = store.get_sent_reply("cid-1", "msg-1")
    assert status == 303
    assert headers["Location"] == f"/attempts/{attempt_id}"
    assert html == ""
    assert dws.calls == [("cid-1", "key-1")]
    assert sent_reply is not None
    assert sent_reply.recall_status == "recalled"
    assert sent_reply.recalled_at is not None


def test_handle_recall_post_blocks_without_recall_key(tmp_path: Path):
    class FakeDws:
        def recall_bot_message(self, conversation_id, process_query_key):
            raise AssertionError("should not call dws")

    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    attempt_id = seed_attempt(store)
    store.record_sent_reply("cid-1", "msg-1", "先按A方案走（by磊哥分身）")

    status, headers, html = handle_recall_post(store, FakeDws(), attempt_id)

    assert status == 400
    assert headers == {}
    assert "撤销不可用" in html


def test_handle_reviewed_message_reply_matches_sender_group_and_text(
    monkeypatch,
    tmp_path: Path,
):
    class FakeDws:
        def __init__(self):
            self.sent_messages = []
            self.reply_messages = []

        def search_conversations(self, query):
            assert query == "【招聘】大模型项目经理/大模型数据解决方案专家"
            return [
                DingTalkConversation(
                    open_conversation_id="cid-1",
                    title="【招聘】大模型项目经理/大模型数据解决方案专家",
                    single_chat=False,
                    unread_point=0,
                )
            ]

        def read_mentioned_messages(self, conversation, limit=50):
            assert conversation.open_conversation_id == "cid-1"
            assert limit == 100
            return [
                DingTalkMessage(
                    open_conversation_id="cid-1",
                    open_message_id="msg-1",
                    conversation_title=conversation.title,
                    single_chat=False,
                    sender_name="Mina 邹",
                    sender_open_dingtalk_id="open-mina",
                    sender_user_id="user-mina",
                    create_time="2026-05-25 13:30:26",
                    content="@Derek Zen(磊哥) 磊哥分身，大模型项目经理需要具备什么能力",
                )
            ]

        def read_recent_messages(self, conversation):
            return []

        def read_unread_messages(self, conversation):
            return []

        def send_message(
            self,
            conversation_id,
            text,
            at_users=None,
            user_id=None,
            open_dingtalk_id=None,
        ):
            self.sent_messages.append((conversation_id, text, at_users, user_id))
            return {"result": {"processQueryKey": "recall-1"}}

        def reply_message(
            self,
            conversation_id,
            ref_message_id,
            ref_sender_open_dingtalk_id,
            text,
        ):
            self.reply_messages.append(
                (conversation_id, ref_message_id, ref_sender_open_dingtalk_id, text)
            )
            return {"result": {"processQueryKey": "recall-1"}}

    monkeypatch.setattr(
        "ceo_agent_service.worker.send_macos_notification",
        lambda **kwargs: None,
    )
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    dws = FakeDws()

    result = handle_reviewed_message_reply(
        store,
        dws,
        user_name="Mina 邹",
        group_name="【招聘】大模型项目经理/大模型数据解决方案专家",
        message_str="@Derek Zen(磊哥) 磊哥分身，大模型项目经理需要具备什么能力",
        reply_text="这个岗位核心看业务拆解、模型理解、项目推进和学习速度。",
    )

    attempt = store.get_reply_attempt(result["attempt_id"])
    sent_reply = store.get_sent_reply("cid-1", "msg-1")
    assert result["send_status"] == "sent"
    assert attempt is not None
    assert attempt.trigger_sender == "Mina 邹"
    assert attempt.trigger_text == "@Derek Zen(磊哥) 磊哥分身，大模型项目经理需要具备什么能力"
    assert (
        attempt.final_reply_text
        == "这个岗位核心看业务拆解、模型理解、项目推进和学习速度。（by磊哥分身）"
    )
    assert dws.sent_messages == []
    assert dws.reply_messages == [
        (
            "cid-1",
            "msg-1",
            "open-mina",
            attempt.final_reply_text,
        )
    ]
    assert sent_reply is not None
    assert sent_reply.recall_key == "recall-1"


def test_handle_reviewed_message_reply_uses_stored_group_and_recent_message(
    monkeypatch,
    tmp_path: Path,
):
    class FakeDws:
        def __init__(self):
            self.reply_messages = []

        def search_conversations(self, query):
            assert query == "官网迭代群"
            return []

        def read_mentioned_messages(self, conversation, limit=50):
            return []

        def read_recent_messages(self, conversation):
            assert conversation.open_conversation_id == "cid-site"
            assert conversation.single_chat is False
            return [
                DingTalkMessage(
                    open_conversation_id="cid-site",
                    open_message_id="msg-site-1",
                    conversation_title=conversation.title,
                    single_chat=False,
                    sender_name="Claire",
                    sender_open_dingtalk_id="open-claire",
                    sender_user_id="user-claire",
                    create_time="2026-05-28 04:04:53",
                    content="@All 新的官网更新一共16页，请大家打开每一个的html文档",
                )
            ]

        def read_unread_messages(self, conversation):
            return []

        def reply_message(
            self,
            conversation_id,
            ref_message_id,
            ref_sender_open_dingtalk_id,
            text,
        ):
            self.reply_messages.append(
                (conversation_id, ref_message_id, ref_sender_open_dingtalk_id, text)
            )
            return {"result": {"processQueryKey": "recall-site-1"}}

    monkeypatch.setattr(
        "ceo_agent_service.worker.send_macos_notification",
        lambda **kwargs: None,
    )
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    store.upsert_conversation(
        "cid-site",
        title="官网迭代群",
        single_chat=False,
        codex_session_id=None,
    )
    dws = FakeDws()

    result = handle_reviewed_message_reply(
        store,
        dws,
        user_name="Claire",
        group_name="官网迭代群",
        message_str="@All 新的官网更新一共16页，请大家打开每一个的html文档",
        reply_text="我已经完成审核，会把核心 comment 补到 tracker。",
        reviewer_feedback=(
            "官网是 marketing 重要内容，CEO 直接相关；这类消息需要审核并回复。"
        ),
    )

    attempt = store.get_reply_attempt(result["attempt_id"])
    assert result["send_status"] == "sent"
    assert attempt is not None
    assert (
        attempt.final_reply_text
        == "我已经完成审核，会把核心 comment 补到 tracker。（by磊哥分身）"
    )
    assert (
        attempt.reviewer_feedback
        == "官网是 marketing 重要内容，CEO 直接相关；这类消息需要审核并回复。"
    )
    assert attempt.corrected_reply_text == "我已经完成审核，会把核心 comment 补到 tracker。"
    assert dws.reply_messages == [
        (
            "cid-site",
            "msg-site-1",
            "open-claire",
            attempt.final_reply_text,
        )
    ]


def test_handle_reviewed_message_reply_matches_private_message_without_mention(
    monkeypatch,
    tmp_path: Path,
):
    class FakeDws:
        def __init__(self):
            self.sent_messages = []
            self.read_mentioned_calls = 0

        def search_conversations(self, query):
            assert query == "Mina 邹"
            return [
                DingTalkConversation(
                    open_conversation_id="cid-private",
                    title="Mina 邹",
                    single_chat=True,
                    unread_point=1,
                )
            ]

        def read_mentioned_messages(self, conversation, limit=50):
            self.read_mentioned_calls += 1
            raise AssertionError("private lookup should not use mention list")

        def read_recent_messages(self, conversation):
            assert conversation.open_conversation_id == "cid-private"
            return [
                DingTalkMessage(
                    open_conversation_id="cid-private",
                    open_message_id="msg-private-1",
                    conversation_title=conversation.title,
                    single_chat=True,
                    sender_name="Mina 邹",
                    sender_user_id="user-mina",
                    create_time="2026-05-25 13:40:26",
                    content="磊哥分身，大模型项目经理需要具备什么能力",
                )
            ]

        def read_unread_messages(self, conversation):
            return []

        def send_message(
            self,
            conversation_id,
            text,
            at_users=None,
            user_id=None,
            open_dingtalk_id=None,
        ):
            self.sent_messages.append((conversation_id, text, at_users, user_id))
            return {"result": {"processQueryKey": "recall-private-1"}}

    monkeypatch.setattr(
        "ceo_agent_service.worker.send_macos_notification",
        lambda **kwargs: None,
    )
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    dws = FakeDws()

    result = handle_reviewed_message_reply(
        store,
        dws,
        user_name="Mina 邹",
        group_name="Mina 邹",
        message_str="磊哥分身，大模型项目经理需要具备什么能力",
        reply_text="这个岗位核心看业务拆解、模型理解、项目推进和学习速度。",
    )

    attempt = store.get_reply_attempt(result["attempt_id"])
    sent_reply = store.get_sent_reply("cid-private", "msg-private-1")
    assert result["send_status"] == "sent"
    assert attempt is not None
    assert attempt.trigger_sender == "Mina 邹"
    assert attempt.trigger_text == "磊哥分身，大模型项目经理需要具备什么能力"
    assert "> Mina 邹: 磊哥分身，大模型项目经理需要具备什么能力" in attempt.final_reply_text
    assert dws.sent_messages == [
        (
            None,
            attempt.final_reply_text,
            None,
            "user-mina",
        )
    ]
    assert sent_reply is not None
    assert sent_reply.recall_key == "recall-private-1"


def test_handle_reviewed_message_reply_uses_stored_private_conversation_when_search_misses(
    monkeypatch,
    tmp_path: Path,
):
    class FakeDws:
        def __init__(self):
            self.sent_messages = []

        def search_conversations(self, query):
            assert query == "Mina 邹"
            return []

        def read_recent_messages(self, conversation):
            assert conversation.open_conversation_id == "cid-private"
            assert conversation.single_chat is True
            return [
                DingTalkMessage(
                    open_conversation_id="cid-private",
                    open_message_id="msg-private-1",
                    conversation_title=conversation.title,
                    single_chat=True,
                    sender_name="Mina 邹",
                    sender_user_id="user-mina",
                    create_time="2026-05-25 13:40:26",
                    content="好",
                )
            ]

        def read_unread_messages(self, conversation):
            return []

        def send_message(
            self,
            conversation_id,
            text,
            at_users=None,
            user_id=None,
            open_dingtalk_id=None,
        ):
            self.sent_messages.append((conversation_id, text, at_users, user_id))
            return {"result": {"processQueryKey": "recall-private-1"}}

    monkeypatch.setattr(
        "ceo_agent_service.worker.send_macos_notification",
        lambda **kwargs: None,
    )
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    store.upsert_conversation(
        "cid-private",
        title="Mina 邹",
        single_chat=True,
        codex_session_id=None,
    )
    dws = FakeDws()

    result = handle_reviewed_message_reply(
        store,
        dws,
        user_name="Mina 邹",
        group_name="Mina 邹",
        message_str="好",
        reply_text="收到，那你先按这个口径推进。",
    )

    attempt = store.get_reply_attempt(result["attempt_id"])
    assert result["send_status"] == "sent"
    assert attempt is not None
    assert "> Mina 邹: 好" in attempt.final_reply_text
    assert dws.sent_messages == [
        (
            None,
            attempt.final_reply_text,
            None,
            "user-mina",
        )
    ]


def test_render_error_list_shows_recent_errors(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    store.record_error("cid-1", "msg-1", "send", "authorization required")

    html = render_error_list(store)

    assert "Errors" in html
    assert "send" in html
    assert "authorization required" in html
    assert "cid-1" in html


def test_run_audit_web_uses_stable_uvicorn_protocols(monkeypatch, tmp_path: Path):
    calls = {}

    def fake_run(app, **kwargs):
        calls["app"] = app
        calls["kwargs"] = kwargs

    monkeypatch.setattr("ceo_agent_service.audit_web.uvicorn.run", fake_run)

    run_audit_web(tmp_path / "worker.sqlite3", host="127.0.0.1", port=8765)

    assert calls["app"] is not None
    assert calls["kwargs"]["host"] == "127.0.0.1"
    assert calls["kwargs"]["port"] == 8765
    assert calls["kwargs"]["loop"] == "asyncio"
    assert calls["kwargs"]["http"] == "h11"


def test_run_audit_web_reload_uses_stable_uvicorn_protocols(
    monkeypatch,
    tmp_path: Path,
):
    calls = {}

    def fake_run(app, **kwargs):
        calls["app"] = app
        calls["kwargs"] = kwargs

    monkeypatch.setenv("CEO_WORKER_DB", "")
    monkeypatch.delenv("CEO_DING_ROBOT_CODE", raising=False)
    monkeypatch.delenv("CEO_DING_ROBOT_NAME", raising=False)
    monkeypatch.setattr("ceo_agent_service.audit_web.uvicorn.run", fake_run)

    run_audit_web(
        tmp_path / "worker.sqlite3",
        host="127.0.0.1",
        port=8765,
        reload=True,
        reload_dirs=[tmp_path],
    )

    assert calls["app"] == "ceo_agent_service.audit_web:create_default_audit_app"
    assert calls["kwargs"]["factory"] is True
    assert calls["kwargs"]["reload"] is True
    assert calls["kwargs"]["loop"] == "asyncio"
    assert calls["kwargs"]["http"] == "h11"
