import json
import os
import sqlite3
import subprocess
import threading
import time
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient

import app.audit_web as audit_web_module
from app.audit_web import (
    create_audit_app,
    create_default_audit_app,
    handle_audit_rules_post,
    handle_developer_prompt_post,
    handle_prompt_variables_post,
    handle_system_config_post,
    handle_user_prompt_post,
    handle_feedback_post,
    handle_needs_human_decision_post,
    handle_rerun_attempt_post,
    handle_agent_run_resolution_post,
    handle_user_feedback_resolve_post,
    handle_user_feedback_sync_post,
    handle_recall_post,
    handle_reviewed_message_reply,
    build_worker_status_payload,
    render_attempt_detail,
    render_attempt_list,
    render_codex_session_detail,
    render_codex_session_list,
    render_config_page,
    render_developer_prompt_editor,
    render_error_list,
    render_log_list,
    render_oa_approval_detail,
    render_service_bugfix_candidates,
    render_task_project_detail,
    render_tasks_page,
    render_tutorial_page,
    render_workers_page,
    render_user_feedback_list,
    run_audit_web,
)
from app.audit_rules import read_audit_rules_template
from app.developer_prompt import read_developer_prompt_template
from app.config import load_env_file
from app.dingtalk_models import DingTalkMessage
from app.setup_wizard_models import SetupWizardEvent
from app.setup_wizard import SETUP_WIZARD_STEPS
from app.store import (
    MAX_RECONCILIATION_EVENTS,
    AgentRole,
    AgentRun,
    AutoReplyStore,
)
from app.wechat.models import WechatMessage


def _claim_audit_run(store, task, *, owner="worker"):
    return store.claim_agent_run(
        task.id,
        task.execution_generation,
        role=AgentRole.AUDIT,
        proposal_revision=0,
        turn_attempt=0,
        parent_agent_run_id=None,
        operation_id=f"audit-agent:{task.id}:{task.execution_generation}",
        owner=owner,
    )


def task_script_json(html: str, element_id: str):
    marker = f'<script id="{element_id}" type="application/json">'
    return json.loads(html.split(marker, 1)[1].split("</script>", 1)[0])


def loopback_test_client(app) -> TestClient:
    return TestClient(
        app,
        client=("127.0.0.1", 50000),
        headers={"Host": "127.0.0.1:8765"},
    )


def complete_setup_wizard(store: AutoReplyStore) -> None:
    for step in SETUP_WIZARD_STEPS:
        store.upsert_setup_wizard_step(
            step_id=step.id,
            status="done",
            summary="complete",
        )


