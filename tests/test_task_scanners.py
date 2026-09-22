import json
import os
from pathlib import Path
from datetime import datetime, timedelta

from app.agent_cron.commands import (
    SERVICE_COMMAND_OPTIONS,
    ServiceCommandConsumerContext,
    ServiceCommandRegistry,
)
from app.dws_client import DwsOaApprovalCandidate
from app.skill_features import FeatureRegistry
from app.store import AutoReplyStore
from app.task_scanners import (
    scan_ai_minutes,
    scan_meeting_todos,
    scan_pending_oa_approvals,
)


def test_disabled_work_tracking_does_not_scan_or_enqueue_ai_minutes(tmp_path):
    store = AutoReplyStore(tmp_path / "scanner.sqlite3")
    registry = FeatureRegistry(state_path=tmp_path / "skill-state.json")
    registry.set_enabled("work_tracking", False)

    class Dws:
        def list_minutes(self):
            raise AssertionError("disabled scanner must not read AI minutes")

    assert scan_ai_minutes(store, Dws(), feature_registry=registry) == 0
    assert store.claim_work_summary_inputs(limit=1) == []


def test_scan_meeting_todos_carries_scheduled_consumer_context(tmp_path):
    class FakeDws:
        def __init__(self):
            self.list_limits = []

        def list_minutes(self, *, limit):
            self.list_limits.append(limit)
            return [{"taskUuid": "minutes-1", "title": "产品会"}]

        def get_minutes_todos(self, task_uuid):
            return {"result": {"actions": ['{"value":"确认发布范围"}']}}

    store = AutoReplyStore(tmp_path / "task.sqlite3")
    context = ServiceCommandConsumerContext(
        scheduled_task_id=7,
        scheduled_task_run_id=11,
        prompt="使用 $ceo-work-tracking 处理真实工作来源。",
        skill_names=("ceo-work-tracking",),
        skill_protocol="# Targeted Work Tracking Skill",
    )
    implementations = {option.name: lambda: "unused" for option in SERVICE_COMMAND_OPTIONS}
    dws = FakeDws()
    implementations["scan-meeting-todos-once"] = lambda: str(
        scan_meeting_todos(store, dws)
    )

    ServiceCommandRegistry(implementations).run(
        "scan-meeting-todos-once",
        consumer_context=context,
    )

    [claimed] = store.claim_work_summary_inputs(limit=10)
    payload = json.loads(claimed.payload_json)
    assert payload["scheduled_consumer"] == context.to_payload()
    assert dws.list_limits == [50]


def test_scan_ai_minutes_enqueues_adapter_items(tmp_path):
    class FakeDws:
        def list_minutes(self):
            return [
                {
                    "taskUuid": "minutes-1",
                    "title": "售前知识库周会",
                    "createdAt": "2026-06-07 09:00:00",
                    "summary": "需要补齐来源链接",
                }
            ]

    store = AutoReplyStore(tmp_path / "task.sqlite3")

    count = scan_ai_minutes(store, FakeDws(), enqueue_existing_on_first_scan=True)

    assert count == 1
    claimed = store.claim_work_summary_inputs(limit=10)
    assert len(claimed) == 1
    assert claimed[0].source_type == "ai_minutes"
    assert claimed[0].source_ref == "minutes-1"
    assert "售前知识库周会" in claimed[0].payload_json


def test_scan_ai_minutes_accepts_live_dws_row_shape(tmp_path):
    class FakeDws:
        def list_minutes(self):
            return [
                {
                    "uuid": "minutes-1",
                    "title": "吴柯欣 - 招聘专员-2026050701 - 三面",
                    "startTimeISO": "2026-06-07T13:24:06+08:00",
                }
            ]

    store = AutoReplyStore(tmp_path / "task.sqlite3")

    count = scan_ai_minutes(store, FakeDws(), enqueue_existing_on_first_scan=True)

    assert count == 1
    claimed = store.claim_work_summary_inputs(limit=10)
    assert len(claimed) == 1
    assert claimed[0].source_ref == "minutes-1"
    assert "2026-06-07T13:24:06+08:00" in claimed[0].payload_json


def test_scan_ai_minutes_walks_paginated_adapter(tmp_path):
    class FakeDws:
        def __init__(self):
            self.tokens = []

        def list_minutes_page(self, *, limit, cursor):
            self.tokens.append((limit, cursor))
            if not cursor:
                return {
                    "items": [
                        {
                            "taskUuid": "minutes-1",
                            "title": "第一页",
                            "createdAt": "2026-08-10T12:00:00+00:00",
                        }
                    ],
                    "has_more": True,
                    "next_token": "token-2",
                }
            return {
                "items": [
                    {
                        "taskUuid": "minutes-2",
                        "title": "第二页",
                        "createdAt": "2026-08-10T10:00:00+00:00",
                    }
                ],
                "has_more": False,
                "next_token": "",
            }

        def list_minutes(self):
            raise AssertionError("paginated adapter should be used")

    dws = FakeDws()
    store = AutoReplyStore(tmp_path / "task.sqlite3")

    store.set_daily_scan_state(
        "ai_minutes",
        last_success_at="2026-08-10T00:00:00+00:00",
        cursor_json=json.dumps(
            {
                "seen_ids": ["minutes-2"],
                "oldest_seen_at": "2026-08-10T10:00:00+00:00",
            }
        ),
    )
    count = scan_ai_minutes(store, dws, enqueue_existing_on_first_scan=True)

    assert count == 1
    assert dws.tokens == [(50, ""), (50, "token-2")]
    claimed = store.claim_work_summary_inputs(limit=10)
    assert {row.source_ref for row in claimed} == {"minutes-1"}


