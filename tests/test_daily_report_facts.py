from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path
import sqlite3

from app.daily_report_facts import collect_daily_report_facts, report_window
from app.email_store import EmailStore
from app.store import AutoReplyStore


REPORT_DATE = date(2026, 9, 24)
# Beijing 2026-09-24 00:30 and 23:59:59, and the instant before the day.
INSIDE_EARLY = "2026-09-23 16:30:00"
INSIDE_LATE = "2026-09-24 15:59:59"
BEFORE = "2026-09-23 15:59:59"


def _set(store: AutoReplyStore, table: str, row_id: int, **values) -> None:
    assignments = ", ".join(f"{column}=?" for column in values)
    with sqlite3.connect(store.path) as db:
        db.execute(
            f"update {table} set {assignments} where id=?",
            (*values.values(), row_id),
        )


def _signal(store: AutoReplyStore, key: str) -> int:
    return store.create_business_task_signal(
        source_type="reply_attempt",
        source_ref=key,
        source_time="2026-09-24T02:00:00Z",
        evidence_text="王明，周五前提交美国客户报价第一版。",
        context_json="{}",
        dedupe_key=key,
    )


def _task(store: AutoReplyStore, title: str, *, last_activity_at: str) -> int:
    return store.create_business_task(
        title=title,
        stage="candidate",
        owner_name="王明",
        last_activity_at=last_activity_at,
    )


def _event(store: AutoReplyStore, task_id: int, reason: str, *, created_at: str) -> None:
    with sqlite3.connect(store.path) as db:
        db.execute(
            """insert into business_task_events
               (task_id, event_type, signal_id, before_json, after_json, reason, created_at)
               values (?, 'details_changed', null, '{}', '{}', ?, ?)""",
            (task_id, reason, created_at),
        )


def _attention(store: AutoReplyStore, key: str, *, category: str, created_at: str) -> int:
    signal_id = _signal(store, f"attention:{key}")
    with store._connect() as db:
        anchor_id = store.create_business_anchor_in_transaction(
            anchor_type="customer", anchor_ref=key, title=f"客户 {key}", _db=db
        )
        return store.create_business_attention_item_in_transaction(
            stable_key=key,
            category=category,
            title=f"{key} 报价",
            business_area="sales",
            why_attention="报价比约定晚了一周",
            current_state="客户在等第一版",
            ceo_action="决定是否降价",
            anchor_id=anchor_id,
            evidence_signal_id=signal_id,
            now=created_at,
            _db=db,
        )


def _attempt(
    store: AutoReplyStore, message_id: str, *, send_status: str, created_at: str, **values
) -> int:
    attempt_id = store.record_reply_attempt(
        conversation_id="cid-1",
        conversation_title="产研群",
        trigger_message_id=message_id,
        trigger_sender="Colleague",
        trigger_text="这个方案要不要改？",
        action="agent_run",
        sensitivity_kind="general",
        send_status=send_status,
        **values,
    )
    _set(store, "reply_attempts", attempt_id, created_at=created_at)
    return attempt_id


def test_report_window_is_one_beijing_day_in_utc() -> None:
    assert report_window(REPORT_DATE) == (
        datetime(2026, 9, 23, 16, tzinfo=UTC),
        datetime(2026, 9, 24, 16, tzinfo=UTC),
    )


def test_empty_store_still_yields_a_complete_fact_document(tmp_path: Path) -> None:
    store = AutoReplyStore(tmp_path / "empty.sqlite3")
    facts = collect_daily_report_facts(store, EmailStore(store.path), REPORT_DATE)

    assert facts["report_date"] == "2026-09-24"
    assert facts["meetings"] == []
    assert facts["tasks_active_today"] == []
    assert facts["business_attention"] == []
    assert facts["important_emails"] == []
    assert facts["handled_today"] == []
    assert facts["waiting_on_derek"] == []
    assert facts["coverage"] == {
        "important_emails": 0,
        "meetings": 0,
        "tasks_active_today": 0,
        "business_attention": 0,
        "handled_today": 0,
        "skipped_today": 0,
        "waiting_on_derek": 0,
    }