def test_orchestrated_attempt_detail_links_consumer_and_execution_sessions(
    tmp_path: Path,
    monkeypatch,
):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    assert store.enqueue_reply_task(
        conversation_id="cid-1",
        conversation_title="Product",
        single_chat=False,
        trigger_message_id="msg-1",
        trigger_create_time="2026-08-07 09:00:00",
        trigger_sender="Mina",
        trigger_text="Publish the reviewed update.",
        trigger_message_json='{"openMessageId":"msg-1"}',
        execution_generation="generation-1",
    )
    task = store.claim_reply_task(1)
    assert task is not None

    parent_id = None
    terminal_run = None
    for revision in range(2):
        consumer = store.claim_agent_run(
            task.id,
            task.execution_generation,
            role=AgentRole.CONSUMER,
            proposal_revision=revision,
            turn_attempt=0,
            parent_agent_run_id=parent_id,
            operation_id="",
            owner=f"consumer-{revision}",
        ).run
        store.set_agent_run_session(
            consumer.id,
            f"consumer-session-{revision}",
            owner=f"consumer-{revision}",
        )
        store.complete_agent_run(
            consumer.id,
            {"outcome": "proposal", "summary": f"candidate {revision}"},
            owner=f"consumer-{revision}",
        )
        audit = store.claim_agent_run(
            task.id,
            task.execution_generation,
            role=AgentRole.AUDIT,
            proposal_revision=revision,
            turn_attempt=0,
            parent_agent_run_id=consumer.id,
            operation_id=f"operation-{revision}",
            owner=f"audit-{revision}",
        ).run
        store.set_agent_run_session(
            audit.id,
            f"audit-session-{revision}",
            owner=f"audit-{revision}",
        )
        terminal_run = store.complete_agent_run(
            audit.id,
            {"outcome": "executed", "summary": f"audit {revision}"},
            owner=f"audit-{revision}",
        )
        parent_id = terminal_run.id

    assert terminal_run is not None
    attempt_id = store.finalize_orchestrated_reply_task(
        task_id=task.id,
        expected_execution_generation=task.execution_generation,
        run_id=terminal_run.id,
        task_status="done",
        task_error="",
        available_at="",
        conversation_id=task.conversation_id,
        conversation_title=task.conversation_title,
        trigger_message_id=task.trigger_message_id,
        trigger_sender=task.trigger_sender,
        trigger_text=task.trigger_text,
        codex_reason="Published and verified.",
        codex_session_id=terminal_run.codex_session_id,
        codex_transcript_start_line=0,
        codex_transcript_end_line=1,
        audit_tool_events_json='[{"type":"mcp_tool_call","tool":"noise"}]',
        audit_summary="Published and verified.",
        send_status="completed",
        send_error="",
        channel="dingtalk",
    )

    attempt = store.get_reply_attempt(attempt_id)
    status, detail = render_attempt_detail(store, attempt_id)
    history = render_attempt_list(store, include_chart=False)

    assert attempt is not None and attempt.agent_run_id == terminal_run.id
    assert status == 200
    assert "2 revisions" in detail
    assert "查看 Consumer 记录" in detail
    assert f"/attempts/{attempt_id}/execution/consumer" in detail
    assert "查看执行审计" in detail
    assert f"/attempts/{attempt_id}/execution/audit" in detail
    assert "consumer-session-1" not in detail
    assert "audit-session-1" not in detail
    assert "Consumer Agent A" not in history
    assert "Audit Agent B" not in history
    assert "mcp_tool_call" not in history

    codex_home = tmp_path / ".codex"
    session_path = (
        codex_home
        / "sessions"
        / "2026"
        / "08"
        / "10"
        / "consumer-session-1.jsonl"
    )
    session_path.parent.mkdir(parents=True)
    session_path.write_text(
        "\n".join(
            [
                json.dumps({"type": "session_meta", "payload": {"id": "consumer-session-1"}}),
                json.dumps(
                    {
                        "type": "response_item",
                        "payload": {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "已完成执行。"}]},
                    }
                ),
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr("app.codex_history.DEFAULT_CODEX_HOME", codex_home)
    response = TestClient(create_audit_app(store.path)).get(
        f"/attempts/{attempt_id}/execution/consumer"
    )

    assert response.status_code == 200
    assert "执行记录" in response.text
    assert "已完成执行。" in response.text
    assert "consumer-session-1" not in response.text
    assert str(session_path) not in response.text


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


def _history_attempt_card(html: str, attempt_id: int) -> str:
    marker = f'data-history-detail-href="/attempts/{attempt_id}"'
    marker_index = html.index(marker)
    start = html.rfind("<article", 0, marker_index)
    end = html.index("</article>", marker_index) + len("</article>")
    return html[start:end]


def _seed_confirmed_approval_attempt(
    store: AutoReplyStore,
    *,
    suffix: str = "one",
) -> int:
    process_instance_id = f"proc-history-confirmed-{suffix}"
    operation_id = f"oa-approval-approve-history-{suffix}"
    store.enqueue_reply_task(
        conversation_id=f"cid-history-confirmed-approval-{suffix}",
        conversation_title=f"Confirmed approval {suffix}",
        single_chat=False,
        trigger_message_id=f"msg-history-confirmed-approval-{suffix}",
        trigger_create_time="2026-08-18 09:00:00",
        trigger_sender="Mina",
        trigger_text="Approve the confirmed budget.",
    )
    task = store.claim_reply_tasks(limit=1)[0]
    consumer = store.claim_agent_run(
        task.id,
        task.execution_generation,
        role=AgentRole.CONSUMER,
        proposal_revision=0,
        turn_attempt=0,
        parent_agent_run_id=None,
        operation_id="",
        owner=f"approval-consumer-{suffix}",
    ).run
    store.complete_agent_run(
        consumer.id,
        {
            "outcome": "proposal",
            "summary": "Approve the confirmed budget.",
            "proposal": {
                "objective": "Approve the confirmed budget.",
                "actions": [
                    {
                        "description": "Approve the budget.",
                        "capability": "misleading_capability",
                        "operation": "misleading operation",
                        "target": {"process_instance_id": "misleading-target"},
                        "payload": {
                            "argv": [
                                "dws",
                                "oa",
                                "approval",
                                "approve",
                                "--instance-id",
                                process_instance_id,
                                "--task-id",
                                f"task-history-confirmed-{suffix}",
                                "--yes",
                            ]
                        },
                        "expected_verification": "Read back the approval result.",
                    }
                ],
                "sourced_facts": [],
                "authored_judgment": "The budget meets the approved criteria.",
            },
            "decision_options": [],
            "error": {
                "code": "",
                "retryable": False,
                "authorization_required": False,
            },
        },
        owner=f"approval-consumer-{suffix}",
    )
    audit = store.claim_agent_run(
        task.id,
        task.execution_generation,
        role=AgentRole.AUDIT,
        proposal_revision=0,
        turn_attempt=0,
        parent_agent_run_id=consumer.id,
        operation_id=operation_id,
        owner=f"approval-audit-{suffix}",
    ).run
    audit = store.complete_agent_run(
        audit.id,
        {
            "outcome": "executed",
            "summary": "Approval execution was confirmed.",
            "proposal_revision": 0,
            "side_effect_state": "confirmed",
            "feedback": None,
            "external_result": {
                "operation_id": operation_id,
                "verification_summary": "Approval state read back successfully.",
                "live_result_reference": {
                    "process_instance_id": process_instance_id
                },
            },
            "reconciliation": [],
            "decision_options": [],
            "error": {
                "code": "",
                "retryable": False,
                "authorization_required": False,
            },
        },
        owner=f"approval-audit-{suffix}",
        side_effect_state="confirmed",
    )
    return store.finalize_orchestrated_reply_task(
        task_id=task.id,
        expected_execution_generation=task.execution_generation,
        run_id=audit.id,
        task_status="done",
        task_error="",
        available_at="",
        conversation_id=task.conversation_id,
        conversation_title=task.conversation_title,
        trigger_message_id=task.trigger_message_id,
        trigger_sender=task.trigger_sender,
        trigger_text=task.trigger_text,
        codex_reason="Approval execution was confirmed.",
        codex_session_id="",
        codex_transcript_start_line=0,
        codex_transcript_end_line=0,
        audit_tool_events_json="[]",
        audit_summary="Approval execution was confirmed.",
        send_status="completed",
        send_error="",
        channel="dingtalk",
        oa_process_instance_id=process_instance_id,
        oa_task_id=f"task-history-confirmed-{suffix}",
        oa_action="review",
    )


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
    assert (
        f'<article class="attempt-item history-kind-reply" role="link" tabindex="0" '
        f'data-history-detail-href="/attempts/{attempt_id}">'
    ) in html
    assert '<span class="history-type-badge history-type-reply">Reply</span>' in html
    assert "data-history-clickable-items" in html
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


def test_history_hides_runtime_internals_and_shows_agent_outcome(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    attempt_id = store.record_reply_attempt(
        conversation_id="cid-direct-agent",
        conversation_title="产品群",
        trigger_message_id="msg-direct-agent",
        trigger_sender="Mina",
        trigger_text="请确认发布结果",
        action="send_reply",
        sensitivity_kind="general",
        audit_summary="已在群内回复并确认发送成功。",
    )
    store.update_reply_attempt(
        attempt_id,
        final_reply_text="发布结果已确认。",
        permission_action="allow",
        send_status="sent",
    )

    html = render_attempt_list(store)

    assert "已在群内回复并确认发送成功。" in html
    assert f'data-history-detail-href="/attempts/{attempt_id}"' in html
    assert "Universal" not in html
    assert "planner" not in html.casefold()
    assert "action index" not in html.casefold()


def test_render_attempt_list_marks_oa_history_type(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    attempt_id = store.record_reply_attempt(
        conversation_id="cid-oa",
        conversation_title="OA 审批",
        trigger_message_id="msg-oa",
        trigger_sender="Derek",
        trigger_text="审批这个项目",
        action="oa_approval",
        sensitivity_kind="general",
        codex_reason="审批材料完整",
        draft_reply_text="同意",
        oa_process_instance_id="proc-1",
        oa_task_id="task-1",
    )
    store.update_reply_attempt(
        attempt_id,
        final_reply_text="同意",
        permission_action="allow",
        send_status="sent",
    )

    html = render_attempt_list(store)

    assert (
        f'<article class="attempt-item history-kind-oa" role="link" tabindex="0" '
        f'data-history-detail-href="/attempts/{attempt_id}">'
    ) in html
    assert '<span class="history-type-badge history-type-oa">审批</span>' in html


def test_history_approval_card_uses_confirmed_structured_business_result(
    tmp_path: Path,
):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    attempt_id = _seed_confirmed_approval_attempt(store)

    html = render_attempt_list(
        store,
        include_chart=False,
        search_object_type="approval",
    )
    card = _history_attempt_card(html, attempt_id)

    assert '<span class="history-type-badge history-type-oa">审批</span>' in card
    assert "history-approval-result" in card
    assert "✓ 已同意" in card
    assert "💬 Completed" in card
    assert "🧾 review" not in card


def test_history_batches_structured_approval_run_summaries_once(
    tmp_path: Path,
    monkeypatch,
):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    first_business_id = _seed_confirmed_approval_attempt(store, suffix="first")
    second_business_id = _seed_confirmed_approval_attempt(store, suffix="second")
    first_business = store.get_reply_attempt(first_business_id)
    second_business = store.get_reply_attempt(second_business_id)
    assert first_business is not None and first_business.agent_run_id is not None
    assert second_business is not None and second_business.agent_run_id is not None
    latest_ids = [
        store.record_reply_attempt(
            conversation_id=business.conversation_id,
            conversation_title=business.conversation_title,
            trigger_message_id=f"{business.trigger_message_id}-retry",
            trigger_sender=business.trigger_sender,
            trigger_text=business.trigger_text,
            action="agent_run",
            sensitivity_kind="general",
            oa_process_instance_id=business.oa_process_instance_id,
            oa_task_id=business.oa_task_id,
            oa_action="review",
            send_status="failed",
        )
        for business in (first_business, second_business)
    ]

    bulk_calls: list[list[int]] = []
    history_bulk_calls: list[list[str]] = []
    agent_run_calls: list[int] = []
    bulk_method = getattr(store, "list_agent_run_summaries_for_terminal_runs", None)
    history_bulk_method = store.list_oa_attempt_histories
    original_agent_runs = audit_web_module._agent_runs_for_attempt

    def track_bulk(run_ids: list[int]):
        bulk_calls.append(run_ids)
        return bulk_method(run_ids) if bulk_method is not None else {}

    def track_legacy_agent_runs(*args, **kwargs):
        agent_run_calls.append(args[1].id)
        return original_agent_runs(*args, **kwargs)

    def track_history_bulk(process_ids: list[str]):
        history_bulk_calls.append(process_ids)
        return history_bulk_method(process_ids)

    def reject_single_history(*args, **kwargs):
        raise AssertionError("History cards must not load approval attempts one process at a time")

    monkeypatch.setattr(
        store,
        "list_agent_run_summaries_for_terminal_runs",
        track_bulk,
        raising=False,
    )
    monkeypatch.setattr(
        audit_web_module,
        "_agent_runs_for_attempt",
        track_legacy_agent_runs,
    )
    monkeypatch.setattr(store, "list_oa_attempt_histories", track_history_bulk)
    monkeypatch.setattr(store, "list_oa_attempt_history", reject_single_history)

    html = render_attempt_list(
        store,
        include_chart=False,
        search_object_type="approval",
    )

    for latest_id in latest_ids:
        card = _history_attempt_card(html, latest_id)
        assert "✓ 已同意" in card
        assert "💬 Failed" in card
        assert f'action="/attempts/{latest_id}/rerun?return_to=/history"' in card
    assert f'data-history-detail-href="/attempts/{first_business_id}"' not in html
    assert f'data-history-detail-href="/attempts/{second_business_id}"' not in html
    assert len(history_bulk_calls) == 1
    assert set(history_bulk_calls[0]) == {
        first_business.oa_process_instance_id,
        second_business.oa_process_instance_id,
    }
    assert len(bulk_calls) == 1
    assert set(bulk_calls[0]) == {
        first_business.agent_run_id,
        second_business.agent_run_id,
    }
    assert agent_run_calls == []


def test_history_approval_cards_show_direct_return_and_unknown_results(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    returned_id = store.record_reply_attempt(
        conversation_id="cid-history-direct-return",
        conversation_title="Direct return approval",
        trigger_message_id="msg-history-direct-return",
        trigger_sender="Mina",
        trigger_text="Return this approval.",
        action="oa_approval",
        sensitivity_kind="general",
        oa_process_instance_id="proc-history-direct-return",
        oa_action="退回",
        oa_action_result_json='{"errcode": 0}',
        send_status="commented",
    )
    unknown_id = store.record_reply_attempt(
        conversation_id="cid-history-unknown-approval",
        conversation_title="Unknown approval",
        trigger_message_id="msg-history-unknown-approval",
        trigger_sender="Mina",
        trigger_text="Review this approval.",
        action="oa_approval",
        sensitivity_kind="general",
        oa_process_instance_id="proc-history-unknown",
        oa_action="review",
        send_status="completed",
    )

    html = render_attempt_list(
        store,
        include_chart=False,
        search_object_type="approval",
    )
    returned_card = _history_attempt_card(html, returned_id)
    unknown_card = _history_attempt_card(html, unknown_id)

    assert "✎ 已留言，仍待审批" in returned_card
    assert "结果未知" in unknown_card
    assert "🧾" not in returned_card
    assert "🧾" not in unknown_card
    assert (
        '<span class="pill status-action history-approval-result '
        'action-state-unknown">结果未知</span>'
    ) in unknown_card
    assert "style=" not in unknown_card
    assert (
        ".action-state-unknown{background:var(--surface);color:var(--stone);"
        "border-color:var(--hairline)}"
    ) in html


def test_history_approval_cards_merge_business_evidence_with_latest_system_state(
    tmp_path: Path,
):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    old_comment_id = store.record_reply_attempt(
        conversation_id="cid-history-production-a",
        conversation_title="Production-shaped approval A",
        trigger_message_id="msg-history-production-a-comment",
        trigger_sender="Mina",
        trigger_text="Review approval A.",
        action="oa_approval",
        sensitivity_kind="general",
        oa_process_instance_id="proc-history-production-a",
        oa_action="退回",
        send_status="commented",
    )
    latest_approval_id = store.record_reply_attempt(
        conversation_id="cid-history-production-a",
        conversation_title="Production-shaped approval A",
        trigger_message_id="msg-history-production-a-approved",
        trigger_sender="Mina",
        trigger_text="Review approval A.",
        action="oa_approval",
        sensitivity_kind="general",
        oa_process_instance_id="proc-history-production-a",
        oa_action="通过",
        oa_action_result_json=json.dumps(
            {"success": True, "result": True, "errorCode": None}
        ),
        send_status="skipped",
    )
    old_failed_group_comment_id = store.record_reply_attempt(
        conversation_id="cid-history-production-b",
        conversation_title="Production-shaped approval B",
        trigger_message_id="msg-history-production-b-comment",
        trigger_sender="Mina",
        trigger_text="Review approval B.",
        action="oa_approval",
        sensitivity_kind="general",
        oa_process_instance_id="proc-history-production-b",
        oa_action="comment",
        send_status="commented",
    )
    latest_failure_id = store.record_reply_attempt(
        conversation_id="cid-history-production-b",
        conversation_title="Production-shaped approval B",
        trigger_message_id="msg-history-production-b-failed",
        trigger_sender="Mina",
        trigger_text="Review approval B.",
        action="agent_run",
        sensitivity_kind="general",
        oa_process_instance_id="proc-history-production-b",
        oa_action="review",
        audit_summary="Approval retry failed.",
        send_status="failed",
    )

    html = render_attempt_list(
        store,
        include_chart=False,
        search_object_type="approval",
    )
    approved_card = _history_attempt_card(html, latest_approval_id)
    failed_card = _history_attempt_card(html, latest_failure_id)

    assert "✓ 已同意" in approved_card
    assert "💬 Skipped" in approved_card
    assert "✎ 已留言，仍待审批" in failed_card
    assert "💬 Failed" in failed_card
    assert f'action="/attempts/{latest_failure_id}/rerun?return_to=/history"' in failed_card
    assert f'data-history-detail-href="/attempts/{old_comment_id}"' not in html
    assert f'data-history-detail-href="/attempts/{old_failed_group_comment_id}"' not in html


def test_history_css_has_readable_dark_palette(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    seed_attempt(store)

    html = render_attempt_list(store, include_chart=False)

    assert "@media (prefers-color-scheme:dark)" in html
    assert ":root{color-scheme:dark;--ink:#f5f7fa;" in html
    assert "body{background:var(--canvas);color:var(--ink)}" in html
    assert ".history-approval-result{color:var(--ink)}" in html
    assert ".table-type-select,.table-page-size,select option{" in html


def test_history_neutral_approval_results_use_steel_text_contrast(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    store.enqueue_reply_task(
        conversation_id="cid-history-no-action-approval",
        conversation_title="No action approval",
        single_chat=False,
        trigger_message_id="msg-history-no-action-approval",
        trigger_create_time="2026-08-18 11:00:00",
        trigger_sender="Mina",
        trigger_text="Check this completed approval.",
    )
    [task] = store.claim_reply_tasks(limit=1)
    consumer = store.claim_agent_run(
        task.id,
        task.execution_generation,
        role=AgentRole.CONSUMER,
        proposal_revision=0,
        turn_attempt=0,
        parent_agent_run_id=None,
        operation_id="",
        owner="no-action-consumer",
    ).run
    consumer = store.complete_agent_run(
        consumer.id,
        {
            "outcome": "no_action",
            "summary": "The approval was already complete.",
            "proposal": None,
            "decision_options": [],
            "error": {
                "code": "",
                "retryable": False,
                "authorization_required": False,
            },
        },
        owner="no-action-consumer",
    )
    no_action_id = store.finalize_orchestrated_reply_task(
        task_id=task.id,
        expected_execution_generation=task.execution_generation,
        run_id=consumer.id,
        task_status="done",
        task_error="",
        available_at="",
        conversation_id=task.conversation_id,
        conversation_title=task.conversation_title,
        trigger_message_id=task.trigger_message_id,
        trigger_sender=task.trigger_sender,
        trigger_text=task.trigger_text,
        codex_reason="The approval was already complete.",
        codex_session_id="",
        codex_transcript_start_line=0,
        codex_transcript_end_line=0,
        audit_tool_events_json="[]",
        audit_summary="The approval was already complete.",
        send_status="skipped",
        send_error="",
        channel="dingtalk",
        oa_process_instance_id="proc-history-no-action",
        oa_task_id="task-history-no-action",
        oa_action="review",
    )
    unknown_id = store.record_reply_attempt(
        conversation_id="cid-history-unknown-contrast",
        conversation_title="Unknown approval",
        trigger_message_id="msg-history-unknown-contrast",
        trigger_sender="Mina",
        trigger_text="Review this approval.",
        action="oa_approval",
        sensitivity_kind="general",
        oa_process_instance_id="proc-history-unknown-contrast",
        oa_action="review",
        send_status="completed",
    )

    html = render_attempt_list(
        store,
        include_chart=False,
        search_object_type="approval",
    )
    no_action_card = _history_attempt_card(html, no_action_id)
    unknown_card = _history_attempt_card(html, unknown_id)

    assert 'history-approval-result action-state-skipped">无需处理' in no_action_card
    assert 'history-approval-result action-state-unknown">结果未知' in unknown_card
    assert (
        ".history-approval-result.action-state-skipped,"
        ".history-approval-result.action-state-unknown{background:var(--surface);"
        "color:var(--steel);border-color:var(--hairline)}"
    ) in html


def test_history_approval_workflow_results_keep_failure_attention_actions(
    tmp_path: Path,
):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    needs_human_id = store.record_reply_attempt(
        conversation_id="cid-history-needs-human",
        conversation_title="Needs human approval",
        trigger_message_id="msg-history-needs-human",
        trigger_sender="Mina",
        trigger_text="Choose the approval outcome.",
        action="agent_run",
        sensitivity_kind="general",
        oa_process_instance_id="proc-history-needs-human",
        oa_action="review",
        send_status="needs_human",
    )
    failed_id = store.record_reply_attempt(
        conversation_id="cid-history-failed-approval",
        conversation_title="Failed approval",
        trigger_message_id="msg-history-failed-approval",
        trigger_sender="Mina",
        trigger_text="Process the failed approval.",
        action="agent_run",
        sensitivity_kind="general",
        oa_process_instance_id="proc-history-failed",
        oa_action="review",
        audit_summary="Approval processing did not complete",
        send_status="failed",
    )

    html = render_attempt_list(
        store,
        include_chart=False,
        search_object_type="approval",
    )
    needs_human_card = _history_attempt_card(html, needs_human_id)
    failed_card = _history_attempt_card(html, failed_id)

    assert "待你处理" in needs_human_card
    assert "处理失败" in failed_card
    assert "原因：</strong>Approval processing did not complete" in failed_card
    assert f'action="/attempts/{failed_id}/rerun?return_to=/history"' in failed_card
    assert ">重试当前任务</button>" in failed_card


def test_history_recovered_approval_keeps_business_and_recovery_pills(
    tmp_path: Path,
):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    store.enqueue_reply_task(
        conversation_id="cid-history-recovered-approval",
        conversation_title="Recovered approval",
        single_chat=False,
        trigger_message_id="msg-history-recovered-approval",
        trigger_create_time="2026-08-18 10:00:00",
        trigger_sender="Mina",
        trigger_text="Recover this approval.",
    )
    [task] = store.claim_reply_tasks(limit=1)
    attempt_id = store.record_reply_attempt(
        conversation_id=task.conversation_id,
        conversation_title=task.conversation_title,
        trigger_message_id=task.trigger_message_id,
        trigger_sender=task.trigger_sender,
        trigger_text=task.trigger_text,
        action="agent_run",
        sensitivity_kind="general",
        oa_process_instance_id="proc-history-recovered-approval",
        oa_action="review",
        send_status="failed",
    )
    store.complete_reply_task(
        task.id,
        expected_execution_generation=task.execution_generation,
    )

    card = _history_attempt_card(
        render_attempt_list(
            store,
            include_chart=False,
            search_object_type="approval",
        ),
        attempt_id,
    )

    assert "处理失败" in card
    assert "↻ Recovered" in card
    assert "🧾 review" not in card


def test_history_superseded_approval_keeps_system_pill_without_raw_actions(
    tmp_path: Path,
):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    failed_id = store.record_reply_attempt(
        conversation_id="cid-history-superseded-approval",
        conversation_title="Superseded approval",
        trigger_message_id="msg-history-superseded-approval",
        trigger_sender="Mina",
        trigger_text="Process this approval.",
        action="agent_run",
        sensitivity_kind="general",
        oa_process_instance_id="proc-history-superseded-approval-old",
        oa_action="review",
        send_status="failed",
    )
    later_id = store.record_reply_attempt(
        conversation_id="cid-history-superseded-approval",
        conversation_title="Superseded approval",
        trigger_message_id="msg-history-superseded-approval",
        trigger_sender="Mina",
        trigger_text="Process this approval.",
        action="agent_run",
        sensitivity_kind="general",
        oa_process_instance_id="proc-history-superseded-approval-new",
        oa_action="review",
        send_status="completed",
    )

    failed_card = _history_attempt_card(
        render_attempt_list(
            store,
            include_chart=False,
            search_object_type="approval",
        ),
        failed_id,
    )

    assert failed_card.count("history-approval-result") == 1
    assert f'href="/attempts/{later_id}">🔁 已由 #{later_id} 后续处理</a>' in failed_card
    assert '<section class="history-attention">' in failed_card
    assert "💬 Completed" not in failed_card
    assert "💬 Skipped" not in failed_card
    assert "🧾 review" not in failed_card


def test_history_non_approval_sent_reply_keeps_reply_badge_and_sent_pill(
    tmp_path: Path,
):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    attempt_id = seed_attempt(store)

    card = _history_attempt_card(
        render_attempt_list(store, include_chart=False),
        attempt_id,
    )

    assert '<span class="history-type-badge history-type-reply">Reply</span>' in card
    assert "💬 Sent" in card


def test_attempt_detail_links_oa_metadata_to_process_history(tmp_path: Path):
    db_path = tmp_path / "worker.sqlite3"
    store = AutoReplyStore(db_path)
    attempt_id = store.record_reply_attempt(
        conversation_id="cid-oa",
        conversation_title="OA 审批",
        trigger_message_id="msg-oa-1",
        trigger_sender="Derek",
        trigger_text="审批",
        action="oa_approval",
        sensitivity_kind="internal_personnel",
        codex_reason="退回",
        draft_reply_text="请补材料",
        audit_summary="材料不足。",
        oa_process_instance_id="proc/oa 1",
        oa_task_id="task-1",
        oa_url="https://aflow.dingtalk.com/detail?procInstId=proc-oa-1&taskId=task-1",
        oa_action="退回",
        oa_remark="请补材料",
        send_status="commented",
    )

    status, html = render_attempt_detail(store, attempt_id)

    assert status == 200
    assert "/oa-approvals/proc%2Foa%201" in html
    assert "/oa-approvals/proc/oa 1" not in html
    assert "查看同一审批历史" in html

    client = TestClient(create_audit_app(db_path))
    response = client.get("/oa-approvals/proc%2Foa%201")
    assert response.status_code == 200
    assert "proc/oa 1" in response.text


def test_oa_approval_detail_route_shows_summary_and_history(tmp_path: Path):
    db_path = tmp_path / "worker.sqlite3"
    store = AutoReplyStore(db_path)
    older_id = store.record_reply_attempt(
        conversation_id="cid-oa",
        conversation_title="OA 审批",
        trigger_message_id="msg-oa-1",
        trigger_sender="Derek",
        trigger_text="审批",
        action="oa_approval",
        sensitivity_kind="internal_personnel",
        codex_reason="退回",
        draft_reply_text="请补材料",
        oa_process_instance_id="proc-oa-1",
        oa_task_id="task-1",
        oa_action="退回",
        oa_remark="请补材料",
        send_status="commented",
    )
    newer_id = store.record_reply_attempt(
        conversation_id="cid-oa",
        conversation_title="OA 审批",
        trigger_message_id="msg-oa-2",
        trigger_sender="Derek",
        trigger_text="继续审批",
        action="oa_approval",
        sensitivity_kind="internal_personnel",
        codex_reason="同意",
        draft_reply_text="同意",
        oa_process_instance_id="proc-oa-1",
        oa_task_id="task-2",
        oa_url="https://aflow.dingtalk.com/detail?procInstId=proc-oa-1&taskId=task-2",
        oa_action="同意",
        oa_remark="同意，材料已补齐。",
        send_status="skipped",
    )

    status, html = render_oa_approval_detail(store, "proc-oa-1")

    assert status == 200
    assert "process" in html
    assert "proc-oa-1" in html
    assert "reason" in html
    assert "comment" in html
    assert f"/attempts/{newer_id}" in html
    assert f"/attempts/{older_id}" in html
    assert html.index(f"/attempts/{newer_id}") < html.index(f"/attempts/{older_id}")

    client = TestClient(create_audit_app(db_path))
    response = client.get("/oa-approvals/proc-oa-1")
    assert response.status_code == 200
    assert "Attempt history" in response.text


def test_service_bugfix_candidates_page_lists_pending_feedback(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    store.create_service_bugfix_candidate(
        feedback_event_key="event-1",
        feedback_token="token-1",
        attempt_id=42,
        title="自动回复服务报错",
        reason="用户反馈明确指向 CEO 服务自身的 bug、失败或回归。",
        feedback_comment="分身自动回复服务报错，需要修复。",
        conversation_title="技术部",
        trigger_text="上一条回复失败了",
    )

    html = render_service_bugfix_candidates(store)

    assert "待处理服务修复" in html
    assert "自动回复服务报错" in html
    assert "/attempts/42" in html
    assert "技术部" in html


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
    assert "💬 Skipped" in html
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

    html = render_attempt_list(store, search_object_type="wechat")

    assert html.count(f"/wechat/deliveries/{delivery_id}/approve?next=/") == 1
    assert html.count(f"/wechat/deliveries/{delivery_id}/reject?next=/") == 1


def test_history_wechat_actions_use_batched_delivery_lookup(tmp_path: Path, monkeypatch):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    delivery_id = _seed_wechat_pending(store)

    def unexpected_per_row_lookup(*_args, **_kwargs):
        raise AssertionError("history rendering must not query each WeChat attempt")

    monkeypatch.setattr(store, "get_reply_task_for_message", unexpected_per_row_lookup)
    monkeypatch.setattr(store, "get_wechat_delivery_for_task", unexpected_per_row_lookup)

    html = render_attempt_list(store, search_object_type="wechat")

    assert f"/wechat/deliveries/{delivery_id}/approve?next=/" in html


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

    assert "task-history-item" not in html
    assert (
        'class="attempt-item history-kind-task" role="link" tabindex="0" '
        f'data-history-detail-href="/tasks/{project_id}#follow-up-{follow_up_id}"'
        in html
    )
    assert '<span class="history-type-badge history-type-task">Task</span>' in html
    assert (
        f'data-history-detail-href="/tasks/{project_id}#follow-up-{follow_up_id}"'
        in html
    )
    assert "onclick=\"if (!event.target.closest('a'))" not in html
    assert f"/tasks/{project_id}#follow-up-{follow_up_id}" in html
    assert "查看 task" in html
    assert f'id="todo-{todo_id}"' in detail
    assert f'id="follow-up-{follow_up_id}"' in detail


def test_render_attempt_list_shows_draft_follow_up_as_scheduled(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    project_id = store.create_work_project(
        title="宝马项目周末攻坚与客户Demo推进",
        category="sales",
        priority="P0",
        risk_level="high",
        owner_name="Claire Huang",
    )
    follow_up_id = store.create_follow_up_draft(
        project_id=project_id,
        owner_name="Claire Huang",
        target_kind="direct",
        question_text="准备宝马专家邀请材料了吗？",
        scheduled_at="2099-07-23 01:00:00",
        status="draft",
    )

    html = render_attempt_list(store, search_object_type="task")

    assert f"#follow-up-{follow_up_id}" in html
    assert (
        f'data-history-detail-href="/tasks/{project_id}#follow-up-{follow_up_id}"'
        in html
    )
    assert '<span class="history-type-badge history-type-task">Task</span>' in html
    assert (
        '<span class="pill status-action action-state-pending">'
        "Scheduled on Jul 23, 9:00 AM</span>"
    ) in html
    assert ">Pending</span>" not in html
    assert ">Scheduled on Jul 23, 9:00 AM</span>" in html
    assert ">Processing</span>" not in html


def test_meeting_history_uses_reply_card_and_detail_contract(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    run_id = seed_meeting_attempt(store)

    html = render_attempt_list(store)

    assert f'/meeting-attempts/{run_id}' in html
    assert (
        f'<article class="attempt-item history-kind-meeting" role="link" tabindex="0" '
        f'data-history-detail-href="/meeting-attempts/{run_id}">'
    ) in html
    assert '<span class="history-type-badge history-type-meeting">Meeting</span>' in html
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


def test_meeting_attempt_detail_keeps_ready_run_sent_after_later_run(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    job_id = store.upsert_meeting_alignment_job(
        meeting_id="minutes-later-ready",
        title="招聘站会",
        source_json="{}",
        participants_json="[]",
        ended_at="2026-07-22T10:12:44+00:00",
        eligible_at="2026-07-22T10:22:44+00:00",
        status="pending",
    )
    first_run = store.record_meeting_alignment_run(
        job_id=job_id,
        codex_session_id="meeting-session-first",
        decision_json='{"action":"send","target":{"title":"HR"}}',
        audit_summary="首次生成完成",
        status="ready_to_send",
        error="",
    )
    store.record_meeting_alignment_run(
        job_id=job_id,
        codex_session_id="meeting-session-second",
        decision_json='{"action":"send","target":{"title":"HR"}}',
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

    client = TestClient(create_audit_app(store.path))
    response = client.get(f"/meeting-attempts/{first_run}")

    assert response.status_code == 200
    assert (
        '<div class="attempt-detail-label">status</div>'
        '<div class="attempt-detail-value">sent</div>'
    ) in response.text
    assert '<div class="attempt-detail-value">failed</div>' not in response.text


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


def test_history_object_dropdown_controls_results(tmp_path: Path):
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
    store.record_reply_attempt(
        conversation_id="cid-agent-approval-history",
        conversation_title="Agent Approval Search Group",
        trigger_message_id="msg-agent-approval-history",
        trigger_sender="Derek OA",
        trigger_text="风险预算审批扫描已完成审阅",
        action="agent_run",
        sensitivity_kind="general",
        oa_process_instance_id="proc-agent-history-filter",
        oa_task_id="task-agent-history-filter",
        oa_action="review",
        send_status="needs_human",
    )
    store.record_reply_attempt(
        conversation_id="cid-approval-history",
        conversation_title="Approval Search Group",
        trigger_message_id="msg-approval-history",
        trigger_sender="Mina",
        trigger_text="风险预算审批需要确认",
        action="oa_approval",
        sensitivity_kind="general",
        oa_process_instance_id="proc-history-filter",
        oa_task_id="task-history-filter",
        oa_action="同意",
        oa_remark="风险预算符合审批要求",
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
    assert '<select name="object_type" class="table-type-select history-object-type-select" aria-label="History object filter" onchange="this.form.submit()">' in default_html
    assert '<option value="" selected>对象：全部</option>' in default_html
    assert '<option value="replay">replay</option>' in default_html
    assert '<option value="wechat">wechat</option>' in default_html
    assert '<option value="approval">审批</option>' in default_html
    assert '<option value="task">task</option>' in default_html
    assert '<option value="meeting">meeting</option>' in default_html
    assert 'type="checkbox" name="object_type"' not in default_html
    assert 'history-object-type-filter' not in default_html
    assert "History Search Group" in default_html
    assert "Approval Search Group" in default_html
    assert "Agent Approval Search Group" in default_html
    assert "Task Search Group" in default_html
    assert "相似 Codex sessions" in default_html

    replay_only_html = render_attempt_list(
        store,
        query="风险预算",
        search_object_type="replay",
        query_embedding=[1.0, 0.0],
    )
    assert "History Search Group" in replay_only_html
    assert "Approval Search Group" not in replay_only_html
    assert "Task Search Group" not in replay_only_html
    assert "相似 Codex sessions" not in replay_only_html
    assert '<option value="replay" selected>replay</option>' in replay_only_html

    approval_only_html = render_attempt_list(
        store,
        query="风险预算",
        search_object_type="approval",
        query_embedding=[1.0, 0.0],
    )
    assert "Approval Search Group" in approval_only_html
    assert "Agent Approval Search Group" in approval_only_html
    assert "History Search Group" not in approval_only_html
    assert "Task Search Group" not in approval_only_html
    assert "相似 Codex sessions" not in approval_only_html
    assert '<option value="approval" selected>审批</option>' in approval_only_html

    meeting_only_html = render_attempt_list(
        store,
        query="风险预算",
        search_object_type="meeting",
        query_embedding=[1.0, 0.0],
    )
    assert "History Search Group" not in meeting_only_html
    assert "Task Search Group" not in meeting_only_html
    assert "相似 Codex sessions" in meeting_only_html
    assert f"/meeting-attempts/{run_id}" in meeting_only_html
    assert '<option value="meeting" selected>meeting</option>' in meeting_only_html

    task_only_html = render_attempt_list(
        store,
        query="风险预算",
        search_object_type="task",
        query_embedding=[1.0, 0.0],
    )
    assert "History Search Group" not in task_only_html
    assert "Task Search Group" in task_only_html
    assert "相似 Codex sessions" not in task_only_html
    assert '<option value="task" selected>task</option>' in task_only_html

    object_type_html = render_attempt_list(
        store,
        limit=1,
        query="风险预算",
        search_object_type="meeting",
        query_embedding=[1.0, 0.0],
    )
    assert '<option value="meeting" selected>meeting</option>' in object_type_html


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
    assert '<option value="" selected>对象：全部</option>' in default_html

    wechat_html = render_attempt_list(
        store,
        query="channel filter",
        search_object_type="wechat",
    )
    assert "WeChat History Group" in wechat_html
    assert "DingTalk History Group" not in wechat_html
    assert '<option value="wechat" selected>wechat</option>' in wechat_html

    replay_html = render_attempt_list(
        store,
        query="channel filter",
        search_object_type="replay",
    )
    assert "DingTalk History Group" in replay_html
    assert "WeChat History Group" not in replay_html


def test_history_object_filter_empty_or_invalid_value_defaults_to_all(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    store.record_reply_attempt(
        conversation_id="cid-history-invalid-filter",
        conversation_title="Invalid Filter History Group",
        trigger_message_id="msg-history-invalid-filter",
        trigger_sender="Mina",
        trigger_text="invalid object filter",
        action="send_reply",
        sensitivity_kind="general",
        send_status="sent",
    )
    store.record_reply_attempt(
        conversation_id="cid-history-invalid-filter-wechat",
        conversation_title="Invalid Filter WeChat Group",
        trigger_message_id="msg-history-invalid-filter-wechat",
        trigger_sender="Alex",
        trigger_text="invalid object filter",
        action="send_reply",
        sensitivity_kind="general",
        send_status="sent",
        channel="wechat",
    )

    empty_html = render_attempt_list(store, query="invalid object filter")
    invalid_html = render_attempt_list(
        store,
        query="invalid object filter",
        search_object_type=" not-a-history-object ",
    )

    assert audit_web_module._history_search_object_type(" APPROVAL ") == "approval"
    assert audit_web_module._history_search_object_type("not-a-history-object") == ""
    for html in (empty_html, invalid_html):
        assert '<option value="" selected>对象：全部</option>' in html
        assert "Invalid Filter History Group" in html
        assert "Invalid Filter WeChat Group" in html


def test_history_pagination_preserves_single_object_filter_query_params(
    tmp_path: Path,
):
    db_path = tmp_path / "worker.sqlite3"
    store = AutoReplyStore(db_path)
    complete_setup_wizard(store)
    for index in range(101):
        store.record_reply_attempt(
            conversation_id=f"cid-approval-page-{index}",
            conversation_title=f"Approval Page Group {index}",
            trigger_message_id=f"msg-approval-page-{index}",
            trigger_sender="Mina",
            trigger_text="风险预算 A/B",
            action="oa_approval",
            sensitivity_kind="general",
            oa_process_instance_id=f"proc-approval-page-{index}",
            oa_task_id=f"task-approval-page-{index}",
            oa_action="同意",
            send_status="sent",
        )

    response = TestClient(create_audit_app(db_path)).get(
        "/history?q=%E9%A3%8E%E9%99%A9%E9%A2%84%E7%AE%97+A%2FB&type=sent&object_type=approval&limit=50&page=2"
    )

    assert response.status_code == 200
    toolbar_html = response.text.split(
        '<form class="table-toolbar" data-table-toolbar="history"', 1
    )[1].split("</form>", 1)[0]
    assert '<option value="approval" selected>审批</option>' in toolbar_html
    assert (
        'href="/history?limit=50&amp;q=%E9%A3%8E%E9%99%A9%E9%A2%84%E7%AE%97+A%2FB&amp;type=sent&amp;object_type=approval"'
        in toolbar_html
    )
    assert (
        'href="/history?page=3&amp;limit=50&amp;q=%E9%A3%8E%E9%99%A9%E9%A2%84%E7%AE%97+A%2FB&amp;type=sent&amp;object_type=approval"'
        in toolbar_html
    )


def test_history_default_pagination_url_omits_object_type(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    for index in range(2):
        store.record_reply_attempt(
            conversation_id=f"cid-default-page-{index}",
            conversation_title=f"Default Page Group {index}",
            trigger_message_id=f"msg-default-page-{index}",
            trigger_sender="Mina",
            trigger_text="default page query",
            action="send_reply",
            sensitivity_kind="general",
            send_status="sent",
        )

    html = render_attempt_list(
        store,
        limit=1,
        query="default page query",
        type_filter="sent",
    )

    toolbar_html = html.split(
        '<form class="table-toolbar" data-table-toolbar="history"', 1
    )[1].split("</form>", 1)[0]
    assert 'href="/history?page=2&amp;limit=1&amp;q=default+page+query&amp;type=sent"' in toolbar_html
    assert "object_type=" not in toolbar_html


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


def test_history_chart_shows_provider_capacity_wait_without_failed_red_series(
    tmp_path: Path,
):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    store.enqueue_reply_task(
        conversation_id="cid-provider-wait",
        conversation_title="Provider recovery",
        single_chat=False,
        trigger_message_id="msg-provider-wait",
        trigger_create_time="2026-08-08 01:00:00",
        trigger_sender="System",
        trigger_text="Handle this after capacity returns.",
    )
    [task] = store.claim_reply_tasks(limit=1)
    store.defer_reply_task(
        task.id,
        "codex_provider_capacity_exhausted",
        expected_execution_generation=task.execution_generation,
        available_at="2026-08-08 02:00:00",
    )
    store.record_reply_attempt(
        conversation_id=task.conversation_id,
        conversation_title=task.conversation_title,
        trigger_message_id=task.trigger_message_id,
        trigger_sender=task.trigger_sender,
        trigger_text=task.trigger_text,
        action="agent_run",
        sensitivity_kind="general",
        codex_reason="Codex provider capacity is temporarily unavailable.",
        send_status="failed",
    )

    payload = audit_web_module._history_chart_payload(store)
    series_names = {series["name"] for series in payload["series"]}

    assert "⏳ Provider recovery" in series_names
    assert "💬 Failed" not in series_names


def test_history_chart_marks_failed_reply_recovered_after_task_completion(
    tmp_path: Path,
):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    store.enqueue_reply_task(
        conversation_id="cid-recovered-reply",
        conversation_title="Recovery",
        single_chat=False,
        trigger_message_id="msg-recovered-reply",
        trigger_create_time="2026-08-08 01:00:00",
        trigger_sender="System",
        trigger_text="Recover this reply.",
    )
    [task] = store.claim_reply_tasks(limit=1)
    store.record_reply_attempt(
        conversation_id=task.conversation_id,
        conversation_title=task.conversation_title,
        trigger_message_id=task.trigger_message_id,
        trigger_sender=task.trigger_sender,
        trigger_text=task.trigger_text,
        action="agent_run",
        sensitivity_kind="general",
        send_status="failed",
    )
    store.complete_reply_task(
        task.id,
        expected_execution_generation=task.execution_generation,
    )

    payload = audit_web_module._history_chart_payload(store)
    series_names = {series["name"] for series in payload["series"]}

    assert "↻ Recovered" in series_names
    assert "💬 Failed" not in series_names


def test_history_chart_marks_failed_attempt_recovered_by_later_attempt(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    failed_id = store.record_reply_attempt(
        conversation_id="cid-recovered-later-attempt",
        conversation_title="Recovery",
        trigger_message_id="msg-recovered-later-attempt",
        trigger_sender="System",
        trigger_text="Recover after a replacement attempt.",
        action="agent_run",
        sensitivity_kind="general",
        send_status="failed",
    )
    store.record_reply_attempt(
        conversation_id="cid-recovered-later-attempt",
        conversation_title="Recovery",
        trigger_message_id="msg-recovered-later-attempt",
        trigger_sender="System",
        trigger_text="Recover after a replacement attempt.",
        action="send_reply",
        sensitivity_kind="general",
        send_status="sent",
    )

    payload = audit_web_module._history_chart_payload(store)
    series_names = {series["name"] for series in payload["series"]}

    assert failed_id
    assert "↻ Recovered" in series_names
    assert "💬 Failed" not in series_names


def test_history_chart_marks_failed_reply_processing_while_task_is_active(
    tmp_path: Path,
):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    store.enqueue_reply_task(
        conversation_id="cid-active-reply",
        conversation_title="Active recovery",
        single_chat=False,
        trigger_message_id="msg-active-reply",
        trigger_create_time="2026-08-08 01:00:00",
        trigger_sender="System",
        trigger_text="Process this reply.",
    )
    [task] = store.claim_reply_tasks(limit=1)
    store.record_reply_attempt(
        conversation_id=task.conversation_id,
        conversation_title=task.conversation_title,
        trigger_message_id=task.trigger_message_id,
        trigger_sender=task.trigger_sender,
        trigger_text=task.trigger_text,
        action="agent_run",
        sensitivity_kind="general",
        send_status="failed",
    )

    payload = audit_web_module._history_chart_payload(store)
    series_names = {series["name"] for series in payload["series"]}

    assert "💬 Processing" in series_names
    assert "💬 Failed" not in series_names


def test_history_chart_marks_recovered_meeting_retries_without_failed_red_series(
    tmp_path: Path,
):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    job_id = store.upsert_meeting_alignment_job(
        meeting_id="meeting-chart-recovered",
        title="Weekly sync",
        source_json="{}",
        participants_json="[]",
        ended_at="2026-08-08T01:00:00+00:00",
        eligible_at="2026-08-08T01:10:00+00:00",
        status="pending",
    )
    store.record_meeting_alignment_run(
        job_id=job_id,
        codex_session_id="meeting-chart-retry",
        decision_json="{}",
        audit_summary="Temporary provider failure.",
        status="retry",
        error="codex_provider_unavailable",
    )
    store.record_meeting_alignment_run(
        job_id=job_id,
        codex_session_id="meeting-chart-sent",
        decision_json='{"action":"send"}',
        audit_summary="Recovery completed.",
        status="ready_to_send",
        error="",
    )
    store.update_meeting_alignment_job(job_id, status="sent")

    payload = audit_web_module._history_chart_payload(store)
    series_names = {series["name"] for series in payload["series"]}

    assert "↻ Recovered" in series_names
    assert "💬 Failed" not in series_names


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
    assert 'href="/history?page=2&amp;limit=1&amp;q=question&amp;type=sent"' in first_page
    assert 'aria-label="上一页"' in first_page
    assert 'aria-label="下一页"' in first_page
    assert 'class="table-page-link active" aria-current="page">1</span>' in first_page
    assert 'class="table-page-arrow disabled" aria-label="上一页"' in first_page
    assert f"/attempts/{older_id}" in second_page
    assert f"/attempts/{newer_id}" not in second_page
    assert 'href="/history?limit=1&amp;q=question&amp;type=sent"' in second_page
    assert 'class="table-page-link active" aria-current="page">2</span>' in second_page
    assert 'class="table-page-arrow disabled" aria-label="下一页"' in second_page


def test_history_type_filter_accepts_blocked_attempts(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    blocked_id = store.record_reply_attempt(
        conversation_id="cid-blocked-history",
        conversation_title="Blocked History Group",
        trigger_message_id="msg-blocked-history",
        trigger_sender="Blake",
        trigger_text="blocked question",
        action="send_reply",
        sensitivity_kind="general",
        send_status="blocked",
    )
    store.update_reply_attempt(blocked_id, send_error="missing required material")
    sent_id = store.record_reply_attempt(
        conversation_id="cid-sent-history",
        conversation_title="Sent History Group",
        trigger_message_id="msg-sent-history",
        trigger_sender="Sana",
        trigger_text="sent question",
        action="send_reply",
        sensitivity_kind="general",
        send_status="sent",
    )

    html = render_attempt_list(store, type_filter=("blocked",))

    assert f"/attempts/{blocked_id}" in html
    assert f"/attempts/{sent_id}" not in html
    assert "Blocked History Group" in html
    assert "Sent History Group" not in html
    assert '<option value="blocked" selected>blocked</option>' in html
    assert '<span class="table-toolbar-total">共 1 条</span>' in html


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
    client = loopback_test_client(app)

    response = client.get("/history?page=2&limit=50")

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
    client = loopback_test_client(app)

    response = client.get("/history?type=sent&type=reacted&limit=1")

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
    assert 'href="/history?page=2&amp;limit=1&amp;type=sent&amp;type=reacted"' in html
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
    client = loopback_test_client(app)

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
    client = loopback_test_client(app)
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


def test_top_nav_highlights_current_page_and_disables_current_link(
    tmp_path: Path,
    monkeypatch,
):
    monkeypatch.setattr(
        audit_web_module,
        "_launchd_service_status",
        lambda label: {
            "label": label,
            "ok": True,
            "state": "running",
            "detail": "running",
            "pid": "123",
            "runs": "1",
        },
    )
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    seed_attempt(store)

    history_html = render_attempt_list(store)
    tutorial_html = render_tutorial_page(store=store)
    user_feedback_html = render_user_feedback_list(store)
    config_html = render_config_page(db_path=store.path)
    errors_html = render_error_list(store)
    tasks_html = render_tasks_page(store)
    workers_html = render_workers_page(store)

    assert history_html.index('<a class="nav-item" href="/">Agent</a>') < history_html.index(
        '<span class="nav-item active" aria-current="page">History</span>'
    )
    assert '<span class="nav-item active" aria-current="page">History</span>' in history_html
    assert '<a class="nav-item" href="/">History</a>' not in history_html
    assert '<a class="nav-item" href="/user-feedback">用户反馈</a>' in history_html
    assert '<a class="nav-item" href="/settings">Settings</a>' in history_html
    assert "Tutorial" not in history_html
    assert 'href="/workers"' not in history_html
    assert 'href="/config"' not in history_html
    assert 'href="/logs"' not in history_html

    assert 'href="/tutorial"' not in tutorial_html

    assert '<span class="nav-item active" aria-current="page">用户反馈</span>' in user_feedback_html
    assert '<a class="nav-item" href="/user-feedback">用户反馈</a>' not in user_feedback_html

    assert '<span class="nav-item active" aria-current="page">Settings</span>' in config_html
    assert '<a class="nav-item" href="/settings">Settings</a>' not in config_html

    assert 'href="/codex"' not in history_html
    assert "Codex Sessions" not in history_html

    assert '<span class="nav-item active" aria-current="page">Settings</span>' in errors_html

    assert '<span class="nav-item active" aria-current="page">Tasks</span>' in tasks_html
    assert '<a class="nav-item" href="/tasks">Tasks</a>' not in tasks_html

    assert '<span class="nav-item active" aria-current="page">Settings</span>' in workers_html


def test_legacy_top_nav_uses_a_centered_three_column_header():
    css = audit_web_module.CSS

    assert (
        ".topbar{display:grid;grid-template-columns:minmax(0,1fr) auto "
        "minmax(0,1fr);align-items:center;gap:24px;min-height:72px}"
    ) in css
    assert ".topbar>.nav{grid-column:2;justify-self:center}" in css
    assert ".nav{display:flex;align-items:center;justify-content:center;" in css
    assert (
        "@media (max-width:960px){.topbar{grid-template-columns:minmax(0,1fr);"
        "align-items:start;gap:14px;padding:14px 0}.topbar>.nav{grid-column:1;"
        "justify-self:center}"
    ) in css


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
    assert "/settings?tab=config&amp;config_tab=system" in html
    assert "/settings?tab=logs" in html
    assert "/tasks" in html
    assert 'href="/history"' in html
    assert '<a class="nav-item" href="/">Agent</a>' in html
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

    response = client.get("/")

    assert response.status_code == 200
    assert "Initialization Wizard" in response.text
    assert 'href="/tutorial"' not in response.text


def test_tutorial_is_hidden_after_setup_completes(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    complete_setup_wizard(store)
    client = TestClient(create_audit_app(store.path))

    response = client.get("/tutorial", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/history"


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
    calls = 0
    rendered = threading.Event()

    def locked_attempt_list(*args, **kwargs):
        nonlocal calls
        del args, kwargs
        calls += 1
        rendered.set()
        raise sqlite3.OperationalError("database is locked")

    db_path = tmp_path / "worker.sqlite3"
    complete_setup_wizard(AutoReplyStore(db_path))
    monkeypatch.setattr(audit_web_module, "render_attempt_list", locked_attempt_list)
    with TestClient(create_audit_app(db_path)) as client:
        assert rendered.wait(timeout=1)
        response = client.get("/history")

    assert response.status_code == 200
    assert "History is temporarily busy" in response.text
    assert "refresh" in response.text
    assert calls == 1


def test_history_route_renders_chart_on_default_page(tmp_path: Path):
    db_path = tmp_path / "worker.sqlite3"
    complete_setup_wizard(AutoReplyStore(db_path))
    client = TestClient(create_audit_app(db_path))

    response = client.get("/history")

    assert response.status_code == 200
    assert "CEO Agent Audit" in response.text
    assert "最近 24 小时事件" in response.text


def test_history_route_reuses_recent_default_render(monkeypatch, tmp_path: Path):
    calls = 0

    def render_once(*args, **kwargs):
        nonlocal calls
        del args, kwargs
        calls += 1
        return f"render-{calls}"

    db_path = tmp_path / "worker.sqlite3"
    complete_setup_wizard(AutoReplyStore(db_path))
    monkeypatch.setattr(audit_web_module, "render_attempt_list", render_once)
    client = TestClient(create_audit_app(db_path))

    first = client.get("/history")
    second = client.get("/history")
    filtered = client.get("/history?object_type=meeting")

    assert first.text == "render-1"
    assert second.text == "render-1"
    assert filtered.text == "render-2"
    assert calls == 2


def test_audit_app_prewarms_default_history(monkeypatch, tmp_path: Path):
    calls = 0
    rendered = threading.Event()

    def render_once(*args, **kwargs):
        nonlocal calls
        del args, kwargs
        calls += 1
        rendered.set()
        return f"render-{calls}"

    monkeypatch.setattr(audit_web_module, "render_attempt_list", render_once)

    with TestClient(create_audit_app(tmp_path / "worker.sqlite3")):
        assert rendered.wait(timeout=1)
        assert calls == 1


def test_audit_app_serves_busy_page_before_slow_history_prewarm(monkeypatch, tmp_path: Path):
    release_render = threading.Event()
    render_started = threading.Event()

    def render_slowly(*args, **kwargs):
        del args, kwargs
        render_started.set()
        release_render.wait(timeout=2.0)
        return "ready"

    monkeypatch.setattr(audit_web_module, "render_attempt_list", render_slowly)
    try:
        db_path = tmp_path / "worker.sqlite3"
        complete_setup_wizard(AutoReplyStore(db_path))
        started_at = time.monotonic()
        with TestClient(create_audit_app(db_path)) as client:
            # TestClient startup has fixed framework overhead; this only proves
            # that startup does not wait for the deliberately blocked render.
            assert time.monotonic() - started_at < 1.0
            assert render_started.wait(timeout=1)
            response = client.get("/history")
            assert "History is temporarily busy" in response.text
    finally:
        release_render.set()


def test_recent_html_cache_refreshes_after_ttl():
    now = [0.0]

    class ImmediateThread:
        def __init__(self, *, target, args, daemon):
            self.target = target
            self.args = args
            self.daemon = daemon

        def start(self):
            self.target(*self.args)

    cache = audit_web_module._RecentHtmlCache(
        2.0,
        clock=lambda: now[0],
        thread_factory=ImmediateThread,
    )
    calls = 0

    def render_once():
        nonlocal calls
        calls += 1
        return f"render-{calls}"

    assert cache.get_or_render(render_once) == "render-1"
    now[0] = 3.0
    assert cache.get_or_render(render_once) == "render-1"
    assert cache.get_or_render(render_once) == "render-2"
    assert calls == 2


def test_recent_payload_cache_returns_fallback_while_first_refresh_runs():
    started = threading.Event()
    release = threading.Event()
    cache = audit_web_module._RecentPayloadCache(10.0)

    def render_once():
        started.set()
        assert release.wait(timeout=1)
        return {"state": "ready"}

    fallback = {"state": "refreshing"}
    assert cache.get_or_refresh(render_once, lambda: fallback) == fallback
    assert started.wait(timeout=1)
    release.set()
    for _ in range(20):
        payload = cache.get_or_refresh(render_once, lambda: fallback)
        if payload["state"] == "ready":
            break
        time.sleep(0.01)
    assert payload == {"state": "ready"}


def test_tutorial_check_route_records_real_step_status(tmp_path: Path):
    db_path = tmp_path / "worker.sqlite3"
    client = loopback_test_client(create_audit_app(db_path))

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
    client = loopback_test_client(create_audit_app(db_path))

    response = client.post("/tutorial/run/setup_service_config")

    assert response.status_code == 200
    assert response.json()["status"] == "done"
    events = AutoReplyStore(db_path).list_setup_wizard_events("service_config")
    assert events[0]["action_id"] == "setup_service_config"


def test_tutorial_run_route_rejects_blocked_action(tmp_path: Path):
    client = loopback_test_client(create_audit_app(tmp_path / "worker.sqlite3"))

    response = client.post("/tutorial/run/setup_service_config")

    assert response.status_code == 409


def test_tutorial_run_route_persists_failed_action_status(
    monkeypatch,
    tmp_path: Path,
):
    def fake_run(action_id, *, repo_root, env):
        del repo_root, env
        assert action_id == "setup_mcp"
        return SetupWizardEvent(
            step_id="mcp",
            action_id="setup_mcp",
            status="failed",
            summary="MEMORY_CONNECTOR_URL is missing.",
        )

    monkeypatch.setattr(audit_web_module, "run_setup_action", fake_run)
    db_path = tmp_path / "worker.sqlite3"
    store = AutoReplyStore(db_path)
    store.upsert_setup_wizard_step(step_id="preflight", status="done", summary="ok")
    store.upsert_setup_wizard_step(
        step_id="cli_components",
        status="done",
        summary="ok",
    )
    client = loopback_test_client(create_audit_app(db_path))

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
    client = loopback_test_client(create_audit_app(db_path))

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
    client = loopback_test_client(create_audit_app(tmp_path / "worker.sqlite3"))

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


def test_tasks_page_renders_sent_todos_with_owner_project_filters(tmp_path: Path):
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
        description="确认交付验收时间和阻塞。",
        owner_user_id="owner-1",
        owner_name="Alex",
        status="open",
        priority="P1",
        deadline_at="2026-07-01 18:00:00",
    )
    store.create_work_todo_dingtalk_link(
        work_todo_id=todo_id,
        dingtalk_task_id="dt-task-1",
        executor_user_id="owner-1",
        executor_name="Alex",
        title_snapshot="给客户同步验收 ETA：来源于客户群红灯风险",
        deadline_at_snapshot="2026-07-01 18:00:00",
        priority_snapshot="P1",
        status="active",
        last_push_at="2026-06-27 09:00:00",
    )
    store.create_follow_up_draft(
        project_id=project_id,
        todo_id=todo_id,
        owner_user_id="owner-1",
        owner_name="Alex",
        target_conversation_id="cid-1",
        target_kind="group",
        question_text="基于客户群红灯风险，请确认验收 ETA。",
        status="sent",
        sent_at="2026-06-27 10:00:00",
    )

    html = render_tasks_page(store)
    rows = task_script_json(html, "sent-todos-data")
    filters = task_script_json(html, "sent-todos-filters")

    assert "Sent TODOs" in html
    assert 'id="sent-todos-table"' in html
    assert 'id="sent-todo-owner-filter"' in html
    assert 'id="sent-todo-project-filter"' in html
    assert 'title: "Original Text"' in html
    assert "sent-todo-search-input" in html
    assert "typeFilter.addEventListener" in html
    assert "ownerFilter.addEventListener" in html
    assert "projectFilter.addEventListener" in html
    assert len(rows) == 2
    assert rows[0]["kindLabel"] == "Follow-up"
    assert rows[0]["originalText"] == "基于客户群红灯风险，请确认验收 ETA。"
    assert rows[0]["detailUrl"] == f"/tasks/{project_id}#todo-{todo_id}"
    assert rows[1]["kindLabel"] == "DingTalk Todo"
    assert rows[1]["originalText"] == "给客户同步验收 ETA：来源于客户群红灯风险"
    assert filters["owners"] == ["Alex"]
    assert filters["projects"] == ["客户交付"]
    assert filters["types"] == ["DingTalk Todo", "Follow-up"]


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
    client = loopback_test_client(create_audit_app(tmp_path / "worker.sqlite3"))

    response = client.get("/tasks/999")

    assert response.status_code == 404
    assert "Project not found" in response.text


def test_task_management_search_api_returns_task_context(tmp_path: Path):
    db_path = tmp_path / "worker.sqlite3"
    store = AutoReplyStore(db_path)
    project_id = store.create_work_project(
        title="技术部招聘",
        category="recruiting",
        status="active",
        priority="P1",
        risk_level="medium",
        owner_user_id="owner-1",
        owner_name="Mina",
        current_state="候选人评估中",
        next_step="确认 Colin 复试结论",
        source_conversations_json=json.dumps(
            [{"id": "cid-hiring", "title": "技术部招聘群"}],
            ensure_ascii=False,
        ),
    )
    todo_id = store.create_work_todo(
        project_id=project_id,
        title="评估 Colin 售前解决方案候选人",
        description="确认候选人的技术面、售前方案能力和下一轮安排。",
        owner_user_id="owner-1",
        owner_name="Mina",
        priority="P1",
        deadline_at="2026-07-25 18:00:00",
        next_follow_up_at="2026-07-24 15:00:00",
    )
    follow_up_id = store.create_follow_up_draft(
        project_id=project_id,
        todo_id=todo_id,
        owner_user_id="owner-1",
        owner_name="Mina",
        target_conversation_id="cid-hiring",
        target_kind="group",
        question_text="Colin 的复试结论定了吗？",
        scheduled_at="2026-07-24 15:00:00",
    )
    client = TestClient(create_audit_app(db_path))

    response = client.get(
        "/api/task-management/search",
        params={
            "q": "这个任务现在是什么状态？",
            "conversation_id": "cid-hiring",
            "owner_user_id": "owner-1",
            "limit": "3",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True
    assert payload["count"] == 1
    item = payload["items"][0]
    assert item["project"]["id"] == project_id
    assert item["project"]["detail_url"] == f"/tasks/{project_id}"
    assert "source_conversation_match" in item["match"]["reasons"]
    assert item["todos"][0]["id"] == todo_id
    assert item["todos"][0]["detail_url"] == f"/tasks/{project_id}#todo-{todo_id}"
    assert item["todos"][0]["deadline_at"] == "2026-07-25 18:00:00"
    assert item["todos"][0]["follow_ups"][0]["id"] == follow_up_id
    assert item["todos"][0]["follow_ups"][0]["detail_url"] == (
        f"/tasks/{project_id}#follow-up-{follow_up_id}"
    )


def test_task_management_project_api_returns_detail_and_404(tmp_path: Path):
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

    response = client.get(f"/api/task-management/projects/{project_id}")
    missing = client.get("/api/task-management/projects/999")

    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True
    assert payload["item"]["project"]["id"] == project_id
    assert payload["item"]["todos"][0]["title"] == "补齐来源链接"
    assert missing.status_code == 404
    assert missing.json()["error"] == "project_not_found"


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
    assert "CEO_PROMPT_VAR_CALENDAR_RULES_PATH" not in html
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
    assert "CEO_CONSUMER_WORKERS" in html
    assert "同一会话仍由 SQLite 会话锁串行执行" in html
    assert "CEO_MEETING_PRODUCER_INTERVAL_SECONDS" in html
    assert "meeting producer 扫描 dws minutes 的间隔秒数" in html
    assert "CEO_MEETING_CONSUMER_POLL_INTERVAL_SECONDS" in html
    assert "meeting consumer 检查 pending meeting job 的间隔秒数" in html
    assert "CEO_MEETING_SETTLE_SECONDS" in html
    assert "会议结束后等待多久再允许 meeting consumer 处理" in html
    assert "CEO_TASK_WORK_ITEM_INTERVAL_SECONDS" in html
    assert "task-maintenance 处理 work item/OKR review 的间隔秒数" in html
    assert "CEO_TASK_DAILY_INTERVAL_SECONDS" in html
    assert "task-maintenance 扫 task sources 的间隔秒数" in html
    assert "CEO_TASK_FOLLOW_UP_INTERVAL_SECONDS" in html
    assert "follow-up-delivery 处理 due follow-ups 的间隔秒数" in html
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


def test_system_config_hides_and_rejects_unknown_env_keys(tmp_path, monkeypatch):
    env_path = tmp_path / ".env"
    env_path.write_text(
        "CEO_WORKSPACE=/tmp/memory\nPRIVATE_SERVICE_TOKEN=do-not-render\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("CEO_ENV_FILE", str(env_path))

    html = render_config_page(active_tab="system")
    status, _, _ = handle_system_config_post(
        b"system_key=PRIVATE_SERVICE_TOKEN&system_value=changed"
    )

    assert status == 303
    assert "PRIVATE_SERVICE_TOKEN" not in html
    assert "do-not-render" not in html
    assert "PRIVATE_SERVICE_TOKEN=do-not-render" in env_path.read_text(
        encoding="utf-8"
    )


def test_render_config_page_shows_channel_doctor(tmp_path, monkeypatch):
    from app.channel_gate import ChannelGateResult, ChannelGateState

    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    store.set_service_state(
        "channel_login_request:lark",
        json.dumps(
            {
                "status": "running",
                "reason_code": "status_auth_invalid",
                "started_at": "2026-07-28T12:00:00+00:00",
                "pid": 4242,
                "token": "must-not-render",
                "path": "/private/auth",
            }
        ),
    )

    monkeypatch.setattr(
        "app.channel_gate.DwsChannelGate.check",
        lambda self: ChannelGateResult(
            channel="dingtalk",
            state=ChannelGateState.READY,
            reason_code="ready",
            commands=(
                ("dws", "auth", "status"),
                ("dws", "contact", "user", "get-self"),
            ),
        ),
    )
    monkeypatch.setattr(
        "app.channel_gate.LarkChannelGate.check",
        lambda self: ChannelGateResult(
            channel="lark",
            state=ChannelGateState.BLOCKED,
            reason_code="executable_missing",
            detail="lark-cli command not found",
            commands=(("lark-cli", "auth", "status"),),
        ),
    )

    html = render_config_page(
        active_tab="channels",
        db_path=tmp_path / "worker.sqlite3",
    )

    assert "Channel doctor" in html
    assert "dingtalk" in html
    assert "lark" in html
    assert "已就绪" in html
    assert "已阻断" in html
    assert "executable_missing" in html
    assert "lark-cli command not found" in html
    assert "dws auth status" in html
    assert "dws contact user get-self" in html
    assert "lark-cli auth status" in html
    assert "本次检查" in html
    assert "尚无成功记录" in html
    assert "已避免重复弹出授权页" in html
    assert "2026-07-28T12:00:00+00:00" in html
    assert "4242" not in html
    assert "must-not-render" not in html
    assert "/private/auth" not in html
    assert "<th>Status 检查</th>" in html
    assert "<th>Live probe</th>" in html
    assert "auth archive" not in html.casefold()


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
        "&system_key=CEO_CONSUMER_WORKERS"
        "&system_value=2"
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
        "&system_key=CEO_TASK_FOLLOW_UP_INTERVAL_SECONDS"
        "&system_value=3600"
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
    assert "CEO_CONSUMER_WORKERS=2" in env_text
    assert "CEO_MEETING_PRODUCER_INTERVAL_SECONDS=60" in env_text
    assert "CEO_MEETING_CONSUMER_POLL_INTERVAL_SECONDS=10" in env_text
    assert "CEO_MEETING_SETTLE_SECONDS=600" in env_text
    assert "CEO_TASK_WORK_ITEM_INTERVAL_SECONDS=60" in env_text
    assert "CEO_TASK_DAILY_INTERVAL_SECONDS=86400" in env_text
    assert "CEO_TASK_FOLLOW_UP_INTERVAL_SECONDS=3600" in env_text
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
    client = loopback_test_client(create_audit_app(tmp_path / "worker.sqlite3"))

    response = client.post("/open-dingtalk?cid=75217569357")

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

    response = client.post("/open-dingtalk?conversation_id=cid-1")

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
    assert (
        'fetch("/open-dingtalk?conversation_id=cid-1", '
        '{method: "POST", cache: "no-store"})'
    ) in response.text
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

    response = client.post("/open-dingtalk?cid=")

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
    client = loopback_test_client(create_audit_app(tmp_path / "worker.sqlite3"))

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
    assert 'method: "POST"' in response.text
    assert 'method: "GET"' not in response.text
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
    assert "requireInteraction: true" in response.text
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


def test_browser_notifications_page_shows_only_current_unresolved_problems(
    tmp_path: Path,
):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    current_decision = store.record_reply_attempt(
        conversation_id="cid-decision",
        conversation_title="HR",
        trigger_message_id="msg-decision",
        trigger_sender="Mina",
        trigger_text="need a choice",
        action="agent_run",
        sensitivity_kind="general",
        audit_summary="请选择下一步。",
        send_status="needs_human",
    )
    current_failure = store.record_reply_attempt(
        conversation_id="cid-failure",
        conversation_title="Operations",
        trigger_message_id="msg-failure",
        trigger_sender="Mina",
        trigger_text="current failure",
        action="agent_run",
        sensitivity_kind="general",
        audit_summary="Provider is unavailable.",
        send_status="failed",
    )
    superseded_failure = store.record_reply_attempt(
        conversation_id="cid-resolved",
        conversation_title="Operations",
        trigger_message_id="msg-resolved",
        trigger_sender="Mina",
        trigger_text="old failure",
        action="agent_run",
        sensitivity_kind="general",
        audit_summary="Old provider failure.",
        send_status="failed",
    )
    store.record_reply_attempt(
        conversation_id="cid-resolved",
        conversation_title="Operations",
        trigger_message_id="msg-resolved",
        trigger_sender="Mina",
        trigger_text="old failure",
        action="send_reply",
        sensitivity_kind="general",
        send_status="sent",
    )

    response = TestClient(create_audit_app(store.path)).get("/notifications")

    assert response.status_code == 200
    assert "待处理问题" in response.text
    assert f"Attempt #{current_decision}" in response.text
    assert "请选择下一步。" in response.text
    assert f"Attempt #{current_failure}" in response.text
    assert "Provider is unavailable." in response.text
    assert f"Attempt #{superseded_failure}" not in response.text


def test_browser_notifications_exclude_active_and_provider_recovery_tasks(
    tmp_path: Path,
):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    store.enqueue_reply_task(
        conversation_id="cid-active",
        conversation_title="Active task",
        single_chat=False,
        trigger_message_id="msg-active",
        trigger_create_time="2026-08-08 01:00:00",
        trigger_sender="System",
        trigger_text="Active task.",
    )
    [active_task] = store.claim_reply_tasks(limit=1)
    active_attempt = store.record_reply_attempt(
        conversation_id=active_task.conversation_id,
        conversation_title=active_task.conversation_title,
        trigger_message_id=active_task.trigger_message_id,
        trigger_sender=active_task.trigger_sender,
        trigger_text=active_task.trigger_text,
        action="agent_run",
        sensitivity_kind="general",
        send_status="failed",
    )
    active_decision = store.record_reply_attempt(
        conversation_id=active_task.conversation_id,
        conversation_title=active_task.conversation_title,
        trigger_message_id=active_task.trigger_message_id,
        trigger_sender=active_task.trigger_sender,
        trigger_text=active_task.trigger_text,
        action="agent_run",
        sensitivity_kind="general",
        send_status="needs_human",
    )

    store.enqueue_reply_task(
        conversation_id="cid-provider",
        conversation_title="Provider recovery",
        single_chat=False,
        trigger_message_id="msg-provider",
        trigger_create_time="2026-08-08 01:00:00",
        trigger_sender="System",
        trigger_text="Provider recovery.",
    )
    [provider_task] = store.claim_reply_tasks(limit=1)
    store.defer_reply_task(
        provider_task.id,
        "codex_provider_unavailable",
        expected_execution_generation=provider_task.execution_generation,
        available_at="2026-08-08 02:00:00",
    )
    provider_attempt = store.record_reply_attempt(
        conversation_id=provider_task.conversation_id,
        conversation_title=provider_task.conversation_title,
        trigger_message_id=provider_task.trigger_message_id,
        trigger_sender=provider_task.trigger_sender,
        trigger_text=provider_task.trigger_text,
        action="agent_run",
        sensitivity_kind="general",
        send_status="failed",
    )

    store.enqueue_reply_task(
        conversation_id="cid-rerun",
        conversation_title="Rerun in progress",
        single_chat=False,
        trigger_message_id="msg-rerun",
        trigger_create_time="2026-08-08 01:00:00",
        trigger_sender="System",
        trigger_text="Rerun task.",
    )
    rerun_attempt = store.record_reply_attempt(
        conversation_id="cid-rerun",
        conversation_title="Rerun in progress",
        trigger_message_id="msg-rerun",
        trigger_sender="System",
        trigger_text="Rerun task.",
        action="agent_run",
        sensitivity_kind="general",
        send_status="failed",
    )

    store.enqueue_reply_task(
        conversation_id="cid-completed",
        conversation_title="Completed task",
        single_chat=False,
        trigger_message_id="msg-completed",
        trigger_create_time="2026-08-08 01:00:00",
        trigger_sender="System",
        trigger_text="Completed task.",
    )
    [completed_task] = store.claim_reply_tasks(limit=1)
    store.complete_reply_task(
        completed_task.id,
        expected_execution_generation=completed_task.execution_generation,
    )
    completed_attempt = store.record_reply_attempt(
        conversation_id=completed_task.conversation_id,
        conversation_title=completed_task.conversation_title,
        trigger_message_id=completed_task.trigger_message_id,
        trigger_sender=completed_task.trigger_sender,
        trigger_text=completed_task.trigger_text,
        action="agent_run",
        sensitivity_kind="general",
        send_status="blocked",
    )

    html = audit_web_module.render_browser_notifications_page(store)

    assert f"Attempt #{active_attempt}" not in html
    assert f"Attempt #{active_decision}" not in html
    assert f"Attempt #{provider_attempt}" not in html
    assert f"Attempt #{rerun_attempt}" not in html
    assert f"Attempt #{completed_attempt}" not in html
    assert store.count_current_unresolved_problem_attempts() == 0


def test_browser_notification_post_accepts_stable_inbox_notification(tmp_path: Path):
    client = loopback_test_client(create_audit_app(tmp_path / "worker.sqlite3"))

    response = client.post(
        "/browser-notifications",
        json={
            "title": "CEO 有待处理问题",
            "message": "2 项问题待处理。",
            "url": "",
            "id": "ceo-agent-service-problems",
            "detail_url": "/notifications",
        },
    )

    assert response.status_code == 200
    event = audit_web_module._BROWSER_NOTIFICATION_HISTORY[-1]
    assert event["id"] == "ceo-agent-service-problems"
    assert event["detail_url"] == "/notifications"


def test_browser_notification_post_emits_named_dismiss_event(tmp_path: Path):
    client = loopback_test_client(create_audit_app(tmp_path / "worker.sqlite3"))

    response = client.post(
        "/browser-notifications",
        json={"id": "ceo-agent-service-trigger-current", "dismiss": True},
    )

    assert response.status_code == 200
    event = audit_web_module._BROWSER_NOTIFICATION_HISTORY[-1]
    assert event["id"] == "ceo-agent-service-trigger-current"
    assert event["event_type"] == "dismiss"


def test_browser_notification_post_reports_no_subscribers(tmp_path: Path):
    client = loopback_test_client(create_audit_app(tmp_path / "worker.sqlite3"))

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
    client = loopback_test_client(app)

    response = client.get("/settings?tab=config")
    legacy_response = client.get("/config")

    assert response.status_code == 200
    assert "Producer 路由配置" in response.text
    assert "/settings?tab=config&amp;config_tab=developer" in response.text
    assert '<a class="prompt-tab active" href="/settings?tab=config">Config</a>' in response.text
    assert "Producer 路由配置" in legacy_response.text


def test_render_page_brand_links_to_history():
    html = render_config_page()

    assert '<a class="brand brand-home" href="/history" aria-label="History home">' in html


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
    assert "CEO_PROMPT_VAR_CALENDAR_RULES_PATH" not in html
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


def test_render_config_page_shows_editable_audit_rules_and_fixed_previews(
    tmp_path: Path,
    monkeypatch,
):
    template_path = tmp_path / "audit_rules.md"
    template_path.write_text("Check publication authority.", encoding="utf-8")
    monkeypatch.setenv("CEO_AUDIT_RULES_TEMPLATE_PATH", str(template_path))

    html = render_config_page(active_tab="audit-rules", saved=True)

    assert 'href="/config?tab=audit-rules"' in html
    assert 'class="prompt-tab active"' in html
    assert 'action="/config?tab=audit-rules"' in html
    assert "Check publication authority." in html
    assert "Consumer preview" in html
    assert "Audit preview" in html
    assert "Last saved" in html
    assert "do not execute" in html
    assert "do not rewrite" in html
    textarea = html.split('<textarea id="template"', 1)[1].split("</textarea>", 1)[0]
    assert "Check publication authority." in textarea
    assert "do not execute" not in textarea
    assert "do not rewrite" not in textarea
    assert "Saved." in html


def test_config_audit_rules_tab_saves_empty_custom_body(
    tmp_path: Path,
    monkeypatch,
):
    path = tmp_path / "audit_rules.md"
    monkeypatch.setenv("CEO_AUDIT_RULES_TEMPLATE_PATH", str(path))

    status, headers, html = handle_audit_rules_post(b"template=")

    assert status == 303
    assert headers["Location"] == "/config?tab=audit-rules&saved=1"
    assert html == ""
    assert read_audit_rules_template() == ""


def test_config_audit_rules_post_rejects_invalid_template_without_overwrite(
    tmp_path: Path,
    monkeypatch,
):
    path = tmp_path / "audit_rules.md"
    path.write_text("Keep this rule.", encoding="utf-8")
    monkeypatch.setenv("CEO_AUDIT_RULES_TEMPLATE_PATH", str(path))

    status, headers, html = handle_audit_rules_post(
        b"template=%3Cvar%3A+missing_rule%3E"
    )

    assert status == 400
    assert headers == {}
    assert "Template validation error" in html
    assert "plain text" in html
    assert "&lt;var: missing_rule&gt;" in html
    assert "Keep this rule." in html
    assert path.read_text(encoding="utf-8") == "Keep this rule."


def test_config_audit_rules_post_rejects_reserved_prompt_section_without_overwrite(
    tmp_path: Path,
    monkeypatch,
):
    path = tmp_path / "audit_rules.md"
    path.write_text("Keep this rule.", encoding="utf-8")
    monkeypatch.setenv("CEO_AUDIT_RULES_TEMPLATE_PATH", str(path))

    status, headers, html = handle_audit_rules_post(
        b"template=%23%23++dYnAmIc+++SkIlL+%23%23%23%0Ainjected"
    )

    assert status == 400
    assert headers == {}
    assert "Template validation error" in html
    assert "reserved core heading" in html
    assert "Keep this rule." in html
    assert path.read_text(encoding="utf-8") == "Keep this rule."


@pytest.mark.parametrize(
    ("persisted", "expected_error"),
    (
        ("[dynamic-skill] injected", "reserved structural marker"),
        ("&lt;h2&gt;Dynamic Skill&lt;/h2&gt;", "structural HTML"),
        ("[dyna\u200bmic-skill] injected", "default-ignorable"),
    ),
)
def test_config_page_surfaces_persisted_invalid_audit_rules_without_rewriting(
    tmp_path: Path,
    monkeypatch,
    persisted: str,
    expected_error: str,
):
    path = tmp_path / "audit_rules.md"
    path.write_text(persisted, encoding="utf-8")
    monkeypatch.setenv("CEO_AUDIT_RULES_TEMPLATE_PATH", str(path))

    html = render_config_page(active_tab="audit-rules")

    assert "Template render error" in html
    assert expected_error in html
    assert "Consumer preview" in html
    assert "Audit preview" in html
    assert path.read_text(encoding="utf-8") == persisted


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


def test_render_attempt_list_reuses_one_read_connection(tmp_path: Path, monkeypatch):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    seed_attempt(store)
    original_connect = sqlite3.connect
    connection_calls = 0

    def tracked_connect(*args, **kwargs):
        nonlocal connection_calls
        connection_calls += 1
        return original_connect(*args, **kwargs)

    monkeypatch.setattr(sqlite3, "connect", tracked_connect)

    html = render_attempt_list(store, include_chart=True)

    assert "#1" in html
    assert connection_calls == 1


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
    task = store.claim_reply_task(1)
    assert task is not None
    store.fail_reply_task(
        1,
        "delivery failed",
        expected_execution_generation=task.execution_generation,
    )

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


def test_pending_reconciliation_explains_context_and_requires_no_user_decision(
    tmp_path: Path,
):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    attempt_id = store.record_reply_attempt(
        conversation_id="cid-oa",
        conversation_title="工作通知:北京星尘纪元智能科技有限公司",
        trigger_message_id="msg-oa",
        trigger_sender="OA审批",
        trigger_text="张静在招聘需求申请里提到了你，并说明以流程评论为准。",
        action="agent_run",
        sensitivity_kind="internal_personnel",
        send_status="pending_reconciliation",
        audit_summary="审批动作结果未知，等待只读核对当前审批状态。",
    )
    store.update_reply_attempt(attempt_id, send_error="audit_recovery_failed")

    status, detail = render_attempt_detail(store, attempt_id)
    history = render_attempt_list(store, include_chart=False)

    assert status == 200
    assert "正在核对执行结果" in detail
    assert "系统只会读取外部状态，不会重复审批或发送通知" in detail
    assert "你当前无需操作" in detail
    assert "等待你的决策" not in detail
    assert "🔎 正在核对执行结果" in history
    assert "Pending Reconciliation" not in history


def test_pending_reconciliation_names_objective_and_actions():
    attempt = audit_web_module.ReplyAttempt(
        id=1,
        conversation_id="cid-oa",
        conversation_title="审批通知",
        trigger_message_id="msg-oa",
        trigger_sender="OA审批",
        trigger_text="请处理招聘需求审批",
        action="agent_run",
        sensitivity_kind="internal_personnel",
        codex_reason="",
        draft_reply_text="",
        final_reply_text="",
        permission_action="",
        permission_reason="",
        send_status="pending_reconciliation",
        send_error="audit_recovery_failed",
        retry_count=0,
        created_at="2026-08-10 12:00:00",
        updated_at="2026-08-10 12:00:00",
    )
    consumer = AgentRun(
        id=10,
        reply_task_id=20,
        execution_generation="initial",
        role=AgentRole.CONSUMER,
        proposal_revision=0,
        turn_attempt=0,
        parent_agent_run_id=None,
        operation_id="",
        status="completed",
        final_result_json=json.dumps(
            {
                "outcome": "proposal",
                "summary": "准备处理审批",
                "proposal": {
                    "objective": "处理招聘需求审批",
                    "actions": [
                        {
                            "description": "同意招聘需求申请。",
                            "capability": "agent_cli.dws",
                            "operation": "oa approval approve",
                            "target": {"instance_id": "process-1"},
                            "payload": {"argv": ["dws", "oa"]},
                            "expected_verification": "读回审批结果",
                        },
                        {
                            "description": "通知申请人审批结果。",
                            "capability": "agent_cli.dws",
                            "operation": "chat message send",
                            "target": {"user": "user-1"},
                            "payload": {"argv": ["dws", "chat"]},
                            "expected_verification": "读回消息",
                        },
                    ],
                    "sourced_facts": [],
                    "authored_judgment": "材料满足当前审批条件。",
                },
                "error": {
                    "code": "",
                    "retryable": False,
                    "authorization_required": False,
                },
            },
            ensure_ascii=False,
        ),
        created_at="2026-08-10 12:00:00",
        updated_at="2026-08-10 12:00:00",
    )

    html = audit_web_module._attempt_status_card(attempt, None, [consumer])

    assert "事项：处理招聘需求审批" in html
    assert "同意招聘需求申请" in html
    assert "通知申请人审批结果" in html
    assert "你当前无需操作" in html
    assert "。；" not in html
    assert "。。" not in html


def test_nonterminal_later_attempt_is_not_reported_as_completed(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    old_id = store.record_reply_attempt(
        conversation_id="cid-oa",
        conversation_title="审批通知",
        trigger_message_id="msg-oa",
        trigger_sender="OA审批",
        trigger_text="请处理招聘需求审批",
        action="agent_run",
        sensitivity_kind="internal_personnel",
        send_status="pending_reconciliation",
    )
    later_id = store.record_reply_attempt(
        conversation_id="cid-oa",
        conversation_title="审批通知",
        trigger_message_id="msg-oa",
        trigger_sender="OA审批",
        trigger_text="请处理招聘需求审批",
        action="agent_run",
        sensitivity_kind="internal_personnel",
        send_status="pending_reconciliation",
    )

    status, html = render_attempt_detail(store, old_id)

    assert status == 200
    assert f"Attempt #{later_id}" in html
    assert "继续核对同一事项" in html
    assert "后续处理，无需你操作" not in html


def test_terminal_later_attempt_replaces_stale_pending_detail_fields(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    old_id = store.record_reply_attempt(
        conversation_id="cid-oa",
        conversation_title="审批通知",
        trigger_message_id="msg-oa",
        trigger_sender="OA审批",
        trigger_text="请处理招聘需求审批",
        action="agent_run",
        sensitivity_kind="internal_personnel",
        send_status="pending_reconciliation",
    )
    store.update_reply_attempt(old_id, send_error="audit_recovery_failed")
    later_id = store.record_reply_attempt(
        conversation_id="cid-oa",
        conversation_title="审批通知",
        trigger_message_id="msg-oa",
        trigger_sender="OA审批",
        trigger_text="请处理招聘需求审批",
        action="agent_run",
        sensitivity_kind="internal_personnel",
        send_status="completed",
        audit_summary=(
            "审批已同意；已向实际申请人发送审批结果，外部读回确认消息存在。"
        ),
    )

    status, html = render_attempt_detail(store, old_id)

    assert status == 200
    assert f"已完成（后续记录 #{later_id}）" in html
    assert "历史错误已由后续处理解决" in html
    assert "事项：</strong>请处理招聘需求审批" in html
    assert "需要你决策：</strong>否" in html
    assert "处理结果：</strong>后续任务已完成" in html
    assert "审批已同意；已向实际申请人发送审批结果" not in html
    assert "audit_recovery_failed" not in html


def test_render_attempt_detail_marks_closed_blocked_work_as_historical(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    assert store.enqueue_reply_task(
        conversation_id="cid-blocked",
        conversation_title="OKR 更新",
        single_chat=False,
        trigger_message_id="msg-blocked",
        trigger_create_time="2026-08-12 10:00:00",
        trigger_sender="Mina",
        trigger_text="请更新 OKR 进展",
    )
    task = store.claim_reply_task(1)
    assert task is not None
    store.complete_reply_task(1, expected_execution_generation=task.execution_generation)
    attempt_id = store.record_reply_attempt(
        conversation_id="cid-blocked",
        conversation_title="OKR 更新",
        trigger_message_id="msg-blocked",
        trigger_sender="Mina",
        trigger_text="请更新 OKR 进展",
        action="agent_run",
        sensitivity_kind="internal",
        codex_reason="实时 OKR 接口未登录，因此没有执行更新。",
        send_status="blocked",
    )
    store.update_reply_attempt(
        attempt_id,
        send_status="blocked",
        send_error="DINGTEAM_OKR_NOT_AUTHENTICATED",
        permission_action="closed_after_review",
    )

    status, html = render_attempt_detail(store, attempt_id)

    assert status == 200
    assert "已核验结案（未自动执行）" in html
    assert "受阻原因见下方“Codex reason”；该外部操作未执行。" in html
    assert "该事项已核验结案；外部动作未自动执行" in html
    assert "◌ 已核验结案" in html
    assert "DINGTEAM_OKR_NOT_AUTHENTICATED" not in html


def test_terminal_later_attempt_keeps_original_failure_reason_visible(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    old_id = store.record_reply_attempt(
        conversation_id="cid-resolved-failure",
        conversation_title="审批通知",
        trigger_message_id="msg-resolved-failure",
        trigger_sender="OA审批",
        trigger_text="请处理离职审批",
        action="agent_run",
        sensitivity_kind="internal_personnel",
        codex_reason="approval_detail_unavailable",
        audit_summary="审批详情读取链路未完成，未执行外部动作。",
        send_status="failed",
    )
    store.update_reply_attempt(old_id, send_error="approval_detail_unavailable")
    later_id = store.record_reply_attempt(
        conversation_id="cid-resolved-failure",
        conversation_title="审批通知",
        trigger_message_id="msg-resolved-failure",
        trigger_sender="OA审批",
        trigger_text="请处理离职审批",
        action="agent_run",
        sensitivity_kind="internal_personnel",
        send_status="completed",
        audit_summary="审批已处理并完成回读。",
    )

    status, html = render_attempt_detail(store, old_id)

    assert status == 200
    assert f"Attempt #{later_id}" in html
    assert "原失败原因：</strong>审批详情读取链路未完成，未执行外部动作。" in html
    assert "需要你决策：</strong>否" in html


def test_codex_process_failure_is_explained_without_internal_code():
    explanation = audit_web_module._failure_code_explanation("codex_process_failed")

    assert explanation == "Agent 执行进程未成功完成，因此本轮没有得到可验证结果。"
    assert "codex_process_failed" not in explanation


def test_failure_reason_uses_human_stage_label_without_double_punctuation(
    tmp_path: Path,
):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    attempt_id = store.record_reply_attempt(
        conversation_id="cid-readable-stage",
        conversation_title="审批通知",
        trigger_message_id="msg-readable-stage",
        trigger_sender="OA审批",
        trigger_text="请处理审批",
        action="agent_run",
        sensitivity_kind="internal_personnel",
        send_status="failed",
    )
    attempt = store.get_reply_attempt(attempt_id)
    assert attempt is not None
    run = AgentRun.model_construct(
        role=AgentRole.CONSUMER,
        status="failed",
        structured_error_json=json.dumps(
            {
                "code": "codex_process_failed",
                "detail": "处理未完成，失败代码：codex_process_failed。",
            }
        ),
        side_effect_state="none",
    )

    reason = audit_web_module._agent_failure_reason_text(attempt, [run])

    assert reason == (
        "生成回复阶段：Agent 执行进程未成功完成，因此本轮没有得到可验证结果；"
        "未执行外部操作。"
    )
    assert "consumer:" not in reason
    assert "。；" not in reason


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


def test_oa_attempt_detail_links_to_later_verified_action_for_same_process(
    tmp_path: Path,
):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    blocked_id = store.record_reply_attempt(
        conversation_id="cid-original",
        conversation_title="审批通知",
        trigger_message_id="msg-original",
        trigger_sender="工作通知",
        trigger_text="审批提醒",
        action="oa_approval",
        sensitivity_kind="internal_personnel",
        oa_process_instance_id="proc-1",
        oa_task_id="task-1",
        oa_action="comment",
        send_status="blocked",
    )
    verified_id = store.record_reply_attempt(
        conversation_id="cid-follow-up",
        conversation_title="审批待办",
        trigger_message_id="msg-follow-up",
        trigger_sender="Derek OA",
        trigger_text="审批待办扫描",
        action="agent_run",
        sensitivity_kind="general",
        oa_process_instance_id="proc-1",
        oa_task_id="task-1",
        oa_action="approve",
        send_status="completed",
    )

    status, html = render_attempt_detail(store, blocked_id)

    assert status == 200
    assert f"已由 #{verified_id} 后续处理" in html


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


def test_history_failed_item_shows_reason_effect_and_actions_inline(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    attempt_id = store.record_reply_attempt(
        conversation_id="cid-actionable",
        conversation_title="HR",
        trigger_message_id="msg-actionable",
        trigger_sender="Mina",
        trigger_text="Please review this.",
        action="agent_run",
        sensitivity_kind="general",
        audit_summary="Current task did not complete",
        send_status="failed",
    )

    html = render_attempt_list(store, include_chart=False)

    assert "状态：</strong>需要你处理" in html
    assert "原因：</strong>Current task did not complete" in html
    assert "外部副作用：</strong>未执行任何外部动作" in html
    assert f'action="/attempts/{attempt_id}/rerun?return_to=/history"' in html
    assert ">重试当前任务</button>" in html
    assert ">暂不处理</button>" in html
    assert ">人工处理</a>" in html
    assert ">技术详情</a>" in html
    assert "你需要做什么：</strong>请选择一种处理方式" in html
    assert "重试会沿用同一任务，不会创建新的业务事项" in html
    assert '<span class="attempt-label">答</span>' not in html
    assert '<span class="attempt-label">结果</span>' not in html


def test_history_only_latest_failed_attempt_for_trigger_offers_actions(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    old_id = store.record_reply_attempt(
        conversation_id="cid-duplicate-failure",
        conversation_title="HR",
        trigger_message_id="msg-duplicate-failure",
        trigger_sender="Mina",
        trigger_text="Please review this once.",
        action="agent_run",
        sensitivity_kind="general",
        audit_summary="First execution failed",
        send_status="failed",
    )
    latest_id = store.record_reply_attempt(
        conversation_id="cid-duplicate-failure",
        conversation_title="HR",
        trigger_message_id="msg-duplicate-failure",
        trigger_sender="Mina",
        trigger_text="Please review this once.",
        action="agent_run",
        sensitivity_kind="general",
        audit_summary="Latest execution failed",
        send_status="failed",
    )

    html = render_attempt_list(store, include_chart=False)

    assert html.count(">重试当前任务</button>") == 1
    assert html.count(">暂不处理</button>") == 1
    assert f"#{old_id}" in html
    assert (
        f'已由 <a href="/attempts/{latest_id}">#{latest_id}</a> 接管，无需操作。'
        in html
    )


def test_history_retrying_item_shows_persisted_plan_without_human_choices(
    tmp_path: Path,
):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    store.enqueue_reply_task(
        conversation_id="cid-retrying",
        conversation_title="HR",
        single_chat=False,
        trigger_message_id="msg-retrying",
        trigger_create_time="2026-08-11 05:00:00",
        trigger_sender="Mina",
        trigger_text="Please review this.",
    )
    task = store.claim_reply_tasks(limit=1)[0]
    retry_at = "2026-08-11 05:14:00"
    store.defer_reply_task(
        task.id,
        "Codex provider unavailable",
        expected_execution_generation=task.execution_generation,
        available_at=retry_at,
    )
    store.record_reply_attempt(
        conversation_id=task.conversation_id,
        conversation_title=task.conversation_title,
        trigger_message_id=task.trigger_message_id,
        trigger_sender=task.trigger_sender,
        trigger_text=task.trigger_text,
        action="agent_run",
        sensitivity_kind="general",
        codex_reason="Codex provider unavailable",
        send_status="failed",
    )
    persisted_task = store.get_reply_task(task.id)
    assert persisted_task is not None

    html = render_attempt_list(store, include_chart=False)

    assert "状态：</strong>系统失败，正在自动恢复" in html
    assert f"第 {persisted_task.attempts}/3 次" in html
    assert audit_web_module._format_local_time(retry_at) in html
    assert ">重试当前任务</button>" not in html
    assert ">暂不处理</button>" not in html
    assert ">技术详情</a>" in html


def test_history_needs_human_item_shows_agent_choices_inline(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    store.enqueue_reply_task(
        conversation_id="cid-choice-history",
        conversation_title="Management",
        single_chat=False,
        trigger_message_id="msg-choice-history",
        trigger_create_time="2026-08-11 05:00:00",
        trigger_sender="Mina",
        trigger_text="Choose a plan.",
    )
    task = store.claim_reply_tasks(limit=1)[0]
    claimed = store.claim_agent_run(
        task.id,
        task.execution_generation,
        role=AgentRole.CONSUMER,
        proposal_revision=0,
        turn_attempt=0,
        parent_agent_run_id=None,
        operation_id="",
        owner="consumer",
    )
    run = store.complete_agent_run(
        claimed.run.id,
        {
            "outcome": "needs_human",
            "summary": "A management choice is required.",
            "proposal": None,
            "decision_options": [
                {
                    "key": "A",
                    "label": "同意当前方案",
                    "instruction": "同意已核验方案并发布。",
                    "consequence": "会执行已审计的外部动作。",
                },
                {
                    "key": "B",
                    "label": "要求补充材料",
                    "instruction": "要求补充材料并发布。",
                    "consequence": "当前外部动作不会执行。",
                },
            ],
            "error": {
                "code": "decision_required",
                "retryable": False,
                "authorization_required": False,
            },
        },
        owner="consumer",
    )
    attempt_id = store.finalize_orchestrated_reply_task(
        task_id=task.id,
        expected_execution_generation=task.execution_generation,
        run_id=run.id,
        task_status="done",
        task_error="",
        available_at="",
        conversation_id=task.conversation_id,
        conversation_title=task.conversation_title,
        trigger_message_id=task.trigger_message_id,
        trigger_sender=task.trigger_sender,
        trigger_text=task.trigger_text,
        codex_reason="A management choice is required.",
        codex_session_id="",
        codex_transcript_start_line=0,
        codex_transcript_end_line=0,
        audit_tool_events_json="[]",
        audit_summary="A management choice is required.",
        send_status="needs_human",
        send_error="needs_human",
        channel="dingtalk",
    )

    html = render_attempt_list(store, include_chart=False)

    assert "A. 同意当前方案" in html
    assert "B. 要求补充材料" in html
    assert f'action="/attempts/{attempt_id}/human-decision?return_to=/history"' in html


def test_attempt_detail_uses_same_attention_reason_and_effect_as_history(
    tmp_path: Path,
):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    attempt_id = store.record_reply_attempt(
        conversation_id="cid-detail-attention",
        conversation_title="Operations",
        trigger_message_id="msg-detail-attention",
        trigger_sender="Mina",
        trigger_text="Please complete this task.",
        action="agent_run",
        sensitivity_kind="general",
        audit_summary="Current task did not complete",
        send_status="failed",
    )

    status, html = render_attempt_detail(store, attempt_id)

    assert status == 200
    assert "事项：</strong>Please complete this task." in html
    assert "需要你决策：</strong>否" in html
    assert "状态：</strong>需要你处理" in html
    assert "原因：</strong>Current task did not complete" in html
    assert "外部副作用：</strong>未执行任何外部动作" in html


def test_retrying_meeting_shows_persisted_plan_without_manager_actions(
    tmp_path: Path,
):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    job_id = store.upsert_meeting_alignment_job(
        meeting_id="meeting-retry-history",
        title="Weekly sync",
        source_json="{}",
        participants_json="[]",
        ended_at="2026-08-11T04:00:00+00:00",
        eligible_at="2026-08-11T04:10:00+00:00",
        status="pending",
    )
    [claimed_job] = store.claim_meeting_alignment_jobs(
        limit=1,
        now="2026-08-11T05:00:00+00:00",
    )
    retry_at = "2026-08-11T05:14:00+00:00"
    store.schedule_meeting_alignment_job_retry(
        job_id,
        "Meeting provider unavailable",
        available_at=retry_at,
    )
    run_id = store.record_meeting_alignment_run(
        job_id=job_id,
        codex_session_id="meeting-retry-session",
        decision_json="{}",
        audit_summary="Meeting provider unavailable",
        status="retry",
        error="Meeting provider unavailable",
    )

    html = render_attempt_list(store, include_chart=False)

    assert f"#meeting-{run_id}" in html
    assert "状态：</strong>系统失败，正在自动恢复" in html
    assert f"第 {claimed_job.attempts}/3 次" in html
    assert audit_web_module._format_local_time(retry_at) in html
    assert f'href="/meeting-attempts/{run_id}">技术详情</a>' in html
    assert ">重试当前任务</button>" not in html


def test_failed_meeting_and_follow_up_expose_reason_and_safe_choices(
    tmp_path: Path,
):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    job_id = store.upsert_meeting_alignment_job(
        meeting_id="meeting-failed-history",
        title="Hiring sync",
        source_json="{}",
        participants_json="[]",
        ended_at="2026-08-11T04:00:00+00:00",
        eligible_at="2026-08-11T04:10:00+00:00",
        status="pending",
    )
    store.update_meeting_alignment_job(
        job_id,
        status="failed",
        error="Meeting delivery failed",
    )
    meeting_run_id = store.record_meeting_alignment_run(
        job_id=job_id,
        codex_session_id="meeting-failed-session",
        decision_json="{}",
        audit_summary="Meeting delivery failed",
        status="failed",
        error="Meeting delivery failed",
    )
    project_id = store.create_work_project(
        title="Hiring",
        category="people",
        priority="P1",
        risk_level="medium",
        owner_name="Mina",
    )
    follow_up_id = store.create_follow_up_draft(
        project_id=project_id,
        owner_name="Mina",
        target_kind="direct",
        question_text="Please provide the update.",
        scheduled_at="2026-08-11 05:00:00",
        status="failed",
        send_result_json='{"error":"Follow-up delivery failed"}',
    )

    html = render_attempt_list(store, include_chart=False)

    assert "Meeting delivery failed" in html
    assert f'href="/meeting-attempts/{meeting_run_id}">人工处理</a>' in html
    assert "Follow-up delivery failed" in html
    assert (
        f'href="/tasks/{project_id}#follow-up-{follow_up_id}">人工处理</a>'
        in html
    )


def test_confirmed_not_sent_follow_up_has_inline_actions_on_same_draft(
    tmp_path: Path,
):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    project_id = store.create_work_project(
        title="Hiring",
        category="recruiting",
        priority="P1",
        risk_level="high",
    )
    todo_id = store.create_work_todo(
        project_id=project_id,
        title="Confirm candidate decision",
        owner_user_id="inactive-owner",
        owner_name="Former owner",
        status="open",
        priority="P1",
    )
    follow_up_id = store.create_follow_up_draft(
        project_id=project_id,
        todo_id=todo_id,
        owner_user_id="inactive-owner",
        owner_name="Former owner",
        target_kind="direct",
        question_text="Please confirm the candidate decision.",
        scheduled_at="2026-08-11 05:00:00",
        status="failed",
        send_result_json=json.dumps(
            {
                "reason": "direct_message_target_rejected",
                "delivery_state": "not_sent",
                "error": "The recipient is inactive; no message was delivered.",
                "external_side_effect": "none",
            }
        ),
    )
    draft = store.get_follow_up_draft(follow_up_id)
    assert draft is not None

    html = render_attempt_list(store, include_chart=False)

    assert "The recipient is inactive; no message was delivered." in html
    assert "direct_message_target_rejected" not in html
    assert "已确认未发送跟进消息" in html
    assert "让 Agent 重新核验负责人" in html
    assert "取消本次跟进" in html
    assert f'/follow-ups/{follow_up_id}/resolution-form' in html

    status, detail_html = render_task_project_detail(store, project_id)
    assert status == 200
    assert "The recipient is inactive; no message was delivered." in detail_html
    assert "你需要做什么" in detail_html
    assert "让 Agent 重新核验负责人" in detail_html
    assert "取消本次跟进" in detail_html

    client = loopback_test_client(create_audit_app(store.path))
    response = client.post(
        f"/follow-ups/{follow_up_id}/resolution-form",
        data={
            "expected_revision": str(draft.revision),
            "resolution": "repair_target",
            "return_to": "/history",
        },
        follow_redirects=False,
    )

    assert response.status_code == 303, response.text
    assert response.headers["location"] == "/history"
    repaired = store.get_follow_up_draft(follow_up_id)
    assert repaired is not None
    assert repaired.id == follow_up_id
    assert repaired.status == "draft"
    assert repaired.revision == draft.revision + 1


def test_recovered_reply_attempt_is_not_reported_or_rendered_as_failed(
    tmp_path: Path,
):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    store.enqueue_reply_task(
        conversation_id="cid-recovered",
        conversation_title="Recovery",
        single_chat=False,
        trigger_message_id="msg-recovered",
        trigger_create_time="2026-08-08 01:00:00",
        trigger_sender="System",
        trigger_text="Recover this reply.",
    )
    [task] = store.claim_reply_tasks(limit=1)
    store.record_reply_attempt(
        conversation_id=task.conversation_id,
        conversation_title=task.conversation_title,
        trigger_message_id=task.trigger_message_id,
        trigger_sender=task.trigger_sender,
        trigger_text=task.trigger_text,
        action="agent_run",
        sensitivity_kind="general",
        send_status="failed",
    )
    store.complete_reply_task(
        task.id,
        expected_execution_generation=task.execution_generation,
    )

    payload = build_worker_status_payload(store)
    reply_queue = next(
        queue for queue in payload["queues"] if queue["name"] == "Reply attempts"
    )
    html = render_attempt_list(store)

    assert reply_queue["failed"] == 0
    assert reply_queue["counts"]["recovered"] == 1
    assert all(row["category"] != "Reply" for row in payload["attention_rows"])
    assert 'class="pill status-action action-state-recovered">↻ Recovered</span>' in html
    assert 'class="pill status-action action-state-failed">💬 Failed</span>' not in html


def test_worker_attention_collapses_reply_attempt_into_matching_reply_task(
    tmp_path: Path,
):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    store.enqueue_reply_task(
        conversation_id="cid-attention",
        conversation_title="Attention",
        single_chat=False,
        trigger_message_id="msg-attention",
        trigger_create_time="2026-08-10 08:00:00",
        trigger_sender="Mina",
        trigger_text="Please handle this.",
    )
    [task] = store.claim_reply_tasks(limit=1)
    store.fail_reply_task(
        task.id,
        "codex_result_invalid",
        expected_execution_generation=task.execution_generation,
    )
    attempt_id = store.record_reply_attempt(
        conversation_id="cid-attention",
        conversation_title="Attention",
        trigger_message_id="msg-attention",
        trigger_sender="Mina",
        trigger_text="Please handle this.",
        action="agent_run",
        sensitivity_kind="general",
        send_status="failed",
    )
    store.update_reply_attempt(attempt_id, send_error="codex_result_invalid")

    payload = build_worker_status_payload(store)
    matching_rows = [
        row
        for row in payload["attention_rows"]
        if row["context"] == "Attention" and row["summary"] == "Please handle this."
    ]

    assert [(row["category"], row["id"]) for row in matching_rows] == [
        ("Reply task", str(task.id))
    ]


def test_worker_attention_excludes_healthy_follow_up_drafts(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    project_id = store.create_work_project(
        title="Client delivery",
        category="projects",
        status="active",
        priority="P1",
        risk_level="medium",
    )
    store.create_follow_up_draft(
        project_id=project_id,
        todo_id=0,
        owner_name="Alex",
        question_text="Any update?",
        status="draft",
    )

    payload = build_worker_status_payload(store)

    assert all(row["category"] != "Follow-up" for row in payload["attention_rows"])


def test_worker_attention_uses_work_input_summary_instead_of_internal_reference(
    tmp_path: Path,
):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    work_input_id = store.enqueue_work_summary_input(
        "ai_minutes",
        "opaque-internal-reference",
        '{"summary":"Review the current meeting follow-up."}',
    )

    payload = build_worker_status_payload(store)
    row = next(
        item
        for item in payload["attention_rows"]
        if item["category"] == "Work item" and item["id"] == str(work_input_id)
    )

    assert row["context"] == "ai_minutes"
    assert row["summary"] == "Review the current meeting follow-up."


def test_worker_attention_uses_work_input_title_before_raw_reference(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    work_input_id = store.enqueue_work_summary_input(
        "ai_minutes",
        '{"opaque":"internal-reference"}',
        '{"title":"Hiring debrief"}',
    )

    payload = build_worker_status_payload(store)
    row = next(
        item
        for item in payload["attention_rows"]
        if item["category"] == "Work item" and item["id"] == str(work_input_id)
    )

    assert row["summary"] == "Hiring debrief"


def test_worker_status_uses_wechat_delivery_outcome_not_raw_failed_status(
    tmp_path: Path,
):
    rejected_store = AutoReplyStore(tmp_path / "rejected.sqlite3")
    rejected_delivery_id = _seed_wechat_pending(rejected_store)
    rejected_store.set_wechat_delivery_status(
        rejected_delivery_id,
        "failed",
        error="user_rejected",
    )
    rejected_payload = build_worker_status_payload(rejected_store)
    rejected_queue = next(
        queue
        for queue in rejected_payload["queues"]
        if queue["name"] == "WeChat deliveries"
    )

    unknown_store = AutoReplyStore(tmp_path / "unknown.sqlite3")
    unknown_delivery_id = _seed_wechat_pending(unknown_store)
    unknown_store.mark_wechat_delivery_sending(unknown_delivery_id)
    unknown_store.set_wechat_delivery_status(
        unknown_delivery_id,
        "send_unknown",
        error="read_only_reconciliation_inconclusive",
    )
    unknown_payload = build_worker_status_payload(unknown_store)
    unknown_queue = next(
        queue
        for queue in unknown_payload["queues"]
        if queue["name"] == "WeChat deliveries"
    )

    assert rejected_queue["failed"] == 0
    assert rejected_queue["counts"]["skipped"] == 1
    assert unknown_queue["failed"] == 1
    assert unknown_queue["latest_error"] == "read_only_reconciliation_inconclusive"


def test_worker_attention_explains_pre_action_wechat_delivery_failure(
    tmp_path: Path,
):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    delivery_id = _seed_wechat_pending(store)
    store.mark_wechat_delivery_sending(delivery_id)
    store.set_wechat_delivery_status(
        delivery_id,
        "failed",
        error="target_open_failed",
        pre_action_failure=True,
    )

    payload = build_worker_status_payload(store)
    row = next(
        item
        for item in payload["attention_rows"]
        if item["category"] == "WeChat delivery" and item["id"] == str(delivery_id)
    )

    assert row["status"] == "failed"
    assert row["error"] == (
        "The target could not be opened before send; no message was sent. "
        "A fresh target check is required before retry."
    )


def test_render_attempt_list_labels_explained_blocked_as_blocked(
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

    assert 'class="pill status-action action-state-blocked">💬 Blocked</span>' in html
    assert "Blocked terminal" not in html


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
    complete_setup_wizard(store)
    app = create_audit_app(store.path)
    client = TestClient(app)

    response = client.get("/history")
    detail_response = client.get(f"/attempts/{attempt_id}")

    assert response.status_code == 200
    assert "CEO Agent Audit" in response.text
    assert "技术部" in response.text
    assert detail_response.status_code == 200
    assert "agent 执行记录" in detail_response.text


def test_fastapi_app_serves_built_workbench_assets_with_secure_boundaries(
    tmp_path: Path,
):
    asset_dir = tmp_path / "app" / "static" / "workbench"
    hashed_assets = asset_dir / "assets"
    hashed_assets.mkdir(parents=True)
    index = (
        b'<!doctype html><html><head><link rel="stylesheet" '
        b'href="/workbench-assets/assets/index-a1b2c3.css"></head>'
        b'<body><a href="/history">History</a><script type="module" '
        b'src="/workbench-assets/assets/index-d4e5f6.js"></script></body></html>'
    )
    (asset_dir / "index.html").write_bytes(index)
    (hashed_assets / "index-a1b2c3.css").write_text("body { color: black; }")
    (hashed_assets / "index-d4e5f6.js").write_text("document.body.dataset.ready = '1';")
    (asset_dir / "favicon.svg").write_text("<svg></svg>")
    outside = tmp_path / "outside.js"
    outside.write_text("globalThis.secret = true;")
    (hashed_assets / "outside-link.js").symlink_to(outside)

    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    complete_setup_wizard(store)
    app = create_audit_app(
        store.path,
        workbench_asset_dir=asset_dir,
        workbench_workspace=tmp_path,
    )

    with TestClient(app) as client:
        root = client.get("/")
        javascript = client.get(
            "/workbench-assets/assets/index-d4e5f6.js"
        )
        stylesheet = client.get(
            "/workbench-assets/assets/index-a1b2c3.css"
        )
        favicon = client.get("/workbench-assets/favicon.svg")
        missing = client.get("/workbench-assets/assets/missing.js")
        directory = client.get("/workbench-assets/assets/")
        traversal = client.get("/workbench-assets/%2e%2e/outside.js")
        symlink = client.get("/workbench-assets/assets/outside-link.js")
        api = client.get("/api")
        history = client.get("/history")
        tasks = client.get("/tasks")
        workers = client.get("/workers")
        settings = client.get("/settings")

    content_security_policy = (
        "default-src 'self'; script-src 'self'; style-src 'self'; "
        "img-src 'self' data:; connect-src 'self'; object-src 'none'; "
        "base-uri 'none'; frame-ancestors 'none'"
    )
    assert root.status_code == 200
    assert root.content == index
    assert root.headers["cache-control"] == "no-cache"
    assert root.headers["content-security-policy"] == content_security_policy
    assert root.headers["x-content-type-options"] == "nosniff"
    assert root.headers["referrer-policy"] == "no-referrer"
    assert root.headers["permissions-policy"] == (
        "camera=(), microphone=(), geolocation=()"
    )

    assert javascript.status_code == 200
    assert javascript.headers["content-type"].startswith("text/javascript")
    assert javascript.headers["cache-control"] == (
        "public, max-age=31536000, immutable"
    )
    assert javascript.headers["content-security-policy"] == content_security_policy
    assert stylesheet.status_code == 200
    assert stylesheet.headers["content-type"].startswith("text/css")
    assert stylesheet.headers["cache-control"] == (
        "public, max-age=31536000, immutable"
    )
    assert favicon.status_code == 200
    assert favicon.headers["cache-control"] == "no-cache"

    assert missing.status_code == 404
    assert missing.headers["cache-control"] == "no-cache"
    assert directory.status_code == 404
    assert traversal.status_code == 404
    assert symlink.status_code == 404
    assert outside.read_text() not in symlink.text
    assert api.status_code == 404
    assert api.content != index
    assert history.status_code == 200
    assert "CEO Agent Audit" in history.text
    assert tasks.status_code == 200
    assert workers.status_code == 200
    assert settings.status_code == 200


def test_workbench_root_rejects_index_symlink_outside_asset_directory(
    tmp_path: Path,
):
    asset_dir = tmp_path / "assets"
    asset_dir.mkdir()
    outside = tmp_path / "outside.html"
    outside.write_text("outside secret")
    (asset_dir / "index.html").symlink_to(outside)
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    complete_setup_wizard(store)

    with TestClient(
        create_audit_app(
            store.path,
            workbench_asset_dir=asset_dir,
            workbench_workspace=tmp_path,
        )
    ) as client:
        response = client.get("/")

    assert response.status_code == 503
    assert "outside secret" not in response.text


def test_workbench_root_never_reads_index_swapped_after_path_validation(
    tmp_path: Path,
    monkeypatch,
):
    asset_dir = tmp_path / "assets"
    asset_dir.mkdir()
    index = asset_dir / "index.html"
    expected = b"<!doctype html><title>Safe workbench</title>"
    index.write_bytes(expected)
    outside = tmp_path / "outside.html"
    outside.write_text("outside secret")
    original_open = audit_web_module._open_workbench_index

    def swap_after_open(path: Path) -> tuple[int, int] | None:
        opened = original_open(path)
        if path == asset_dir and opened is not None:
            index.unlink()
            index.symlink_to(outside)
        return opened

    monkeypatch.setattr(audit_web_module, "_open_workbench_index", swap_after_open)
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    complete_setup_wizard(store)

    with TestClient(
        create_audit_app(
            store.path,
            workbench_asset_dir=asset_dir,
            workbench_workspace=tmp_path,
        )
    ) as client:
        response = client.get("/")

    assert response.status_code == 200
    assert response.content == expected
    assert b"outside secret" not in response.content


def test_fastapi_app_records_feedback_and_redirects(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    attempt_id = seed_attempt(store)
    app = create_audit_app(store.path)
    client = loopback_test_client(app)

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
    assert "无需操作" in html
    assert f'action="/attempts/{attempt_id}/rerun?return_to=/attempts/{attempt_id}"' not in html
    assert f'action="/attempts/{attempt_id}/recall?return_to=/attempts/{attempt_id}"' not in html
    assert "/open-dingtalk-popup?conversation_id=cid-1" in html
    assert "window.open(this.href,'ceo-open-dingtalk','popup,width=420,height=260')" in html
    assert 'class="compact-button open-dingtalk-action"' in html
    assert '<button class="rerun" type="submit">重新处理</button>' not in html
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
    assert "无需操作" in html
    assert f'action="/attempts/{attempt_id}/rerun?return_to=/codex/session-1"' not in html
    assert f'action="/attempts/{attempt_id}/recall?return_to=/codex/session-1"' not in html
    assert "/open-dingtalk-popup?conversation_id=cid-1" in html
    assert "查看钉钉消息" in html
    assert "@Alex Chen 这个怎么处理？" in html
    assert '<details class="event event-assistant" open>' in html
    assert '<details class="event event-session">' in html
    assert '<time>2026-05-14T12:00:01Z</time>' in html

    status, attempt_execution_html = render_codex_session_detail(
        "session-1",
        codex_home=codex_home,
        store=store,
        expose_session_metadata=False,
    )

    assert status == 200
    assert "执行记录" in attempt_execution_html
    assert "session-1" not in attempt_execution_html
    assert str(session_path) not in attempt_execution_html
    assert "已加载 1 条执行记录。" in attempt_execution_html


def test_render_codex_session_detail_returns_404_when_missing(tmp_path: Path):
    status, html = render_codex_session_detail("missing", codex_home=tmp_path)

    assert status == 404
    assert "Codex session not found" in html

    status, html = render_codex_session_detail(
        "missing",
        codex_home=tmp_path,
        expose_session_metadata=False,
    )

    assert status == 404
    assert "执行记录不可用" in html
    assert "missing" not in html


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

    status, html = render_codex_session_detail(
        "missing-session",
        codex_home=tmp_path,
        store=store,
        expose_session_metadata=False,
    )

    assert status == 200
    assert "执行记录不可用" in html
    assert "missing-session" not in html
    assert "Related history" in html


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
    assert "这条回复已发送，无需你操作。" in html
    assert f'action="/attempts/{attempt_id}/recall?return_to=/attempts/{attempt_id}"' not in html
    assert "recall-card" not in html


def test_render_attempt_detail_shows_rerun_only_in_banner_actions(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    attempt_id = seed_attempt(store)
    store.update_reply_attempt(
        attempt_id,
        send_status="failed",
        send_error="temporary failure",
    )

    status, html = render_attempt_detail(store, attempt_id)

    assert status == 200
    assert "attempt-banner-actions" in html
    assert f'action="/attempts/{attempt_id}/rerun?return_to=/attempts/{attempt_id}"' in html
    assert '<button class="rerun" type="submit">重新处理</button>' in html
    assert "rerun-card" not in html


def test_render_attempt_detail_returns_404_when_missing(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")

    status, html = render_attempt_detail(store, 99)

    assert status == 404
    assert "Attempt not found" in html


def test_render_attempt_detail_shows_later_oa_result_instead_of_stale_blocked_pill(
    tmp_path: Path,
):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    blocked_id = store.record_reply_attempt(
        conversation_id="cid-1",
        conversation_title="贾金鹏",
        trigger_message_id="msg-1",
        trigger_sender="贾金鹏",
        trigger_text="这个你确认好了吗？",
        action="oa_approval",
        sensitivity_kind="general",
        oa_process_instance_id="process-1",
        oa_task_id="task-1",
        oa_action="退回",
        oa_remark="请补齐材料后重新提交",
        send_status="blocked",
    )
    later_id = store.record_reply_attempt(
        conversation_id="cid-1",
        conversation_title="贾金鹏",
        trigger_message_id="msg-1",
        trigger_sender="贾金鹏",
        trigger_text="这个你确认好了吗？",
        action="oa_approval",
        sensitivity_kind="general",
        oa_process_instance_id="process-1",
        oa_task_id="task-1",
        oa_action="comment",
        oa_remark="请补齐材料后重新提交",
        send_status="commented",
    )

    status, html = render_attempt_detail(store, blocked_id)

    assert status == 200
    reply_meta = html[
        html.index('<div class="reply-meta">') : html.index("</div><h2>Trigger")
    ]
    assert "💬 Blocked" not in reply_meta
    assert f'href="/attempts/{later_id}"' in reply_meta
    assert f"已由 #{later_id} 后续处理" in reply_meta
    assert "💬 Commented" in reply_meta
    assert "🧾 comment" in reply_meta


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


def test_history_human_decision_accepts_failed_attempt_and_redirects_to_history(
    tmp_path: Path,
):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    store.upsert_conversation(
        "cid-history-decision",
        title="HR",
        single_chat=False,
        codex_session_id="",
    )
    store.enqueue_reply_task(
        conversation_id="cid-history-decision",
        conversation_title="HR",
        single_chat=False,
        trigger_message_id="msg-history-decision",
        trigger_create_time="2026-08-11 05:00:00",
        trigger_sender="Mina",
        trigger_text="Please decide.",
        trigger_message_json="{}",
    )
    task = store.claim_reply_tasks(limit=1)[0]
    store.fail_reply_task(
        task.id,
        "decision required",
        expected_execution_generation=task.execution_generation,
    )
    source_id = store.record_reply_attempt(
        conversation_id=task.conversation_id,
        conversation_title=task.conversation_title,
        trigger_message_id=task.trigger_message_id,
        trigger_sender=task.trigger_sender,
        trigger_text=task.trigger_text,
        action="agent_run",
        sensitivity_kind="general",
        audit_summary="A manager decision is required.",
        send_status="failed",
    )

    status, headers, body = handle_needs_human_decision_post(
        store,
        source_id,
        "instruction=暂不处理".encode(),
        return_to="/",
    )

    source = store.get_reply_attempt(source_id)
    requeued = store.get_reply_task(task.id)
    assert status == 303
    assert headers["Location"] == "/"
    assert body == ""
    assert source is not None and source.send_status == "decision_selected"
    assert requeued is not None and requeued.id == task.id
    assert requeued.status == "pending"

    attempt_count = store.count_reply_attempts()
    generation = requeued.execution_generation
    repeated_status, repeated_headers, _ = handle_needs_human_decision_post(
        store,
        source_id,
        "instruction=暂不处理".encode(),
        return_to="/",
    )
    repeated_task = store.get_reply_task(task.id)

    assert repeated_status == 303
    assert repeated_headers["Location"] == "/"
    assert store.count_reply_attempts() == attempt_count
    assert repeated_task is not None
    assert repeated_task.execution_generation == generation


def test_history_human_decision_rejects_unknown_external_effect(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    store.enqueue_reply_task(
        conversation_id="cid-unknown-decision",
        conversation_title="Operations",
        single_chat=False,
        trigger_message_id="msg-unknown-decision",
        trigger_create_time="2026-08-11 05:00:00",
        trigger_sender="Mina",
        trigger_text="Please decide.",
        trigger_message_json="{}",
    )
    task = store.claim_reply_tasks(limit=1)[0]
    run = _claim_audit_run(store, task).run
    store.mark_agent_run_unknown(
        run.id,
        {"code": "effect_completion_missing"},
        owner="worker",
    )
    source_id = store.finalize_orchestrated_reply_task(
        task_id=task.id,
        expected_execution_generation=task.execution_generation,
        run_id=run.id,
        task_status="failed",
        task_error="effect completion unknown",
        available_at="",
        conversation_id=task.conversation_id,
        conversation_title=task.conversation_title,
        trigger_message_id=task.trigger_message_id,
        trigger_sender=task.trigger_sender,
        trigger_text=task.trigger_text,
        codex_reason="effect completion unknown",
        codex_session_id="",
        codex_transcript_start_line=0,
        codex_transcript_end_line=0,
        audit_tool_events_json="[]",
        audit_summary="effect completion unknown",
        send_status="failed",
        send_error="effect completion unknown",
        channel="dingtalk",
    )

    status, _, _ = handle_needs_human_decision_post(
        store,
        source_id,
        "instruction=暂不处理".encode(),
        return_to="/",
    )

    assert status == 409
    assert store.get_reply_attempt(source_id).send_status == "failed"


def test_agent_run_resolution_api_accepts_only_structured_resolution(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    store.enqueue_reply_task(
        conversation_id="cid-1",
        conversation_title="Friday",
        single_chat=False,
        trigger_message_id="msg-1",
        trigger_create_time="2026-07-29 09:00:00",
        trigger_sender="Mina",
        trigger_text="请处理",
    )
    task = store.claim_reply_tasks(1)[0]
    run = _claim_audit_run(store, task).run
    store.mark_agent_run_unknown(run.id, {"code": "unknown"}, owner="worker")
    store.claim_unknown_agent_run(run.id, owner="reconciler")
    store.defer_unknown_agent_run_reconciliation(
        run.id,
        {"code": "needs_human", "retryable": False},
        owner="reconciler",
        expected_execution_generation=task.execution_generation,
        next_attempt_at="",
        suspended=True,
    )
    client = TestClient(
        create_audit_app(store.path),
        client=("127.0.0.1", 50000),
        headers={"Host": "127.0.0.1:8765"},
    )

    response = client.post(
        f"/agent-runs/{run.id}/resolution",
        json={
            "execution_generation": task.execution_generation,
            "resolution": "confirmed_occurred",
            "reason": "已核对执行回执",
            "actor": "untrusted-client-value",
        },
    )

    assert response.status_code == 200
    assert response.json()["resolution"] == "confirmed_occurred"
    assert store.get_reply_task(task.id).status == "done"
    attempt = store.get_reply_attempt(response.json()["attempt_id"])
    assert attempt is not None
    assert "untrusted-client-value" not in attempt.audit_summary


def test_suspended_unknown_run_exposes_safe_resolution_choices_in_history(
    tmp_path: Path,
):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    store.enqueue_reply_task(
        conversation_id="cid-suspended",
        conversation_title="Operations",
        single_chat=False,
        trigger_message_id="msg-suspended",
        trigger_create_time="2026-08-17 09:00:00",
        trigger_sender="Mina",
        trigger_text="请处理并确认结果。",
        trigger_message_json="{}",
    )
    task = store.claim_reply_tasks(limit=1)[0]
    run = _claim_audit_run(store, task).run
    store.mark_agent_run_unknown(
        run.id,
        {"code": "audit_reconciliation_evidence_mismatch", "retryable": True},
        owner="worker",
    )
    with sqlite3.connect(store.path) as db:
        db.execute(
            "update agent_runs set reconciliation_event_count=? where id=?",
            (MAX_RECONCILIATION_EVENTS, run.id),
        )

    assert store.suspend_reconciliation_event_limited_agent_runs() == 1
    attempt = store.get_latest_reply_attempt_for_trigger(
        task.conversation_id,
        task.trigger_message_id,
    )
    assert attempt is not None

    history_html = render_attempt_list(store)
    assert "确认已执行" in history_html
    assert "确认未执行" in history_html
    assert "无法确认并停止" in history_html
    assert "重试当前任务" not in history_html
    assert f'/agent-runs/{run.id}/resolution-form' in history_html

    status, detail_html = render_attempt_detail(store, attempt.id)
    assert status == 200
    assert "需要你确认外部结果" in detail_html
    assert "确认已执行" in detail_html
    assert "确认未执行" in detail_html
    assert "无法确认并停止" in detail_html
    assert "其他处理指令" not in detail_html

    client = loopback_test_client(create_audit_app(store.path))
    response = client.post(
        f"/agent-runs/{run.id}/resolution-form",
        data={
            "execution_generation": task.execution_generation,
            "resolution": "confirmed_not_occurred",
            "reason": "已回读外部系统，确认动作未发生",
            "return_to": "/history",
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/history"
    assert store.get_agent_run(run.id).side_effect_state == "none"
    assert store.get_reply_task(task.id).status == "pending"


def test_agent_run_resolution_handler_rejects_free_text_without_enum(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    with pytest.raises(ValueError, match="invalid manual reconciliation resolution"):
        handle_agent_run_resolution_post(
            store,
            {
                "run_id": 1,
                "execution_generation": "initial",
                "resolution": "看起来应该成功了",
                "reason": "备注",
            },
        )


def test_agent_run_resolution_api_rejects_non_loopback_client(tmp_path: Path):
    client = TestClient(
        create_audit_app(tmp_path / "audit.sqlite3"),
        client=("192.0.2.10", 50000),
    )

    response = client.post(
        "/agent-runs/1/resolution",
        json={
            "execution_generation": "initial",
            "resolution": "confirmed_occurred",
            "reason": "已核对",
        },
    )

    assert response.status_code == 403


def test_agent_run_resolution_api_rejects_text_plain_csrf(tmp_path: Path):
    client = TestClient(
        create_audit_app(tmp_path / "audit.sqlite3"),
        client=("127.0.0.1", 50000),
    )

    response = client.post(
        "/agent-runs/1/resolution",
        content='{"execution_generation":"initial"}',
        headers={
            "Content-Type": "text/plain",
            "Origin": "https://attacker.example",
        },
    )

    assert response.status_code == 403


def test_reviewed_reply_api_rejects_cross_origin_browser_request(tmp_path: Path):
    client = TestClient(
        create_audit_app(tmp_path / "audit.sqlite3"),
        client=("127.0.0.1", 50000),
    )

    response = client.post(
        "/messages/reviewed-reply",
        json={"attempt_id": 1, "reply_text": "reviewed"},
        headers={"Origin": "https://attacker.example"},
    )

    assert response.status_code == 403


def test_all_audit_mutations_reject_external_origin_even_with_forged_host(
    tmp_path: Path,
    monkeypatch,
):
    env_path = tmp_path / ".env"
    env_path.write_text("CEO_WORKSPACE=/tmp/original\n", encoding="utf-8")
    monkeypatch.setenv("CEO_ENV_FILE", str(env_path))
    client = TestClient(
        create_audit_app(tmp_path / "audit.sqlite3"),
        client=("127.0.0.1", 50000),
    )

    response = client.post(
        "/config/system",
        content="system_key=CEO_WORKSPACE&system_value=/tmp/changed",
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "Host": "attacker.example",
            "Origin": "https://attacker.example",
        },
    )

    assert response.status_code == 403
    assert env_path.read_text(encoding="utf-8") == "CEO_WORKSPACE=/tmp/original\n"


def test_audit_get_rejects_non_loopback_host(tmp_path: Path):
    client = TestClient(
        create_audit_app(tmp_path / "audit.sqlite3"),
        client=("127.0.0.1", 50000),
    )

    response = client.get("/config", headers={"Host": "attacker.example"})

    assert response.status_code == 403


def test_audit_get_accepts_loopback_host(tmp_path: Path):
    client = TestClient(
        create_audit_app(tmp_path / "audit.sqlite3"),
        client=("127.0.0.1", 50000),
    )

    response = client.get("/config", headers={"Host": "127.0.0.1:8765"})

    assert response.status_code == 200


def test_audit_mutation_accepts_loopback_origin(tmp_path: Path, monkeypatch):
    env_path = tmp_path / ".env"
    env_path.write_text("CEO_WORKSPACE=/tmp/original\n", encoding="utf-8")
    monkeypatch.setenv("CEO_ENV_FILE", str(env_path))
    monkeypatch.setenv("CEO_WORKSPACE", "/tmp/original")
    client = TestClient(
        create_audit_app(tmp_path / "audit.sqlite3"),
        client=("127.0.0.1", 50000),
    )

    response = client.post(
        "/config/system",
        content="system_key=CEO_WORKSPACE&system_value=/tmp/changed",
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "Host": "127.0.0.1:8765",
            "Origin": "http://127.0.0.1:8765",
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert "CEO_WORKSPACE=/tmp/changed" in env_path.read_text(encoding="utf-8")


def test_agent_run_resolution_api_rejects_stale_generation(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "audit.sqlite3")
    store.enqueue_reply_task(
        conversation_id="cid-1",
        conversation_title="Friday",
        single_chat=False,
        trigger_message_id="msg-1",
        trigger_create_time="2026-07-29 09:00:00",
        trigger_sender="Mina",
        trigger_text="请处理",
    )
    task = store.claim_reply_tasks(1)[0]
    run = _claim_audit_run(store, task).run
    store.mark_agent_run_unknown(run.id, {"code": "unknown"}, owner="worker")
    store.claim_unknown_agent_run(run.id, owner="reconciler")
    store.defer_unknown_agent_run_reconciliation(
        run.id,
        {"code": "needs_human", "retryable": False},
        owner="reconciler",
        expected_execution_generation=task.execution_generation,
        next_attempt_at="",
        suspended=True,
    )
    app = create_audit_app(db_path=store.path)

    response = TestClient(
        app,
        client=("127.0.0.1", 50000),
        headers={"Host": "127.0.0.1:8765"},
    ).post(
        f"/agent-runs/{run.id}/resolution",
        json={
            "execution_generation": "stale-generation",
            "resolution": "confirmed_not_occurred",
            "reason": "operator verified no effect",
            "actor": "operator@example.com",
        },
    )

    assert response.status_code == 409
    assert store.get_agent_run(run.id).status == "unknown"


def test_handle_rerun_attempt_post_preserves_wechat_channel_without_conversation(
    tmp_path: Path,
):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    trigger = WechatMessage(
        account_id="acct-1",
        conversation_id="melody115",
        message_id="wx-1",
        sender_id="melody115",
        sender_display_name="Melody",
        conversation_type="direct",
        direction="inbound",
        sent_at="2026-07-28T14:00:00+08:00",
        kind="text",
        text="最新问题",
        source_version="4.1.10",
    )
    store.enqueue_reply_task(
        channel="wechat",
        conversation_id=trigger.conversation_id,
        conversation_title="Melody",
        single_chat=True,
        trigger_message_id=trigger.message_id,
        trigger_create_time=trigger.sent_at,
        trigger_sender=trigger.sender_display_name,
        trigger_text=trigger.text,
        trigger_message_json=trigger.model_dump_json(),
    )
    attempt_id = store.record_reply_attempt(
        channel="wechat",
        conversation_id=trigger.conversation_id,
        conversation_title="Melody",
        trigger_message_id=trigger.message_id,
        trigger_sender=trigger.sender_display_name,
        trigger_text=trigger.text,
        action="send_reply",
        sensitivity_kind="normal",
        send_status="failed",
    )
    store.update_reply_attempt(
        attempt_id,
        send_status="failed",
        send_error="target_binding_unverified",
    )

    status, headers, html = handle_rerun_attempt_post(store, attempt_id)

    assert status == 303
    assert headers["Location"] == f"/attempts/{attempt_id}"
    assert html == ""
    task = store.get_reply_task_for_message(
        trigger.conversation_id, trigger.message_id, channel="wechat",
    )
    assert task is not None
    assert task.channel == "wechat"
    assert task.status == "pending"
    assert task.force_new_decision is True
    assert WechatMessage.model_validate_json(task.trigger_message_json) == trigger


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


def test_handle_reviewed_message_reply_uses_immutable_attempt_binding(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    store.enqueue_reply_task(
        conversation_id="cid-stable",
        conversation_title="同名群",
        single_chat=False,
        trigger_message_id="msg-stable",
        trigger_create_time="2026-07-30 09:00:00",
        trigger_sender="Mina",
        trigger_text="重复正文",
    )
    attempt_id = store.record_reply_attempt(
        conversation_id="cid-stable",
        conversation_title="同名群",
        trigger_message_id="msg-stable",
        trigger_sender="Mina",
        trigger_text="重复正文",
        action="send_reply",
        sensitivity_kind="normal",
    )

    result = handle_reviewed_message_reply(
        store,
        attempt_id=attempt_id,
        reply_text="按审核意见重跑",
        reviewer_feedback="使用稳定消息身份",
    )

    task = store.get_reply_task_for_message("cid-stable", "msg-stable")
    reviewed_attempt = store.get_reply_attempt(result["attempt_id"])
    assert reviewed_attempt is not None
    assert reviewed_attempt.conversation_id == "cid-stable"
    assert reviewed_attempt.trigger_message_id == "msg-stable"
    assert task is not None
    assert task.manual_rerun_attempt_id == result["attempt_id"]
    assert task.status == "pending"


def test_needs_human_decision_accepts_only_explicit_judgment_instruction(
    tmp_path: Path,
):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    store.enqueue_reply_task(
        conversation_id="cid-1",
        conversation_title="技术部",
        single_chat=False,
        trigger_message_id="msg-1",
        trigger_create_time="2026-08-04 09:00:00",
        trigger_sender="Mina",
        trigger_text="这个应该怎么处理？",
        trigger_message_json=DingTalkMessage(
            open_conversation_id="cid-1",
            open_message_id="msg-1",
            conversation_title="技术部",
            single_chat=False,
            sender_name="Mina",
            create_time="2026-08-04 09:00:00",
            content="这个应该怎么处理？",
        ).model_dump_json(),
        oa_url="https://aflow.dingtalk.com/detail?procInstId=proc-1&taskId=task-1",
    )
    attempt_id = store.record_reply_attempt(
        conversation_id="cid-1",
        conversation_title="技术部",
        trigger_message_id="msg-1",
        trigger_sender="Mina",
        trigger_text="这个应该怎么处理？",
        action="agent_run",
        sensitivity_kind="general",
        codex_reason="目标和范围存在实际歧义。",
        audit_summary="目标和范围存在实际歧义。",
        send_status="needs_human",
        oa_url="https://aflow.dingtalk.com/detail?procInstId=proc-1&taskId=task-1",
    )
    store.update_reply_attempt(attempt_id, send_error="needs_human")

    status, html = render_attempt_detail(store, attempt_id)
    assert status == 200
    assert "需要你的判断" in html
    assert "事项：</strong>这个应该怎么处理？" in html
    assert "目标和范围存在实际歧义" in html
    assert "按当前事实继续处理并发布" not in html
    assert "先追问一个具体澄清问题并发布" not in html
    assert "其他处理指令" in html

    status, headers, body = handle_needs_human_decision_post(
        store,
        attempt_id,
        "instruction=采用方案二并说明交付边界".encode(),
    )

    source = store.get_reply_attempt(attempt_id)
    restarted_store = AutoReplyStore(store.path)
    task = restarted_store.get_reply_task_for_message("cid-1", "msg-1")
    selected_attempt = store.get_reply_attempt(int(headers["Location"].rsplit("/", 1)[-1]))
    assert status == 303
    assert body == ""
    assert source is not None
    assert source.send_status == "decision_selected"
    assert "Human decision for source attempt" in source.reviewer_feedback
    assert task is not None
    assert task.status == "pending"
    assert task.oa_url == "https://aflow.dingtalk.com/detail?procInstId=proc-1&taskId=task-1"
    assert selected_attempt is not None
    assert selected_attempt.reviewer_feedback == source.reviewer_feedback
    assert "采用方案二并说明交付边界" in selected_attempt.reviewer_feedback

    wechat_attempt_id = store.record_reply_attempt(
        conversation_id="wechat-cid-1",
        conversation_title="WeChat test",
        trigger_message_id="wechat-msg-1",
        trigger_sender="Mina",
        trigger_text="这个应该怎么处理？",
        action="agent_run",
        sensitivity_kind="general",
        codex_reason="目标和范围存在实际歧义。",
        audit_summary="目标和范围存在实际歧义。",
        send_status="needs_human",
        channel="wechat",
    )
    status, html = render_attempt_detail(store, wechat_attempt_id)
    assert status == 200
    assert "需要你选择" not in html


def test_needs_human_detail_renders_agent_supplied_choices(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    attempt_id = store.record_reply_attempt(
        conversation_id="cid-choice",
        conversation_title="管理群",
        trigger_message_id="msg-choice",
        trigger_sender="Mina",
        trigger_text="这个事项请确认。",
        action="agent_run",
        sensitivity_kind="general",
        audit_summary="两个管理决策都会改变外部状态。",
        send_status="needs_human",
    )
    attempt = store.get_reply_attempt(attempt_id)
    assert attempt is not None
    run = AgentRun.model_validate(
        {
            "id": 1,
            "reply_task_id": 1,
            "execution_generation": "initial",
            "role": "consumer",
            "proposal_revision": 0,
            "turn_attempt": 0,
            "parent_agent_run_id": None,
            "operation_id": "",
            "status": "completed",
            "final_result_json": json.dumps(
                {
                    "outcome": "needs_human",
                    "summary": "需要管理判断。",
                    "proposal": None,
                    "decision_options": [
                        {
                            "key": "A",
                            "label": "同意当前方案",
                            "instruction": "同意已核验方案并发布。",
                            "consequence": "会执行已审计的外部动作。",
                        },
                        {
                            "key": "B",
                            "label": "要求补充材料",
                            "instruction": "要求补充材料并发布。",
                            "consequence": "当前外部动作不会执行。",
                        },
                    ],
                    "error": {
                        "code": "decision_required",
                        "retryable": False,
                        "authorization_required": False,
                    },
                }
            ),
            "created_at": "2026-08-11 10:00:00",
            "updated_at": "2026-08-11 10:00:00",
        }
    )

    html = audit_web_module._needs_human_decision_card(attempt, [run])

    assert "A. 同意当前方案" in html
    assert "B. 要求补充材料" in html
    assert "会执行已审计的外部动作。" in html
    assert 'name="instruction" value="同意已核验方案并发布。"' in html


def test_needs_human_detail_renders_audit_supplied_choices(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    attempt_id = store.record_reply_attempt(
        conversation_id="cid-audit-choice",
        conversation_title="管理群",
        trigger_message_id="msg-audit-choice",
        trigger_sender="Mina",
        trigger_text="这个执行冲突请确认。",
        action="agent_run",
        sensitivity_kind="general",
        audit_summary="实时状态与此前回执冲突，需要管理判断。",
        send_status="needs_human",
    )
    attempt = store.get_reply_attempt(attempt_id)
    assert attempt is not None
    run = AgentRun.model_validate(
        {
            "id": 2,
            "reply_task_id": 1,
            "execution_generation": "initial",
            "role": "audit",
            "proposal_revision": 0,
            "turn_attempt": 0,
            "parent_agent_run_id": 1,
            "operation_id": "op-1",
            "status": "completed",
            "final_result_json": json.dumps(
                {
                    "outcome": "needs_human",
                    "summary": "实时状态与此前回执冲突，需要管理判断。",
                    "proposal_revision": 0,
                    "side_effect_state": "none",
                    "feedback": None,
                    "external_result": None,
                    "reconciliation": [],
                    "decision_options": [
                        {
                            "key": "A",
                            "label": "恢复到已确认位置",
                            "instruction": "把材料恢复到此前已确认的位置。",
                            "consequence": "会执行一次经过审计的位置调整。",
                        },
                        {
                            "key": "B",
                            "label": "保持当前状态",
                            "instruction": "保持当前状态并结束本事项。",
                            "consequence": "不会执行新的外部动作。",
                        },
                    ],
                    "error": {
                        "code": "live_state_conflict",
                        "retryable": False,
                        "authorization_required": False,
                    },
                }
            ),
            "created_at": "2026-08-18 10:00:00",
            "updated_at": "2026-08-18 10:00:00",
        }
    )

    html = audit_web_module._needs_human_decision_card(attempt, [run])

    assert "A. 恢复到已确认位置" in html
    assert "B. 保持当前状态" in html
    assert "不会执行新的外部动作。" in html


def test_needs_human_detail_prefers_options_persisted_on_actionable_attempt(
    tmp_path: Path,
):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    attempt_id = store.record_reply_attempt(
        conversation_id="cid-reopened-choice",
        conversation_title="管理群",
        trigger_message_id="msg-reopened-choice",
        trigger_sender="Mina",
        trigger_text="完成后核验发现状态冲突。",
        action="agent_run",
        sensitivity_kind="general",
        audit_summary="原动作已完成，但实时状态后来发生变化。",
        send_status="needs_human",
        human_decision_options_json=json.dumps(
            [
                {
                    "key": "A",
                    "label": "恢复已确认状态",
                    "instruction": "恢复到此前已确认的状态。",
                    "consequence": "会执行一次经过审计的恢复动作。",
                },
                {
                    "key": "B",
                    "label": "保持当前状态",
                    "instruction": "保持当前状态并关闭事项。",
                    "consequence": "不会执行新的外部动作。",
                },
            ]
        ),
    )
    attempt = store.get_reply_attempt(attempt_id)
    assert attempt is not None

    html = audit_web_module._needs_human_decision_card(attempt, [])

    assert "A. 恢复已确认状态" in html
    assert "B. 保持当前状态" in html
    assert "不会执行新的外部动作。" in html


def test_reviewed_reply_api_rejects_mutable_text_lookup_payload(tmp_path: Path):
    client = TestClient(
        create_audit_app(tmp_path / "worker.sqlite3"),
        client=("127.0.0.1", 50000),
    )

    response = client.post(
        "/messages/reviewed-reply",
        json={
            "group_name": "同名群",
            "user_name": "Mina",
            "message_str": "重复正文",
            "reply_text": "不应按文本反查",
        },
        headers={"Host": "127.0.0.1:8765"},
    )

    assert response.status_code == 409



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


def test_render_workers_page_shows_service_and_queue_status(
    tmp_path: Path,
    monkeypatch,
):
    monkeypatch.setattr(
        audit_web_module,
        "_launchd_service_status",
        lambda label: {
            "label": label,
            "target": "gui/501/com.ceo-agent-service.main",
            "ok": True,
            "state": "running",
            "detail": "running",
            "pid": "12345",
            "runs": "4",
            "initialized": "1",
        },
    )
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    project_id = store.create_work_project(
        title="客户交付",
        category="projects",
        status="active",
        priority="P1",
        risk_level="medium",
    )
    store.enqueue_work_summary_input("reply_attempt", "1", '{"summary":"新增任务"}')
    store.create_follow_up_draft(
        project_id=project_id,
        todo_id=0,
        owner_name="Alex",
        question_text="客户交付进展如何？",
        status="draft",
    )
    attempt_id = store.record_reply_attempt(
        conversation_id="cid-1",
        conversation_title="客户群",
        trigger_message_id="msg-1",
        trigger_sender="Mina",
        trigger_text="请看一下",
        action="send_reply",
        sensitivity_kind="general",
        draft_reply_text="收到",
    )
    store.update_reply_attempt(
        attempt_id,
        send_status="failed",
        send_error="send failed",
    )
    todo_id = store.create_work_todo(
        project_id=project_id,
        title="确认客户验收 ETA",
        owner_user_id="owner-1",
        status="open",
        priority="P1",
        deadline_at="2026-07-01 18:00:00",
    )
    store.create_work_todo_dingtalk_link(
        work_todo_id=todo_id,
        dingtalk_task_id="",
        status="failed",
        last_error="code=TOKEN_VERIFIED_FAILED",
    )

    payload = build_worker_status_payload(store)
    html = render_workers_page(store)

    assert payload["service"]["state"] == "running"
    assert payload["summary"]["pending"] >= 2
    assert payload["summary"]["failed"] >= 1
    assert payload["summary"]["retryable"] >= 1
    assert any(queue["name"] == "Work items" for queue in payload["queues"])
    assert any(queue["name"] == "Follow-ups" for queue in payload["queues"])
    assert "Workers" in html
    assert "Queues" in html
    assert "Attention" in html
    assert "Retryable" in html
    assert "12345" in html
    assert "Work items" in html
    assert "Follow-ups" in html
    assert "send failed" in html
    assert '<span class="nav-item active" aria-current="page">Settings</span>' in html


def test_workers_routes_render_page_and_json(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(
        audit_web_module,
        "_launchd_service_status",
        lambda label: {
            "label": label,
            "ok": True,
            "state": "running",
            "detail": "running",
            "pid": "12345",
            "runs": "4",
            "initialized": "1",
        },
    )
    db_path = tmp_path / "worker.sqlite3"
    store = AutoReplyStore(db_path)
    store.enqueue_work_summary_input("reply_attempt", "1", '{"summary":"新增任务"}')
    client = TestClient(create_audit_app(db_path))

    page_response = client.get("/workers")

    assert page_response.status_code == 200
    assert "Workers" in page_response.text
    assert "Status refresh in progress." in page_response.text or "Work items" in page_response.text
    payload = {}
    for _ in range(20):
        api_response = client.get("/api/workers/status")
        assert api_response.status_code == 200
        payload = api_response.json()
        if payload["service"]["state"] == "running":
            break
        time.sleep(0.01)
    assert payload["service"]["state"] == "running"
    assert payload["summary"]["pending"] >= 1


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


def test_render_log_list_marks_persisted_error_resolution_resolved(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    store.record_error("cid-1", "msg-1", "reply_task", "temporary failure")
    [error] = store.list_errors()
    store.resolve_errors([error.id], resolution="recovered by queue retry")

    html = render_log_list(store)

    assert "resolved: recovered by queue retry" in html
    assert '<span class="pill status-active">active</span>' not in html


def test_render_log_list_marks_old_unresolved_error_historical(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    with store._connect() as db:
        db.execute(
            """insert into errors (conversation_id, message_id, kind, detail, created_at)
               values ('cid-1', 'msg-1', 'reply_task', 'old temporary failure',
               '2026-01-01 00:00:00')"""
        )

    html = render_log_list(store)

    assert "historical" in html
    assert '<span class="pill status-active">active</span>' not in html
    assert '<span class="pill status-skipped">historical</span>' in html


def test_render_log_list_marks_old_failed_attempt_historical(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    attempt_id = store.record_reply_attempt(
        conversation_id="cid-1",
        conversation_title="历史群",
        trigger_message_id="msg-1",
        trigger_sender="Mina",
        trigger_text="历史读取失败",
        action="agent_run",
        sensitivity_kind="general",
        send_status="failed",
    )
    with store._connect() as db:
        db.execute(
            "update reply_attempts set created_at='2026-01-01 00:00:00', updated_at='2026-01-01 00:00:00' where id=?",
            (attempt_id,),
        )

    html = render_log_list(store)

    assert '<span class="pill status-skipped">historical</span>' in html
    assert '<span class="pill status-failed">failed</span>' not in html


def test_render_log_list_marks_superseded_failed_attempt_recovered(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    failed_id = store.record_reply_attempt(
        conversation_id="cid-1",
        conversation_title="恢复群",
        trigger_message_id="msg-1",
        trigger_sender="Mina",
        trigger_text="先失败后恢复",
        action="agent_run",
        sensitivity_kind="general",
        send_status="failed",
    )
    store.record_reply_attempt(
        conversation_id="cid-1",
        conversation_title="恢复群",
        trigger_message_id="msg-1",
        trigger_sender="Mina",
        trigger_text="先失败后恢复",
        action="send_reply",
        sensitivity_kind="general",
        send_status="sent",
    )

    html = render_log_list(store)

    assert failed_id
    assert '<span class="pill status-resolved">recovered by later attempt</span>' in html


def test_render_log_list_renders_non_error_terminal_states_without_red_status(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    skipped_id = store.record_reply_attempt(
        conversation_id="cid-1",
        conversation_title="终态群",
        trigger_message_id="msg-1",
        trigger_sender="Mina",
        trigger_text="无需发送",
        action="send_reply",
        sensitivity_kind="general",
        send_status="skipped",
    )
    work_input_id = store.enqueue_work_summary_input(
        "reply_attempt",
        "1",
        '{"summary":"无需汇总"}',
    )
    store.mark_work_summary_input_discarded(work_input_id, "not actionable")

    html = render_log_list(store)

    assert skipped_id
    assert '<span class="pill status-skipped">skipped</span>' in html
    assert '<span class="pill status-skipped">discarded</span>' in html
    assert '<span class="pill status-active">skipped</span>' not in html
    assert '<span class="pill status-active">discarded</span>' not in html


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
    assert '<span class="nav-item active" aria-current="page">Settings</span>' in logs_response.text
    assert errors_response.status_code == 200
    assert "Logs" in errors_response.text
    assert "authorization required" in errors_response.text


def test_audit_app_reuses_initialized_store_across_read_routes(tmp_path: Path, monkeypatch):
    db_path = tmp_path / "worker.sqlite3"
    initialized_store = AutoReplyStore(db_path)
    store_factory_calls = 0

    def audit_store_factory(path: Path) -> AutoReplyStore:
        nonlocal store_factory_calls
        assert path == db_path
        store_factory_calls += 1
        return initialized_store

    monkeypatch.setattr(audit_web_module, "_audit_store", audit_store_factory)
    client = TestClient(create_audit_app(db_path))

    assert client.get("/user-feedback").status_code == 200
    assert client.get("/workers").status_code == 200
    assert store_factory_calls == 1


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