def test_scan_ai_minutes_migrates_id_only_boundary_without_stale_pagination(tmp_path):
    class FakeDws:
        def __init__(self):
            self.tokens = []

        def list_minutes(self):
            raise AssertionError("paginated adapter should be used")

        def list_minutes_page(self, *, limit, cursor):
            self.tokens.append((limit, cursor))
            return {
                "items": [
                    {
                        "taskUuid": "minutes-new",
                        "title": "恢复窗口的新纪要",
                        "createdAt": "2026-08-10T12:00:00+00:00",
                    },
                    {
                        "taskUuid": "minutes-known",
                        "title": "已记录纪要",
                        "createdAt": "2026-08-10T11:00:00+00:00",
                    },
                ],
                "has_more": True,
                "next_token": "stale-cursor",
            }

    dws = FakeDws()
    store = AutoReplyStore(tmp_path / "task.sqlite3")
    store.set_daily_scan_state(
        "ai_minutes",
        last_success_at="2026-08-10T00:00:00+00:00",
        cursor_json=json.dumps({"seen_ids": ["minutes-known"]}),
    )

    assert scan_ai_minutes(store, dws, enqueue_existing_on_first_scan=True) == 1
    assert dws.tokens == [(50, "")]
    assert [row.source_ref for row in store.claim_work_summary_inputs(limit=10)] == [
        "minutes-new"
    ]
    state = json.loads(store.get_daily_scan_state("ai_minutes")["cursor_json"])
    assert state["oldest_seen_at"] == "2026-08-10T11:00:00+00:00"


def test_scan_ai_minutes_baselines_only_latest_page_on_first_scan(tmp_path):
    class FakeDws:
        def __init__(self):
            self.tokens = []

        def list_minutes(self):
            raise AssertionError("paginated adapter should be used")

        def list_minutes_page(self, *, limit, cursor):
            self.tokens.append((limit, cursor))
            return {
                "items": [
                    {
                        "taskUuid": "minutes-1",
                        "title": "最新会议",
                        "createdAt": "2026-08-10T12:00:00+00:00",
                    }
                ],
                "has_more": True,
                "next_token": "older-page",
            }

    dws = FakeDws()
    store = AutoReplyStore(tmp_path / "task.sqlite3")

    assert scan_ai_minutes(store, dws) == 0
    assert dws.tokens == [(50, "")]
    assert json.loads(store.get_daily_scan_state("ai_minutes")["cursor_json"]) == {
        "oldest_seen_at": "2026-08-10T12:00:00+00:00",
        "seen_ids": ["minutes-1"],
    }


def test_scan_ai_minutes_preserves_seen_cursor_when_pagination_fails(tmp_path):
    class FakeDws:
        def list_minutes(self):
            raise AssertionError("paginated adapter should be used")

        def list_minutes_page(self, *, limit, cursor):
            raise RuntimeError("page request failed")

    store = AutoReplyStore(tmp_path / "task.sqlite3")
    state = {"seen_ids": ["minutes-1"]}
    store.set_daily_scan_state(
        "ai_minutes",
        last_success_at="2026-08-10T00:00:00+00:00",
        cursor_json=json.dumps(state),
    )

    assert scan_ai_minutes(store, FakeDws()) == 0
    saved = store.get_daily_scan_state("ai_minutes")
    assert json.loads(saved["cursor_json"]) == state
    assert saved["last_success_at"] == "2026-08-10T00:00:00+00:00"
    assert saved["last_error"] == "page request failed"


def test_scan_ai_minutes_keeps_completed_pages_when_a_later_page_fails(tmp_path):
    class PartialDws:
        def list_minutes_page(self, *, limit, cursor):
            if not cursor:
                return {
                    "items": [{"taskUuid": "minutes-1", "title": "第一页"}],
                    "has_more": True,
                    "next_token": "token-2",
                }
            raise RuntimeError("cursor expired")

    store = AutoReplyStore(tmp_path / "task.sqlite3")

    count = scan_ai_minutes(
        store,
        PartialDws(),
        enqueue_existing_on_first_scan=True,
    )

    assert count == 1
    state = store.get_daily_scan_state("ai_minutes")
    assert state is not None
    assert state["last_error"] == ""
    assert json.loads(state["cursor_json"])["pagination_deferred"] is True


def test_scan_ai_minutes_records_empty_completed_page_before_cursor_failure(tmp_path):
    class EmptyFirstPageDws:
        def list_minutes_page(self, *, limit, cursor):
            if not cursor:
                return {
                    "items": [],
                    "has_more": True,
                    "next_token": "token-2",
                }
            raise RuntimeError("cursor expired")

    store = AutoReplyStore(tmp_path / "task.sqlite3")

    assert scan_ai_minutes(store, EmptyFirstPageDws()) == 0
    state = store.get_daily_scan_state("ai_minutes")
    assert state is not None
    assert state["last_error"] == ""
    assert json.loads(state["cursor_json"])["pagination_deferred"] is True


def test_scan_ai_minutes_baselines_existing_items_on_first_scan(tmp_path):
    class FakeDws:
        def __init__(self):
            self.items = [
                {"taskUuid": "minutes-1", "title": "历史会议"},
            ]

        def list_minutes(self):
            return self.items

    dws = FakeDws()
    store = AutoReplyStore(tmp_path / "task.sqlite3")

    assert scan_ai_minutes(store, dws) == 0
    assert store.claim_work_summary_inputs(limit=10) == []

    dws.items = [
        {"taskUuid": "minutes-1", "title": "历史会议"},
        {"taskUuid": "minutes-2", "title": "新增会议"},
    ]

    assert scan_ai_minutes(store, dws) == 1
    claimed = store.claim_work_summary_inputs(limit=10)
    assert len(claimed) == 1
    assert claimed[0].source_ref == "minutes-2"


def test_scan_ai_minutes_limit_preserves_unqueued_new_items(tmp_path):
    class FakeDws:
        def __init__(self):
            self.items = [
                {"taskUuid": "minutes-1", "title": "历史会议"},
            ]

        def list_minutes(self):
            return self.items

    dws = FakeDws()
    store = AutoReplyStore(tmp_path / "task.sqlite3")
    assert scan_ai_minutes(store, dws) == 0

    dws.items = [
        {"taskUuid": "minutes-1", "title": "历史会议"},
        {"taskUuid": "minutes-2", "title": "新增会议 2"},
        {"taskUuid": "minutes-3", "title": "新增会议 3"},
    ]

    assert scan_ai_minutes(store, dws, max_new_items=1) == 1
    first_claimed = store.claim_work_summary_inputs(limit=10)
    assert [row.source_ref for row in first_claimed] == ["minutes-2"]

    assert scan_ai_minutes(store, dws, max_new_items=1) == 1
    second_claimed = store.claim_work_summary_inputs(limit=10)
    assert [row.source_ref for row in second_claimed] == ["minutes-3"]


def test_scan_ai_minutes_records_unavailable_adapter(tmp_path):
    store = AutoReplyStore(tmp_path / "task.sqlite3")

    count = scan_ai_minutes(store, object())

    assert count == 0
    state = store.get_daily_scan_state("ai_minutes")
    assert state is not None
    assert state["last_error"] == "dws list_minutes unavailable"


