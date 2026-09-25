"""Derek, 2026-09-25: History names every kind of work the service runs.

The type filter used to be a list hard-coded in the page, and the union query
put DingTalk messages, calendar invitations, email replies and scheduled Agent
runs under one value, ``replay``.  Scheduled service-command runs and email
provider actions were not in History at all.
"""

from __future__ import annotations

import json
from pathlib import Path
import sqlite3

import pytest

from app import audit_web, history_types
from app.agent_cron.commands import SERVICE_COMMAND_OPTIONS
from app.agent_cron.scheduler import PREVIOUS_EXECUTION_ACTIVE
from app.dingtalk_models import DingTalkMessage
from app.email_store import EmailStore
from app.store import AutoReplyStore
from app.worker import DingTalkAutoReplyWorker
from tests.test_console_web_api import _client


def _history(client, **params) -> dict:
    response = client.get("/api/console/history", params={"page_size": 100, **params})
    assert response.status_code == 200
    return response.json()


def _attempt(
    store: AutoReplyStore,
    *,
    message_id: str,
    trigger_text: str,
    channel: str = "dingtalk",
    action: str = "agent_run",
    send_status: str = "completed",
    title: str = "Conversation",
) -> None:
    store.record_reply_attempt(
        conversation_id=f"conversation-{message_id}",
        conversation_title=title,
        trigger_message_id=message_id,
        trigger_sender="Sender",
        trigger_text=trigger_text,
        action=action,
        sensitivity_kind="general",
        codex_reason="test",
        audit_summary="test",
        send_status=send_status,
        channel=channel,
    )


def test_the_type_endpoint_lists_the_registry_in_order_with_chinese_labels(
    tmp_path: Path,
):
    AutoReplyStore(tmp_path / "worker.sqlite3")
    with _client(tmp_path) as client:
        response = client.get("/api/console/history/types")

    assert response.status_code == 200
    payload = response.json()
    assert payload["items"] == [
        {"value": "queue", "label": "队列"},
        {"value": "dingtalk", "label": "钉钉消息"},
        {"value": "calendar", "label": "日历邀请"},
        {"value": "approval", "label": "OA 审批"},
        {"value": "email", "label": "邮件"},
        {"value": "email_unsubscribe", "label": "邮件退订"},
        {"value": "email_action", "label": "邮件动作"},
        {"value": "wechat", "label": "微信"},
        {"value": "meeting", "label": "会议跟进"},
        {"value": "task", "label": "Task"},
        {"value": "scheduled_agent", "label": "定时任务"},
        {"value": "scheduled_command", "label": "定时命令"},
    ]
    assert payload["meta"]["total"] == len(payload["items"])
    assert payload["meta"]["snapshot_at"]


def test_every_history_type_the_query_writes_for_a_visible_source_is_listed(
    tmp_path: Path,
):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    EmailStore(store.path)
    query = store._operation_logs_base_query(history_types.HISTORY_SOURCE_TABLES)
    assert "'replay'" not in store._operation_logs_base_query()
    written = {
        value
        for value in history_types.HISTORY_TYPE_VALUES | {history_types.SERVICE_ERROR}
        if f"'{value}'" in query
    }
    # Every visible type except the live queue (read from reply_tasks, not
    # the union) comes out of the visible branches.
    assert written == history_types.HISTORY_TYPE_VALUES - {history_types.QUEUE}
    assert f"'{history_types.SERVICE_ERROR}'" not in query


def test_the_overlap_skip_reason_matches_the_scheduler():
    assert PREVIOUS_EXECUTION_ACTIVE in history_types.SCHEDULED_ROUTINE_SKIP_REASONS


def test_commands_shown_on_success_are_the_ones_without_a_consumer_agent():
    assert set(history_types.SCHEDULED_COMMANDS_SHOWN_ON_SUCCESS) == {
        option.name
        for option in SERVICE_COMMAND_OPTIONS
        if not option.consumer_prompt_enabled
    }
    assert "sync-minutes-once" in history_types.SCHEDULED_COMMANDS_SHOWN_ON_SUCCESS
    assert "produce-once" not in history_types.SCHEDULED_COMMANDS_SHOWN_ON_SUCCESS


CALENDAR_SAMPLES = [
    "[日程] 周会",
    "  [日程] 前面有空格",
    "日程：评审\n详情：dingtalk://dingtalkclient/page/calendar_detail?uniqueId=abc&corpId=x",
    "卡片 redirect_url=https%3A%2F%2Fn.dingtalk.com%3FuniqueId%3Dabc",
    "卡片 newCalendar%3d1 的编码链接",
    "打开 calendarDetail 页面",
    "newCalendar=1",
    "普通消息：明天的日程我再确认一下",
    "uniqueid=abc 大小写不同",
    "",
]


