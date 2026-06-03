import os
import subprocess
from pathlib import Path

from fastapi.testclient import TestClient

import app.audit_web as audit_web_module
from app.audit_web import (
    create_audit_app,
    handle_developer_prompt_post,
    handle_prompt_variables_post,
    handle_system_config_post,
    handle_user_prompt_post,
    handle_feedback_post,
    handle_user_feedback_resolve_post,
    handle_user_feedback_sync_post,
    handle_recall_post,
    handle_reviewed_message_reply,
    render_attempt_detail,
    render_attempt_list,
    render_codex_session_detail,
    render_codex_session_list,
    render_config_page,
    render_developer_prompt_editor,
    render_error_list,
    render_user_feedback_list,
    run_audit_web,
)
from app.developer_prompt import read_developer_prompt_template
from app.config import load_env_file
from app.dingtalk_models import DingTalkConversation, DingTalkMessage
from app.store import AutoReplyStore


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
        trigger_text="@Alex Chen 这个怎么处理？",
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
        final_reply_text="> Xiaomin: 这个怎么处理？\n\n先按A方案走（by明哥分身）",
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
    assert "💬 Sent" in html
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


def test_render_attempt_list_paginates_attempts(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    older_id = store.record_reply_attempt(
        conversation_id="cid-old",
        conversation_title="Older Group",
        trigger_message_id="msg-old",
        trigger_sender="Older",
        trigger_text="older question",
        action="send_reply",
        sensitivity_kind="general",
    )
    newer_id = store.record_reply_attempt(
        conversation_id="cid-new",
        conversation_title="Newer Group",
        trigger_message_id="msg-new",
        trigger_sender="Newer",
        trigger_text="newer question",
        action="send_reply",
        sensitivity_kind="general",
    )

    first_page = render_attempt_list(store, limit=1, page=1)
    second_page = render_attempt_list(store, limit=1, page=2)

    assert f"/attempts/{newer_id}" in first_page
    assert f"/attempts/{older_id}" not in first_page
    assert 'href="/?page=2"' in first_page
    assert "1-1" in first_page
    assert "1 / 2" in first_page
    assert 'aria-label="第一页"' in first_page
    assert 'aria-label="上一页"' in first_page
    assert 'aria-label="下一页"' in first_page
    assert 'aria-label="最后一页"' in first_page
    assert 'pagination-button is-disabled" aria-label="第一页"' in first_page
    assert 'pagination-button is-disabled pagination-arrow" aria-label="上一页"' in first_page
    assert f"/attempts/{older_id}" in second_page
    assert f"/attempts/{newer_id}" not in second_page
    assert 'href="/"' in second_page
    assert "2-2" in second_page
    assert "2 / 2" in second_page
    assert 'pagination-button is-disabled pagination-arrow" aria-label="下一页"' in second_page
    assert 'pagination-button is-disabled" aria-label="最后一页"' in second_page


def test_history_route_reads_page_query(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    first_id = 0
    for index in range(100):
        attempt_id = store.record_reply_attempt(
            conversation_id=f"cid-{index}",
            conversation_title=f"Group {index}",
            trigger_message_id=f"msg-{index}",
            trigger_sender="Mina",
            trigger_text=f"question {index}",
            action="send_reply",
            sensitivity_kind="general",
        )
        if index == 0:
            first_id = attempt_id
    app = create_audit_app(store.path)
    client = TestClient(app)

    response = client.get("/?page=2")

    assert response.status_code == 200
    assert f"/attempts/{first_id}" in response.text
    assert "51-100" in response.text
    assert "2 / 2" in response.text


def test_render_attempt_list_shows_counterparty_feedback(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    seed_attempt(store)
    store.record_sent_reply(
        "cid-1",
        "msg-1",
        "先按A方案走",
        feedback_token="token-1",
    )
    store.upsert_feedback_event(
        key="event-1",
        feedback_token="token-1",
        rating="useful",
        rating_label="很有用",
        comment="这个建议能直接用",
        source="ceo-agent-spike",
        received_at="2026-06-02T08:00:00.000Z",
    )

    html = render_attempt_list(store)

    assert "反馈：☆☆☆☆ | 这个建议能直接用" in html
    assert "对方反馈 很有用" not in html


def test_render_attempt_list_hides_pending_counterparty_feedback(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    seed_attempt(store)
    store.record_sent_reply(
        "cid-1",
        "msg-1",
        "先按A方案走",
        feedback_token="token-1",
    )

    html = render_attempt_list(store)

    assert "等待对方反馈" not in html


def test_render_user_feedback_list_marks_pending_and_resolved(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    pending_attempt_id = seed_attempt(store)
    store.upsert_conversation(
        "cid-2",
        title="产品群",
        single_chat=False,
        codex_session_id="session-2",
    )
    resolved_attempt_id = store.record_reply_attempt(
        conversation_id="cid-2",
        conversation_title="产品群",
        trigger_message_id="msg-2",
        trigger_sender="Mina",
        trigger_text="这个回复有帮助吗？",
        action="send_reply",
        sensitivity_kind="general",
        codex_reason="direct ask",
        draft_reply_text="收到，我来看",
    )
    store.update_reply_attempt(
        resolved_attempt_id,
        final_reply_text="收到，我来看",
        permission_action="allow",
        send_status="sent",
    )
    store.record_sent_reply(
        "cid-1",
        "msg-1",
        "先按A方案走",
        feedback_token="token-pending",
    )
    store.record_sent_reply(
        "cid-2",
        "msg-2",
        "收到，我来看",
        feedback_token="token-resolved",
    )
    store.upsert_feedback_event(
        key="event-pending",
        feedback_token="token-pending",
        rating="not_useful",
        rating_label="不太有用",
        comment="没有回答到我的问题",
        source="ceo-agent-spike",
        received_at="2026-06-02T08:05:00.000Z",
    )
    store.upsert_feedback_event(
        key="event-resolved",
        feedback_token="token-resolved",
        rating="useful",
        rating_label="很有用",
        comment="测试一下反馈功能",
        source="ceo-agent-spike",
        received_at="2026-06-02T08:06:00.000Z",
    )
    store.record_reply_feedback(
        resolved_attempt_id,
        feedback="已看，后续收敛一点",
        corrected_reply_text="收到，我来看。",
    )

    html = render_user_feedback_list(store)

    assert "用户反馈" in html
    assert "pending" in html
    assert "resolved" in html
    assert "☆☆" in html
    assert "☆☆☆☆" in html
    assert "没有回答到我的问题" in html
    assert "测试一下反馈功能" in html
    assert "<th>Token</th>" not in html
    assert "token-pending" not in html
    assert "user-feedback-actions" in html
    assert 'action="/user-feedback/resolve"' in html
    assert 'name="key" value="event-pending"' in html
    assert "标记 resolved" in html
    assert f'href="/attempts/{pending_attempt_id}"' in html
    assert f'href="/attempts/{resolved_attempt_id}"' in html


def test_render_user_feedback_list_paginates(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    seed_attempt(store)
    store.upsert_feedback_event(
        key="older",
        feedback_token="older-token",
        rating="useful",
        rating_label="很有用",
        comment="older feedback",
        source="ceo-agent-spike",
        received_at="2026-06-02T08:00:00.000Z",
    )
    store.upsert_feedback_event(
        key="newer",
        feedback_token="newer-token",
        rating="not_useful",
        rating_label="不太有用",
        comment="newer feedback",
        source="ceo-agent-spike",
        received_at="2026-06-02T09:00:00.000Z",
    )

    first_page = render_user_feedback_list(store, limit=1, page=1)
    second_page = render_user_feedback_list(store, limit=1, page=2)

    assert "newer feedback" in first_page
    assert "older feedback" not in first_page
    assert 'href="/user-feedback?page=2"' in first_page
    assert "1-1" in first_page
    assert "1 / 2" in first_page
    assert "older feedback" in second_page
    assert "newer feedback" not in second_page
    assert 'href="/user-feedback"' in second_page
    assert "2-2" in second_page
    assert "2 / 2" in second_page


def test_feedback_pages_do_not_sync_external_events_during_render(
    tmp_path: Path, monkeypatch
):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    seed_attempt(store)
    store.record_sent_reply(
        "cid-1",
        "msg-1",
        "先按A方案走",
        feedback_token="token-1",
    )

    def fail_sync(*_args, **_kwargs):
        raise AssertionError("render should not sync external feedback")

    monkeypatch.setattr(
        audit_web_module,
        "_sync_feedback_events_for_sent_replies",
        fail_sync,
    )

    assert "用户反馈" in render_user_feedback_list(store)
    assert "CEO Agent Audit" in render_attempt_list(store)
    status, html = render_attempt_detail(store, 1)
    assert status == 200
    assert "Attempt #1" in html


def test_handle_user_feedback_sync_post_triggers_explicit_sync(
    tmp_path: Path, monkeypatch
):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    seed_attempt(store)
    store.record_sent_reply(
        "cid-1",
        "msg-1",
        "先按A方案走",
        feedback_token="token-1",
    )
    calls = []

    def fake_sync(_store, sent_replies):
        calls.append(list(sent_replies))

    monkeypatch.setattr(
        audit_web_module,
        "_sync_feedback_events_for_sent_replies",
        fake_sync,
    )

    status, headers, html = handle_user_feedback_sync_post(store)

    assert status == 303
    assert headers["Location"] == "/user-feedback"
    assert html == ""
    assert len(calls) == 1
    assert calls[0][0].feedback_token == "token-1"


def test_handle_user_feedback_resolve_post_marks_feedback_resolved(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    seed_attempt(store)
    store.record_sent_reply(
        "cid-1",
        "msg-1",
        "先按A方案走",
        feedback_token="token-1",
    )
    store.upsert_feedback_event(
        key="event-1",
        feedback_token="token-1",
        rating="useful",
        rating_label="很有用",
        comment="不需要内部反馈",
        source="ceo-agent-spike",
        received_at="2026-06-02T08:00:00.000Z",
    )

    status, headers, html = handle_user_feedback_resolve_post(
        store,
        b"key=event-1",
    )
    feedback_html = render_user_feedback_list(store)

    assert status == 303
    assert headers["Location"] == "/user-feedback"
    assert html == ""
    assert "resolved" in feedback_html
    assert "标记 resolved" not in feedback_html
    assert "已处理" in feedback_html


def test_user_feedback_nav_badge_shows_pending_count(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    seed_attempt(store)
    store.record_sent_reply(
        "cid-1",
        "msg-1",
        "先按A方案走",
        feedback_token="token-1",
    )
    store.upsert_feedback_event(
        key="event-1",
        feedback_token="token-1",
        rating="useful",
        rating_label="很有用",
        comment="需要处理",
        source="ceo-agent-spike",
        received_at="2026-06-02T08:00:00.000Z",
    )

    pending_html = render_attempt_list(store)
    store.resolve_feedback_event("event-1")
    resolved_html = render_attempt_list(store)

    assert '<span class="nav-badge">1</span>' in pending_html
    assert '<span class="nav-badge">1</span>' not in resolved_html


def test_user_feedback_resolve_route_redirects_to_feedback_page(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    seed_attempt(store)
    store.upsert_feedback_event(
        key="event-1",
        feedback_token="token-1",
        rating="useful",
        rating_label="很有用",
        comment="不需要内部反馈",
        source="ceo-agent-spike",
        received_at="2026-06-02T08:00:00.000Z",
    )
    app = create_audit_app(store.path)
    client = TestClient(app)

    response = client.post(
        "/user-feedback/resolve",
        data={"key": "event-1"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/user-feedback"


def test_user_feedback_route_renders_feedback_page(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    seed_attempt(store)
    app = create_audit_app(store.path)
    client = TestClient(app)

    response = client.get("/user-feedback")

    assert response.status_code == 200
    assert "用户反馈" in response.text
    assert 'action="/user-feedback/sync"' in response.text
    assert "暂无用户反馈" in response.text


def test_user_feedback_sync_route_redirects_to_feedback_page(
    tmp_path: Path, monkeypatch
):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    seed_attempt(store)
    app = create_audit_app(store.path)
    client = TestClient(app)
    calls = []

    def fake_sync(_store, sent_replies):
        calls.append(list(sent_replies))

    monkeypatch.setattr(
        audit_web_module,
        "_sync_feedback_events_for_sent_replies",
        fake_sync,
    )

    response = client.post("/user-feedback/sync", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/user-feedback"
    assert len(calls) == 1


def test_render_history_page_includes_favicon_and_refresh(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    seed_attempt(store)

    html = render_attempt_list(store)

    assert 'rel="icon"' in html
    assert 'href="data:image/svg+xml,' in html
    assert "%2300d4a4" in html
    assert 'http-equiv="refresh"' in html
    assert 'content="15"' in html
    assert "ceo-agent-service-notification-leader" in html
    assert 'new EventSource("/notifications/events")' in html
    assert "navigator.serviceWorker" in html
    assert '"/notification-service-worker.js"' in html
    assert "registration.showNotification(payload.title, options)" in html
    assert "new Notification(" not in html
    assert "notification.onclick" not in html
    assert "payload.dingtalk_url" not in html
    assert "window.location.href" not in html
    assert "window.open(payload.url" not in html


def test_top_nav_highlights_current_page_and_disables_current_link(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    seed_attempt(store)

    history_html = render_attempt_list(store)
    user_feedback_html = render_user_feedback_list(store)
    config_html = render_config_page()
    codex_html = render_codex_session_list(store)
    errors_html = render_error_list(store)

    assert '<span class="nav-item active" aria-current="page">History</span>' in history_html
    assert '<a class="nav-item" href="/">History</a>' not in history_html
    assert '<a class="nav-item" href="/user-feedback">用户反馈</a>' in history_html
    assert '<a class="nav-item" href="/config">Config</a>' in history_html

    assert '<span class="nav-item active" aria-current="page">用户反馈</span>' in user_feedback_html
    assert '<a class="nav-item" href="/user-feedback">用户反馈</a>' not in user_feedback_html

    assert '<span class="nav-item active" aria-current="page">Config</span>' in config_html
    assert '<a class="nav-item" href="/config">Config</a>' not in config_html

    assert '<span class="nav-item active" aria-current="page">Codex Sessions</span>' in codex_html
    assert '<a class="nav-item" href="/codex">Codex Sessions</a>' not in codex_html

    assert '<span class="nav-item active" aria-current="page">Errors</span>' in errors_html
    assert '<a class="nav-item" href="/errors">Errors</a>' not in errors_html


def test_non_history_pages_do_not_auto_refresh(tmp_path: Path):
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
        '{"timestamp":"2026-05-14T12:00:00Z","type":"session_meta","payload":{"id":"session-1"}}',
        encoding="utf-8",
    )

    _, attempt_html = render_attempt_detail(store, attempt_id)
    codex_list_html = render_codex_session_list(store)
    _, codex_detail_html = render_codex_session_detail(
        "session-1",
        codex_home=codex_home,
        store=store,
    )
    error_html = render_error_list(store)
    developer_prompt_html = render_developer_prompt_editor()
    config_html = render_config_page()

    assert 'http-equiv="refresh"' not in attempt_html
    assert 'http-equiv="refresh"' not in codex_list_html
    assert 'http-equiv="refresh"' not in codex_detail_html
    assert 'http-equiv="refresh"' not in error_html
    assert 'http-equiv="refresh"' not in developer_prompt_html
    assert 'http-equiv="refresh"' not in config_html


def test_render_config_page_shows_message_routing_logic():
    html = render_config_page()

    assert "Prompt config" in html
    assert "Producer routing config" not in html
    assert "Template syntax" not in html
    assert html.index("Prompt config") < html.index('aria-label="Config sections"')
    assert "Runtime config" not in html
    assert "Variable definitions" not in html
    assert html.index("Config variables") < html.index('aria-label="Config sections"')
    assert html.index("Dynamic functions") < html.index('aria-label="Config sections"')
    assert '<details class="config-collapse">' in html
    assert '<summary><h3>Config variables</h3></summary>' in html
    assert '<summary><h3>Dynamic functions</h3></summary>' in html
    assert '<details class="config-collapse" open>' not in html
    assert "&lt;code: app.user_prompt_blocks:current_message_block()&gt;" in html
    assert "work_profile_instruction()" in html
    assert "&lt;code: app.prompt:work_profile_instruction()&gt;" in html
    assert "Info" in html
    assert 'class="prompt-tab active"' in html
    assert "markdown-doc" not in html
    assert "| `CEO_MENTION_ALIASES` |" not in html
    assert "<pre># Producer routing config" not in html
    assert '<table class="config-variable-table">' in html
    assert 'class="config-value-input"' in html
    assert "<h3>快路径</h3>" in html
    assert "Producer 路由配置" in html
    assert "每次 producer 运行都会调用" in html
    assert 'value="CEO_MENTION_ALIASES"' not in html
    assert 'value="@Alex Chen, @明哥"' not in html
    assert 'value="principal"' not in html
    assert 'value="handoff_name"' not in html
    assert 'value="responsibility_summary"' not in html
    assert 'value="CEO_PROMPT_VAR_RESPONSIBILITY_SUMMARY"' in html
    assert "CEO_PROMPT_VAR_RESPONSIBILITY_SUMMARY" in html
    assert 'value="MESSAGE_RECOVERY_INTERVAL"' not in html
    assert 'value="CEO_CURRENT_USER_DISPLAY_NAMES"' not in html
    assert 'value="CEO_STYLE_SPEAKER_NAMES"' not in html
    assert 'value="CEO_FORBIDDEN_PATH_PREFIXES"' not in html
    assert 'value="CEO_PRINCIPAL_NAME"' not in html
    assert 'value="CEO_PRINCIPAL_DISPLAY_NAME"' not in html
    assert 'value="CEO_PRINCIPAL_HANDOFF_NAME"' not in html
    assert 'value="CEO_RESPONSIBILITY_SUMMARY"' not in html
    assert '<code class="config-token">read_mentioned_messages</code>' in html
    assert '<code class="config-token">@Alex Chen/@明哥</code>' in html
    assert "Fast path" not in html
    assert "Slow path" not in html
    assert "Group chat" not in html
    assert "Direct chat" not in html
    assert "快路径" in html
    assert "慢路径" in html
    assert "群聊" in html
    assert "私聊" in html
    assert "list_unread_conversations" in html
    assert "read_mentioned_messages" in html
    assert "@Alex Chen/@明哥" in html
    assert "私聊文档会进入 agent 判断" in html
    assert "/config" in html


def test_render_config_page_shows_system_config_tab_with_descriptions():
    html = render_config_page(active_tab="system")

    assert "System Config" in html
    assert "系统运行参数" in html
    assert "运行时身份缓存" in html
    assert "current_user_id" in html
    assert "message field" not in html
    assert "org profile field" not in html
    assert "不从 .env 手填" in html
    assert "只展示本人身份真值" in html
    assert 'method="post" action="/config/system"' in html
    assert 'name="system_key"' in html
    assert 'name="system_value"' in html
    assert 'class="prompt-tab active"' in html
    assert "不写入 Prompt" in html
    assert "CEO_PRODUCER_INTERVAL_SECONDS" in html
    assert "主服务内 producer loop 的运行间隔" in html
    assert "CEO_CONSUMER_POLL_INTERVAL_SECONDS" in html
    assert "CEO_POLL_INTERVAL_SECONDS" in html
    assert "CEO_BATCH_SECONDS" in html
    assert "FAST_PATH_UNREAD_BACKOFF" in html
    assert "快路径扫描到未读会话后等待多久再读取" in html
    assert "MESSAGE_RECOVERY_INTERVAL" in html
    assert "MEMORY_CONNECTOR_USER_ID" in html
    assert "CEO_MENTION_ALIASES" in html
    assert "群聊/消息触发时识别点名" in html
    assert "每次慢路径兜底扫描之间至少间隔多久" in html
    assert "USER_ALIAS" in html
    assert "用户别名" in html
    assert "DOCUMENT_EXTRACTION_IDS" in html
    assert "抽取该身份的发言或材料" in html
    assert "CEO_FORBIDDEN_PATH_PREFIXES" in html
    assert "按路径前缀识别本机路径泄漏" in html
    assert "CEO_CURRENT_USER_DISPLAY_NAMES" not in html
    assert "CEO_FORBIDDEN_PATH_PREFIXES" in html
    system_section = html.split("<h2>系统运行参数</h2>", 1)[1]
    assert "forbidden_reply_text_terms" not in system_section
    assert "CEO_PROMPT_VAR_FORBIDDEN_REPLY_TEXT_TERMS" not in system_section


def test_handle_system_config_post_saves_runtime_params_to_env_file(
    tmp_path: Path,
    monkeypatch,
):
    env_path = tmp_path / ".env"
    env_path.write_text("CEO_WORKSPACE=/tmp/memory\n", encoding="utf-8")
    monkeypatch.setenv("CEO_ENV_FILE", str(env_path))

    body = (
        "system_key=CEO_PRODUCER_INTERVAL_SECONDS"
        "&system_value=60"
        "&system_key=CEO_CONSUMER_POLL_INTERVAL_SECONDS"
        "&system_value=10"
        "&system_key=FAST_PATH_UNREAD_BACKOFF"
        "&system_value=5m"
        "&system_key=MESSAGE_RECOVERY_INTERVAL"
        "&system_value=30m"
        "&system_key=SINGLE_CHAT_READ_RECOVERY_WINDOW"
        "&system_value=12h"
        "&system_key=SINGLE_CHAT_READ_RECOVERY_LIMIT"
        "&system_value=25"
        "&system_key=GROUP_READ_RECOVERY_WINDOW"
        "&system_value=6h"
        "&system_key=GROUP_READ_RECOVERY_LIMIT"
        "&system_value=2"
    ).encode()

    status, headers, html = handle_system_config_post(body)

    assert status == 303
    assert headers["Location"] == "/config?tab=system&saved=1"
    assert html == ""
    env_text = env_path.read_text(encoding="utf-8")
    assert "CEO_WORKSPACE=/tmp/memory" in env_text
    assert "CEO_PRODUCER_INTERVAL_SECONDS=60" in env_text
    assert "CEO_CONSUMER_POLL_INTERVAL_SECONDS=10" in env_text
    assert "FAST_PATH_UNREAD_BACKOFF=5m" in env_text
    assert "MESSAGE_RECOVERY_INTERVAL=30m" in env_text
    assert "SINGLE_CHAT_READ_RECOVERY_WINDOW=12h" in env_text
    assert "SINGLE_CHAT_READ_RECOVERY_LIMIT=25" in env_text
    assert "GROUP_READ_RECOVERY_WINDOW=6h" in env_text
    assert "GROUP_READ_RECOVERY_LIMIT=2" in env_text
    assert "MESSAGE_RECOVERY_INTERVAL" not in read_developer_prompt_template()


def test_open_dingtalk_bridge_opens_conversation_url(tmp_path: Path, monkeypatch):
    commands = []

    def fake_run(command, check):
        commands.append((command, check))
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(
        "app.audit_web.subprocess.run",
        fake_run,
    )
    client = TestClient(create_audit_app(tmp_path / "worker.sqlite3"))

    response = client.get("/open-dingtalk?cid=75217569357")

    assert response.status_code == 200
    assert response.json() == {
        "ok": True,
        "dingtalk_url": "dingtalk://dingtalkclient/page/conversation?cid=75217569357",
        "open_returncode": 0,
    }
    assert commands == [
        (
            [
                "/usr/bin/open",
                "dingtalk://dingtalkclient/page/conversation?cid=75217569357",
            ],
            False,
        )
    ]


def test_open_dingtalk_bridge_opens_pc_jsapi_bridge_for_open_conversation_id(
    tmp_path: Path, monkeypatch
):
    commands = []

    def fake_run(command, check):
        commands.append((command, check))
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(
        "app.audit_web.subprocess.run",
        fake_run,
    )
    client = TestClient(create_audit_app(tmp_path / "worker.sqlite3"))

    response = client.get("/open-dingtalk?conversation_id=cid-1")

    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True
    assert payload["bridge_url"] == (
        "http://testserver/dingtalk/open-chat-bridge?conversation_id=cid-1"
    )
    assert payload["dingtalk_url"].startswith(
        "dingtalk://dingtalkclient/page/link?url="
    )
    assert "&pc_slide=true" in payload["dingtalk_url"]
    assert "open_platform_link" not in payload["dingtalk_url"]
    assert "jumpToChat" not in payload["dingtalk_url"]
    assert commands == [
        (
            [
                "/usr/bin/open",
                payload["dingtalk_url"],
            ],
            False,
        )
    ]


def test_open_dingtalk_bridge_rejects_missing_cid(tmp_path: Path, monkeypatch):
    commands = []
    monkeypatch.setattr(
        "app.audit_web.subprocess.run",
        lambda command, check: commands.append((command, check)),
    )
    client = TestClient(create_audit_app(tmp_path / "worker.sqlite3"))

    response = client.get("/open-dingtalk?cid=")

    assert response.status_code == 400
    assert response.json() == {"ok": False, "error": "missing_cid"}
    assert commands == []


def test_dingtalk_open_chat_bridge_calls_open_conversation_jsapi(tmp_path: Path):
    client = TestClient(create_audit_app(tmp_path / "worker.sqlite3"))

    response = client.get("/dingtalk/open-chat-bridge?conversation_id=cid-1")

    assert response.status_code == 200
    assert "https://g.alicdn.com/dingding/dingtalk-jsapi/" in response.text
    assert "dd.openChatByConversationId" in response.text
    assert "toConversationByOpenConversationId" in response.text
    assert "/dingtalk/bridge-status" in response.text
    assert "window.dd.ready" in response.text
    assert "dd-ready-timeout" in response.text
    assert "dd.closePage" in response.text
    assert "jumpToChat" not in response.text


def test_dingtalk_bridge_status_records_events(tmp_path: Path):
    client = TestClient(create_audit_app(tmp_path / "worker.sqlite3"))

    response = client.post(
        "/dingtalk/bridge-status",
        json={
            "conversation_id": "cid-1",
            "stage": "loaded",
            "detail": "DingTalk",
        },
    )

    assert response.status_code == 200
    assert response.json() == {"ok": True}
    assert client.get("/dingtalk/bridge-status").json()["events"][-1] == {
        "conversation_id": "cid-1",
        "stage": "loaded",
        "detail": "DingTalk",
    }


def test_notification_service_worker_fetches_bridge_without_opening_window(
    tmp_path: Path,
):
    client = TestClient(create_audit_app(tmp_path / "worker.sqlite3"))

    response = client.get("/notification-service-worker.js")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/javascript")
    assert response.headers["cache-control"] == "no-cache"
    assert "notificationclick" in response.text
    assert "skipWaiting" in response.text
    assert "clients.claim" in response.text
    assert 'await fetch(data.url, {' in response.text
    assert "clients.matchAll" in response.text
    assert "client.focus" in response.text
    assert "clients.openWindow" not in response.text
    assert "window.open" not in response.text


def test_browser_notifications_page_is_available(tmp_path: Path):
    client = TestClient(create_audit_app(tmp_path / "worker.sqlite3"))

    response = client.get("/notifications")

    assert response.status_code == 200
    assert "Chrome 通知" in response.text
    assert "Notification.requestPermission" in response.text
    assert 'new EventSource("/notifications/events")' in response.text
    assert "navigator.serviceWorker" in response.text
    assert '"/notification-service-worker.js"' in response.text
    assert "registration.showNotification(payload.title, options)" in response.text
    assert "new Notification(" not in response.text
    assert "notification.onclick" not in response.text
    assert "payload.dingtalk_url" not in response.text
    assert "window.location.href" not in response.text
    assert "window.open(payload.url" not in response.text
    assert "granted connected" in response.text
    assert "granted standby" in response.text
    assert '<span class="nav-item active" aria-current="page">Notifications</span>' not in response.text
    assert '<a class="nav-item" href="/notifications">Notifications</a>' not in response.text


def test_browser_notification_post_reports_no_subscribers(tmp_path: Path):
    client = TestClient(create_audit_app(tmp_path / "worker.sqlite3"))

    response = client.post(
        "/browser-notifications",
        json={
            "title": "CEO auto reply",
            "message": "已回复",
            "url": "http://127.0.0.1:8765/open-dingtalk?cid=75217569357",
        },
    )

    assert response.status_code == 200
    assert response.json() == {
        "ok": True,
        "delivered": False,
        "subscribers": 0,
        "dingtalk_url": "dingtalk://dingtalkclient/page/conversation?cid=75217569357",
    }


def test_env_file_overrides_existing_environment(tmp_path: Path, monkeypatch):
    env_path = tmp_path / ".env"
    env_path.write_text("MESSAGE_RECOVERY_INTERVAL=45m\n", encoding="utf-8")
    monkeypatch.setenv("MESSAGE_RECOVERY_INTERVAL", "1h")

    load_env_file(env_path)

    assert "MESSAGE_RECOVERY_INTERVAL" in env_path.read_text(encoding="utf-8")
    assert os.environ["MESSAGE_RECOVERY_INTERVAL"] == "45m"


def test_render_config_dynamic_functions_do_not_hardcode_principal_name(monkeypatch):
    monkeypatch.setenv("USER_ALIAS", "Alex")

    html = render_config_page()

    assert "work_profile_instruction()" in html
    assert "读取并注入工作人格 Profile；通常用于 Developer Prompt。" in html
    assert "Alex 工作人格 Profile" not in html


def test_config_route_is_available(tmp_path: Path):
    app = create_audit_app(tmp_path / "worker.sqlite3")
    client = TestClient(app)

    response = client.get("/config")

    assert response.status_code == 200
    assert "Producer 路由配置" in response.text
    assert "/config?tab=developer" in response.text


def test_render_page_brand_links_to_history():
    html = render_config_page()

    assert '<a class="brand brand-home" href="/" aria-label="History home">' in html


def test_render_developer_prompt_editor_shows_template_and_preview(
    tmp_path: Path,
    monkeypatch,
):
    template_path = tmp_path / "developer.md"
    template_path.write_text(
        "\n".join(
                [
                    "<vars>",
                    "principal = Alex",
                    "</vars>",
                "",
                "# Editable",
                "",
                "Hi <var: principal>",
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("CEO_DEVELOPER_PROMPT_TEMPLATE_PATH", str(template_path))
    monkeypatch.setenv("USER_ALIAS", "Alex")

    html = render_config_page(active_tab="developer", saved=True)

    assert "Prompt config" in html
    assert "Developer Prompt" in html
    assert "User Prompt" in html
    assert "/config?tab=info" in html
    assert "/config?tab=developer" in html
    assert "/config?tab=user" in html
    assert 'class="prompt-tab active"' in html
    assert "Template syntax" not in html
    assert html.index("Prompt config") < html.index('aria-label="Config sections"')
    assert str(template_path) in html
    assert 'name="variables"' not in html
    assert 'name="variable_key"' in html
    assert 'name="variable_value"' in html
    assert 'name="template"' in html
    assert "Config variables" in html
    assert "&lt;var: principal&gt;" in html
    assert "&lt;code: app.config:user_alias()&gt;" not in html
    assert 'value="principal"' not in html
    assert 'value="responsibility_summary"' not in html
    assert 'value="CEO_PROMPT_VAR_RESPONSIBILITY_SUMMARY"' in html
    assert "CEO_PROMPT_VAR_RESPONSIBILITY_SUMMARY" in html
    assert 'value="CEO_PRINCIPAL_NAME"' not in html
    assert 'value="CEO_PRINCIPAL_DISPLAY_NAME"' not in html
    assert 'value="CEO_PRINCIPAL_HANDOFF_NAME"' not in html
    assert "Hi Alex" in html
    assert "Saved." in html


def test_render_prompt_editor_shows_user_prompt_tab(tmp_path: Path, monkeypatch):
    template_path = tmp_path / "user.md"
    template_path.write_text(
        "USER <code: app.user_prompt_blocks:current_message_block()>",
        encoding="utf-8",
    )
    monkeypatch.setenv("CEO_USER_PROMPT_TEMPLATE_PATH", str(template_path))

    html = render_config_page(active_tab="user", saved=True)

    assert "Prompt config" in html
    assert "Prompt" in html
    assert "Info" in html
    assert "Developer Prompt" in html
    assert "User Prompt" in html
    assert 'class="prompt-tab active"' in html
    assert "Template syntax" not in html
    assert html.index("Prompt config") < html.index('aria-label="Config sections"')
    assert str(template_path) in html
    assert 'name="variables"' not in html
    assert 'name="variable_key"' in html
    assert 'name="template"' in html
    assert "&lt;code: app.user_prompt_blocks:current_message_block()&gt;" in html
    assert "work_profile_instruction()" in html
    assert "&lt;code: app.prompt:work_profile_instruction()&gt;" in html
    assert "Dynamic functions" in html
    assert "dynamic-preview" in html
    assert "相似历史回复风格例子" in html
    assert "先定优先级，再确认谁负责" in html
    assert "current_message_block()" in html
    assert "sender_org_block()" in html
    assert "Default preview" in html
    assert "会话: 示例群" in html
    assert "&quot;open_message_id&quot;: &quot;ctx-1&quot;" in html
    assert "&quot;sender&quot;: {" in html
    assert "&quot;quoted&quot;: {" in html
    assert "USER 当前待处理消息:" in html
    assert "Saved." in html


def test_handle_developer_prompt_post_saves_template(tmp_path: Path, monkeypatch):
    template_path = tmp_path / "developer.md"
    template_path.write_text(
        "<vars>\nprincipal = Alex\n</vars>\n\n# Old\nHi <var: principal>",
        encoding="utf-8",
    )
    monkeypatch.setenv("CEO_DEVELOPER_PROMPT_TEMPLATE_PATH", str(template_path))
    body = "template=%23+Updated%0AHi+%3Cvar%3A+principal%3E".encode()

    status, headers, html = handle_developer_prompt_post(body)

    assert status == 303
    assert headers["Location"] == "/config?tab=developer&saved=1"
    assert html == ""
    assert template_path.read_text(encoding="utf-8") == (
        "# Updated\nHi <var: principal>"
    )


def test_handle_prompt_variables_post_saves_variables_without_changing_template(
    tmp_path: Path,
    monkeypatch,
):
    template_path = tmp_path / "developer.md"
    template_path.write_text(
        "<vars>\nprincipal = Alex\n</vars>\n\n# Body\nHi <var: principal>",
        encoding="utf-8",
    )
    monkeypatch.setenv("CEO_DEVELOPER_PROMPT_TEMPLATE_PATH", str(template_path))
    monkeypatch.setenv("CEO_ENV_FILE", str(tmp_path / ".env"))
    body = (
        "active_tab=user"
        "&variable_key=CEO_PROMPT_VAR_RESPONSIBILITY_SUMMARY"
        "&variable_value=%E7%AE%97%E6%B3%95%E5%9B%A2%E9%98%9F%E8%81%8C%E8%B4%A3"
        "&variable_key=CEO_PROMPT_VAR_OA_APPROVAL_RULES"
        "&variable_value=management%2FOA%2F%E9%92%89%E9%92%89%E5%AE%A1%E6%89%B9%E5%AE%A1%E9%98%85%E5%8E%9F%E5%88%99.md"
        "&variable_key="
        "&variable_value="
    ).encode()

    status, headers, html = handle_prompt_variables_post(body)

    assert status == 303
    assert headers["Location"] == "/config?tab=user&saved=1"
    assert html == ""
    assert template_path.read_text(encoding="utf-8") == (
        "<vars>\nprincipal = Alex\n</vars>\n\n# Body\nHi <var: principal>"
    )
    env_text = (tmp_path / ".env").read_text(encoding="utf-8")
    assert "CEO_PROMPT_VAR_RESPONSIBILITY_SUMMARY" in env_text
    assert "算法团队职责" in env_text


def test_handle_user_prompt_post_saves_template(tmp_path: Path, monkeypatch):
    template_path = tmp_path / "user.md"
    monkeypatch.setenv("CEO_USER_PROMPT_TEMPLATE_PATH", str(template_path))
    body = (
        "template=USER+%3Ccode%3A+"
        "app.user_prompt_blocks%3Acurrent_message_block%28%29%3E"
    ).encode()

    status, headers, html = handle_user_prompt_post(body)

    assert status == 303
    assert headers["Location"] == "/config?tab=user&saved=1"
    assert html == ""
    assert template_path.read_text(encoding="utf-8") == (
        "USER <code: app.user_prompt_blocks:current_message_block()>"
    )


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
        trigger_text="@Alex Chen(明哥) 这个候选人怎么看？",
    )

    html = render_attempt_list(store)

    assert "💬 Pending" in html
    assert "#task-1" in html
    assert "HR管理" in html
    assert "Mina" in html
    assert "@Alex Chen(明哥) 这个候选人怎么看？" in html


def test_render_attempt_list_shows_processing_reply_tasks(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    store.enqueue_reply_task(
        conversation_id="cid-1",
        conversation_title="HR管理",
        single_chat=False,
        trigger_message_id="msg-queued",
        trigger_create_time="2026-05-28 18:00:00",
        trigger_sender="Mina",
        trigger_text="@Alex Chen(明哥) 这个候选人怎么看？",
    )
    store.claim_reply_tasks(limit=1)

    html = render_attempt_list(store)

    assert "#task-1" in html
    assert "processing" in html


def test_render_attempt_list_does_not_pin_failed_reply_tasks(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    store.enqueue_reply_task(
        conversation_id="cid-1",
        conversation_title="HR管理",
        single_chat=False,
        trigger_message_id="msg-failed",
        trigger_create_time="2026-05-28 18:00:00",
        trigger_sender="Mina",
        trigger_text="@Alex Chen(明哥) 这个候选人怎么看？",
    )
    store.fail_reply_task(1, "delivery failed")

    html = render_attempt_list(store)

    assert "#task-1" not in html
    assert "Queued / processing" not in html


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
        trigger_text="@Alex Chen 这个怎么处理？",
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
    assert "missing codex_session_id" not in html
    assert (
        "No Codex session is linked; review this attempt using the stored audit fields only."
        in html
    )
    assert "send_reply has no audit documents" not in html
    assert (
        "No audit documents or tool events were attached; this answer was generated from conversation context only."
        in html
    )


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
    assert "💬 Skipped" in html
    assert "🧾 通过" in html


def test_attempt_history_and_detail_render_calendar_response_metadata(
    tmp_path: Path,
):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    attempt_id = store.record_reply_attempt(
        conversation_id="cid-1",
        conversation_title="Mina",
        trigger_message_id="msg-1",
        trigger_sender="Mina",
        trigger_text="[日程]",
        action="no_reply",
        sensitivity_kind="general",
        codex_reason="calendar invite accepted",
        calendar_event_id="event-1",
        calendar_response_status="accepted",
        calendar_response_result_json='{"success":true}',
        send_status="skipped",
    )

    list_html = render_attempt_list(store)
    status, detail_html = render_attempt_detail(store, attempt_id)

    assert status == 200
    assert "💬 Skipped" in list_html
    assert "📆 Accepted" in list_html
    assert "Calendar response" in detail_html
    assert "event-1" in detail_html
    assert "accepted" in detail_html
    assert "Calendar response result" in detail_html


def test_render_attempt_list_uses_unified_emoji_action_pills(
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

    assert 'class="pill status-action">💬 Skipped</span>' in html
    assert '<span class="pill action-no_reply"' not in html
    assert '<span class="pill status-skipped"' not in html


def test_render_attempt_detail_allows_explained_empty_documents(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    attempt_id = store.record_reply_attempt(
        conversation_id="cid-1",
        conversation_title="技术部",
        trigger_message_id="msg-1",
        trigger_sender="Xiaomin",
        trigger_text="@Alex Chen 这个怎么处理？",
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
        trigger_text="@Alex Chen 这个怎么处理？",
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


def test_render_attempt_list_shows_context_only_info_icon_instead_of_warning(
    tmp_path: Path,
):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    store.record_reply_attempt(
        conversation_id="cid-1",
        conversation_title="技术部",
        trigger_message_id="msg-1",
        trigger_sender="Xiaomin",
        trigger_text="@Alex Chen 这个怎么处理？",
        action="send_reply",
        sensitivity_kind="general",
        draft_reply_text="先按A方案走",
        codex_session_id="session-1",
        audit_documents_json='[{"path":"chat","relevance":"直接上下文"}]',
        audit_tool_events_json="[]",
        audit_summary="已根据当前对话上下文生成回复。",
    )

    html = render_attempt_list(store)

    assert "Quality warning" not in html
    assert "send_reply has no audit tool events" not in html
    assert 'class="attempt-info"' in html
    assert "data-tooltip=" in html
    assert "title=" not in html
    assert ".attempt-info::after" in html
    assert "left:0;bottom:calc(100% + 8px)" in html
    assert "background:#fff3c4" in html
    assert (
        html.index('href="/attempts/1">#1</a>')
        < html.index('class="attempt-info"')
        < html.index('class="pill status-action"')
    )
    assert "No tools were used; this answer was generated from conversation context only." in html


def test_render_attempt_list_shows_missing_documents_info_icon_instead_of_warning(
    tmp_path: Path,
):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    store.record_reply_attempt(
        conversation_id="cid-1",
        conversation_title="技术部",
        trigger_message_id="msg-1",
        trigger_sender="Xiaomin",
        trigger_text="@Alex Chen 这个怎么处理？",
        action="send_reply",
        sensitivity_kind="general",
        draft_reply_text="先按A方案走",
        codex_session_id="session-1",
        audit_documents_json="[]",
        audit_tool_events_json='[{"tool":"exec_command","command":"rg 上下文"}]',
        audit_summary="已根据当前对话上下文生成回复。",
    )

    html = render_attempt_list(store)

    assert "Quality warning" not in html
    assert "send_reply has no audit documents" not in html
    assert 'class="attempt-info"' in html
    assert "data-tooltip=" in html
    assert "title=" not in html
    assert ".attempt-info::after" in html
    assert (
        "No audit documents were attached; this answer was generated without document evidence."
        in html
    )


def test_render_attempt_list_shows_missing_codex_session_info_icon_instead_of_warning(
    tmp_path: Path,
):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    store.record_reply_attempt(
        conversation_id="cid-1",
        conversation_title="技术部",
        trigger_message_id="msg-1",
        trigger_sender="Xiaomin",
        trigger_text="@Alex Chen 这个怎么处理？",
        action="send_reply",
        sensitivity_kind="general",
        draft_reply_text="先按A方案走",
        audit_documents_json='[{"path":"chat","relevance":"直接上下文"}]',
        audit_tool_events_json='[{"tool":"exec_command","command":"rg 上下文"}]',
        audit_summary="已根据当前对话上下文生成回复。",
    )

    html = render_attempt_list(store)

    assert "Quality warning" not in html
    assert "missing codex_session_id" not in html
    assert 'class="attempt-info"' in html
    assert (
        "No Codex session is linked; review this attempt using the stored audit fields only."
        in html
    )


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
    store.record_sent_reply("cid-1", "msg-1", "先按A方案走（by明哥分身）")

    status, html = render_attempt_detail(store, attempt_id)

    assert status == 200
    assert "attempt-conversation-banner" in html
    assert "群名" in html
    assert "技术部" in html
    assert "触发人：Xiaomin" in html
    assert html.index("群名") < html.index("内部反馈/建议修改")
    assert html.index("Trigger") < html.index("生成回复")
    assert html.index("Trigger") < html.index("先按A方案走（by明哥分身）")
    assert "review-grid" in html
    assert "reply-pre" in html
    assert "@Alex Chen 这个怎么处理？" in html
    assert "direct ask" in html
    assert "Audit summary" in html
    assert "查看岗位画像后建议先按A方案走" in html
    assert "Audit documents" in html
    assert '<details class="card collapsible-card">' in html
    assert html.index("Audit documents") < html.index("面试/岗位画像.md")
    assert "面试/岗位画像.md" in html
    assert "Audit tool events" in html
    assert html.index("Audit tool events") < html.index("rg 岗位")
    assert "rg 岗位" in html
    assert "json-pre" in html
    assert "json-key" in html
    assert "json-string" in html
    assert "\n  " in html
    assert "先按A方案走" in html
    assert "Draft reply (raw Codex reply)" in html
    assert "Final reply (send-ready text)" in html
    assert "permission" in html
    assert "内部反馈/建议修改" in html
    assert "反馈意见" in html
    assert "建议回复" in html
    assert f'action="/attempts/{attempt_id}/feedback"' in html
    assert "textarea" in html
    assert "Codex local history" in html
    assert "/codex/session-1" in html
    assert "撤销发送" in html
    assert "撤销不可用" in html


def test_render_attempt_detail_shows_counterparty_feedback(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    attempt_id = seed_attempt(store)
    store.record_sent_reply(
        "cid-1",
        "msg-1",
        "先按A方案走",
        feedback_token="token-2",
    )
    store.upsert_feedback_event(
        key="event-2",
        feedback_token="token-2",
        rating="not_useful",
        rating_label="不太有用",
        comment="没有回答到我的问题",
        source="ceo-agent-spike",
        received_at="2026-06-02T08:05:00.000Z",
    )

    status, html = render_attempt_detail(store, attempt_id)

    assert status == 200
    assert "对方反馈" in html
    assert html.index("内部反馈/建议修改") < html.index("对方反馈")
    assert "token-2" in html
    assert "不太有用" in html
    assert "没有回答到我的问题" in html
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
    assert "💬 Sent" in html


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
                '{"timestamp":"2026-05-14T12:00:00Z","type":"session_meta","payload":{"id":"session-1","cwd":"/Users/principal/Documents/memory"}}',
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
    assert "@Alex Chen 这个怎么处理？" in html
    assert '<details class="event event-assistant" open>' in html
    assert '<details class="event event-session">' in html
    assert '<time>2026-05-14T12:00:01Z</time>' in html


def test_render_codex_session_detail_returns_404_when_missing(tmp_path: Path):
    status, html = render_codex_session_detail("missing", codex_home=tmp_path)

    assert status == 404
    assert "Codex session not found" in html


def test_render_codex_session_detail_shows_related_history_when_file_missing(
    tmp_path: Path,
):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    attempt_id = store.record_reply_attempt(
        conversation_id="cid-1",
        conversation_title="Phina",
        trigger_message_id="msg-1",
        trigger_sender="Phina",
        trigger_text="明哥，这个怎么处理？",
        action="send_reply",
        sensitivity_kind="general",
        draft_reply_text="先按A方案走",
        codex_session_id="missing-session",
        audit_summary="已审阅。",
    )

    status, html = render_codex_session_detail(
        "missing-session",
        codex_home=tmp_path,
        store=store,
    )

    assert status == 200
    assert "Codex session unavailable" in html
    assert "Codex session not found" not in html
    assert "The local Codex transcript file for this session is no longer available" in html
    assert "Related history" in html
    assert f"/attempts/{attempt_id}" in html
    assert "明哥，这个怎么处理？" in html


def test_render_attempt_detail_shows_recall_button_when_recall_key_exists(
    tmp_path: Path,
):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    attempt_id = seed_attempt(store)
    store.record_sent_reply(
        "cid-1",
        "msg-1",
        "先按A方案走（by明哥分身）",
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
        "先按A方案走（by明哥分身）",
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
    store.record_sent_reply("cid-1", "msg-1", "先按A方案走（by明哥分身）")

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
                    content="@Alex Chen(明哥) 明哥分身，大模型项目经理需要具备什么能力",
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
        "app.worker.send_macos_notification",
        lambda **kwargs: None,
    )
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    dws = FakeDws()

    result = handle_reviewed_message_reply(
        store,
        dws,
        user_name="Mina 邹",
        group_name="【招聘】大模型项目经理/大模型数据解决方案专家",
        message_str="@Alex Chen(明哥) 明哥分身，大模型项目经理需要具备什么能力",
        reply_text="这个岗位核心看业务拆解、模型理解、项目推进和学习速度。",
    )

    attempt = store.get_reply_attempt(result["attempt_id"])
    sent_reply = store.get_sent_reply("cid-1", "msg-1")
    assert result["send_status"] == "sent"
    assert attempt is not None
    assert attempt.trigger_sender == "Mina 邹"
    assert attempt.trigger_text == "@Alex Chen(明哥) 明哥分身，大模型项目经理需要具备什么能力"
    assert (
        attempt.final_reply_text
        == "这个岗位核心看业务拆解、模型理解、项目推进和学习速度。（by明哥分身）"
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
        "app.worker.send_macos_notification",
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
        == "我已经完成审核，会把核心 comment 补到 tracker。（by明哥分身）"
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
                    content="明哥分身，大模型项目经理需要具备什么能力",
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
        "app.worker.send_macos_notification",
        lambda **kwargs: None,
    )
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    dws = FakeDws()

    result = handle_reviewed_message_reply(
        store,
        dws,
        user_name="Mina 邹",
        group_name="Mina 邹",
        message_str="明哥分身，大模型项目经理需要具备什么能力",
        reply_text="这个岗位核心看业务拆解、模型理解、项目推进和学习速度。",
    )

    attempt = store.get_reply_attempt(result["attempt_id"])
    sent_reply = store.get_sent_reply("cid-private", "msg-private-1")
    assert result["send_status"] == "sent"
    assert attempt is not None
    assert attempt.trigger_sender == "Mina 邹"
    assert attempt.trigger_text == "明哥分身，大模型项目经理需要具备什么能力"
    assert "> Mina 邹: 明哥分身，大模型项目经理需要具备什么能力" in attempt.final_reply_text
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
        "app.worker.send_macos_notification",
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
    assert "active" in html


def test_render_error_list_paginates(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    store.record_error("cid-1", "msg-1", "codex", "older error")
    store.record_error("cid-2", "msg-2", "send", "newer error")

    first_page = render_error_list(store, limit=1, page=1)
    second_page = render_error_list(store, limit=1, page=2)

    assert "newer error" in first_page
    assert "older error" not in first_page
    assert 'href="/errors?page=2"' in first_page
    assert "1-1" in first_page
    assert "1 / 2" in first_page
    assert "older error" in second_page
    assert "newer error" not in second_page
    assert 'href="/errors"' in second_page
    assert "2-2" in second_page
    assert "2 / 2" in second_page


def test_render_error_list_marks_sent_trigger_errors_resolved(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    store.record_error(
        "cid-1",
        "msg-1",
        "send",
        "'CachedDwsClient' object has no attribute 'reply_message'",
    )
    attempt_id = store.record_reply_attempt(
        conversation_id="cid-1",
        conversation_title="国内外融资群",
        trigger_message_id="msg-1",
        trigger_sender="Lily",
        trigger_text="@Alex Chen 这个怎么看？",
        action="send_reply",
        sensitivity_kind="general",
        draft_reply_text="先按这个口径回复。",
    )
    store.update_reply_attempt(
        attempt_id,
        final_reply_text="先按这个口径回复。",
        send_status="sent",
    )
    store.record_sent_reply("cid-1", "msg-1", "先按这个口径回复。")

    html = render_error_list(store)

    assert "resolved: sent" in html
    assert '<span class="pill status-active">active</span>' not in html


def test_run_audit_web_uses_stable_uvicorn_protocols(monkeypatch, tmp_path: Path):
    calls = {}

    def fake_run(app, **kwargs):
        calls["app"] = app
        calls["kwargs"] = kwargs

    monkeypatch.setattr("app.audit_web.uvicorn.run", fake_run)

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
    monkeypatch.setattr("app.audit_web.uvicorn.run", fake_run)

    run_audit_web(
        tmp_path / "worker.sqlite3",
        host="127.0.0.1",
        port=8765,
        reload=True,
        reload_dirs=[tmp_path],
    )

    assert calls["app"] == "app.audit_web:create_default_audit_app"
    assert calls["kwargs"]["factory"] is True
    assert calls["kwargs"]["reload"] is True
    assert calls["kwargs"]["loop"] == "asyncio"
    assert calls["kwargs"]["http"] == "h11"