def test_scan_ai_minutes_records_adapter_errors(tmp_path):
    class BrokenDws:
        def list_minutes(self):
            raise RuntimeError("auth expired")

    store = AutoReplyStore(tmp_path / "task.sqlite3")

    count = scan_ai_minutes(store, BrokenDws())

    assert count == 0
    state = store.get_daily_scan_state("ai_minutes")
    assert state is not None
    assert state["last_error"] == "auth expired"


def test_scan_meeting_todos_enqueues_only_meetings_with_action_items(tmp_path):
    class FakeDws:
        def list_minutes(self, *, limit):
            assert limit == 50
            return [
                {
                    "taskUuid": "minutes-1",
                    "title": "产品周会",
                    "createdAt": "2026-09-14T09:00:00+08:00",
                },
                {
                    "taskUuid": "minutes-2",
                    "title": "无行动项会议",
                    "createdAt": "2026-09-14T10:00:00+08:00",
                },
            ]

        def get_minutes_todos(self, task_uuid):
            if task_uuid == "minutes-1":
                return {
                    "result": {
                        "actions": [
                            '{"value":"Alex 在周五前确认上线范围"}'
                        ]
                    }
                }
            return {"result": {"actions": []}}

    store = AutoReplyStore(tmp_path / "task.sqlite3")

    assert scan_meeting_todos(store, FakeDws()) == 1

    claimed = store.claim_work_summary_inputs(limit=10)
    assert len(claimed) == 1
    assert claimed[0].source_type == "ai_minutes"
    assert claimed[0].source_ref.startswith("minutes-1#todos-sha256=")
    payload = json.loads(claimed[0].payload_json)
    assert payload["source"]["title"] == "产品周会行动项"
    assert "Alex 在周五前确认上线范围" in payload["summary"]
    assert payload["context"]["source_conversation_kind"] == "minutes"


def test_scan_meeting_todos_requeues_only_when_todos_change(tmp_path):
    class FakeDws:
        def __init__(self):
            self.actions = ['{"value":"第一版行动项"}']
            self.title = "经营会"
            self.request_id = "request-1"

        def list_minutes(self, *, limit):
            assert limit == 50
            return [{"taskUuid": "minutes-1", "title": self.title}]

        def get_minutes_todos(self, task_uuid):
            assert task_uuid == "minutes-1"
            return {
                "request_id": self.request_id,
                "result": {"actions": self.actions},
            }

    dws = FakeDws()
    store = AutoReplyStore(tmp_path / "task.sqlite3")

    assert scan_meeting_todos(store, dws) == 1
    first = store.claim_work_summary_inputs(limit=10)
    assert len(first) == 1
    store.mark_work_summary_input_done(first[0].id)

    assert scan_meeting_todos(store, dws) == 0
    assert store.claim_work_summary_inputs(limit=10) == []

    dws.title = "经营会（标题已修订）"
    dws.request_id = "request-2"
    assert scan_meeting_todos(store, dws) == 0
    assert store.claim_work_summary_inputs(limit=10) == []

    dws.actions = ['{"value":"第二版行动项"}']
    assert scan_meeting_todos(store, dws) == 1
    second = store.claim_work_summary_inputs(limit=10)
    assert len(second) == 1
    assert second[0].source_ref != first[0].source_ref
    assert "第二版行动项" in second[0].payload_json


def test_scan_meeting_todos_does_not_advance_failed_or_deferred_items(tmp_path):
    class FakeDws:
        def __init__(self):
            self.fail_first = True

        def list_minutes(self, *, limit):
            assert limit == 50
            return [
                {"taskUuid": "minutes-1", "title": "读取失败"},
                {"taskUuid": "minutes-2", "title": "超过本轮上限"},
            ]

        def get_minutes_todos(self, task_uuid):
            if task_uuid == "minutes-1" and self.fail_first:
                raise RuntimeError("todo read failed")
            return {"result": {"actions": [f'{{"value":"{task_uuid}"}}']}}

    dws = FakeDws()
    store = AutoReplyStore(tmp_path / "task.sqlite3")

    assert scan_meeting_todos(store, dws, max_new_items=1) == 1
    state = store.get_daily_scan_state("meeting_todos")
    assert state is not None
    assert "minutes-1: todo read failed" in state["last_error"]

    first = store.claim_work_summary_inputs(limit=10)
    assert len(first) == 1
    assert first[0].source_ref.startswith("minutes-2#todos-sha256=")
    store.mark_work_summary_input_done(first[0].id)

    dws.fail_first = False
    assert scan_meeting_todos(store, dws, max_new_items=1) == 1
    second = store.claim_work_summary_inputs(limit=10)
    assert len(second) == 1
    assert second[0].source_ref.startswith("minutes-1#todos-sha256=")