@pytest.mark.parametrize("content", CALENDAR_SAMPLES)
def test_calendar_rule_matches_the_invitation_producer(content: str):
    """The History rule and the producer's own test agree on every card."""
    message = DingTalkMessage(
        open_conversation_id="c",
        open_message_id="m",
        conversation_title="t",
        single_chat=True,
        sender_name="s",
        create_time="2026-09-25 08:00:00",
        content=content,
    )
    expected = DingTalkAutoReplyWorker._is_calendar_message(message)
    db = sqlite3.connect(":memory:")
    db.execute(
        "create table reply_attempts (action text, calendar_event_id text, trigger_text text)"
    )
    db.execute("insert into reply_attempts values ('agent_run', '', ?)", (content,))
    (matched,) = db.execute(
        f"select {history_types.calendar_attempt_sql()} from reply_attempts"
    ).fetchone()
    assert bool(matched) is expected


def test_dingtalk_messages_calendar_invites_and_other_channels_get_their_own_types(
    tmp_path: Path,
):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    _attempt(store, message_id="plain", trigger_text="磊哥，明天的评审能参加吗？")
    _attempt(
        store,
        message_id="invite",
        trigger_text=(
            "日程：1030需求评审会议\n时间：2026-09-24 09:59 - 15:30\n"
            "详情：dingtalk://dingtalkclient/page/calendar_detail?uniqueId=cnhz&corpId=x"
        ),
    )
    _attempt(
        store,
        message_id="scheduled-run",
        trigger_text="生成今天的 CEO 每日总结",
        channel="scheduled",
    )
    _attempt(
        store,
        message_id="email-reply",
        trigger_text="Could we meet next week?",
        channel="email",
        action="send_reply",
        send_status="sent",
    )

    with _client(tmp_path) as client:
        everything = _history(client)
        by_message = {
            item["input"]: item["type"] for item in everything["items"]
        }
        dingtalk = _history(client, object_type="dingtalk")
        calendar = _history(client, object_type="calendar")
        scheduled = _history(client, object_type="scheduled_agent")
        email = _history(client, object_type="email")

    assert set(by_message.values()) == {"dingtalk", "calendar", "scheduled_agent", "email"}
    assert dingtalk["meta"]["total"] == 1
    assert "评审能参加吗" in dingtalk["items"][0]["input"]
    assert calendar["meta"]["total"] == 1
    assert "1030需求评审会议" in calendar["items"][0]["input"]
    assert scheduled["meta"]["total"] == 1
    assert email["meta"]["total"] == 1


def test_a_retired_or_unknown_type_filters_nothing_instead_of_failing(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    _attempt(store, message_id="plain", trigger_text="hello")
    _attempt(store, message_id="wechat", trigger_text="hi", channel="wechat")

    with _client(tmp_path) as client:
        everything = _history(client)
        replay = _history(client, object_type="replay")
        unknown = _history(client, object_type="no-such-type")

    assert everything["meta"]["total"] == 2
    assert replay["meta"]["total"] == 2
    assert unknown["meta"]["total"] == 2


def _scheduled_task(db, *, task_id: int, name: str, command: str) -> None:
    db.execute(
        "insert into scheduled_tasks (id, name, prompt, command, cron_expression, "
        "timezone, runtime_id, enabled) values (?, ?, 'p', ?, '0 * * * *', "
        "'Asia/Shanghai', '', 1)",
        (task_id, name, command),
    )


def _scheduled_run(
    db,
    *,
    task_id: int,
    name: str,
    command: str,
    minute: int,
    status: str,
    summary: str = "",
    reason: str = "",
) -> None:
    at = f"2026-09-25T08:{minute:02d}:00+00:00"
    db.execute(
        "insert into scheduled_task_runs (event_id, scheduled_task_id, trigger_kind, "
        "scheduled_for, first_scheduled_for, dispatch_status, skip_or_error_reason, "
        "snapshot_json, execution_kind, execution_id, created_at, dispatched_at, "
        "result_summary) values (?, ?, 'scheduled', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            f"event-{task_id}-{minute}",
            task_id,
            at,
            at,
            status,
            reason,
            json.dumps({"name": name, "command": command}, ensure_ascii=False),
            "service_command" if status == "dispatched" else "",
            command if status == "dispatched" else "",
            at,
            at if status != "pending" else None,
            summary,
        ),
    )


