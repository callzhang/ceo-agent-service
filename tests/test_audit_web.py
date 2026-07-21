import json
import os
import sqlite3
import subprocess
from pathlib import Path
from zoneinfo import ZoneInfo

from fastapi.testclient import TestClient

import app.audit_web as audit_web_module
from app.audit_web import (
    create_audit_app,
    create_default_audit_app,
    handle_developer_prompt_post,
    handle_prompt_variables_post,
    handle_system_config_post,
    handle_user_prompt_post,
    handle_feedback_post,
    handle_rerun_attempt_post,
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
    render_log_list,
    render_task_project_detail,
    render_tasks_page,
    render_tutorial_page,
    render_user_feedback_list,
    run_audit_web,
)
from app.developer_prompt import read_developer_prompt_template
from app.config import load_env_file
from app.dingtalk_models import DingTalkConversation, DingTalkMessage
from app.setup_wizard_models import SetupWizardEvent
from app.store import AutoReplyStore
from app.universal_context import UniversalTaskContext
from app.universal_executor import build_universal_action_execution
from app.universal_plan import PlannedAction, PlannedActionKind, UniversalAudit, UniversalPlan


def task_script_json(html: str, element_id: str):
    marker = f'<script id="{element_id}" type="application/json">'
    return json.loads(html.split(marker, 1)[1].split("</script>", 1)[0])


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


def seed_meeting_attempt(store: AutoReplyStore) -> int:
    job_id = store.upsert_meeting_alignment_job(
        meeting_id="minutes-history-1",
        title="项目评审会",
        source_json='{"summary":"讨论上线范围","source_url":"https://minutes.example/1"}',
        participants_json='[{"name":"Derek"},{"name":"Mina"}]',
        ended_at="2026-07-14T09:50:00+08:00",
        eligible_at="2026-07-14T10:00:00+08:00",
        status="pending",
    )
    store.update_meeting_alignment_job(
        job_id,
        status="sent",
        target_kind="group",
        target_id="cid-project",
        target_title="项目群",
        mentions_json='[{"name":"Mina","user_id":"user-mina"}]',
        final_message="会后对齐：@Mina 请确认风险预算。",
        send_result_json='{"status":"sent"}',
    )
    return store.record_meeting_alignment_run(
        job_id=job_id,
        codex_session_id="meeting-session-history-1",
        decision_json='{"action":"send","target":{"title":"项目群"}}',
        audit_summary="会后对齐：上线范围仍未一致。",
        status="sent",
        error="",
        audit_tool_events_json=json.dumps(
            [
                {
                    "event_type": "response_item",
                    "tool": "exec_command",
                    "call_id": "meeting-call-1",
                    "title": "Read meeting memory",
                    "relevance": "确认会议相关历史判断",
                    "input": '{"cmd":"rg 上线范围 /Users/principal/Documents/memory"}',
                    "command": "rg 上线范围 /Users/principal/Documents/memory",
                },
                {
                    "event_type": "response_item",
                    "tool": "tool_output",
                    "call_id": "meeting-call-1",
                    "output": "memory.md:1:上线范围需要先确认风险预算",
                },
            ],
            ensure_ascii=False,
        ),
    )


def test_format_local_time_converts_utc_sqlite_timestamp():
    assert audit_web_module._format_local_time(
        "2026-06-03 09:55:59",
        local_tz=ZoneInfo("America/Los_Angeles"),
    ) == "2026-06-03 02:55:59"


def test_format_local_time_converts_iso_timestamp_with_timezone():
    assert audit_web_module._format_local_time(
        "2026-06-03T09:55:59Z",
        local_tz=ZoneInfo("Asia/Shanghai"),
    ) == "2026-06-03 17:55:59"


def test_format_local_time_preserves_empty_or_unknown_value():
    assert audit_web_module._format_local_time("") == ""
    assert audit_web_module._format_local_time("not-a-time") == "not-a-time"