def test_scan_pending_oa_approvals_enqueues_daily_review_task(tmp_path):
    class FakeDws:
        def __init__(self):
            self.pages = []
            self.task_reads = []

        def read_oa_approval_detail(self, process_instance_id):
            raise AssertionError("the broken DWS detail adapter must not be used")

        def list_pending_oa_approvals(self, *, page, size, start, end):
            self.pages.append((page, size, start, end))
            return [
                DwsOaApprovalCandidate(
                    process_instance_id="proc-1",
                    title="张三提交的录用申请",
                    process_name="录用申请",
                )
            ]

        def get_current_user_id(self):
            return "principal-user-1"

        def read_oa_approval_tasks(self, process_instance_id):
            self.task_reads.append(process_instance_id)
            return {
                "result": {
                    "taskIdList": [
                        {"taskId": 102648882814},
                        {"taskId": 102648910080},
                    ]
                }
            }

        def read_oa_process_instance_openapi(self, process_instance_id):
            return {
                "result": {
                    "tasks": [
                        {
                            "taskId": 102648882814,
                            "userId": "other-user",
                            "status": "COMPLETED",
                        },
                        {
                            "taskId": 102648910080,
                            "userId": "principal-user-1",
                            "status": "RUNNING",
                        },
                    ]
                }
            }

    dws = FakeDws()
    store = AutoReplyStore(tmp_path / "task.sqlite3")
    context = ServiceCommandConsumerContext(
        scheduled_task_id=7,
        scheduled_task_run_id=11,
        prompt=(
            "使用 $dingtalk-oa-approval 与 $stardust-oa-finance-review "
            "审阅真实 DingTalk OA。"
        ),
        skill_names=("dingtalk-oa-approval", "stardust-oa-finance-review"),
        skill_protocol="# OA review skills",
    )
    implementations = {
        option.name: (lambda: "unused") for option in SERVICE_COMMAND_OPTIONS
    }
    implementations["scan-oa-approvals"] = lambda: str(
        scan_pending_oa_approvals(
            store,
            dws,
            now=datetime.fromisoformat("2026-07-27T09:30:00+08:00"),
        )
    )

    queued = int(
        ServiceCommandRegistry(implementations).run(
            "scan-oa-approvals",
            consumer_context=context,
        )
    )

    assert queued == 1
    # DingTalk rejects list-pending pages above 20 items (400002 参数错误).
    assert dws.pages == [
        (
            1,
            20,
            "2025-07-27T09:30:00+08:00",
            "2026-07-27T09:30:00+08:00",
        )
    ]
    assert dws.task_reads == ["proc-1"]
    task = store.claim_reply_tasks(limit=1)[0]
    # One conversation per approval: a shared session let a later
    # approval inherit an earlier one's transcript and skip its work.
    assert task.conversation_id == "oa_pending_scan:proc-1"
    assert task.conversation_title == "审批待办"
    assert task.trigger_message_id.startswith("oa-pending:proc-1:")
    assert "张三提交的录用申请" in task.trigger_text
    assert "申请人的最新明确陈述是其申请事实的权威来源" not in task.trigger_text
    assert "审批判断与动作一律以 dingtalk-oa-approval 为准" not in task.trigger_text
    assert "通用 Skill 只定义审批机制与材料核验边界" in task.trigger_text
    assert "live processCode 精确匹配的业务规则卡" in task.trigger_text
    assert "procInstId=proc-1&taskId=102648910080" in task.oa_url
    assert '"source":"oa_pending_scan"' in task.trigger_message_json
    payload = json.loads(task.trigger_message_json)
    assert payload["raw_payload"]["scheduled_consumer"] == context.to_payload()


def test_scan_pending_oa_approvals_closes_completed_oa_needs_human_attempt(tmp_path):
    class FakeDws:
        detail_reads: list[str] = []

        def list_pending_oa_approvals(self, *, page, size, start, end):
            return []

        def read_oa_approval_tasks(self, process_instance_id):
            raise AssertionError("a completed OA instance has no pending task to read")

        def read_oa_process_instance_openapi(self, process_instance_id):
            self.detail_reads.append(process_instance_id)
            return {
                "result": {
                    "status": "COMPLETED",
                    "processInstanceResult": "agree",
                }
            }

    store = AutoReplyStore(tmp_path / "task.sqlite3")
    trigger_message_id = "oa-pending:proc-completed:revision-1"
    store.enqueue_reply_task(
        conversation_id="oa_pending_scan",
        conversation_title="审批待办",
        single_chat=True,
        trigger_message_id=trigger_message_id,
        trigger_create_time="2026-09-10T12:00:00+00:00",
        trigger_sender="Derek OA",
        trigger_text="审批待办扫描",
        oa_url=(
            "https://aflow.dingtalk.com/detail?"
            "procInstId=proc-completed&taskId=task-completed"
        ),
    )
    attempt_id = store.record_reply_attempt(
        conversation_id="oa_pending_scan",
        conversation_title="审批待办",
        trigger_message_id=trigger_message_id,
        trigger_sender="Derek OA",
        trigger_text="审批待办扫描",
        action="agent_run",
        sensitivity_kind="general",
        send_status="needs_human",
    )
    with store._connect() as db:
        db.execute(
            "update reply_tasks set status='done' "
            "where conversation_id=? and trigger_message_id=?",
            ("oa_pending_scan", trigger_message_id),
        )

    dws = FakeDws()
    assert scan_pending_oa_approvals(store, dws) == 0
    assert dws.detail_reads == ["proc-completed"]
    assert store.get_reply_attempt(attempt_id).send_status == "skipped"
    state = store.get_daily_scan_state("oa_pending")
    assert state is not None
    assert json.loads(state["cursor_json"])["reconciled_completed_attempt_ids"] == [
        attempt_id
    ]


def test_scan_pending_oa_approvals_keeps_running_oa_needs_human_attempt(tmp_path):
    class FakeDws:
        def list_pending_oa_approvals(self, *, page, size, start, end):
            return []

        def read_oa_approval_tasks(self, process_instance_id):
            return {"result": {"tasks": []}}

        def read_oa_process_instance_openapi(self, process_instance_id):
            return {"result": {"status": "RUNNING"}}

    store = AutoReplyStore(tmp_path / "task.sqlite3")
    attempt_id = store.record_reply_attempt(
        conversation_id="oa_pending_scan",
        conversation_title="审批待办",
        trigger_message_id="oa-pending:proc-running:revision-1",
        trigger_sender="Derek OA",
        trigger_text="审批待办扫描",
        action="agent_run",
        sensitivity_kind="general",
        oa_process_instance_id="proc-running",
        send_status="needs_human",
    )

    assert scan_pending_oa_approvals(store, FakeDws()) == 0
    assert store.get_reply_attempt(attempt_id).send_status == "needs_human"


def test_scan_pending_oa_approvals_closes_running_process_task_no_longer_pending(
    tmp_path,
):
    class FakeDws:
        def list_pending_oa_approvals(self, *, page, size, start, end):
            return []

        def read_oa_approval_tasks(self, process_instance_id):
            raise AssertionError("the process is no longer pending for the principal")

        def read_oa_process_instance_openapi(self, process_instance_id):
            return {"result": {"status": "RUNNING"}}

    store = AutoReplyStore(tmp_path / "task.sqlite3")
    attempt_id = store.record_reply_attempt(
        conversation_id="oa_pending_scan",
        conversation_title="审批待办",
        trigger_message_id="oa-pending:proc-running:revision-2",
        trigger_sender="Derek OA",
        trigger_text="审批待办扫描",
        action="agent_run",
        sensitivity_kind="general",
        oa_process_instance_id="proc-running",
        oa_task_id="task-old",
        send_status="needs_human",
    )

    assert scan_pending_oa_approvals(store, FakeDws()) == 0
    attempt = store.get_reply_attempt(attempt_id)
    assert attempt is not None
    assert attempt.send_status == "skipped"
    with store._connect() as db:
        resolution = db.execute(
            "select resolution from reply_attempts where id=?",
            (attempt_id,),
        ).fetchone()["resolution"]
    assert "OA_TASK_NOT_PENDING" in resolution