def test_service_command_runs_appear_with_their_result_and_recovery(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    with store._connect() as db:
        _scheduled_task(db, task_id=7, name="下载新增的钉钉 AI 听记", command="sync-minutes-once")
        _scheduled_task(db, task_id=10, name="分类新邮件", command="email-message-check-once")
        _scheduled_task(db, task_id=1, name="处理新的钉钉消息", command="produce-once")
        _scheduled_run(
            db, task_id=7, name="下载新增的钉钉 AI 听记", command="sync-minutes-once",
            minute=1, status="dispatched",
            summary="sync-minutes-once discovered=3 synced=3 failed=0",
        )
        # Failed, then a later run succeeded: recovered.
        _scheduled_run(
            db, task_id=10, name="分类新邮件", command="email-message-check-once",
            minute=2, status="failed",
            reason="scheduled_task_service_command_failed: imap timeout",
        )
        _scheduled_run(
            db, task_id=10, name="分类新邮件", command="email-message-check-once",
            minute=3, status="dispatched",
            summary="email-message-check-once accounts=1 discovered=0 failures=0",
        )
        # Failed with nothing after it: still failed.
        _scheduled_run(
            db, task_id=10, name="分类新邮件", command="email-message-check-once",
            minute=4, status="failed",
            reason="scheduled_task_service_command_failed: orphan email message",
        )
        # A producer's successful run is not a History row: what it queued is.
        _scheduled_run(
            db, task_id=1, name="处理新的钉钉消息", command="produce-once",
            minute=5, status="dispatched", summary="produce-once queued=0",
        )
        # The overlap guard is bookkeeping, not a result.
        _scheduled_run(
            db, task_id=1, name="处理新的钉钉消息", command="produce-once",
            minute=6, status="skipped", reason=PREVIOUS_EXECUTION_ACTIVE,
        )

    with _client(tmp_path) as client:
        rows = _history(client, object_type="scheduled_command")["items"]
        failed = _history(client, status="failed")["items"]

    by_summary = {row["summary"]: row for row in rows}
    assert len(rows) == 3
    synced = by_summary["sync-minutes-once discovered=3 synced=3 failed=0"]
    assert synced["status"] == "done"
    assert synced["title"] == "下载新增的钉钉 AI 听记"
    assert synced["type"] == "scheduled_command"
    assert synced["kind"] == "scheduled_run"
    assert synced["detail_url"] == "/scheduled-tasks?id=7"
    assert by_summary[
        "scheduled_task_service_command_failed: imap timeout"
    ]["status"] == "recovered"
    assert by_summary[
        "scheduled_task_service_command_failed: orphan email message"
    ]["status"] == "failed"
    assert [row["summary"] for row in failed] == [
        "scheduled_task_service_command_failed: orphan email message"
    ]
    assert not any("produce-once" in row["summary"] for row in rows)
    # Timestamps are in the other sources' form, so they sort together.
    assert synced["occurred_at"] == "2026-09-25 08:01:00"


def _email_action(
    store: AutoReplyStore,
    *,
    number: int,
    status: str,
    action_type: str = "move",
    attempt_count: int = 1,
    next_attempt_at: str = "",
    current: bool = True,
    error: str = "",
) -> None:
    """Insert one processed message, its plan and one provider action.

    Open ``EmailStore(store.path)`` once before the first call: it checks the
    stored plans when it opens.
    """
    classification_id = 9000 + number
    plan_id = f"email-action-plan:{number}"
    with store._connect() as db:
        db.execute(
            "insert into email_classifications ("
            "id, account_id, folder, uidvalidity, uid, rfc_message_id, "
            "stable_message_identity, sender, subject, category, confidence, "
            "margin, probabilities_json, model_id, config_version, status, "
            "classification_source, received_at, current_action_plan_id) "
            "values (?, 'acct', 'INBOX', 2, ?, ?, ?, 'daniela@example.com', "
            "'MorningStar Technical Deepdive', 'work', 0.9, 0.5, '{}', 'model-1', "
            "'v1', 'processed', 'model', 'Mon, 08 Sep 2025 12:08:13 +0000', ?)",
            (
                classification_id,
                number,
                f"<mid-{number}@example.com>",
                f"acct:mid-{number}",
                plan_id if current else "email-action-plan:newer",
            ),
        )
        db.execute(
            "insert into email_action_plans ("
            "action_plan_id, action_plan_version, classification_id, account_id, "
            "category, classification_source, confidence, model_id, config_version, "
            "actions_json, action_parameters_json, created_at) "
            "values (?, 1, ?, 'acct', 'work', 'model', 0.9, 'model-1', 'v1', "
            "'[\"move\"]', '{\"move\":{\"target_folder\":\"工作\"}}', "
            "'2026-09-16T07:27:13+00:00')",
            (plan_id, classification_id),
        )
        db.execute(
            "insert into email_actions ("
            "action_id, action_plan_id, classification_id, account_id, action_type, "
            "parameters_json, config_version, status, attempt_count, next_attempt_at, "
            "provider_operation, provider_target, error, created_at, updated_at) "
            "values (?, ?, ?, 'acct', ?, '{\"target_folder\":\"工作\"}', 'v1', "
            "?, ?, ?, 'MOVE', 'acct:mid', ?, "
            "'2026-09-16T07:27:13+00:00', ?)",
            (
                f"email-action:{number}",
                plan_id,
                classification_id,
                action_type,
                status,
                attempt_count,
                next_attempt_at,
                error,
                f"2026-09-16T07:{number:02d}:00+00:00",
            ),
        )


def test_email_provider_actions_appear_with_the_attention_failure_rule(
    tmp_path: Path,
):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    EmailStore(store.path)
    _email_action(store, number=1, status="done")
    _email_action(store, number=2, status="done", action_type="flag_important")
    # Exhausted: failed in History, as in Attention.
    _email_action(
        store, number=3, status="failed", attempt_count=3,
        error="provider_read_failed:ImapMessageUnavailable",
    )
    # Inside its retry window: the worker will run it again.
    _email_action(
        store, number=4, status="failed", attempt_count=1,
        next_attempt_at="2026-09-16T07:40:00+00:00", error="stale_processing_recovered",
    )
    # A superseded plan's failure will never be executed.
    _email_action(
        store, number=5, status="failed", attempt_count=3, current=False,
        error="provider_read_failed:ImapMessageUnavailable",
    )

    with _client(tmp_path) as client:
        rows = _history(client, object_type="email_action")["items"]
        failed = _history(client, status="failed")["items"]
    attention = audit_web._queue_attention_rows(store)

    statuses = {row["occurred_at"][-5:-3]: row["status"] for row in rows}
    assert statuses == {"01": "done", "02": "done", "03": "failed", "04": "pending", "05": "skipped"}
    first = next(row for row in rows if row["occurred_at"].endswith("07:01:00"))
    assert first["title"] == "MorningStar Technical Deepdive"
    assert first["actor"] == "daniela@example.com"
    assert first["summary"] == "移动到「工作」"
    assert first["type"] == "email_action"
    assert first["kind"] == "email_action"
    assert first["detail_url"] == "/email?tab=all&selected=9001"
    flagged = next(row for row in rows if row["occurred_at"].endswith("07:02:00"))
    assert flagged["summary"] == "标为重要"
    assert [row["summary"] for row in failed] == [
        "移动到「工作」：provider_read_failed:ImapMessageUnavailable"
    ]
    # One failed email action in History, and the same one in Attention.
    assert [row["id"] for row in attention if row["category"] == "Email action"] == [
        "email-action:3"
    ]


def test_history_without_email_tables_still_answers(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    total, rows = store.list_operation_logs_with_count(
        limit=20, source_tables=history_types.HISTORY_SOURCE_TABLES
    )
    assert (total, rows) == (0, [])
    EmailStore(store.path)
    _email_action(store, number=1, status="done")
    total, _rows = store.list_operation_logs_with_count(
        limit=20,
        source_tables=history_types.HISTORY_SOURCE_TABLES,
        _skip_history_cache=True,
    )
    assert total == 1


def test_history_filters_take_several_types_and_statuses(tmp_path: Path):
    # Derek, 2026-09-25: both History menus are checkbox lists.
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    _attempt(store, message_id="plain", trigger_text="磊哥，明天的评审能参加吗？")
    _attempt(store, message_id="failed", trigger_text="另一条消息", send_status="failed")
    _attempt(store, message_id="wechat", trigger_text="hi", channel="wechat")
    _attempt(
        store, message_id="scheduled-run", trigger_text="生成今天的 CEO 每日总结",
        channel="scheduled",
    )

    with _client(tmp_path) as client:
        two_types = _history(client, object_type="wechat,scheduled_agent")
        with_retired = _history(client, object_type="wechat,replay")
        two_statuses = _history(client, status="failed,completed", object_type="dingtalk")

    assert {item["type"] for item in two_types["items"]} == {"wechat", "scheduled_agent"}
    assert two_types["meta"]["total"] == 2
    assert with_retired["meta"]["total"] == 1
    assert {item["status"] for item in two_statuses["items"]} == {"failed", "completed"}