def test_render_attempt_list_shows_history_rows(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    attempt_id = seed_attempt(store)

    html = render_attempt_list(store)

    assert "CEO Agent Audit" in html
    assert "最近 24 小时事件" in html
    assert 'id="history-event-chart"' in html
    assert "echarts@5" in html
    assert "historyEventChartData" in html
    assert '"name": "💬 Sent"' in html
    assert f"/attempts/{attempt_id}" in html
    assert "技术部" in html
    assert "Xiaomin" in html
    assert "💬 Sent" in html
    assert 'class="pill status-action action-state-sent">💬 Sent</span>' in html
    assert "attempt-feed" in html
    assert "attempt-item" in html
    assert "attempt-line" in html
    assert 'class="table-toolbar"' in html
    assert 'class="table-toolbar-search"' in html
    assert 'class="table-type-select"' in html
    assert "type: all" in html
    assert '<option value="sent">sent</option>' in html
    assert "sent" in html
    assert "reacted" in html
    assert "skipped" in html
    assert "failed" in html
    assert "20/页" in html
    assert "问" in html
    assert "答" in html
    assert "attempt-body" not in html
    assert "&gt; Xiaomin:" not in html
    assert f"/attempts/{attempt_id}" in html
    assert "查看/反馈" in html
    assert ">Codex</a>" not in html
    assert "/codex/session-1" not in html


def _seed_wechat_pending(store: AutoReplyStore) -> int:
    """A WeChat reply attempt plus its single ready_to_send delivery. Returns the
    delivery id."""
    store.record_reply_attempt(
        conversation_id="wxg@chatroom",
        conversation_title="AI数据市场行业友商资讯",
        trigger_message_id="wx-m1",
        trigger_sender="群友",
        trigger_text="大家怎么看 RAG？",
        action="send_reply",
        sensitivity_kind="general",
        codex_reason="context-aware",
        draft_reply_text="我的看法是……",
        send_status="pending",
        channel="wechat",
    )
    store.enqueue_reply_task(
        channel="wechat",
        conversation_id="wxg@chatroom",
        conversation_title="AI数据市场行业友商资讯",
        single_chat=False,
        trigger_message_id="wx-m1",
        trigger_create_time="2026-07-18T10:00:00",
        trigger_sender="群友",
        trigger_text="大家怎么看 RAG？",
    )
    task = store.list_reply_tasks(statuses=("pending",), limit=10)[-1]
    store.create_wechat_delivery(
        reply_task_id=task.id,
        account_id="acct-1",
        target_type="group",
        target_id="wxg@chatroom",
        conversation_id="wxg@chatroom",
        reply_text="我的看法是……",
    )
    return store.get_wechat_delivery_for_task(task.id).id


def test_history_shows_wechat_badge_and_send_buttons_for_pending_delivery(
    tmp_path: Path,
):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    delivery_id = _seed_wechat_pending(store)

    html = render_attempt_list(store)

    # WeChat items are labelled and get inline 发送/拒绝 buttons wired to the
    # matching delivery, returning to the history page (next=/).
    assert "微信</span>" in html
    assert f"/wechat/deliveries/{delivery_id}/approve?next=/" in html
    assert f"/wechat/deliveries/{delivery_id}/reject?next=/" in html
    assert ">发送</button>" in html
    assert ">拒绝</button>" in html


def test_history_no_send_buttons_for_dingtalk_attempt(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    seed_attempt(store)  # DingTalk, already sent

    html = render_attempt_list(store)

    assert "微信</span>" not in html
    assert "/wechat/deliveries/" not in html
    assert ">发送</button>" not in html


def test_history_send_buttons_gone_after_delivery_leaves_pending(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    delivery_id = _seed_wechat_pending(store)
    # user rejected (or it was sent) -> no longer ready_to_send
    store.set_wechat_delivery_status(delivery_id, "failed", error="user_rejected")

    html = render_attempt_list(store)

    # badge still shows (it is a WeChat item), but no actionable buttons remain
    assert "微信</span>" in html
    assert "💬 Failed" in html
    assert "💬 Pending" not in html
    assert f"/wechat/deliveries/{delivery_id}/approve" not in html
    assert ">发送</button>" not in html


def test_history_wechat_send_button_matches_exact_trigger(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    delivery_id = _seed_wechat_pending(store)
    store.record_reply_attempt(
        conversation_id="wxg@chatroom",
        conversation_title="AI数据市场行业友商资讯",
        trigger_message_id="wx-without-delivery",
        trigger_sender="另一位群友",
        trigger_text="另一条消息",
        action="no_reply",
        sensitivity_kind="general",
        send_status="skipped",
        channel="wechat",
    )

    html = render_attempt_list(store, search_object_types=("wechat",))

    assert html.count(f"/wechat/deliveries/{delivery_id}/approve?next=/") == 1
    assert html.count(f"/wechat/deliveries/{delivery_id}/reject?next=/") == 1


def test_render_attempt_list_links_task_history_to_task_detail(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    project_id = store.create_work_project(
        title="AI 待办推进",
        category="product",
        priority="P1",
        risk_level="medium",
        owner_name="Mina",
    )
    todo_id = store.create_work_todo(
        project_id=project_id,
        title="向 Mina 解释待办更新",
        description="说明重要事项判断口径，并同步更新后的 TODO。",
        owner_name="Mina",
    )
    follow_up_id = store.create_follow_up_draft(
        project_id=project_id,
        todo_id=todo_id,
        owner_user_id="user-mina",
        owner_name="Mina",
        target_kind="direct",
        question_text="Mina，这个 TODO 描述是否清楚？",
        scheduled_at="2026-07-15 10:00:00",
        status="sent",
    )

    html = render_attempt_list(store)
    detail = render_task_project_detail(store, project_id)[1]

    assert "task-history-item" in html
    assert f"/tasks/{project_id}#follow-up-{follow_up_id}" in html
    assert "查看 task" in html
    assert f'id="todo-{todo_id}"' in detail
    assert f'id="follow-up-{follow_up_id}"' in detail


def test_meeting_history_uses_reply_card_and_detail_contract(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    run_id = seed_meeting_attempt(store)

    html = render_attempt_list(store)

    assert f'/meeting-attempts/{run_id}' in html
    assert 'class="attempt-item"' in html
    assert "会后对齐" in html
    assert "项目群" in html

    client = TestClient(create_audit_app(store.path))
    detail = client.get(f"/meeting-attempts/{run_id}")
    assert detail.status_code == 200
    assert "项目评审会" in detail.text
    assert "attempt-conversation-banner" in detail.text
    assert "attempt-banner-actions" in detail.text
    assert "attempt-detail-grid" in detail.text
    assert "review-grid" in detail.text
    assert "reply-pre" in detail.text
    assert "source_json" not in detail.text
    assert "decision_json" not in detail.text
    assert "Meeting source" not in detail.text
    assert "Decision summary" not in detail.text
    assert "Message and delivery" not in detail.text
    assert "Tool uses" in detail.text
    assert "Read meeting memory" in detail.text
    assert "确认会议相关历史判断" in detail.text
    assert "rg 上线范围 /Users/principal/Documents/memory" in detail.text
    assert "memory.md:1:上线范围需要先确认风险预算" in detail.text
    assert "Mention resolution" in detail.text
    assert "/codex/meeting-session-history-1" in detail.text

    chart = audit_web_module._history_chart_payload(store)
    assert chart["total"] == 1
    assert {series["name"] for series in chart["series"]} == {"💬 Sent"}

    missing_session_status, missing_session_html = render_codex_session_detail(
        "meeting-session-history-1",
        codex_home=tmp_path / "missing-codex-home",
        store=store,
    )
    assert missing_session_status == 200
    assert f"/meeting-attempts/{run_id}" in missing_session_html


def test_history_search_shows_similar_codex_sessions(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    run_id = seed_meeting_attempt(store)
    store.upsert_codex_session_search_index(
        session_id="meeting-session-history-1",
        source_type="meeting_alignment",
        source_id=str(run_id),
        title="历史上线范围对齐",
        summary_text="历史相似会议：上线范围、风险预算、故障面。",
        fts_text="历史 相似 会议 上线 范围 风险 预算 故障 面",
        embedding=[1.0, 0.0],
    )

    html = render_attempt_list(
        store,
        query="风险预算故障面",
        query_embedding=[1.0, 0.0],
    )

    assert "相似 Codex sessions" in html
    assert "历史上线范围对齐" in html
    assert "历史相似会议：上线范围、风险预算、故障面。" in html
    assert "/codex/meeting-session-history-1" in html
    assert f"/meeting-attempts/{run_id}" in html


def test_history_search_object_type_checkboxes_control_results(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    store.record_reply_attempt(
        conversation_id="cid-history",
        conversation_title="History Search Group",
        trigger_message_id="msg-history",
        trigger_sender="Mina",
        trigger_text="风险预算需要确认",
        action="send_reply",
        sensitivity_kind="general",
        send_status="sent",
    )
    store.enqueue_reply_task(
        conversation_id="cid-task",
        conversation_title="Task Search Group",
        single_chat=False,
        trigger_message_id="msg-task",
        trigger_create_time="2026-07-15 09:00:00",
        trigger_sender="Tara",
        trigger_text="风险预算 task pending",
        available_at="2026-07-15 09:00:00",
    )
    run_id = seed_meeting_attempt(store)
    store.upsert_codex_session_search_index(
        session_id="meeting-session-history-1",
        source_type="meeting_alignment",
        source_id=str(run_id),
        title="历史上线范围对齐",
        summary_text="历史相似会议：上线范围、风险预算、故障面。",
        fts_text="历史 相似 会议 上线 范围 风险 预算 故障 面",
        embedding=[1.0, 0.0],
    )

    default_html = render_attempt_list(
        store,
        query="风险预算",
        query_embedding=[1.0, 0.0],
    )
    assert 'name="object_type" value="replay" checked' in default_html
    assert 'name="object_type" value="task" checked' in default_html
    assert 'name="object_type" value="meeting" checked' in default_html
    assert "History Search Group" in default_html
    assert "Task Search Group" in default_html
    assert "相似 Codex sessions" in default_html

    replay_only_html = render_attempt_list(
        store,
        query="风险预算",
        search_object_types=("replay",),
        query_embedding=[1.0, 0.0],
    )
    assert "History Search Group" in replay_only_html
    assert "Task Search Group" not in replay_only_html
    assert "相似 Codex sessions" not in replay_only_html

    meeting_only_html = render_attempt_list(
        store,
        query="风险预算",
        search_object_types=("meeting",),
        query_embedding=[1.0, 0.0],
    )
    assert "History Search Group" not in meeting_only_html
    assert "Task Search Group" not in meeting_only_html
    assert "相似 Codex sessions" in meeting_only_html
    assert f"/meeting-attempts/{run_id}" in meeting_only_html

    task_only_html = render_attempt_list(
        store,
        query="风险预算",
        search_object_types=("task",),
        query_embedding=[1.0, 0.0],
    )
    assert "History Search Group" not in task_only_html
    assert "Task Search Group" in task_only_html
    assert "相似 Codex sessions" not in task_only_html

    object_type_html = render_attempt_list(
        store,
        limit=1,
        query="风险预算",
        search_object_types=("meeting",),
        query_embedding=[1.0, 0.0],
    )
    assert 'name="object_type" value="replay"' in object_type_html
    assert 'name="object_type" value="replay" checked' not in object_type_html
    assert 'name="object_type" value="task" checked' not in object_type_html
    assert 'name="object_type" value="meeting" checked' in object_type_html


def test_history_wechat_object_filter_separates_message_channels(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    store.record_reply_attempt(
        conversation_id="cid-dingtalk-history",
        conversation_title="DingTalk History Group",
        trigger_message_id="msg-dingtalk-history",
        trigger_sender="Mina",
        trigger_text="channel filter",
        action="send_reply",
        sensitivity_kind="general",
        send_status="sent",
    )
    store.record_reply_attempt(
        conversation_id="cid-wechat-history",
        conversation_title="WeChat History Group",
        trigger_message_id="msg-wechat-history",
        trigger_sender="Alex",
        trigger_text="channel filter",
        action="send_reply",
        sensitivity_kind="general",
        send_status="sent",
        channel="wechat",
    )

    default_html = render_attempt_list(store, query="channel filter")
    assert 'name="object_type" value="wechat" checked' in default_html

    wechat_html = render_attempt_list(
        store,
        query="channel filter",
        search_object_types=("wechat",),
    )
    assert "WeChat History Group" in wechat_html
    assert "DingTalk History Group" not in wechat_html

    replay_html = render_attempt_list(
        store,
        query="channel filter",
        search_object_types=("replay",),
    )
    assert "DingTalk History Group" in replay_html
    assert "WeChat History Group" not in replay_html


def test_history_chart_labels_terminal_reactions_and_oa_actions(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    store.record_reply_attempt(
        conversation_id="cid-reacted",
        conversation_title="Friday",
        trigger_message_id="msg-reacted",
        trigger_sender="Xiaomin",
        trigger_text="[群公告]@所有人 今天 bug 日清。",
        action="no_reply",
        sensitivity_kind="general",
        codex_reason="用轻量 reaction 表达支持。",
        send_status="reacted",
    )
    store.record_reply_attempt(
        conversation_id="cid-oa",
        conversation_title="审批通知",
        trigger_message_id="msg-oa",
        trigger_sender="工作通知",
        trigger_text="[Ding]审批提醒",
        action="oa_approval",
        sensitivity_kind="internal_finance",
        codex_reason="退回",
        oa_action="退回",
        send_status="commented",
    )
    store.record_reply_attempt(
        conversation_id="cid-blocked",
        conversation_title="Friday",
        trigger_message_id="msg-blocked",
        trigger_sender="Xiaomin",
        trigger_text="@Alex Chen 需要外部授权。",
        action="send_reply",
        sensitivity_kind="general",
        codex_reason="external authorization required",
        send_status="blocked",
    )

    payload = audit_web_module._history_chart_payload(store)
    series_names = {series["name"] for series in payload["series"]}

    assert "🙂 Reacted" in series_names
    assert "🧾 Returned" in series_names
    assert "💬 Blocked" in series_names
    assert "💬 Failed" not in series_names
    assert "💬 Processing" not in series_names


def test_table_toolbar_uses_fixed_alignment_metrics(tmp_path: Path):
    html = render_attempt_list(AutoReplyStore(tmp_path / "worker.sqlite3"))

    assert ".table-toolbar-search{position:relative;display:flex;align-items:center;margin:0;width:320px;max-width:100%}" in html
    assert ".table-type-select{width:116px}" in html
    assert ".table-page-links{display:flex;align-items:center;justify-content:center;gap:3px;width:204px" in html
    assert ".table-page-link,.table-page-arrow,.table-page-ellipsis{display:inline-flex;align-items:center;justify-content:center;height:32px" in html
    assert ".table-toolbar-total{min-width:72px;text-align:right" in html


def test_table_toolbar_uses_shared_component_and_live_search(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")

    history_html = render_attempt_list(store)
    tasks_html = render_tasks_page(store)
    logs_html = render_log_list(store)

    assert 'data-table-toolbar="history"' in history_html
    assert 'data-table-toolbar="tasks"' in tasks_html
    assert 'data-table-toolbar="logs"' in logs_html
    assert 'data-live-search="server"' in history_html
    assert 'data-live-search="server"' in logs_html
    assert 'data-live-search="server"' not in tasks_html
    assert history_html.count("data-table-toolbar-live-search") == 1
    assert logs_html.count("data-table-toolbar-live-search") == 1
    assert 'data-live-search-input' in history_html
    assert 'data-live-search-input' in tasks_html
    assert 'data-live-search-input' in logs_html
    assert 'params.delete("page")' in history_html
    assert 'setTimeout(submitSearch, 250)' in logs_html
    assert 'data-live-search-region="history"' in history_html
    assert 'data-live-search-region="logs"' in logs_html
    assert "window.location.assign(query" not in history_html
    assert "fetch(targetUrl.toString()" in history_html
    assert "history.replaceState" in logs_html


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
        send_status="sent",
    )
    newer_id = store.record_reply_attempt(
        conversation_id="cid-new",
        conversation_title="Newer Group",
        trigger_message_id="msg-new",
        trigger_sender="Newer",
        trigger_text="newer question",
        action="send_reply",
        sensitivity_kind="general",
        send_status="sent",
    )

    first_page = render_attempt_list(
        store,
        limit=1,
        page=1,
        type_filter=("sent",),
        query="question",
    )
    second_page = render_attempt_list(
        store,
        limit=1,
        page=2,
        type_filter=("sent",),
        query="question",
    )

    assert f"/attempts/{newer_id}" in first_page
    assert f"/attempts/{older_id}" not in first_page
    assert 'value="question"' in first_page
    assert '<option value="sent" selected>sent</option>' in first_page
    assert '<option value="1" selected>1/页</option>' in first_page
    assert '<span class="table-toolbar-total">共 2 条</span>' in first_page
    assert 'href="/?page=2&amp;limit=1&amp;q=question&amp;type=sent"' in first_page
    assert 'aria-label="上一页"' in first_page
    assert 'aria-label="下一页"' in first_page
    assert 'class="table-page-link active" aria-current="page">1</span>' in first_page
    assert 'class="table-page-arrow disabled" aria-label="上一页"' in first_page
    assert f"/attempts/{older_id}" in second_page
    assert f"/attempts/{newer_id}" not in second_page
    assert 'href="/?limit=1&amp;q=question&amp;type=sent"' in second_page
    assert 'class="table-page-link active" aria-current="page">2</span>' in second_page
    assert 'class="table-page-arrow disabled" aria-label="下一页"' in second_page


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

    response = client.get("/?page=2&limit=50")

    assert response.status_code == 200
    assert f"/attempts/{first_id}" in response.text
    assert 'class="table-page-link active" aria-current="page">2</span>' in response.text
    assert '<option value="50" selected>50/页</option>' in response.text


def test_history_route_reads_multi_type_query(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    store.record_reply_attempt(
        conversation_id="cid-sent",
        conversation_title="Sent Group",
        trigger_message_id="msg-sent",
        trigger_sender="Mina",
        trigger_text="sent question",
        action="send_reply",
        sensitivity_kind="general",
        send_status="sent",
    )
    reacted_id = store.record_reply_attempt(
        conversation_id="cid-reacted",
        conversation_title="Reacted Group",
        trigger_message_id="msg-reacted",
        trigger_sender="Mina",
        trigger_text="reacted question",
        action="add_emoji",
        sensitivity_kind="general",
        send_status="reacted",
    )
    store.record_reply_attempt(
        conversation_id="cid-skipped",
        conversation_title="Skipped Group",
        trigger_message_id="msg-skipped",
        trigger_sender="Mina",
        trigger_text="skipped question",
        action="no_reply",
        sensitivity_kind="general",
        send_status="skipped",
    )
    app = create_audit_app(store.path)
    client = TestClient(app)

    response = client.get("/?type=sent&type=reacted&limit=1")

    assert response.status_code == 200
    assert f"/attempts/{reacted_id}" in response.text
    assert "Skipped Group" not in response.text
    assert "type: sent, reacted" in response.text
    assert '<option value="sent">sent</option>' in response.text
    assert '<option value="reacted">reacted</option>' in response.text


def test_render_attempt_list_filters_by_type_and_preserves_query(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    sent_id = store.record_reply_attempt(
        conversation_id="cid-sent",
        conversation_title="Sent Group",
        trigger_message_id="msg-sent",
        trigger_sender="Mina",
        trigger_text="sent question",
        action="send_reply",
        sensitivity_kind="general",
        send_status="sent",
    )
    reacted_id = store.record_reply_attempt(
        conversation_id="cid-reacted",
        conversation_title="Reacted Group",
        trigger_message_id="msg-reacted",
        trigger_sender="Mina",
        trigger_text="reacted question",
        action="add_emoji",
        sensitivity_kind="general",
        send_status="reacted",
    )
    store.record_reply_attempt(
        conversation_id="cid-skipped",
        conversation_title="Skipped Group",
        trigger_message_id="msg-skipped",
        trigger_sender="Mina",
        trigger_text="skipped question",
        action="no_reply",
        sensitivity_kind="general",
        send_status="skipped",
    )

    html = render_attempt_list(
        store,
        limit=1,
        page=1,
        type_filter=("sent", "reacted"),
    )

    assert f"/attempts/{reacted_id}" in html
    assert f"/attempts/{sent_id}" not in html
    assert "Reacted Group" in html
    assert "Sent Group" not in html
    assert '<option value="sent">sent</option>' in html
    assert '<option value="reacted">reacted</option>' in html
    assert '<option value="skipped">skipped</option>' in html
    assert 'href="/?page=2&amp;limit=1&amp;type=sent&amp;type=reacted"' in html
    assert "共 2 条" in html


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
        "msg-older",
        "已经有反馈的旧回复",
        feedback_token="token-already-synced",
    )
    store.upsert_feedback_event(
        key="event-already-synced",
        feedback_token="token-already-synced",
        rating="useful",
        rating_label="很有用",
        source="ceo-agent-spike",
        received_at="2026-06-02T08:00:00.000Z",
    )
    store.record_sent_reply(
        "cid-1",
        "msg-1",
        "先按A方案走",
        feedback_token="token-1",
    )
    calls = []

    def fake_sync(_store, sent_replies, **kwargs):
        calls.append((list(sent_replies), kwargs))

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
    sent_replies, kwargs = calls[0]
    assert len(sent_replies) == 1
    assert sent_replies[0].feedback_token == "token-1"
    assert kwargs == {
        "timeout_seconds": audit_web_module.USER_FEEDBACK_SYNC_TIMEOUT_SECONDS,
        "limit_per_token": audit_web_module.USER_FEEDBACK_SYNC_LIMIT_PER_TOKEN,
    }


def test_handle_user_feedback_sync_post_uses_small_batch(tmp_path: Path, monkeypatch):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    seed_attempt(store)
    for index in range(audit_web_module.USER_FEEDBACK_SYNC_BATCH_LIMIT + 2):
        store.record_sent_reply(
            "cid-1",
            f"msg-{index}",
            "先按A方案走",
            feedback_token=f"token-{index}",
        )
    calls = []

    def fake_sync(_store, sent_replies, **kwargs):
        calls.append((list(sent_replies), kwargs))

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
    sent_replies, kwargs = calls[0]
    assert len(sent_replies) == audit_web_module.USER_FEEDBACK_SYNC_BATCH_LIMIT
    assert kwargs["timeout_seconds"] == audit_web_module.USER_FEEDBACK_SYNC_TIMEOUT_SECONDS
    assert kwargs["limit_per_token"] == audit_web_module.USER_FEEDBACK_SYNC_LIMIT_PER_TOKEN


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

    def fake_sync(_store, sent_replies, **kwargs):
        calls.append((list(sent_replies), kwargs))

    monkeypatch.setattr(
        audit_web_module,
        "_sync_feedback_events_for_sent_replies",
        fake_sync,
    )

    response = client.post("/user-feedback/sync", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/user-feedback"
    assert len(calls) == 1
    assert calls[0][1]["timeout_seconds"] == (
        audit_web_module.USER_FEEDBACK_SYNC_TIMEOUT_SECONDS
    )
    assert calls[0][1]["limit_per_token"] == (
        audit_web_module.USER_FEEDBACK_SYNC_LIMIT_PER_TOKEN
    )


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
    tutorial_html = render_tutorial_page()
    user_feedback_html = render_user_feedback_list(store)
    config_html = render_config_page()
    codex_html = render_codex_session_list(store)
    errors_html = render_error_list(store)
    tasks_html = render_tasks_page(store)

    assert '<span class="nav-item active" aria-current="page">History</span>' in history_html
    assert '<a class="nav-item" href="/">History</a>' not in history_html
    assert '<a class="nav-item" href="/tutorial">Tutorial</a>' in history_html
    assert '<a class="nav-item" href="/user-feedback">用户反馈</a>' in history_html
    assert '<a class="nav-item" href="/config">Config</a>' in history_html

    assert '<span class="nav-item active" aria-current="page">Tutorial</span>' in tutorial_html
    assert '<a class="nav-item" href="/tutorial">Tutorial</a>' not in tutorial_html

    assert '<span class="nav-item active" aria-current="page">用户反馈</span>' in user_feedback_html
    assert '<a class="nav-item" href="/user-feedback">用户反馈</a>' not in user_feedback_html

    assert '<span class="nav-item active" aria-current="page">Config</span>' in config_html
    assert '<a class="nav-item" href="/config">Config</a>' not in config_html

    assert '<span class="nav-item active" aria-current="page">Codex Sessions</span>' in codex_html
    assert '<a class="nav-item" href="/codex">Codex Sessions</a>' not in codex_html

    assert '<span class="nav-item active" aria-current="page">Logs</span>' in errors_html
    assert '<a class="nav-item" href="/logs">Logs</a>' not in errors_html

    assert '<span class="nav-item active" aria-current="page">Tasks</span>' in tasks_html
    assert '<a class="nav-item" href="/tasks">Tasks</a>' not in tasks_html


def test_render_tutorial_page_shows_wizard_status(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    store.upsert_setup_wizard_step(
        step_id="preflight",
        status="done",
        summary="Python is available",
    )

    html = render_tutorial_page(store=store)

    assert "Initialization Wizard" in html
    assert "Python is available" in html
    assert 'class="setup-step-status setup-status-done"' in html
    assert 'data-action-id="check_cli_components"' in html
    assert "安装检查流程" not in html
    assert "/config?tab=system" in html
    assert "/logs" in html
    assert "/tasks" in html
    assert "Tutorial" in html
    assert "Landing page" not in html


def test_tutorial_wechat_step_only_shows_check_and_connect(
    tmp_path: Path,
):
    from app.wechat.models import WechatReplyScope

    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    store.upsert_setup_wizard_step(
        step_id="preflight",
        status="done",
        summary="ready",
    )
    store.upsert_wechat_read_state(
        account_id="acct-1",
        account_dir="/private/account",
        db_dir="/private/db",
        app_version="4.0",
        self_user_id="wxid-self",
        capability_status="ready",
    )
    store.replace_wechat_reply_scopes(
        "acct-1",
        [
            WechatReplyScope(
                account_id="acct-1",
                target_type="group",
                target_id="group-1@chatroom",
                conversation_id="group-1@chatroom",
                display_name="产品讨论群",
                trigger_mode="mention_current_account",
                binding_status="verified",
            )
        ],
    )

    html = render_tutorial_page(store=store)

    assert 'data-action-id="check_wechat_connection"' in html
    assert 'data-action-id="connect_wechat"' in html
    assert 'data-action-id="verify_wechat"' not in html
    assert 'id="wechat-target-picker"' not in html


def test_render_config_page_shows_wechat_target_picker_for_ready_account(
    tmp_path: Path,
):
    from app.wechat.models import WechatReplyScope

    db_path = tmp_path / "worker.sqlite3"
    store = AutoReplyStore(db_path)
    store.upsert_wechat_read_state(
        account_id="acct-1",
        account_dir="/private/account",
        db_dir="/private/db",
        app_version="4.0",
        self_user_id="wxid-self",
        capability_status="ready",
    )
    store.replace_wechat_reply_scopes(
        "acct-1",
        [
            WechatReplyScope(
                account_id="acct-1",
                target_type="group",
                target_id="group-1@chatroom",
                conversation_id="group-1@chatroom",
                display_name="产品讨论群",
                trigger_mode="mention_current_account",
                binding_status="verified",
            )
        ],
    )

    html = render_config_page(active_tab="wechat", db_path=db_path)

    assert 'href="/config?tab=wechat"' in html
    assert 'id="wechat-target-kind"' not in html
    assert 'id="wechat-target-query"' in html
    assert 'id="wechat-target-results"' in html
    assert 'id="wechat-save-targets"' in html
    assert "/config/wechat/conversations" in html
    assert "/config/wechat/reply-scope" in html
    assert "/tutorial/run/verify_wechat" not in html
    assert "产品讨论群" in html
    assert "群聊仅在有人明确 @你 时回复" in html
    assert 'kind: "all"' in html
    assert 'item.target_type === "group" ? "群聊" : "好友"' in html
    assert "/private/account" not in html
    assert "/private/db" not in html


def test_render_tutorial_page_expands_tilde_worker_db(
    monkeypatch,
    tmp_path: Path,
):
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("CEO_WORKER_DB", "~/dbs/worker.sqlite3")
    monkeypatch.chdir(tmp_path)

    html = render_tutorial_page()

    assert "Initialization Wizard" in html
    assert (home / "dbs" / "worker.sqlite3").exists()
    assert not (tmp_path / "~").exists()


def test_create_default_audit_app_expands_tilde_worker_db(
    monkeypatch,
    tmp_path: Path,
):
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("CEO_WORKER_DB", "~/dbs/default.sqlite3")
    monkeypatch.chdir(tmp_path)

    client = TestClient(create_default_audit_app())
    response = client.get("/tutorial")

    assert response.status_code == 200
    assert (home / "dbs" / "default.sqlite3").exists()
    assert not (tmp_path / "~").exists()


def test_tutorial_route_renders_first_time_setup(tmp_path: Path):
    client = TestClient(create_audit_app(tmp_path / "worker.sqlite3"))

    response = client.get("/tutorial")

    assert response.status_code == 200
    assert "Initialization Wizard" in response.text
    assert '<span class="nav-item active" aria-current="page">Tutorial</span>' in response.text


def test_tutorial_status_route_returns_json(tmp_path: Path):
    client = TestClient(create_audit_app(tmp_path / "worker.sqlite3"))

    response = client.get("/tutorial/status")

    assert response.status_code == 200
    payload = response.json()
    assert payload["steps"][0]["step_id"] == "preflight"
    assert payload["steps"][0]["title"] == "Preflight"


def test_history_route_returns_busy_page_when_database_is_locked(
    monkeypatch,
    tmp_path: Path,
):
    def locked_attempt_list(*args, **kwargs):
        del args, kwargs
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(audit_web_module, "render_attempt_list", locked_attempt_list)
    client = TestClient(create_audit_app(tmp_path / "worker.sqlite3"))

    response = client.get("/")

    assert response.status_code == 200
    assert "History is temporarily busy" in response.text
    assert "refresh" in response.text


def test_history_route_renders_chart_on_default_page(tmp_path: Path):
    client = TestClient(create_audit_app(tmp_path / "worker.sqlite3"))

    response = client.get("/")

    assert response.status_code == 200
    assert "CEO Agent Audit" in response.text
    assert "最近 24 小时事件" in response.text


def test_tutorial_check_route_records_real_step_status(tmp_path: Path):
    db_path = tmp_path / "worker.sqlite3"
    client = TestClient(create_audit_app(db_path))

    response = client.post("/tutorial/check/preflight")

    assert response.status_code == 200
    assert response.json()["step_id"] == "preflight"
    row = AutoReplyStore(db_path).get_setup_wizard_step("preflight")
    assert row is not None
    assert row["summary"]


def test_tutorial_run_route_records_action_event(monkeypatch, tmp_path: Path):
    def fake_run(action_id, *, repo_root, env):
        del repo_root, env
        assert action_id == "setup_service_config"
        return SetupWizardEvent(
            step_id="service_config",
            action_id="setup_service_config",
            status="done",
            summary="created",
        )

    monkeypatch.setattr(audit_web_module, "run_setup_action", fake_run)
    db_path = tmp_path / "worker.sqlite3"
    store = AutoReplyStore(db_path)
    for step_id in ("preflight", "cli_components", "mcp"):
        store.upsert_setup_wizard_step(step_id=step_id, status="done", summary="ok")
    client = TestClient(create_audit_app(db_path))

    response = client.post("/tutorial/run/setup_service_config")

    assert response.status_code == 200
    assert response.json()["status"] == "done"
    events = AutoReplyStore(db_path).list_setup_wizard_events("service_config")
    assert events[0]["action_id"] == "setup_service_config"


def test_tutorial_run_route_rejects_blocked_action(tmp_path: Path):
    client = TestClient(create_audit_app(tmp_path / "worker.sqlite3"))

    response = client.post("/tutorial/run/setup_service_config")

    assert response.status_code == 409


def test_tutorial_run_route_persists_failed_action_status(
    monkeypatch,
    tmp_path: Path,
):
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "empty-codex-home"))
    monkeypatch.setenv("CLAUDE_CONFIG_PATH", str(tmp_path / "claude.json"))
    db_path = tmp_path / "worker.sqlite3"
    store = AutoReplyStore(db_path)
    store.upsert_setup_wizard_step(step_id="preflight", status="done", summary="ok")
    store.upsert_setup_wizard_step(
        step_id="cli_components",
        status="done",
        summary="ok",
    )
    client = TestClient(create_audit_app(db_path))

    response = client.post("/tutorial/run/setup_mcp")

    assert response.status_code == 200
    assert response.json()["status"] == "failed"
    row = AutoReplyStore(db_path).get_setup_wizard_step("mcp")
    assert row is not None
    assert row["status"] == "failed"
    assert row["summary"] == "MEMORY_CONNECTOR_URL is missing."


def test_tutorial_confirm_route_accepts_form_submission(tmp_path: Path):
    db_path = tmp_path / "worker.sqlite3"
    store = AutoReplyStore(db_path)
    store.upsert_setup_wizard_step(step_id="dry_run", status="done", summary="ok")
    client = TestClient(create_audit_app(db_path))

    response = client.post(
        "/tutorial/confirm/live_send",
        data={"confirmed_by": "tester"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/tutorial"
    row = AutoReplyStore(db_path).get_setup_wizard_step("live_send")
    assert row is not None
    assert row["manual_confirmed_by"] == "tester"


def test_tutorial_confirm_route_rejects_non_confirmable_step(tmp_path: Path):
    client = TestClient(create_audit_app(tmp_path / "worker.sqlite3"))

    response = client.post("/tutorial/confirm/service_config")

    assert response.status_code == 404


def test_tasks_page_renders_projects_and_todos_without_global_followups(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "task.sqlite3")
    project_id = store.create_work_project(
        title="售前知识库建设",
        category="sales",
        status="active",
        priority="P1",
        risk_level="medium",
        background="销售支持项目。",
        current_state="整理中",
        next_step="补齐来源链接",
    )
    todo_id = store.create_work_todo(
        project_id=project_id,
        title="补齐来源链接",
        status="open",
        priority="P1",
        deadline_at="2099-06-20 18:00:00",
    )
    store.create_work_todo(
        project_id=project_id,
        title="整理销售材料",
        status="done",
        priority="P2",
        deadline_at="2026-06-11 18:00:00",
    )
    store.create_follow_up_draft(
        project_id=project_id,
        todo_id=todo_id,
        owner_name="Alex",
        target_conversation_id="cid-1",
        target_kind="group",
        question_text="来源链接补齐到哪一步了？",
        status="draft",
    )

    html = render_tasks_page(store)

    assert "售前知识库建设" in html
    assert "补齐来源链接" in html
    assert "来源链接补齐到哪一步了" not in html
    assert "Pending follow-ups" not in html
    assert f"/tasks/{project_id}" in html
    assert '<section class="tasks-page">' in html
    assert '<section class="card">' not in html
    assert '<span id="tasks-count" class="tasks-count">1 tasks</span>' in html
    assert 'id="task-search-input"' in html
    assert "Search</button>" not in html
    assert 'id="tasks-table"' in html
    assert "tabulator-tables@6.4.0/dist/css/tabulator.min.css" in html
    assert "tabulator-tables@6.4.0/dist/js/tabulator.min.js" in html
    assert 'headerFilter: "select"' in html
    assert ".tabulator-row.tabulator-selectable:hover" in html
    assert "background-color:#f5faff" in html
    assert 'layout: "fitColumns"' in html
    assert 'layout: "fitDataStretch"' not in html
    assert "variableHeight: true" in html
    assert 'title: "Status"' in html
    assert 'title: "Category"' in html
    assert 'id="task-sort"' not in html
    assert 'class="task-sort-link' not in html

    rows = task_script_json(html, "tasks-data")
    assert len(rows) == 1
    row = rows[0]
    assert row["title"] == "售前知识库建设"
    assert row["status"] == "in progress"
    assert row["category"] == "sales"
    assert row["priority"] == "P1"
    assert row["riskLevel"] == "medium"
    assert row["progressSummary"] == "1/2 (50%)"
    assert row["detailUrl"] == f"/tasks/{project_id}"
    assert row["todos"][0]["title"] == "补齐来源链接"
    assert row["todos"][0]["due"].startswith("2099-06-21")
    assert row["todos"][0]["done"] is False
    assert row["todos"][1]["title"] == "整理销售材料"
    assert row["todos"][1]["done"] is True
    assert 'title: "Progress"' in html
    assert "progress-bar" in html
    assert 'title: "Open"' not in html
    assert 'table.on("rowClick"' in html
    assert "window.location.href = row.getData().detailUrl" in html
    assert "<a class=\"task-project-title\"" not in html
    assert 'title: "ToDos", field: "todoCount", minWidth: 320, widthGrow: 2' in html


def test_tasks_page_todo_cell_limits_visible_items(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "task.sqlite3")
    project_id = store.create_work_project(
        title="多待办项目",
        category="projects",
        status="active",
        priority="P1",
        risk_level="medium",
    )
    for index in range(5):
        store.create_work_todo(
            project_id=project_id,
            title=f"待办 {index + 1}",
            status="open",
            priority="P1",
        )

    html = render_tasks_page(store)
    rows = task_script_json(html, "tasks-data")

    assert len(rows[0]["todos"]) == 5
    assert "todos.slice(0, 3)" in html
    assert "todo-total" in html
    assert "总共 ${todos.length} 条" in html


def test_tasks_page_filters_projects_by_full_text_query(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "task.sqlite3")
    matching_project_id = store.create_work_project(
        title="售前知识库建设",
        category="sales",
        status="active",
        priority="P1",
        risk_level="medium",
        owner_name="Alex",
        background="销售支持项目。",
        facts_json=json.dumps(
            [
                {
                    "description": "需要补齐来源链接",
                    "source": "reply_attempt:7",
                    "created": "2026-06-07",
                    "updated": "2026-06-07",
                }
            ],
            ensure_ascii=False,
        ),
    )
    store.create_work_todo(
        project_id=matching_project_id,
        title="补齐来源链接",
        owner_name="Alex",
        status="open",
        priority="P1",
    )
    store.create_work_project(
        title="招聘专员圆桌",
        category="recruiting",
        status="active",
        priority="P2",
        risk_level="low",
        owner_name="Bea",
        background="候选人流程讨论。",
    )

    html = render_tasks_page(store, query="来源链接 Alex")

    assert "售前知识库建设" in html
    assert "补齐来源链接" in html
    assert 'value="来源链接 Alex"' in html
    assert '<span id="tasks-count" class="tasks-count">2 tasks</span>' in html
    initial = task_script_json(html, "tasks-initial-state")
    rows = task_script_json(html, "tasks-data")
    assert initial["query"] == "来源链接 Alex"
    assert {row["title"] for row in rows} == {"售前知识库建设", "招聘专员圆桌"}
    assert "来源链接" in next(row for row in rows if row["title"] == "售前知识库建设")["search"]
    assert "alex" in next(row for row in rows if row["title"] == "售前知识库建设")["search"]


def test_tasks_page_paginates_and_preserves_search_params(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "task.sqlite3")
    for index in range(25):
        project_id = store.create_work_project(
            title=f"候选人项目 {index + 1:02d}",
            category="recruiting",
            status="active",
            priority="P1",
            risk_level="medium",
            background="候选人流程。",
        )
        store.create_work_todo(
            project_id=project_id,
            title=f"补齐候选人材料 {index + 1:02d}",
            status="open",
            priority="P1",
        )

    html = render_tasks_page(store, query="候选人", page=2, page_size=20)

    assert '<span id="tasks-count" class="tasks-count">25 tasks</span>' in html
    assert 'class="table-toolbar"' in html
    assert 'class="table-toolbar-search"' in html
    assert '<select id="task-type-filter"' in html
    assert 'id="tasks-pages"' in html
    assert '<option value="20" selected>20/页</option>' in html
    assert '<span id="tasks-total" class="table-toolbar-total">共 25 条</span>' in html
    initial = task_script_json(html, "tasks-initial-state")
    rows = task_script_json(html, "tasks-data")
    assert initial["query"] == "候选人"
    assert initial["page"] == 2
    assert initial["pageSize"] == 20
    assert "候选人项目 05" in html
    assert "候选人项目 25" in html
    assert len(rows) == 25


def test_tasks_page_respects_page_size(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "task.sqlite3")
    for index in range(25):
        store.create_work_project(
            title=f"项目 {index + 1:02d}",
            category="projects",
            status="active",
            priority="P2",
            risk_level="low",
        )

    html = render_tasks_page(store, page_size=50)

    assert '<span id="tasks-count" class="tasks-count">25 tasks</span>' in html
    assert '<option value="50" selected>50/页</option>' in html
    assert task_script_json(html, "tasks-initial-state")["pageSize"] == 50
    assert "项目 01" in html
    assert "项目 25" in html


def test_tasks_page_filters_by_category(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "task.sqlite3")
    store.create_work_project(
        title="招聘项目",
        category="recruiting",
        status="active",
        priority="P2",
        risk_level="low",
    )
    store.create_work_project(
        title="销售项目",
        category="sales",
        status="active",
        priority="P1",
        risk_level="medium",
    )

    html = render_tasks_page(store, category="recruiting")

    assert "招聘项目" in html
    assert "销售项目" in html
    assert task_script_json(html, "tasks-initial-state")["category"] == "recruiting"
    assert task_script_json(html, "tasks-categories") == ["recruiting", "sales"]
    assert '<span id="tasks-count" class="tasks-count">2 tasks</span>' in html
    assert 'headerFilterValue: initial.category || ""' in html


def test_tasks_page_sorts_by_priority_and_risk(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "task.sqlite3")
    store.create_work_project(
        title="低优先级高风险",
        category="projects",
        status="active",
        priority="P2",
        risk_level="high",
    )
    store.create_work_project(
        title="高优先级低风险",
        category="projects",
        status="active",
        priority="P0",
        risk_level="low",
    )
    store.create_work_project(
        title="中优先级中风险",
        category="projects",
        status="active",
        priority="P1",
        risk_level="medium",
    )

    priority_html = render_tasks_page(store, sort="priority_desc")
    risk_html = render_tasks_page(store, sort="risk_desc")

    priority_initial = task_script_json(priority_html, "tasks-initial-state")
    risk_initial = task_script_json(risk_html, "tasks-initial-state")
    priority_rows = {row["title"]: row for row in task_script_json(priority_html, "tasks-data")}
    risk_rows = {row["title"]: row for row in task_script_json(risk_html, "tasks-data")}

    assert priority_initial["sort"] == "priority_desc"
    assert risk_initial["sort"] == "risk_desc"
    assert '"priority_desc": ["priorityRank", "asc"]' in priority_html
    assert '"risk_desc": ["riskRank", "asc"]' in risk_html
    assert priority_rows["高优先级低风险"]["priorityRank"] == 0
    assert priority_rows["中优先级中风险"]["priorityRank"] == 1
    assert priority_rows["低优先级高风险"]["priorityRank"] == 2
    assert risk_rows["低优先级高风险"]["riskRank"] == 0
    assert risk_rows["中优先级中风险"]["riskRank"] == 1
    assert risk_rows["高优先级低风险"]["riskRank"] == 2


def test_tasks_page_filters_by_status_and_sorts_by_other_columns(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "task.sqlite3")
    project_a = store.create_work_project(
        title="Alpha",
        category="projects",
        status="active",
        priority="P1",
        risk_level="medium",
        owner_name="Zoe",
        current_state="beta",
        next_step="call owner",
    )
    project_b = store.create_work_project(
        title="Bravo",
        category="projects",
        status="active",
        priority="P1",
        risk_level="medium",
        owner_name="Ada",
        current_state="alpha",
        next_step="brief team",
    )
    store.create_work_todo(
        project_id=project_a,
        title="Alpha todo",
        status="open",
        priority="P1",
    )
    store.create_work_todo(
        project_id=project_b,
        title="Bravo done",
        status="done",
        priority="P1",
    )
    store.create_work_todo(
        project_id=project_b,
        title="Bravo cancelled",
        status="cancelled",
        priority="P1",
    )

    filtered_html = render_tasks_page(store, task_state="completed")
    owner_html = render_tasks_page(store, sort="owner_asc")
    progress_html = render_tasks_page(store, sort="progress_desc")
    todos_html = render_tasks_page(store, sort="todos_desc")

    assert "Bravo" in filtered_html
    assert "Alpha" in filtered_html
    assert task_script_json(filtered_html, "tasks-initial-state")["taskState"] == "completed"
    assert task_script_json(filtered_html, "tasks-states") == ["in progress", "completed"]
    assert 'headerFilterValue: initial.taskState || ""' in filtered_html
    assert task_script_json(owner_html, "tasks-initial-state")["sort"] == "owner_asc"
    assert task_script_json(progress_html, "tasks-initial-state")["sort"] == "progress_desc"
    assert task_script_json(todos_html, "tasks-initial-state")["sort"] == "todos_desc"
    assert '"owner_asc": ["owner", "asc"]' in owner_html
    assert '"progress_desc": ["progressRatio", "desc"]' in progress_html
    assert '"todos_desc": ["todoCount", "desc"]' in todos_html


def test_tasks_page_computes_table_statuses(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "task.sqlite3")
    completed_id = store.create_work_project(
        title="完成项目",
        category="projects",
        status="active",
        priority="P2",
        risk_level="low",
    )
    overdue_id = store.create_work_project(
        title="逾期项目",
        category="projects",
        status="active",
        priority="P1",
        risk_level="medium",
    )
    in_progress_id = store.create_work_project(
        title="推进项目",
        category="projects",
        status="active",
        priority="P1",
        risk_level="medium",
    )
    store.create_work_project(
        title="未开始项目",
        category="projects",
        status="active",
        priority="P2",
        risk_level="low",
    )
    store.create_work_todo(
        project_id=completed_id,
        title="已经完成",
        status="done",
        priority="P2",
    )
    store.create_work_todo(
        project_id=overdue_id,
        title="已经逾期",
        status="open",
        priority="P1",
        deadline_at="2020-01-01 00:00:00",
    )
    store.create_work_todo(
        project_id=in_progress_id,
        title="正在推进",
        status="open",
        priority="P1",
        deadline_at="2099-01-01 00:00:00",
    )

    html = render_tasks_page(store, page_size=50)

    rows = {row["title"]: row for row in task_script_json(html, "tasks-data")}
    assert rows["完成项目"]["status"] == "completed"
    assert rows["逾期项目"]["status"] == "over due"
    assert rows["推进项目"]["status"] == "in progress"
    assert rows["未开始项目"]["status"] == "not started"
    assert task_script_json(html, "tasks-states") == [
        "over due",
        "in progress",
        "not started",
        "completed",
    ]
    assert 'class="task-state ${escapeHtml(cssClass)}"' in html


def test_task_project_detail_renders_project_todos_and_sources(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "task.sqlite3")
    project_id = store.create_work_project(
        title="售前知识库建设",
        category="sales",
        status="active",
        priority="P1",
        risk_level="medium",
        owner_name="Alex",
        tags_json=json.dumps(["售前", "知识库"], ensure_ascii=False),
        related_people_json=json.dumps(
            [{"name": "Alex", "user_id": "owner-1", "role": "owner"}],
            ensure_ascii=False,
        ),
        source_conversations_json=json.dumps(
            [{"id": "cid-1", "title": "售前项目群", "kind": "group"}],
            ensure_ascii=False,
        ),
        background="销售支持项目。",
        facts_json=json.dumps(
            [
                {
                    "description": "需要补齐来源链接",
                    "source": "reply_attempt:7",
                    "created": "2026-06-07",
                    "updated": "2026-06-07",
                }
            ],
            ensure_ascii=False,
        ),
        current_state="整理中",
        next_step="补齐来源链接",
    )
    todo_id = store.create_work_todo(
        project_id=project_id,
        title="补齐来源链接",
        owner_name="Alex",
        status="open",
        priority="P1",
        deadline_at="2099-06-10 18:00:00",
        next_follow_up_at="2099-06-09 10:00:00",
        follow_up_question="来源链接补齐到哪一步了？",
    )
    store.create_work_update(
        project_id=project_id,
        source_type="reply_attempt",
        source_ref="7",
        summary="新增待办",
        changes_json='{"todo":"created"}',
        merge_reason="same project",
        confidence=0.91,
    )
    store.create_follow_up_draft(
        project_id=project_id,
        todo_id=todo_id,
        owner_name="Alex",
        target_conversation_id="cid-1",
        target_kind="group",
        question_text="来源链接补齐到哪一步了？",
        status="draft",
    )

    status, html = render_task_project_detail(store, project_id)

    assert status == 200
    assert "售前知识库建设" in html
    assert "销售支持项目。" in html
    assert "补齐来源链接" in html
    assert "Alex" in html
    assert "2099-06-11" in html
    assert "需要补齐来源链接" in html
    assert "reply_attempt:7" in html
    assert "新增待办" in html
    assert "来源链接补齐到哪一步了" in html
    assert '<span class="detail-pill">售前</span>' in html
    assert '<span class="detail-pill">知识库</span>' in html
    assert '<span class="detail-pill">Alex</span>' in html
    assert '<span class="detail-pill">售前项目群</span>' in html
    assert "&quot;售前&quot;" not in html
    assert html.count('class="column-sized-table"') == 2
    assert html.count('<col style="width:118px">') == 2
    assert '<col style="width:240px">' in html
    assert 'class="todo-detail-list"' in html
    assert f'<article class="todo-detail-item" id="todo-{todo_id}">' in html
    assert 'class="todo-detail-title"' in html
    assert 'class="todo-detail-fields"' in html
    assert f'<div class="todo-detail-followups" data-parent-todo="{todo_id}">' in html
    assert 'class="todo-followup-bubble"' in html
    assert 'class="todo-followup-head"' in html
    assert '<span class="todo-followup-recipient">Alex</span>' in html
    assert '<span class="todo-followup-status">draft</span>' in html
    assert '<div class="todo-followup-message">来源链接补齐到哪一步了？</div>' in html
    assert '<div class="todo-followup-meta">' not in html
    assert "Follow-ups (1)" in html
    assert "售前项目群" in html
    assert "group:cid-1" not in html
    assert "Unlinked follow-ups" not in html
    assert '<div class="todo-detail-value">-</div>' in html


def test_task_project_detail_keeps_unlinked_followups_separate(tmp_path: Path):
    db_path = tmp_path / "worker.sqlite3"
    store = AutoReplyStore(db_path)
    project_id = store.create_work_project(
        title="售前知识库建设",
        category="sales",
        status="active",
        priority="P1",
        risk_level="medium",
    )
    store.create_work_todo(
        project_id=project_id,
        title="补齐来源链接",
        owner_name="Alex",
        status="open",
        priority="P1",
    )
    store.create_follow_up_draft(
        project_id=project_id,
        todo_id=9999,
        owner_name="Alex",
        target_conversation_id="cid-2",
        target_kind="single",
        question_text="这个 follow-up 还缺少明确 TODO 归属。",
        status="draft",
    )

    status, html = render_task_project_detail(store, project_id)

    assert status == 200
    assert "Unlinked follow-ups" in html
    assert "这个 follow-up 还缺少明确 TODO 归属。" in html
    assert '<a href="#todo-9999">#9999</a>' in html
    assert '<div class="todo-detail-followups"' not in html


def test_task_project_detail_renders_dingtalk_todo_link(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "task.sqlite3")
    project_id = store.create_work_project(
        title="客户交付",
        category="projects",
        status="active",
        priority="P1",
        risk_level="medium",
    )
    todo_id = store.create_work_todo(
        project_id=project_id,
        title="给客户同步验收 ETA",
        owner_name="Alex",
        status="open",
        priority="P1",
    )
    store.create_work_todo_dingtalk_link(
        work_todo_id=todo_id,
        dingtalk_task_id="dt-task-1",
        executor_user_id="owner-1",
        executor_name="Alex",
        title_snapshot="给客户同步验收 ETA",
        deadline_at_snapshot="2026-07-01 18:00:00",
        priority_snapshot="P1",
        status="active",
        last_pull_at="2026-06-27 10:00:00",
        last_push_at="2026-06-27 09:00:00",
    )

    status, html = render_task_project_detail(store, project_id)

    assert status == 200
    assert "DingTalk Todo" in html
    assert "dt-task-1" in html
    assert "active" in html
    assert "Last pull" in html
    assert "2026-06-27" in html


def test_tasks_route_renders_page(tmp_path: Path):
    db_path = tmp_path / "worker.sqlite3"
    store = AutoReplyStore(db_path)
    store.create_work_project(
        title="售前知识库建设",
        category="sales",
        status="active",
        priority="P1",
        risk_level="medium",
    )
    client = TestClient(create_audit_app(db_path))

    response = client.get("/tasks")

    assert response.status_code == 200
    assert "售前知识库建设" in response.text
    assert '<span class="nav-item active" aria-current="page">Tasks</span>' in response.text


def test_tasks_route_applies_search_query(tmp_path: Path):
    db_path = tmp_path / "worker.sqlite3"
    store = AutoReplyStore(db_path)
    store.create_work_project(
        title="售前知识库建设",
        category="sales",
        status="active",
        priority="P1",
        risk_level="medium",
        background="销售支持项目。",
    )
    store.create_work_project(
        title="招聘专员圆桌",
        category="recruiting",
        status="active",
        priority="P2",
        risk_level="low",
    )
    client = TestClient(create_audit_app(db_path))

    response = client.get("/tasks?q=销售支持")

    assert response.status_code == 200
    assert "售前知识库建设" in response.text
    assert "招聘专员圆桌" in response.text
    assert task_script_json(response.text, "tasks-initial-state")["query"] == "销售支持"


def test_tasks_route_applies_pagination_params(tmp_path: Path):
    db_path = tmp_path / "worker.sqlite3"
    store = AutoReplyStore(db_path)
    for index in range(25):
        store.create_work_project(
            title=f"候选人项目 {index + 1:02d}",
            category="recruiting",
            status="active",
            priority="P1",
            risk_level="medium",
            background="候选人流程。",
        )
    client = TestClient(create_audit_app(db_path))

    response = client.get("/tasks?q=候选人&page=2&page_size=20")

    assert response.status_code == 200
    assert "候选人项目 05" in response.text
    assert "候选人项目 25" in response.text
    assert '<option value="20" selected>20/页</option>' in response.text
    initial = task_script_json(response.text, "tasks-initial-state")
    assert initial["query"] == "候选人"
    assert initial["page"] == 2
    assert initial["pageSize"] == 20


def test_tasks_route_applies_category_and_sort_params(tmp_path: Path):
    db_path = tmp_path / "worker.sqlite3"
    store = AutoReplyStore(db_path)
    store.create_work_project(
        title="招聘项目",
        category="recruiting",
        status="active",
        priority="P2",
        risk_level="high",
    )
    store.create_work_project(
        title="销售项目",
        category="sales",
        status="active",
        priority="P0",
        risk_level="low",
    )
    client = TestClient(create_audit_app(db_path))

    response = client.get("/tasks?category=recruiting&sort=risk_desc")

    assert response.status_code == 200
    assert "招聘项目" in response.text
    assert "销售项目" in response.text
    initial = task_script_json(response.text, "tasks-initial-state")
    assert initial["category"] == "recruiting"
    assert initial["sort"] == "risk_desc"
    assert '"risk_desc": ["riskRank", "asc"]' in response.text


def test_task_project_detail_route_renders_project(tmp_path: Path):
    db_path = tmp_path / "worker.sqlite3"
    store = AutoReplyStore(db_path)
    project_id = store.create_work_project(
        title="售前知识库建设",
        category="sales",
        status="active",
        priority="P1",
        risk_level="medium",
    )
    store.create_work_todo(
        project_id=project_id,
        title="补齐来源链接",
        owner_name="Alex",
        status="open",
        priority="P1",
    )
    client = TestClient(create_audit_app(db_path))

    response = client.get(f"/tasks/{project_id}")

    assert response.status_code == 200
    assert "售前知识库建设" in response.text
    assert "补齐来源链接" in response.text


def test_task_project_detail_route_returns_404_for_missing_project(tmp_path: Path):
    client = TestClient(create_audit_app(tmp_path / "worker.sqlite3"))

    response = client.get("/tasks/999")

    assert response.status_code == 404
    assert "Project not found" in response.text


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
    assert "CEO_MEETING_PRODUCER_INTERVAL_SECONDS" in html
    assert "meeting producer 扫描 dws minutes 的间隔秒数" in html
    assert "CEO_MEETING_CONSUMER_POLL_INTERVAL_SECONDS" in html
    assert "meeting consumer 检查 pending meeting job 的间隔秒数" in html
    assert "CEO_MEETING_SETTLE_SECONDS" in html
    assert "会议结束后等待多久再允许 meeting consumer 处理" in html
    assert "CEO_TASK_WORK_ITEM_INTERVAL_SECONDS" in html
    assert "task-maintenance 处理 work item/OKR review 的间隔秒数" in html
    assert "CEO_TASK_DAILY_INTERVAL_SECONDS" in html
    assert "task-maintenance 扫 task sources/follow-ups 的间隔秒数" in html
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
    assert "CEO_WORKSPACE" in html
    assert "本地知识库路径" in html
    assert "CEO_WORKER_DB" in html
    assert "CEO_CORPUS_DIR" in html
    assert "DOCUMENT_EXTRACTION_IDS" in html
    assert "抽取该身份的发言或材料" in html
    assert "CEO_FORBIDDEN_PATH_PREFIXES" in html
    assert "按路径前缀识别本机路径泄漏" in html
    assert "CEO_CURRENT_USER_DISPLAY_NAMES" not in html
    assert "CEO_FORBIDDEN_PATH_PREFIXES" in html
    system_section = html.split("<h2>系统运行参数</h2>", 1)[1]
    assert "保存位置" in system_section


def test_handle_system_config_post_saves_runtime_params_to_env_file(
    tmp_path: Path,
    monkeypatch,
):
    env_path = tmp_path / ".env"
    env_path.write_text("CEO_WORKSPACE=/tmp/memory\n", encoding="utf-8")
    monkeypatch.setenv("CEO_ENV_FILE", str(env_path))
    monkeypatch.setenv("CEO_WORKSPACE", "/tmp/memory")

    body = (
        "system_key=CEO_WORKSPACE"
        "&system_value=/tmp/new-memory"
        "&system_key=CEO_PRODUCER_INTERVAL_SECONDS"
        "&system_value=60"
        "&system_key=CEO_CONSUMER_POLL_INTERVAL_SECONDS"
        "&system_value=10"
        "&system_key=CEO_MEETING_PRODUCER_INTERVAL_SECONDS"
        "&system_value=60"
        "&system_key=CEO_MEETING_CONSUMER_POLL_INTERVAL_SECONDS"
        "&system_value=10"
        "&system_key=CEO_MEETING_SETTLE_SECONDS"
        "&system_value=600"
        "&system_key=CEO_TASK_WORK_ITEM_INTERVAL_SECONDS"
        "&system_value=60"
        "&system_key=CEO_TASK_DAILY_INTERVAL_SECONDS"
        "&system_value=86400"
        "&system_key=FAST_PATH_UNREAD_BACKOFF"
        "&system_value=5m"
        "&system_key=MESSAGE_RECOVERY_INTERVAL"
        "&system_value=30m"
        "&system_key=SINGLE_CHAT_READ_RECOVERY_WINDOW"
        "&system_value=12h"
        "&system_key=SINGLE_CHAT_READ_RECOVERY_LIMIT"
        "&system_value=25"
    ).encode()

    status, headers, html = handle_system_config_post(body)

    assert status == 303
    assert headers["Location"] == "/config?tab=system&saved=1"
    assert html == ""
    env_text = env_path.read_text(encoding="utf-8")
    assert "CEO_WORKSPACE=/tmp/new-memory" in env_text
    assert "CEO_PRODUCER_INTERVAL_SECONDS=60" in env_text
    assert "CEO_CONSUMER_POLL_INTERVAL_SECONDS=10" in env_text
    assert "CEO_MEETING_PRODUCER_INTERVAL_SECONDS=60" in env_text
    assert "CEO_MEETING_CONSUMER_POLL_INTERVAL_SECONDS=10" in env_text
    assert "CEO_MEETING_SETTLE_SECONDS=600" in env_text
    assert "CEO_TASK_WORK_ITEM_INTERVAL_SECONDS=60" in env_text
    assert "CEO_TASK_DAILY_INTERVAL_SECONDS=86400" in env_text
    assert "FAST_PATH_UNREAD_BACKOFF=5m" in env_text
    assert "MESSAGE_RECOVERY_INTERVAL=30m" in env_text
    assert "SINGLE_CHAT_READ_RECOVERY_WINDOW=12h" in env_text
    assert "SINGLE_CHAT_READ_RECOVERY_LIMIT=25" in env_text
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


def test_open_dingtalk_popup_fetches_open_route_and_auto_closes(tmp_path: Path):
    client = TestClient(create_audit_app(tmp_path / "worker.sqlite3"))

    response = client.get("/open-dingtalk-popup?conversation_id=cid-1")

    assert response.status_code == 200
    assert "正在打开钉钉消息" in response.text
    assert 'fetch("/open-dingtalk?conversation_id=cid-1", {cache: "no-store"})' in response.text
    assert "window.close()" in response.text


def test_open_dingtalk_popup_rejects_missing_target(tmp_path: Path):
    client = TestClient(create_audit_app(tmp_path / "worker.sqlite3"))

    response = client.get("/open-dingtalk-popup")

    assert response.status_code == 400
    assert response.text == "missing cid or conversation_id"


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
    assert "toConversationByOpenConversationId" not in response.text
    assert "biz.chat.toConversation" not in response.text
    assert "invokeWithCallbackTimeout" not in response.text
    assert "if (ok)" in response.text
    assert "/dingtalk/bridge-status" in response.text
    assert "window.dd.ready" in response.text
    assert "dd-ready-timeout" in response.text
    assert "dd.closePage" in response.text
    assert "当前会话 API" in response.text
    assert "openChatByConversationId 会话跳转能力" in response.text
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
    assert "client.postMessage" in response.text
    assert "ceo-agent-service:navigate" in response.text
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
    assert "navigator.serviceWorker.addEventListener(\"message\"" in response.text
    assert "window.location.assign(targetPath)" in response.text
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


def test_browser_notification_event_includes_attempt_detail_url():
    event = audit_web_module._browser_notification_event(
        title="CEO auto reply",
        message="已回复",
        url="http://127.0.0.1:8765/open-dingtalk?cid=75217569357&attempt_id=123",
    )

    assert event["detail_url"] == "/attempts/123"


def test_browser_notification_event_ignores_invalid_attempt_id():
    event = audit_web_module._browser_notification_event(
        title="CEO auto reply",
        message="已回复",
        url="http://127.0.0.1:8765/open-dingtalk?cid=75217569357&attempt_id=not-a-number",
    )

    assert event["detail_url"] == ""


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
    assert (
        'class="pill status-action action-state-pending">💬 Pending</span>' in html
    )
    assert "#task-1" in html
    assert "HR管理" in html
    assert "Mina" in html
    assert "@Alex Chen(明哥) 这个候选人怎么看？" in html


def test_render_attempt_list_formats_pending_backoff_time_in_local_timezone(
    tmp_path: Path,
):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    store.enqueue_reply_task(
        conversation_id="cid-1",
        conversation_title="HR管理",
        single_chat=False,
        trigger_message_id="msg-queued",
        trigger_create_time="2026-05-28 18:00:00",
        trigger_sender="Mina",
        trigger_text="@Alex Chen(明哥) 这个候选人怎么看？",
        available_at="2026-06-04 08:06:52",
        error="waiting_fast_path_unread_backoff",
    )

    html = render_attempt_list(store)

    expected_time = audit_web_module._format_local_time("2026-06-04 08:06:52")
    assert f"快路径已触发，等待到 {expected_time} 后确认是否仍需处理" in html
    if expected_time != "2026-06-04 08:06:52":
        assert "等待到 2026-06-04 08:06:52" not in html


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

    status, detail = render_attempt_detail(store, attempt_id)

    assert status == 200
    assert "/codex/session-1" in detail
    assert "/codex/new-session" not in detail
    assert "agent 执行记录" in detail


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
    assert 'class="pill status-action action-state-skipped">💬 Skipped</span>' in html
    assert 'class="pill status-action action-state-approved">🧾 通过</span>' in html


def test_attempt_detail_renders_oa_comment_status(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    attempt_id = store.record_reply_attempt(
        conversation_id="cid-1",
        conversation_title="审批通知",
        trigger_message_id="msg-1",
        trigger_sender="工作通知",
        trigger_text="[Ding]审批提醒",
        action="oa_approval",
        sensitivity_kind="internal_finance",
        codex_reason="退回",
        oa_process_instance_id="proc-1",
        oa_task_id="task-1",
        oa_url="https://aflow.dingtalk.com/detail?procInstId=proc-1",
        oa_action="退回",
        oa_remark="请补充预算来源。",
        oa_action_result_json='{"errcode":0,"errmsg":"ok"}',
        send_status="commented",
    )

    status, html = render_attempt_detail(store, attempt_id)

    assert status == 200
    assert "💬 Commented" in html
    assert "🧾 退回" in html
    assert (
        'class="pill status-action action-state-commented">💬 Commented</span>'
        in html
    )
    assert 'class="pill status-action action-state-returned">🧾 退回</span>' in html


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
        send_status="calendar",
    )

    list_html = render_attempt_list(store)
    status, detail_html = render_attempt_detail(store, attempt_id)

    assert status == 200
    assert "💬 Skipped" not in list_html
    assert "📆 Accepted" in list_html
    assert (
        'class="pill status-action action-state-accepted">📆 Accepted</span>'
        in list_html
    )
    assert "Calendar response" in detail_html
    assert "event-1" in detail_html
    assert "accepted" in detail_html
    assert "Calendar response result" in detail_html


def test_attempt_history_renders_message_reaction_status(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    attempt_id = store.record_reply_attempt(
        conversation_id="cid-1",
        conversation_title="Friday",
        trigger_message_id="msg-1",
        trigger_sender="Xiaomin",
        trigger_text="[群公告]@所有人 今天 bug 日清。",
        action="no_reply",
        sensitivity_kind="general",
        codex_reason="群公告无需正式回复，但适合用表情表示支持。",
    )
    store.update_reply_attempt(attempt_id, send_status="reacted", send_error="emoji: 👍")

    list_html = render_attempt_list(store)
    status, detail_html = render_attempt_detail(store, attempt_id)

    assert (
        'class="pill status-action action-state-reacted">🙂 Reacted</span>'
        in list_html
    )
    assert 'class="pill status-action action-state-reacted">🙂 👍</span>' not in list_html
    assert (
        '<span class="attempt-label">答</span>'
        '<span class="attempt-copy attempt-reaction-copy">👍</span>'
        in list_html
    )
    assert ".attempt-copy{" in list_html
    assert ".attempt-copy{color:var(--charcoal);font-size:13px;" in list_html
    assert ".attempt-reaction-copy{" in list_html
    reaction_css = list_html.split(".attempt-reaction-copy{", 1)[1].split("}", 1)[0]
    assert "font-size:13px" in reaction_css
    assert "font-size:16px" not in reaction_css
    assert status == 200
    assert (
        'class="pill status-action action-state-reacted">🙂 Reacted</span>'
        in detail_html
    )
    assert 'class="pill status-action action-state-reacted">🙂 👍</span>' not in detail_html
    assert '<pre class="reply-pre">👍</pre>' in detail_html


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

    assert 'class="pill status-action action-state-skipped">💬 Skipped</span>' in html
    assert '<span class="pill action-no_reply"' not in html
    assert '<span class="pill status-skipped"' not in html


def test_render_attempt_list_uses_failed_action_pill_color(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    store.record_reply_attempt(
        conversation_id="cid-1",
        conversation_title="Mina",
        trigger_message_id="msg-1",
        trigger_sender="Mina",
        trigger_text="@Alex Chen 这个怎么处理？",
        action="send_reply",
        sensitivity_kind="general",
        codex_reason="delivery failed",
        send_status="failed",
    )

    html = render_attempt_list(store)

    assert 'class="pill status-action action-state-failed">💬 Failed</span>' in html


def test_render_attempt_list_labels_unrecoverable_blocked_as_terminal(
    tmp_path: Path,
):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    attempt_id = store.record_reply_attempt(
        conversation_id="cid-1",
        conversation_title="OKR",
        trigger_message_id="msg-1",
        trigger_sender="Mina",
        trigger_text="@Alex Chen 这个 OKR 怎么评分？",
        action="okr_review",
        sensitivity_kind="general",
        codex_reason="live source auth missing",
        send_status="blocked",
    )
    store.update_reply_attempt(
        attempt_id,
        send_status="blocked",
        send_error="blocked_unrecoverable_external_auth: DingTeam OKR page is not logged in",
    )

    html = render_attempt_list(store)

    assert (
        'class="pill status-action action-state-blocked-terminal">'
        "💬 Blocked terminal</span>"
    ) in html
    assert 'class="pill status-action action-state-blocked">💬 Blocked</span>' not in html


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
        < html.index('class="pill status-action action-state-pending"')
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
    assert "agent 执行记录" in detail_response.text


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
    store.record_sent_reply(
        "cid-1",
        "msg-1",
        "先按A方案走（by明哥分身）",
        send_result_json=json.dumps(
            {"send_result": {"result": {"openMessageId": "sent-msg-1"}}}
        ),
    )

    status, html = render_attempt_detail(store, attempt_id)

    assert status == 200
    assert "attempt-conversation-banner" in html
    assert "attempt-banner-actions" in html
    assert "群名" in html
    assert "技术部" in html
    assert "触发人：Xiaomin" in html
    assert "attempt-detail-grid" in html
    detail_grid = html[
        html.index('<div class="attempt-detail-grid">') :
        html.index("内部反馈/建议修改")
    ]
    assert "conversation" not in detail_grid
    assert "trigger sender" not in detail_grid
    assert "permission" in detail_grid
    assert "allow" in detail_grid
    assert "permission reason" not in html
    assert "agent 执行记录" in html
    assert f'action="/attempts/{attempt_id}/rerun?return_to=/attempts/{attempt_id}"' in html
    assert f'action="/attempts/{attempt_id}/recall?return_to=/attempts/{attempt_id}"' in html
    assert "/open-dingtalk-popup?conversation_id=cid-1" in html
    assert "window.open(this.href,'ceo-open-dingtalk','popup,width=420,height=260')" in html
    assert 'class="compact-button open-dingtalk-action"' in html
    assert '<button class="rerun" type="submit">重跑</button>' in html
    assert html.index("群名") < html.index("内部反馈/建议修改")
    assert html.index('class="agent-log-button" href="/codex/session-1"') < html.index(
        "内部反馈/建议修改"
    )
    assert html.index("attempt-banner-actions") < html.index("trigger message id")
    assert html.index("Trigger") < html.index("生成回复")
    assert html.index("Trigger") < html.index("先按A方案走（by明哥分身）")
    assert html.index("Codex reason") < html.index("生成回复")
    assert html.index("direct ask") < html.index("生成回复")
    assert "review-grid" in html
    assert "reply-pre" in html
    assert "@Alex Chen 这个怎么处理？" in html
    assert "Audit summary" in html
    assert "查看岗位画像后建议先按A方案走" in html
    assert "Tool uses" in html
    assert '<details class="card collapsible-card">' in html
    assert html.index("Tool uses") < html.index("面试/岗位画像.md")
    assert "面试/岗位画像.md" in html
    assert "Audit documents" not in html
    assert "Audit tool events" not in html
    assert html.index("Tool uses") < html.index("rg 岗位")
    assert "rg 岗位" in html
    assert "audit-tool-args" in html
    assert "\n  " in html
    assert "先按A方案走" in html
    assert "Draft reply (raw Codex reply)" in html
    assert "permission" in html
    assert "内部反馈/建议修改" in html
    assert "反馈意见" in html
    assert "建议回复" in html
    assert f'action="/attempts/{attempt_id}/feedback"' in html
    assert "textarea" in html
    assert "/codex/session-1" in html
    assert "Codex local history" not in html
    assert "Final reply (send-ready text)" not in html


def test_history_and_attempt_detail_show_redacted_universal_execution(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    assert store.enqueue_reply_task(
        conversation_id="cid-universal-audit",
        conversation_title="通用任务群",
        single_chat=False,
        trigger_message_id="msg-universal-audit",
        trigger_create_time="2026-07-21 10:00:00",
        trigger_sender="Mina",
        trigger_text="请处理并记录",
    )
    task = store.claim_reply_tasks(limit=1)[0]
    context = UniversalTaskContext(
        task_id=task.id,
        conversation_id=task.conversation_id,
        conversation_title=task.conversation_title,
        single_chat=task.single_chat,
        trigger_message_id=task.trigger_message_id,
        trigger_create_time=task.trigger_create_time,
        trigger_sender=task.trigger_sender,
        trigger_text=task.trigger_text,
        context_messages=(),
        required_dependencies=("dws",),
        force_new_decision=False,
        dry_run=False,
        execution_generation=task.execution_generation,
    )
    plan = UniversalPlan(
        task_kind="document_review",
        reason="Review and answer",
        dependencies=["dws", "memory"],
        actions=[
            PlannedAction(
                kind=PlannedActionKind.SEND_REPLY,
                reason="Send the review",
                sensitivity_kind="general",
                payload={"text": "AUDIT_SECRET_SENTINEL"},
            ),
            PlannedAction(
                kind=PlannedActionKind.MEMORY_WRITE,
                reason="Store the decision",
                payload={"data": "Durable review decision.", "type": "text"},
            ),
        ],
        audit=UniversalAudit(summary="Review complete", confidence=0.9),
    )
    plan_execution = store.create_universal_plan_execution(context, plan)
    reply_execution = build_universal_action_execution(
        context, plan_execution, plan_execution.plan.actions[0], 0
    )
    store.claim_universal_action_execution(reply_execution)
    attempt_id = store.record_universal_reply_attempt(
        reply_execution,
        conversation_id=task.conversation_id,
        conversation_title=task.conversation_title,
        trigger_message_id=task.trigger_message_id,
        trigger_sender=task.trigger_sender,
        trigger_text=task.trigger_text,
        action="send_reply",
        sensitivity_kind="general",
        send_status="sent",
    )
    store.complete_universal_action_execution(reply_execution, attempt_id=attempt_id)
    memory_execution = build_universal_action_execution(
        context, plan_execution, plan_execution.plan.actions[1], 1
    )
    store.claim_universal_action_execution(memory_execution)
    store.mark_universal_action_execution_unknown(memory_execution, "timeout after write")
    store.fail_reply_task(task.id, "action_execution_unknown:memory")

    history_html = render_attempt_list(store)
    status, detail_html = render_attempt_detail(store, attempt_id)

    assert status == 200
    for html in (history_html, detail_html):
        assert "Universal planner" not in html
        assert "document_review" not in html
        assert "记忆" in html
        assert "AUDIT_SECRET_SENTINEL" not in html
    assert "自动处理明细" in detail_html
    assert "回复" in detail_html
    assert "Succeeded" in detail_html
    assert "记录到记忆" in detail_html
    assert "Unknown" in detail_html
    assert "timeout after write" in detail_html
    assert "plan_json" not in detail_html


def test_render_attempt_detail_renders_audit_tool_inputs_and_outputs(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    attempt_id = store.record_reply_attempt(
        conversation_id="cid-1",
        conversation_title="技术部",
        trigger_message_id="msg-1",
        trigger_sender="Xiaomin",
        trigger_text="@Alex Chen 这个怎么处理？",
        action="send_reply",
        sensitivity_kind="general",
        codex_session_id="session-1",
        audit_documents_json=json.dumps(
            [
                {
                    "title": "岗位画像",
                    "relevance": "判断岗位要求",
                    "path": "面试/岗位画像.md",
                    "args": {"section": "requirements"},
                }
            ],
            ensure_ascii=False,
        ),
        audit_tool_events_json=json.dumps(
            [
                {
                    "event_type": "response_item",
                    "tool": "exec_command",
                    "call_id": "call-1",
                    "title": "Search role profile",
                    "relevance": "确认岗位画像是否提到项目经理",
                    "input": '{\n  "cmd": "rg -n 岗位 /Users/principal/Documents/memory/面试"\n}',
                    "command": "rg -n 岗位 /Users/principal/Documents/memory/面试",
                },
                {
                    "event_type": "response_item",
                    "tool": "tool_output",
                    "call_id": "call-1",
                    "output": json.dumps(
                        {
                            "result": json.dumps(
                                {
                                    "ok": "success",
                                    "matches": ["岗位画像.md:1:项目经理"],
                                },
                                ensure_ascii=False,
                            )
                        },
                        ensure_ascii=False,
                    ),
                },
            ],
            ensure_ascii=False,
        ),
        audit_summary="已查看工具输入输出。",
    )

    status, html = render_attempt_detail(store, attempt_id)

    assert status == 200
    assert "Tool uses" in html
    assert "2 total · 1 calls · 1 documents" in html
    assert "岗位画像" in html
    assert "判断岗位要求" in html
    assert "面试/岗位画像.md" in html
    assert "Search role profile" in html
    assert "确认岗位画像是否提到项目经理" in html
    assert "exec_command" in html
    assert "format" in html
    assert "terminal" in html
    assert "args" in html
    assert "rg -n 岗位 /Users/principal/Documents/memory/面试" in html
    assert "output" in html
    assert "audit-tool-output-preview" in html
    assert "audit-tool-output-body" in html
    assert '"result": "{' not in html
    assert "ok" in html
    assert "success" in html
    assert "岗位画像.md:1:项目经理" in html


def test_render_attempt_detail_unwraps_terminal_wrapped_mcp_json_output(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    output = (
        "Wall time: 0.8105 seconds\n"
        "Output:\n"
        + json.dumps(
            {
                "result": json.dumps(
                    {
                        "ok": True,
                        "backend": "memory",
                        "processing_status": "pending",
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            },
            ensure_ascii=False,
        )
    )
    attempt_id = store.record_reply_attempt(
        conversation_id="cid-1",
        conversation_title="MKT core",
        trigger_message_id="msg-1",
        trigger_sender="Phina",
        trigger_text="@Alex Chen 这个怎么处理？",
        action="send_reply",
        sensitivity_kind="general",
        audit_tool_events_json=json.dumps(
            [
                {
                    "event_type": "response_item",
                    "tool": "memory_write",
                    "call_id": "call-memory",
                    "input": json.dumps(
                        {"data": "稳定业务口径", "type": "text"},
                        ensure_ascii=False,
                        indent=2,
                    ),
                },
                {
                    "event_type": "response_item",
                    "tool": "tool_output",
                    "call_id": "call-memory",
                    "output": output,
                },
            ],
            ensure_ascii=False,
        ),
        audit_summary="已写入 memory。",
    )

    status, html = render_attempt_detail(store, attempt_id)

    assert status == 200
    assert "1 total · 1 calls · 0 documents" in html
    assert "mcp/json" in html
    assert '"result": "{' not in html
    assert "processing_status" in html
    assert "pending" in html
    assert "backend" in html
    assert "memory" in html


def test_render_attempt_detail_skips_empty_document_args(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    attempt_id = store.record_reply_attempt(
        conversation_id="cid-1",
        conversation_title="MKT core",
        trigger_message_id="msg-1",
        trigger_sender="Phina",
        trigger_text="@Alex Chen 这个怎么处理？",
        action="send_reply",
        sensitivity_kind="general",
        audit_documents_json=json.dumps(
            [
                {
                    "title": "03.3_StarBench产品说明",
                    "url": "https://alidocs.dingtalk.com/i/nodes/doc123",
                    "relevance": "提供 StarBench 产品定位。",
                }
            ],
            ensure_ascii=False,
        ),
        audit_tool_events_json="[]",
        audit_summary="已查看文档。",
    )

    status, html = render_attempt_detail(store, attempt_id)

    assert status == 200
    assert "1 total · 0 calls · 1 documents" in html
    assert "03.3_StarBench产品说明" in html
    assert "提供 StarBench 产品定位。" in html
    assert "https://alidocs.dingtalk.com/i/nodes/doc123" in html
    tool_uses_html = html[html.index("Tool uses") :]
    assert "audit-tool-args" not in tool_uses_html


def test_render_attempt_detail_renders_dws_material_tool_events(tmp_path: Path):
    command = (
        "dws doc read --node https://alidocs.dingtalk.com/i/nodes/doc123 "
        "--format json"
    )
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    attempt_id = store.record_reply_attempt(
        conversation_id="cid-1",
        conversation_title="技术部",
        trigger_message_id="msg-1",
        trigger_sender="Xiaomin",
        trigger_text="@Alex Chen 这个怎么处理？",
        action="send_reply",
        sensitivity_kind="general",
        audit_tool_events_json=json.dumps(
            [
                {
                    "event_type": "response_item",
                    "tool": "exec_command",
                    "call_id": "call-dws-read",
                    "input": json.dumps({"cmd": command}, ensure_ascii=False, indent=2),
                    "command": command,
                },
                {
                    "event_type": "response_item",
                    "tool": "tool_output",
                    "call_id": "call-dws-read",
                    "output": "OpenAI 合作建议补充版\n建议先补齐材料。",
                },
            ],
            ensure_ascii=False,
        ),
        audit_summary="已读取 DWS 材料。",
    )

    status, html = render_attempt_detail(store, attempt_id)

    assert status == 200
    assert "Tool uses" in html
    assert "exec_command" in html
    assert "args" in html
    assert "dws doc read --node" in html
    assert command in html
    assert "output" in html
    assert "OpenAI 合作建议补充版" in html


def test_render_attempt_detail_renders_dws_material_events_from_codex_session(
    tmp_path: Path,
    monkeypatch,
):
    command = (
        "dws doc read --node https://alidocs.dingtalk.com/i/nodes/doc123 "
        "--format json"
    )
    codex_home = tmp_path / ".codex"
    monkeypatch.setattr("app.codex_history.DEFAULT_CODEX_HOME", codex_home)
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
                json.dumps(
                    {
                        "timestamp": "2026-05-14T12:00:00Z",
                        "type": "session_meta",
                        "payload": {"id": "session-1"},
                    },
                    ensure_ascii=False,
                ),
                json.dumps(
                    {
                        "timestamp": "2026-05-14T12:00:01Z",
                        "type": "response_item",
                        "payload": {
                            "type": "function_call",
                            "name": "exec_command",
                            "call_id": "call-dws-read",
                            "arguments": json.dumps(
                                {"cmd": command},
                                ensure_ascii=False,
                            ),
                        },
                    },
                    ensure_ascii=False,
                ),
                json.dumps(
                    {
                        "timestamp": "2026-05-14T12:00:02Z",
                        "type": "response_item",
                        "payload": {
                            "type": "function_call_output",
                            "call_id": "call-dws-read",
                            "output": "OpenAI 合作建议补充版\n建议先补齐材料。",
                        },
                    },
                    ensure_ascii=False,
                ),
            ]
        ),
        encoding="utf-8",
    )
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    attempt_id = store.record_reply_attempt(
        conversation_id="cid-1",
        conversation_title="技术部",
        trigger_message_id="msg-1",
        trigger_sender="Xiaomin",
        trigger_text="@Alex Chen 这个怎么处理？",
        action="send_reply",
        sensitivity_kind="general",
        codex_session_id="session-1",
        codex_transcript_start_line=0,
        codex_transcript_end_line=3,
        audit_tool_events_json="[]",
        audit_summary="已读取 DWS 材料。",
    )

    status, html = render_attempt_detail(store, attempt_id)

    assert status == 200
    assert "Tool uses" in html
    assert "exec_command" in html
    assert "args" in html
    assert "dws doc read --node" in html
    assert command in html
    assert "output" in html
    assert "OpenAI 合作建议补充版" in html


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
    assert "当前发送方式不支持" not in html


def test_attempt_list_uses_single_review_feedback_entrypoint(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    attempt_id = seed_attempt(store)

    html = render_attempt_list(store)

    assert f'href="/attempts/{attempt_id}"' in html
    assert f'href="/attempts/{attempt_id}#feedback"' not in html
    assert "查看/反馈" in html
    assert ">Codex</a>" not in html


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
    store.record_sent_reply(
        "cid-1",
        "msg-1",
        "先按A方案走（by明哥分身）",
        send_result_json=json.dumps(
            {"send_result": {"result": {"openMessageId": "sent-msg-1"}}}
        ),
    )
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
    assert f'action="/attempts/{attempt_id}/rerun?return_to=/codex/session-1"' in html
    assert f'action="/attempts/{attempt_id}/recall?return_to=/codex/session-1"' in html
    assert "/open-dingtalk-popup?conversation_id=cid-1" in html
    assert "查看钉钉消息" in html
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


def test_render_attempt_detail_does_not_show_recall_action_card(
    tmp_path: Path,
):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    attempt_id = seed_attempt(store)
    store.record_sent_reply(
        "cid-1",
        "msg-1",
        "先按A方案走（by明哥分身）",
        send_result_json=json.dumps(
            {"send_result": {"result": {"openMessageId": "sent-msg-1"}}}
        ),
    )

    status, html = render_attempt_detail(store, attempt_id)

    assert status == 200
    assert "attempt-banner-actions" in html
    assert f'action="/attempts/{attempt_id}/recall?return_to=/attempts/{attempt_id}"' in html
    assert "recall-card" not in html


def test_render_attempt_detail_shows_rerun_only_in_banner_actions(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    attempt_id = seed_attempt(store)

    status, html = render_attempt_detail(store, attempt_id)

    assert status == 200
    assert "attempt-banner-actions" in html
    assert f'action="/attempts/{attempt_id}/rerun?return_to=/attempts/{attempt_id}"' in html
    assert "重跑 attempt" not in html
    assert "rerun-card" not in html


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


def test_handle_rerun_attempt_post_requeues_task_and_redirects(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    attempt_id = seed_attempt(store)

    status, headers, html = handle_rerun_attempt_post(
        store,
        attempt_id,
        worker_factory=lambda settings: (_ for _ in ()).throw(
            AssertionError("rerun POST must not run the worker synchronously")
        ),
    )

    assert status == 303
    assert headers["Location"] == f"/attempts/{attempt_id}"
    assert html == ""
    task = store.get_reply_task_for_message("cid-1", "msg-1")
    assert task is not None
    assert task.status == "pending"
    assert task.force_new_decision is True
    assert task.manual_rerun_attempt_id == attempt_id
    trigger = DingTalkMessage.model_validate_json(task.trigger_message_json)
    assert trigger.open_message_id == "msg-1"
    assert trigger.content == "@Alex Chen 这个怎么处理？"


def test_handle_rerun_attempt_post_replaces_invalid_legacy_task_json(
    tmp_path: Path,
):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    attempt_id = seed_attempt(store)
    store.enqueue_reply_task(
        conversation_id="cid-1",
        conversation_title="技术部",
        single_chat=False,
        trigger_message_id="msg-1",
        trigger_create_time="2026-05-13 18:00:00",
        trigger_sender="Xiaomin",
        trigger_text="old",
        trigger_message_json="{}",
    )

    status, headers, html = handle_rerun_attempt_post(store, attempt_id)

    assert status == 303
    assert headers["Location"] == f"/attempts/{attempt_id}"
    assert html == ""
    task = store.get_reply_task_for_message("cid-1", "msg-1")
    assert task is not None
    trigger = DingTalkMessage.model_validate_json(task.trigger_message_json)
    assert trigger.open_message_id == "msg-1"
    assert trigger.content == "@Alex Chen 这个怎么处理？"


def test_handle_recall_post_calls_dws_message_recall_and_records_success(
    tmp_path: Path,
):
    class FakeDws:
        def __init__(self):
            self.calls = []

        def recall_message(self, conversation_id, message_id):
            self.calls.append((conversation_id, message_id))
            return {"success": True}

    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    attempt_id = seed_attempt(store)
    store.record_sent_reply(
        "cid-1",
        "msg-1",
        "先按A方案走（by明哥分身）",
        send_result_json=json.dumps(
            {"send_result": {"result": {"openMessageId": "sent-msg-1"}}}
        ),
    )
    dws = FakeDws()

    status, headers, html = handle_recall_post(store, dws, attempt_id)

    sent_reply = store.get_sent_reply("cid-1", "msg-1")
    assert status == 303
    assert headers["Location"] == f"/attempts/{attempt_id}"
    assert html == ""
    assert dws.calls == [("cid-1", "sent-msg-1")]
    assert sent_reply is not None
    assert sent_reply.recall_status == "recalled"
    assert sent_reply.recalled_at is not None


def test_handle_recall_post_queries_open_task_id_before_message_recall(
    tmp_path: Path,
):
    class FakeDws:
        def __init__(self):
            self.status_queries = []
            self.recall_calls = []

        def query_message_send_status(self, open_task_id):
            self.status_queries.append(open_task_id)
            return {"result": {"openMessageId": "sent-msg-1"}}

        def recall_message(self, conversation_id, message_id):
            self.recall_calls.append((conversation_id, message_id))
            return {"success": True}

    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    attempt_id = seed_attempt(store)
    store.record_sent_reply(
        "cid-1",
        "msg-1",
        "先按A方案走（by明哥分身）",
        send_result_json=json.dumps(
            {"send_result": {"result": {"openTaskId": "task-1"}}}
        ),
    )
    dws = FakeDws()

    status, headers, html = handle_recall_post(store, dws, attempt_id)

    assert status == 303
    assert headers["Location"] == f"/attempts/{attempt_id}"
    assert html == ""
    assert dws.status_queries == ["task-1"]
    assert dws.recall_calls == [("cid-1", "sent-msg-1")]


def test_handle_recall_post_falls_back_to_bot_key_and_records_success(tmp_path: Path):
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
        def recall_message(self, conversation_id, message_id):
            raise AssertionError("should not call dws")

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
            return [
                DingTalkMessage(
                    open_conversation_id=conversation.open_conversation_id,
                    open_message_id=f"sent-{index}",
                    conversation_title=conversation.title,
                    single_chat=conversation.single_chat,
                    sender_name="Alex Chen(明哥)",
                    create_time="2026-05-25 13:31:00",
                    content=reply[3],
                )
                for index, reply in enumerate(self.reply_messages, start=1)
            ]

        def read_unread_messages(self, conversation):
            return []

        def send_message(
            self,
            conversation_id,
            text,
            at_users=None,
            at_open_dingtalk_ids=None,
            at_open_dingtalk_names=None,
            user_id=None,
            open_dingtalk_id=None,
        ):
            del at_open_dingtalk_names, open_dingtalk_id
            self.sent_messages.append(
                (conversation_id, text, at_open_dingtalk_ids or at_users or [], user_id)
            )
            return {"result": {"processQueryKey": "recall-1"}}

        def reply_message(
            self,
            conversation_id,
            ref_message_id,
            ref_sender_open_dingtalk_id,
            text,
            *,
            at_open_dingtalk_ids=None,
            at_open_dingtalk_names=None,
        ):
            self.reply_messages.append(
                (conversation_id, ref_message_id, ref_sender_open_dingtalk_id, text)
            )
            return {"result": {"processQueryKey": "recall-1"}}

        def send_reply_to_trigger(
            self,
            conversation,
            trigger,
            text,
            at_users=None,
            at_open_dingtalk_ids=None,
            at_open_dingtalk_names=None,
        ):
            del at_users, at_open_dingtalk_ids, at_open_dingtalk_names
            return self.reply_message(
                conversation.open_conversation_id,
                trigger.open_message_id,
                trigger.sender_open_dingtalk_id,
                text,
            )

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
        == "@Mina 邹 这个岗位核心看业务拆解、模型理解、项目推进和学习速度。（by明哥分身）"
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
            self.sent_messages = []
            self.reply_messages = []

        def search_conversations(self, query):
            assert query == "官网迭代群"
            return []

        def read_mentioned_messages(self, conversation, limit=50):
            return []

        def read_recent_messages(self, conversation):
            assert conversation.open_conversation_id == "cid-site"
            assert conversation.single_chat is False
            messages = [
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
            messages.extend(
                DingTalkMessage(
                    open_conversation_id=conversation.open_conversation_id,
                    open_message_id=f"sent-{index}",
                    conversation_title=conversation.title,
                    single_chat=conversation.single_chat,
                    sender_name="Alex Chen(明哥)",
                    create_time="2026-05-28 04:05:00",
                    content=reply[3],
                )
                for index, reply in enumerate(self.reply_messages, start=1)
            )
            return messages

        def read_unread_messages(self, conversation):
            return []

        def send_message(
            self,
            conversation_id,
            text,
            at_users=None,
            at_open_dingtalk_ids=None,
            at_open_dingtalk_names=None,
            user_id=None,
            open_dingtalk_id=None,
        ):
            del at_open_dingtalk_names, open_dingtalk_id
            self.sent_messages.append(
                (conversation_id, text, at_open_dingtalk_ids or at_users or [], user_id)
            )
            return {"result": {"processQueryKey": "recall-site-1"}}

        def reply_message(
            self,
            conversation_id,
            ref_message_id,
            ref_sender_open_dingtalk_id,
            text,
            *,
            at_open_dingtalk_ids=None,
            at_open_dingtalk_names=None,
        ):
            self.reply_messages.append(
                (conversation_id, ref_message_id, ref_sender_open_dingtalk_id, text)
            )
            return {"result": {"processQueryKey": "recall-site-1"}}

        def send_reply_to_trigger(
            self,
            conversation,
            trigger,
            text,
            at_users=None,
            at_open_dingtalk_ids=None,
            at_open_dingtalk_names=None,
        ):
            del at_users, at_open_dingtalk_ids, at_open_dingtalk_names
            return self.reply_message(
                conversation.open_conversation_id,
                trigger.open_message_id,
                trigger.sender_open_dingtalk_id,
                text,
            )

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
        == "@Claire 我已经完成审核，会把核心 comment 补到 tracker。（by明哥分身）"
    )
    assert (
        attempt.reviewer_feedback
        == "官网是 marketing 重要内容，CEO 直接相关；这类消息需要审核并回复。"
    )
    assert attempt.corrected_reply_text == "我已经完成审核，会把核心 comment 补到 tracker。"
    assert dws.sent_messages == []
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
            self.reply_messages = []
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
                    sender_open_dingtalk_id="open-mina",
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
            at_open_dingtalk_ids=None,
            at_open_dingtalk_names=None,
            user_id=None,
            open_dingtalk_id=None,
        ):
            del at_open_dingtalk_ids, at_open_dingtalk_names, open_dingtalk_id
            self.sent_messages.append((conversation_id, text, at_users, user_id))
            return {"result": {"processQueryKey": "recall-private-1"}}

        def reply_message(
            self,
            conversation_id,
            ref_message_id,
            ref_sender_open_dingtalk_id,
            text,
            *,
            at_open_dingtalk_ids=None,
            at_open_dingtalk_names=None,
        ):
            self.reply_messages.append(
                (conversation_id, ref_message_id, ref_sender_open_dingtalk_id, text)
            )
            return {"result": {"processQueryKey": "recall-private-1"}}

        def send_reply_to_trigger(
            self,
            conversation,
            trigger,
            text,
            at_users=None,
            at_open_dingtalk_ids=None,
            at_open_dingtalk_names=None,
        ):
            del at_users, at_open_dingtalk_ids, at_open_dingtalk_names
            return self.reply_message(
                conversation.open_conversation_id,
                trigger.open_message_id,
                trigger.sender_open_dingtalk_id,
                text,
            )

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
    assert (
        attempt.final_reply_text
        == "这个岗位核心看业务拆解、模型理解、项目推进和学习速度。（by明哥分身）"
    )
    assert dws.sent_messages == []
    assert dws.reply_messages == [
        (
            "cid-private",
            "msg-private-1",
            "open-mina",
            attempt.final_reply_text,
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
            self.reply_messages = []

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
                    sender_open_dingtalk_id="open-mina",
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
            at_open_dingtalk_ids=None,
            at_open_dingtalk_names=None,
            user_id=None,
            open_dingtalk_id=None,
        ):
            del at_open_dingtalk_ids, at_open_dingtalk_names, open_dingtalk_id
            self.sent_messages.append((conversation_id, text, at_users, user_id))
            return {"result": {"processQueryKey": "recall-private-1"}}

        def reply_message(
            self,
            conversation_id,
            ref_message_id,
            ref_sender_open_dingtalk_id,
            text,
            *,
            at_open_dingtalk_ids=None,
            at_open_dingtalk_names=None,
        ):
            self.reply_messages.append(
                (conversation_id, ref_message_id, ref_sender_open_dingtalk_id, text)
            )
            return {"result": {"processQueryKey": "recall-private-1"}}

        def send_reply_to_trigger(
            self,
            conversation,
            trigger,
            text,
            at_users=None,
            at_open_dingtalk_ids=None,
            at_open_dingtalk_names=None,
        ):
            del at_users, at_open_dingtalk_ids, at_open_dingtalk_names
            return self.reply_message(
                conversation.open_conversation_id,
                trigger.open_message_id,
                trigger.sender_open_dingtalk_id,
                text,
            )

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
    assert attempt.final_reply_text == "收到，那你先按这个口径推进。（by明哥分身）"
    assert dws.sent_messages == []
    assert dws.reply_messages == [
        (
            "cid-private",
            "msg-private-1",
            "open-mina",
            attempt.final_reply_text,
        )
    ]


def test_render_log_list_shows_recent_operations(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    store.record_error("cid-1", "msg-1", "send", "authorization required")
    store.record_reply_attempt(
        conversation_id="cid-2",
        conversation_title="融资群",
        trigger_message_id="msg-2",
        trigger_sender="Lily",
        trigger_text="@Alex 这个怎么看？",
        action="send_reply",
        sensitivity_kind="general",
        draft_reply_text="先按这个口径回复。",
    )
    project_id = store.create_work_project(
        title="售前知识库建设",
        category="sales",
        status="active",
        priority="P1",
        risk_level="medium",
    )
    store.enqueue_work_summary_input("reply_attempt", "7", '{"summary":"新增任务"}')
    store.create_work_update(
        project_id=project_id,
        source_type="reply_attempt",
        source_ref="7",
        summary="新增待办",
        changes_json='{"todo":"created"}',
    )
    store.create_follow_up_draft(
        project_id=project_id,
        todo_id=1,
        owner_name="Alex",
        target_conversation_id="cid-3",
        target_kind="group",
        question_text="进展如何？",
        status="draft",
    )

    html = render_log_list(store)

    assert "Logs" in html
    assert 'class="log-feed"' in html
    assert 'class="log-item"' in html
    assert 'class="log-main"' in html
    assert 'class="log-body single"' in html
    assert "<table>" not in html
    assert "Reply" in html
    assert "Task input" in html
    assert "Task update" in html
    assert "Follow-up" in html
    assert "send_reply" in html
    assert "新增待办" in html
    assert "进展如何？" in html
    assert "send" in html
    assert html.count("authorization required") == 1
    assert "cid-1" in html
    assert "active" in html


def test_render_log_list_paginates(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    store.record_error("cid-1", "msg-1", "codex", "older error")
    store.record_error("cid-2", "msg-2", "send", "newer error")

    first_page = render_log_list(
        store,
        limit=1,
        page=1,
        query="error",
        log_type="Error",
    )
    second_page = render_log_list(
        store,
        limit=1,
        page=2,
        query="error",
        log_type="Error",
    )

    assert "newer error" in first_page
    assert "older error" not in first_page
    assert 'class="table-toolbar"' in first_page
    assert 'class="table-toolbar-search"' in first_page
    assert 'value="error"' in first_page
    assert '<option value="Error" selected>Error</option>' in first_page
    assert '<option value="1" selected>1/页</option>' in first_page
    assert '<span class="table-toolbar-total">共 2 条</span>' in first_page
    assert 'href="/logs?page=2&amp;limit=1&amp;q=error&amp;type=Error"' in first_page
    assert 'class="table-page-link active" aria-current="page">1</span>' in first_page
    assert "older error" in second_page
    assert "newer error" not in second_page
    assert 'href="/logs?limit=1&amp;q=error&amp;type=Error"' in second_page
    assert 'class="table-page-link active" aria-current="page">2</span>' in second_page


def test_render_log_list_marks_sent_trigger_errors_resolved(tmp_path: Path):
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

    html = render_log_list(store)

    assert "resolved: sent" in html
    assert '<span class="pill status-active">active</span>' not in html


def test_logs_route_renders_logs_and_errors_route_remains_compatible(tmp_path: Path):
    db_path = tmp_path / "worker.sqlite3"
    store = AutoReplyStore(db_path)
    store.record_error("cid-1", "msg-1", "send", "authorization required")
    client = TestClient(create_audit_app(db_path))

    logs_response = client.get("/logs")
    errors_response = client.get("/errors")

    assert logs_response.status_code == 200
    assert "Logs" in logs_response.text
    assert "authorization required" in logs_response.text
    assert '<span class="nav-item active" aria-current="page">Logs</span>' in logs_response.text
    assert errors_response.status_code == 200
    assert "Logs" in errors_response.text
    assert "authorization required" in errors_response.text


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