def test_scan_pending_oa_approvals_does_not_close_absent_task_on_partial_scan(
    tmp_path,
):
    class FakeDws:
        def list_pending_oa_approvals(self, *, page, size, start, end):
            return [
                DwsOaApprovalCandidate(
                    process_instance_id="other-process",
                    title="其他审批",
                )
            ]

        def get_current_user_id(self):
            return "principal-user-1"

        def read_oa_approval_tasks(self, process_instance_id):
            return {"result": {"tasks": [{"taskId": "other-task"}]}}

        def read_oa_process_instance_openapi(self, process_instance_id):
            if process_instance_id == "proc-running":
                return {"result": {"status": "RUNNING"}}
            return {
                "result": {
                    "status": "RUNNING",
                    "tasks": [
                        {
                            "taskId": "other-task",
                            "userId": "principal-user-1",
                            "status": "RUNNING",
                        }
                    ],
                }
            }

        def read_oa_approval_records(self, process_instance_id):
            return {"result": {"operationRecords": []}}

    store = AutoReplyStore(tmp_path / "task.sqlite3")
    attempt_id = store.record_reply_attempt(
        conversation_id="oa_pending_scan",
        conversation_title="审批待办",
        trigger_message_id="oa-pending:proc-running:revision-3",
        trigger_sender="Derek OA",
        trigger_text="审批待办扫描",
        action="agent_run",
        sensitivity_kind="general",
        oa_process_instance_id="proc-running",
        oa_task_id="task-old",
        send_status="needs_human",
    )

    scan_pending_oa_approvals(
        store,
        FakeDws(),
        page_size=1,
        max_pages=1,
    )

    assert store.get_reply_attempt(attempt_id).send_status == "needs_human"


def test_scan_pending_oa_approvals_caches_applicant_open_dingtalk_id(tmp_path):
    class Profile:
        user_id = "applicant-user-1"
        name = "张三"
        title = ""
        open_dingtalk_id = "open-applicant-1"
        manager_user_id = None
        manager_name = ""
        department_ids = set()
        department_names = set()
        org_labels = []
        has_subordinate = None

    class FakeDws:
        def list_pending_oa_approvals(self, *, page, size, start, end):
            return [
                DwsOaApprovalCandidate(
                    process_instance_id="proc-1",
                    title="张三提交的申请",
                    process_name="申请",
                )
            ]

        def get_current_user_id(self):
            return "principal-user-1"

        def read_oa_approval_tasks(self, process_instance_id):
            return {"result": {"tasks": [{"taskId": "task-1", "status": "RUNNING"}]}}

        def read_oa_process_instance_openapi(self, process_instance_id):
            return {
                "result": {
                    "originatorUserid": "applicant-user-1",
                    "tasks": [
                        {
                            "taskId": "task-1",
                            "userId": "principal-user-1",
                            "status": "RUNNING",
                        }
                    ],
                }
            }

        def get_user_profiles(self, user_ids):
            assert user_ids == ["applicant-user-1"]
            return [Profile()]

    store = AutoReplyStore(tmp_path / "task.sqlite3")
    assert scan_pending_oa_approvals(store, FakeDws(), now=datetime.fromisoformat(
        "2026-07-27T09:30:00+08:00"
    )) == 1

    task = store.claim_reply_tasks(limit=1)[0]
    assert '"originatorOpenDingTalkId":"open-applicant-1"' in task.trigger_message_json
    profile = store.get_org_user_profile("applicant-user-1")
    assert profile is not None
    assert profile.open_dingtalk_id == "open-applicant-1"


def test_scan_pending_oa_approvals_reuses_cached_applicant_mapping(tmp_path):
    class FakeDws:
        def list_pending_oa_approvals(self, *, page, size, start, end):
            return [DwsOaApprovalCandidate(
                process_instance_id="proc-1", title="张三提交的申请", process_name="申请"
            )]

        def get_current_user_id(self):
            return "principal-user-1"

        def read_oa_approval_tasks(self, process_instance_id):
            return {"result": {"tasks": [{"taskId": "task-1", "status": "RUNNING"}]}}

        def read_oa_process_instance_openapi(self, process_instance_id):
            return {"result": {"originatorUserid": "applicant-user-1", "tasks": [
                {"taskId": "task-1", "userId": "principal-user-1", "status": "RUNNING"}
            ]}}

        def get_user_profiles(self, user_ids):
            return []

    store = AutoReplyStore(tmp_path / "task.sqlite3")
    store.upsert_org_user_profile(
        user_id="applicant-user-1", name="张三", open_dingtalk_id="open-applicant-1",
        manager_user_id=None, department_ids=set(),
    )
    assert scan_pending_oa_approvals(store, FakeDws(), now=datetime.fromisoformat(
        "2026-07-27T09:30:00+08:00"
    )) == 1
    task = store.claim_reply_tasks(limit=1)[0]
    assert '"originatorOpenDingTalkId":"open-applicant-1"' in task.trigger_message_json


def test_scan_pending_oa_approvals_covers_an_old_process_that_reaches_me_now(tmp_path):
    class FakeDws:
        def __init__(self):
            self.pages = []

        def list_pending_oa_approvals(self, *, page, size, start, end):
            self.pages.append((page, size, start, end))
            return [
                DwsOaApprovalCandidate(
                    process_instance_id="old-proc",
                    title="刘紫煜提交的供应商付款申请",
                )
            ]

        def get_current_user_id(self):
            return "principal-user-1"

        def read_oa_approval_tasks(self, process_instance_id):
            return {"result": {"tasks": [{"taskId": "task-1", "status": "RUNNING"}]}}

        def read_oa_process_instance_openapi(self, process_instance_id):
            return {
                "result": {
                    "tasks": [
                        {
                            "taskId": "task-1",
                            "userId": "principal-user-1",
                            "status": "RUNNING",
                        }
                    ]
                }
            }

        def read_oa_approval_records(self, process_instance_id):
            return {"result": {"operationRecords": []}}

    dws = FakeDws()
    store = AutoReplyStore(tmp_path / "task.sqlite3")

    assert scan_pending_oa_approvals(
        store,
        dws,
        now=datetime.fromisoformat("2026-07-31T19:00:00+08:00"),
    ) == 1

    assert dws.pages[0][2] == "2025-07-31T19:00:00+08:00"