def test_only_tasks_active_inside_the_beijing_day_are_reported(tmp_path: Path) -> None:
    store = AutoReplyStore(tmp_path / "tasks.sqlite3")
    today = _task(store, "美国客户报价", last_activity_at="2026-09-23T16:30:00+00:00")
    _task(store, "昨晚最后一次活动", last_activity_at="2026-09-23T15:59:59+00:00")
    _task(store, "明天的活动", last_activity_at="2026-09-24T16:00:00+00:00")
    _event(store, today, "负责人确认周五交付", created_at=INSIDE_LATE)
    _event(store, today, "上周的旧变化", created_at=BEFORE)

    facts = collect_daily_report_facts(store, EmailStore(store.path), REPORT_DATE)

    assert [task["title"] for task in facts["tasks_active_today"]] == ["美国客户报价"]
    task = facts["tasks_active_today"][0]
    assert task["owner"] == "王明"
    assert [event["reason"] for event in task["events_today"]] == ["负责人确认周五交付"]


def test_every_active_business_attention_item_is_reported_and_new_ones_are_marked(
    tmp_path: Path,
) -> None:
    store = AutoReplyStore(tmp_path / "attention.sqlite3")
    _attention(store, "older", category="watch", created_at="2026-09-20T02:00:00+00:00")
    _attention(store, "fresh", category="decision", created_at="2026-09-24T02:00:00+00:00")

    facts = collect_daily_report_facts(store, EmailStore(store.path), REPORT_DATE)

    assert [
        (item["title"], item["category"], item["new_today"])
        for item in facts["business_attention"]
    ] == [("older 报价", "watch", False), ("fresh 报价", "decision", True)]
    assert facts["business_attention"][1]["ceo_action"] == "决定是否降价"


def test_meetings_are_the_sent_follow_ups_of_meetings_that_ended_today(tmp_path: Path) -> None:
    store = AutoReplyStore(tmp_path / "meetings.sqlite3")
    for meeting_id, ended_at in (
        ("today", "2026-09-24T03:00:00+00:00"),
        ("yesterday", "2026-09-23T10:00:00+00:00"),
    ):
        job_id = store.upsert_meeting_alignment_job(
            meeting_id=meeting_id,
            title=f"{meeting_id} 周会",
            source_json="{}",
            participants_json='["Alice"]',
            ended_at=ended_at,
            eligible_at=ended_at,
            status="sent",
        )
        _set(store, "meeting_alignment_jobs", job_id, final_message=f"{meeting_id} 的会后跟进")

    facts = collect_daily_report_facts(store, EmailStore(store.path), REPORT_DATE)

    assert [meeting["meeting_id"] for meeting in facts["meetings"]] == ["today"]
    assert facts["meetings"][0]["follow_up_message"] == "today 的会后跟进"
    assert facts["meetings"][0]["participants"] == ["Alice"]


def test_handled_items_leave_out_no_action_runs_and_list_what_waits_on_derek(
    tmp_path: Path,
) -> None:
    store = AutoReplyStore(tmp_path / "attempts.sqlite3")
    _attempt(store, "m-done", send_status="completed", created_at=INSIDE_EARLY)
    _attempt(store, "m-skip", send_status="skipped", created_at=INSIDE_EARLY)
    waiting = _attempt(
        store,
        "m-human",
        send_status="needs_human",
        created_at=INSIDE_LATE,
        oa_process_instance_id="proc-1",
    )
    _attempt(store, "m-yesterday", send_status="completed", created_at=BEFORE)

    facts = collect_daily_report_facts(store, EmailStore(store.path), REPORT_DATE)

    assert sorted(item["outcome"] for item in facts["handled_today"]) == [
        "completed",
        "needs_human",
    ]
    assert facts["coverage"]["skipped_today"] == 1
    assert [item["attempt_id"] for item in facts["waiting_on_derek"]] == [waiting]


def test_important_emails_are_the_mail_flagged_important_inside_the_day(
    tmp_path: Path,
) -> None:
    class FlaggedMail:
        def __init__(self) -> None:
            self.window: tuple[datetime, datetime] | None = None

        def list_flagged_important_between(self, start, end):
            self.window = (start, end)
            return [{
                "sender": "cong.wang@stardust.ai",
                "subject": "【特批申请】ALE 项目试产专家返修成本申请",
                "category": "finance",
                "preview": "请磊哥审批",
                "received_at": "Thu, 24 Sep 2026 15:30:00 +0800",
                "finished_at": "2026-09-24T07:31:00+00:00",
            }]

    mail = FlaggedMail()
    facts = collect_daily_report_facts(
        AutoReplyStore(tmp_path / "mail.sqlite3"), mail, REPORT_DATE
    )

    assert mail.window == report_window(REPORT_DATE)
    assert [item["subject"] for item in facts["important_emails"]] == [
        "【特批申请】ALE 项目试产专家返修成本申请"
    ]
    assert facts["coverage"]["important_emails"] == 1