def test_scan_pending_oa_approvals_requeues_when_a_new_remark_arrives(tmp_path):
    class FakeDws:
        latest_operation_time = 1

        def list_pending_oa_approvals(self, *, page, size, start, end):
            return [DwsOaApprovalCandidate(process_instance_id="proc-1", title="付款申请")]

        def get_current_user_id(self):
            return "principal-user-1"

        def read_oa_approval_tasks(self, process_instance_id):
            return {"result": {"tasks": [{"taskId": "task-1", "status": "RUNNING"}]}}

        def read_oa_process_instance_openapi(self, process_instance_id):
            return {
                "result": {
                    "tasks": [
                        {
                            "taskId": "task-1",
                            "userId": "principal-user-1",
                            "status": "RUNNING",
                        }
                    ]
                }
            }

        def read_oa_approval_records(self, process_instance_id):
            return {
                "result": {
                    "operationRecords": [
                        {
                            "operationType": "ADD_REMARK",
                            "operationTime": self.latest_operation_time,
                            "userId": "requester",
                        }
                    ]
                }
            }

    dws = FakeDws()
    store = AutoReplyStore(tmp_path / "task.sqlite3")
    now = datetime.fromisoformat("2026-07-31T19:00:00+08:00")

    assert scan_pending_oa_approvals(store, dws, now=now) == 1
    dws.latest_operation_time = 2
    assert scan_pending_oa_approvals(store, dws, now=now) == 1


def test_scan_pending_oa_approvals_does_not_requeue_after_own_decision(tmp_path):
    """A decision by the reviewer closes the approval for this scan.

    This used to be asserted with an `ADD_REMARK`, which is a comment rather
    than a decision; production showed approvals disappearing after the agent
    commented on them, so only a real decision counts here now.
    """

    class FakeDws:
        latest_operation_time = 1
        latest_operation_user_id = "requester"
        latest_operation_type = "ADD_REMARK"

        def list_pending_oa_approvals(self, *, page, size, start, end):
            return [DwsOaApprovalCandidate(process_instance_id="proc-1", title="付款申请")]

        def get_current_user_id(self):
            return "principal-user-1"

        def read_oa_approval_tasks(self, process_instance_id):
            return {"result": {"tasks": [{"taskId": "task-1", "status": "RUNNING"}]}}

        def read_oa_process_instance_openapi(self, process_instance_id):
            return {
                "result": {
                    "tasks": [
                        {
                            "taskId": "task-1",
                            "userId": "principal-user-1",
                            "status": "RUNNING",
                        }
                    ]
                }
            }

        def read_oa_approval_records(self, process_instance_id):
            return {
                "result": {
                    "operationRecords": [
                        {
                            "operationType": "ADD_REMARK",
                            "operationTime": 1,
                            "userId": "requester",
                        },
                        {
                            "operationType": self.latest_operation_type,
                            "operationTime": self.latest_operation_time,
                            "userId": self.latest_operation_user_id,
                        },
                    ]
                }
            }

    dws = FakeDws()
    store = AutoReplyStore(tmp_path / "task.sqlite3")
    now = datetime.fromisoformat("2026-07-31T19:00:00+08:00")

    # The current reviewer has already decided after the applicant's latest
    # message. A pending-list hit is not new work and must not enter the queue.
    dws.latest_operation_time = 2
    dws.latest_operation_user_id = "principal-user-1"
    dws.latest_operation_type = "EXECUTE_TASK_NORMAL"
    assert scan_pending_oa_approvals(store, dws, now=now) == 0


def test_scan_pending_oa_approvals_skips_when_current_task_id_is_missing(tmp_path):
    class FakeDws:
        def list_pending_oa_approvals(self, *, page, size, start, end):
            return [
                DwsOaApprovalCandidate(
                    process_instance_id="proc-1",
                    title="张三提交的录用申请",
                    process_name="录用申请",
                )
            ]

        def read_oa_approval_tasks(self, process_instance_id):
            return {"result": {"tasks": []}}

        def read_oa_process_instance_openapi(self, process_instance_id):
            return {"result": {"tasks": []}}

    store = AutoReplyStore(tmp_path / "task.sqlite3")

    queued = scan_pending_oa_approvals(
        store,
        FakeDws(),
        now=datetime.fromisoformat("2026-07-27T09:30:00+08:00"),
    )

    assert queued == 0
    assert store.claim_reply_tasks(limit=1) == []


def test_scan_pending_oa_approvals_does_not_guess_unowned_task_ids(tmp_path):
    class FakeDws:
        def list_pending_oa_approvals(self, *, page, size, start, end):
            return [
                DwsOaApprovalCandidate(
                    process_instance_id="proc-1",
                    title="张三提交的录用申请",
                    process_name="录用申请",
                )
            ]

        def get_current_user_id(self):
            return "principal-user-1"

        def read_oa_approval_tasks(self, process_instance_id):
            return {
                "result": {
                    "taskIdList": [
                        {"taskId": 102648882814},
                        {"taskId": 102648910080},
                    ]
                }
            }

        def read_oa_process_instance_openapi(self, process_instance_id):
            return {"result": {"tasks": []}}

    store = AutoReplyStore(tmp_path / "task.sqlite3")

    queued = scan_pending_oa_approvals(
        store,
        FakeDws(),
        now=datetime.fromisoformat("2026-07-27T09:30:00+08:00"),
    )

    assert queued == 0
    assert store.claim_reply_tasks(limit=1) == []
    state = store.get_daily_scan_state("oa_pending")
    assert state is not None
    assert state["last_error"] == ""
    assert json.loads(state["cursor_json"])[
        "skipped_missing_task_id_process_instance_ids"
    ] == ["proc-1"]


def test_scan_pending_oa_approvals_records_task_read_failures(tmp_path):
    class BrokenDws:
        def list_pending_oa_approvals(self, *, page, size, start, end):
            return [
                DwsOaApprovalCandidate(
                    process_instance_id="proc-1",
                    title="张三提交的录用申请",
                    process_name="录用申请",
                )
            ]

        def read_oa_approval_tasks(self, process_instance_id):
            return {"result": {"tasks": [{"taskId": "task-1"}]}}

        def read_oa_process_instance_openapi(self, process_instance_id):
            raise RuntimeError("DingTalk OA detail unavailable")

    store = AutoReplyStore(tmp_path / "task.sqlite3")

    assert scan_pending_oa_approvals(store, BrokenDws()) == 0

    state = store.get_daily_scan_state("oa_pending")
    assert state is not None
    assert state["last_error"] == ""
    assert json.loads(state["cursor_json"])["read_failure_process_instance_ids"] == [
        "proc-1"
    ]


def test_scan_pending_oa_approvals_revisits_an_untouched_approval_the_next_day(tmp_path):
    """Production 2026-09-17: four approvals sat in Derek's list for weeks.

    A turn had ended without a decision, so nothing changed their revision and
    the scanner never offered them again, while DingTalk still showed them
    waiting on him. Same day is still deduplicated; a new day is not.
    """

    class FakeDws:
        def list_pending_oa_approvals(self, *, page, size, start, end):
            return [DwsOaApprovalCandidate(process_instance_id="proc-1", title="付款申请")]

        def get_current_user_id(self):
            return "principal-user-1"

        def read_oa_approval_tasks(self, process_instance_id):
            return {"result": {"tasks": [{"taskId": "task-1", "status": "RUNNING"}]}}

        def read_oa_process_instance_openapi(self, process_instance_id):
            return {
                "result": {
                    "tasks": [
                        {
                            "taskId": "task-1",
                            "userId": "principal-user-1",
                            "status": "RUNNING",
                        }
                    ]
                }
            }

        def read_oa_approval_records(self, process_instance_id):
            return {
                "result": {
                    "operationRecords": [
                        {
                            "operationType": "ADD_REMARK",
                            "operationTime": 1,
                            "userId": "requester",
                        }
                    ]
                }
            }

    dws = FakeDws()
    store = AutoReplyStore(tmp_path / "task.sqlite3")
    first = datetime.fromisoformat("2026-07-31T19:00:00+08:00")

    assert scan_pending_oa_approvals(store, dws, now=first) == 1
    assert scan_pending_oa_approvals(store, dws, now=first) == 0
    assert (
        scan_pending_oa_approvals(
            store, dws, now=first + timedelta(days=1)
        )
        == 1
    )


def test_scan_pending_oa_approvals_keeps_an_approval_the_agent_only_commented_on(
    tmp_path,
):
    """Production 2026-09-17: a comment is not a decision.

    The agent comments as the principal whenever it reviews without deciding.
    That comment became the newest operation record for the principal, the
    revision came back empty, and the approval fell out of every later scan
    while DingTalk still showed it waiting on him -- the outer cause of the
    four-approval backlog, ahead of the same-day deduplication.
    """

    class FakeDws:
        def list_pending_oa_approvals(self, *, page, size, start, end):
            return [
                DwsOaApprovalCandidate(
                    process_instance_id="proc-1", title="软件项目立项全流程"
                )
            ]

        def get_current_user_id(self):
            return "principal-user-1"

        def read_oa_approval_tasks(self, process_instance_id):
            return {"result": {"tasks": [{"taskId": "task-1", "status": "RUNNING"}]}}

        def read_oa_process_instance_openapi(self, process_instance_id):
            return {
                "result": {
                    "tasks": [
                        {
                            "taskId": "task-1",
                            "userId": "principal-user-1",
                            "status": "RUNNING",
                        }
                    ]
                }
            }

        def read_oa_approval_records(self, process_instance_id):
            return {
                "result": {
                    "operationRecords": [
                        {
                            "operationType": "ADD_REMARK",
                            "operationTime": 1,
                            "userId": "requester",
                        },
                        {
                            "operationType": "ADD_REMARK",
                            "operationTime": 2,
                            "userId": "principal-user-1",
                        },
                    ]
                }
            }

    dws = FakeDws()
    store = AutoReplyStore(tmp_path / "task.sqlite3")
    now = datetime.fromisoformat("2026-09-17T19:00:00+08:00")

    assert scan_pending_oa_approvals(store, dws, now=now) == 1


def test_scan_pending_oa_approvals_gives_each_approval_its_own_conversation(tmp_path):
    """Production 2026-09-17: four approvals, one Agent session, no work done.

    The Agent session is keyed by conversation id.  With every approval sharing
    `oa_pending_scan`, each run after the first resumed the previous approval's
    transcript and returned "已在当前会话中完成处理" after zero tool calls -- runs
    20015 and 20016 made none at all, and 20016 reported an approval it had
    never executed while DingTalk still showed the item waiting.
    """

    class FakeDws:
        def list_pending_oa_approvals(self, *, page, size, start, end):
            return [
                DwsOaApprovalCandidate(process_instance_id="proc-1", title="立项"),
                DwsOaApprovalCandidate(process_instance_id="proc-2", title="合同"),
            ]

        def get_current_user_id(self):
            return "principal-user-1"

        def read_oa_approval_tasks(self, process_instance_id):
            return {
                "result": {
                    "tasks": [
                        {"taskId": f"task-{process_instance_id}", "status": "RUNNING"}
                    ]
                }
            }

        def read_oa_process_instance_openapi(self, process_instance_id):
            return {
                "result": {
                    "tasks": [
                        {
                            "taskId": f"task-{process_instance_id}",
                            "userId": "principal-user-1",
                            "status": "RUNNING",
                        }
                    ]
                }
            }

        def read_oa_approval_records(self, process_instance_id):
            return {
                "result": {
                    "operationRecords": [
                        {
                            "operationType": "ADD_REMARK",
                            "operationTime": 1,
                            "userId": "requester",
                        }
                    ]
                }
            }

    store = AutoReplyStore(tmp_path / "task.sqlite3")
    now = datetime.fromisoformat("2026-09-17T19:00:00+08:00")

    assert scan_pending_oa_approvals(store, FakeDws(), now=now) == 2

    conversations = {task.conversation_id for task in store.claim_reply_tasks(limit=10)}
    assert conversations == {"oa_pending_scan:proc-1", "oa_pending_scan:proc-2"}


def test_scan_pending_oa_approvals_waits_for_a_new_comment_once_reviewed(tmp_path):
    """Derek 2026-09-17: once we have commented, wake on a new comment, not a timer.

    A daily reminder for an approval whose ball is in someone else's court is
    noise.  An approval we have NOT commented on is different -- that one can
    have lost its turn silently, so it keeps the daily revisit.
    """

    class FakeDws:
        extra_records: list = []

        def list_pending_oa_approvals(self, *, page, size, start, end):
            return [DwsOaApprovalCandidate(process_instance_id="proc-1", title="付款申请")]

        def get_current_user_id(self):
            return "principal-user-1"

        def read_oa_approval_tasks(self, process_instance_id):
            return {"result": {"tasks": [{"taskId": "task-1", "status": "RUNNING"}]}}

        def read_oa_process_instance_openapi(self, process_instance_id):
            return {
                "result": {
                    "tasks": [
                        {
                            "taskId": "task-1",
                            "userId": "principal-user-1",
                            "status": "RUNNING",
                        }
                    ]
                }
            }

        def read_oa_approval_records(self, process_instance_id):
            return {
                "result": {
                    "operationRecords": [
                        {
                            "operationType": "ADD_REMARK",
                            "operationTime": 1,
                            "userId": "requester",
                        },
                        *self.extra_records,
                    ]
                }
            }

    dws = FakeDws()
    store = AutoReplyStore(tmp_path / "task.sqlite3")
    day_one = datetime.fromisoformat("2026-09-17T19:00:00+08:00")

    # Not reviewed yet: the daily revisit still protects it.
    assert scan_pending_oa_approvals(store, dws, now=day_one) == 1
    assert scan_pending_oa_approvals(store, dws, now=day_one + timedelta(days=1)) == 1

    # We commented. No timer wake-up, however many days pass: our own record
    # is excluded from the revision, so nothing about the approval has moved.
    dws.extra_records = [
        {"operationType": "ADD_REMARK", "operationTime": 2, "userId": "principal-user-1"}
    ]
    assert scan_pending_oa_approvals(store, dws, now=day_one + timedelta(days=2)) == 0
    assert scan_pending_oa_approvals(store, dws, now=day_one + timedelta(days=3)) == 0
    assert scan_pending_oa_approvals(store, dws, now=day_one + timedelta(days=9)) == 0

    # Someone else comments: that is the wake-up signal.
    dws.extra_records = [
        *dws.extra_records,
        {"operationType": "ADD_REMARK", "operationTime": 3, "userId": "requester"},
    ]
    assert scan_pending_oa_approvals(store, dws, now=day_one + timedelta(days=10)) == 1


def test_scan_pending_oa_approvals_starts_each_review_from_a_clean_session(tmp_path):
    """A new scan is a fresh look at DingTalk, not a continuation.

    The session is bound to the conversation, so a requeued approval resumed
    the previous turn's transcript: runs 20017, 20021, 20023 and 20032 on the
    same approval all carried session 01a0b122 and the last three returned
    "已在此前一次运行中完成实时审阅" verbatim with zero tool calls.
    """

    class FakeDws:
        revision_seed = 1

        def list_pending_oa_approvals(self, *, page, size, start, end):
            return [DwsOaApprovalCandidate(process_instance_id="proc-1", title="续签")]

        def get_current_user_id(self):
            return "principal-user-1"

        def read_oa_approval_tasks(self, process_instance_id):
            return {"result": {"tasks": [{"taskId": "task-1", "status": "RUNNING"}]}}

        def read_oa_process_instance_openapi(self, process_instance_id):
            return {
                "result": {
                    "tasks": [
                        {
                            "taskId": "task-1",
                            "userId": "principal-user-1",
                            "status": "RUNNING",
                        }
                    ]
                }
            }

        def read_oa_approval_records(self, process_instance_id):
            return {
                "result": {
                    "operationRecords": [
                        {
                            "operationType": "ADD_REMARK",
                            "operationTime": self.revision_seed,
                            "userId": "requester",
                        }
                    ]
                }
            }

    dws = FakeDws()
    store = AutoReplyStore(tmp_path / "task.sqlite3")
    conversation = "oa_pending_scan:proc-1"
    now = datetime.fromisoformat("2026-09-17T19:00:00+08:00")

    assert scan_pending_oa_approvals(store, dws, now=now) == 1
    store.upsert_conversation_runtime_session(
        conversation, "codex_oauth", "session-from-the-previous-turn"
    )
    assert (
        store.get_conversation_runtime_session(conversation, "codex_oauth") is not None
    )

    # The applicant replies, so the next scan is a genuinely new review.
    dws.revision_seed = 2
    assert scan_pending_oa_approvals(store, dws, now=now + timedelta(days=1)) == 1

    assert store.get_conversation_runtime_session(conversation, "codex_oauth") is None


def test_scan_pending_oa_approvals_points_the_turn_at_our_own_skill(tmp_path):
    """The prompt named the vendor reference, so our Skill was never read.

    Run 20020 read `dingtalk-oa-approval` zero times and `dingtalk-misc` seven,
    which is why eight revisions of our OA rules -- revert-first, the scoring
    rubric, the no-hand-copied-URL rule -- had no effect on the pipeline. The
    vendor Skills are also overwritten by `dws upgrade`, so rules cannot live
    there.
    """

    class FakeDws:
        def list_pending_oa_approvals(self, *, page, size, start, end):
            return [DwsOaApprovalCandidate(process_instance_id="proc-1", title="立项")]

        def get_current_user_id(self):
            return "principal-user-1"

        def read_oa_approval_tasks(self, process_instance_id):
            return {"result": {"tasks": [{"taskId": "task-1", "status": "RUNNING"}]}}

        def read_oa_process_instance_openapi(self, process_instance_id):
            return {
                "result": {
                    "tasks": [
                        {
                            "taskId": "task-1",
                            "userId": "principal-user-1",
                            "status": "RUNNING",
                        }
                    ]
                }
            }

        def read_oa_approval_records(self, process_instance_id):
            return {
                "result": {
                    "operationRecords": [
                        {
                            "operationType": "ADD_REMARK",
                            "operationTime": 1,
                            "userId": "requester",
                        }
                    ]
                }
            }

    store = AutoReplyStore(tmp_path / "task.sqlite3")
    now = datetime.fromisoformat("2026-09-17T19:00:00+08:00")

    assert scan_pending_oa_approvals(store, FakeDws(), now=now) == 1

    [task] = store.claim_reply_tasks(limit=1)
    assert "dingtalk-oa-approval/SKILL.md" in task.trigger_text
    # The vendor reference may still be mentioned, but only as command usage.
    assert "dingtalk-misc 的 references/oa.md 只作为 dws 命令用法参考" in task.trigger_text
    assert "审批判断与动作一律以 dingtalk-oa-approval 为准" not in task.trigger_text
