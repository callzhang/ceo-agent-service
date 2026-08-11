import json
import os
from pathlib import Path
from datetime import datetime

from app.dws_client import DwsOaApprovalCandidate
from app.store import AutoReplyStore
from app.task_scanners import (
    scan_ai_minutes,
    scan_local_workspace_files,
    scan_pending_oa_approvals,
)


def test_scan_local_files_only_under_workspace(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    inside = workspace / "management.md"
    inside.write_text("P1 项目需要三天内确认进展", encoding="utf-8")
    outside = tmp_path / "outside.md"
    outside.write_text("不应该扫描", encoding="utf-8")
    store = AutoReplyStore(tmp_path / "task.sqlite3")

    count = scan_local_workspace_files(
        store,
        workspace=workspace,
        include_globs=("*.md",),
        exclude_globs=(),
        enqueue_existing_on_first_scan=True,
    )

    assert count == 1
    claimed = store.claim_work_summary_inputs(limit=10)
    assert len(claimed) == 1
    assert str(inside) in claimed[0].source_ref
    assert str(outside) not in claimed[0].payload_json


def test_scan_local_files_rejects_workspace_outside_root(tmp_path):
    store = AutoReplyStore(tmp_path / "task.sqlite3")
    missing = tmp_path / "missing"

    count = scan_local_workspace_files(store, workspace=missing)

    assert count == 0
    assert store.get_daily_scan_state("local_files")["last_error"].startswith(
        "workspace missing"
    )


def test_scan_local_files_baselines_existing_files_on_first_scan(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    existing = workspace / "existing.md"
    existing.write_text("历史文件不应该首次入队", encoding="utf-8")
    store = AutoReplyStore(tmp_path / "task.sqlite3")

    assert scan_local_workspace_files(store, workspace=workspace) == 0
    assert store.claim_work_summary_inputs(limit=10) == []

    new_file = workspace / "new.md"
    new_file.write_text("新增文件应该入队", encoding="utf-8")

    assert (
        scan_local_workspace_files(
            store,
            workspace=workspace,
            enqueue_existing_on_first_scan=True,
        )
        == 1
    )
    claimed = store.claim_work_summary_inputs(limit=10)
    assert len(claimed) == 1
    assert str(new_file) in claimed[0].source_ref


def test_scan_local_files_skips_hidden_paths(tmp_path):
    workspace = tmp_path / "workspace"
    hidden_dir = workspace / ".codex"
    hidden_dir.mkdir(parents=True)
    hidden_file = hidden_dir / "SKILL.md"
    hidden_file.write_text("隐藏目录不应该进入 task", encoding="utf-8")
    visible = workspace / "visible.md"
    visible.write_text("可见文件建立 baseline", encoding="utf-8")
    store = AutoReplyStore(tmp_path / "task.sqlite3")

    assert (
        scan_local_workspace_files(
            store,
            workspace=workspace,
            enqueue_existing_on_first_scan=True,
        )
        == 1
    )
    claimed = store.claim_work_summary_inputs(limit=10)
    assert len(claimed) == 1
    assert str(visible) in claimed[0].source_ref
    assert str(hidden_file) not in claimed[0].payload_json


def test_scan_local_files_skips_default_generated_work_dirs(tmp_path):
    workspace = tmp_path / "workspace"
    ai_minutes_dir = workspace / "AI听记"
    frontier_dir = workspace / "tech" / "daily frontier report"
    ai_minutes_dir.mkdir(parents=True)
    frontier_dir.mkdir(parents=True)
    (ai_minutes_dir / "meeting.md").write_text("AI 听记导出", encoding="utf-8")
    (frontier_dir / "report.md").write_text("已发布报告", encoding="utf-8")
    store = AutoReplyStore(tmp_path / "task.sqlite3")

    count = scan_local_workspace_files(
        store,
        workspace=workspace,
        enqueue_existing_on_first_scan=True,
    )

    assert count == 0
    assert store.claim_work_summary_inputs(limit=10) == []


def test_scan_local_files_skips_virtualenv_and_package_cache_paths(tmp_path):
    workspace = tmp_path / "workspace"
    package_dir = (
        workspace / "tmp" / "pdfvenv" / "lib" / "python3.14" / "site-packages"
    )
    package_dir.mkdir(parents=True)
    package_file = package_dir / "LICENSE.txt"
    package_file.write_text(
        "Apache License should not become a work item",
        encoding="utf-8",
    )
    visible = workspace / "visible.txt"
    visible.write_text("业务任务需要跟进", encoding="utf-8")
    store = AutoReplyStore(tmp_path / "task.sqlite3")

    assert (
        scan_local_workspace_files(
            store,
            workspace=workspace,
            enqueue_existing_on_first_scan=True,
        )
        == 1
    )
    claimed = store.claim_work_summary_inputs(limit=10)
    assert len(claimed) == 1
    assert str(visible) in claimed[0].source_ref
    assert str(package_file) not in claimed[0].payload_json


def test_scan_local_files_uses_incremental_mtime_cursor(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    first = workspace / "first.md"
    first.write_text("第一次扫描", encoding="utf-8")
    store = AutoReplyStore(tmp_path / "task.sqlite3")

    assert (
        scan_local_workspace_files(
            store,
            workspace=workspace,
            enqueue_existing_on_first_scan=True,
        )
        == 1
    )
    assert scan_local_workspace_files(store, workspace=workspace) == 0

    second = workspace / "second.md"
    second.write_text("第二次扫描", encoding="utf-8")
    assert (
        scan_local_workspace_files(
            store,
            workspace=workspace,
            enqueue_existing_on_first_scan=True,
        )
        == 1
    )


def test_scan_local_files_does_not_skip_new_file_with_same_mtime(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    first = workspace / "first.md"
    second = workspace / "second.md"
    timestamp = 1_800_000_000
    first.write_text("第一次扫描", encoding="utf-8")
    os.utime(first, (timestamp, timestamp))
    store = AutoReplyStore(tmp_path / "task.sqlite3")

    assert (
        scan_local_workspace_files(
            store,
            workspace=workspace,
            enqueue_existing_on_first_scan=True,
        )
        == 1
    )

    second.write_text("第二次扫描", encoding="utf-8")
    os.utime(second, (timestamp, timestamp))

    assert (
        scan_local_workspace_files(
            store,
            workspace=workspace,
            enqueue_existing_on_first_scan=True,
        )
        == 1
    )
    claimed = store.claim_work_summary_inputs(limit=10)
    assert {
        Path(row.source_ref.split("#", 1)[0]).name for row in claimed
    } == {"first.md", "second.md"}


def test_scan_local_files_requeues_edited_done_file(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    file_path = workspace / "project.md"
    timestamp = 1_800_000_000
    file_path.write_text("第一次扫描", encoding="utf-8")
    os.utime(file_path, (timestamp, timestamp))
    store = AutoReplyStore(tmp_path / "task.sqlite3")

    assert (
        scan_local_workspace_files(
            store,
            workspace=workspace,
            enqueue_existing_on_first_scan=True,
        )
        == 1
    )
    first_claimed = store.claim_work_summary_inputs(limit=10)
    assert len(first_claimed) == 1
    store.mark_work_summary_input_done(first_claimed[0].id)

    file_path.write_text("第二次扫描，需要重新处理", encoding="utf-8")
    os.utime(file_path, (timestamp + 1, timestamp + 1))

    assert (
        scan_local_workspace_files(
            store,
            workspace=workspace,
            enqueue_existing_on_first_scan=True,
        )
        == 1
    )
    second_claimed = store.claim_work_summary_inputs(limit=10)
    assert len(second_claimed) == 1
    assert second_claimed[0].source_ref != first_claimed[0].source_ref
    assert "第二次扫描" in second_claimed[0].payload_json


def test_scan_local_files_requeues_edited_done_file_with_same_mtime(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    file_path = workspace / "project.md"
    timestamp = 1_800_000_000
    file_path.write_text("第一次扫描", encoding="utf-8")
    os.utime(file_path, (timestamp, timestamp))
    store = AutoReplyStore(tmp_path / "task.sqlite3")

    assert (
        scan_local_workspace_files(
            store,
            workspace=workspace,
            enqueue_existing_on_first_scan=True,
        )
        == 1
    )
    first_claimed = store.claim_work_summary_inputs(limit=10)
    assert len(first_claimed) == 1
    store.mark_work_summary_input_done(first_claimed[0].id)

    file_path.write_text("第二次扫描", encoding="utf-8")
    os.utime(file_path, (timestamp, timestamp))

    assert scan_local_workspace_files(store, workspace=workspace) == 1
    second_claimed = store.claim_work_summary_inputs(limit=10)
    assert len(second_claimed) == 1
    assert second_claimed[0].source_ref != first_claimed[0].source_ref
    assert "第二次扫描" in second_claimed[0].payload_json


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
                    "items": [{"taskUuid": "minutes-1", "title": "第一页"}],
                    "has_more": True,
                    "next_token": "token-2",
                }
            return {
                "items": [{"taskUuid": "minutes-2", "title": "第二页"}],
                "has_more": False,
                "next_token": "",
            }

        def list_minutes(self):
            raise AssertionError("paginated adapter should be used")

    dws = FakeDws()
    store = AutoReplyStore(tmp_path / "task.sqlite3")

    count = scan_ai_minutes(store, dws, enqueue_existing_on_first_scan=True)

    assert count == 2
    assert dws.tokens == [(50, ""), (50, "token-2")]
    claimed = store.claim_work_summary_inputs(limit=10)
    assert {row.source_ref for row in claimed} == {"minutes-1", "minutes-2"}


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

    queued = scan_pending_oa_approvals(
        store,
        dws,
        now=datetime.fromisoformat("2026-07-27T09:30:00+08:00"),
    )

    assert queued == 1
    assert dws.pages == [
        (
            1,
            30,
            "2025-07-27T09:30:00+08:00",
            "2026-07-27T09:30:00+08:00",
        )
    ]
    assert dws.task_reads == ["proc-1"]
    task = store.claim_reply_tasks(limit=1)[0]
    assert task.conversation_id == "oa_pending_scan"
    assert task.conversation_title == "审批待办"
    assert task.trigger_message_id.startswith("oa-pending:proc-1:")
    assert "张三提交的录用申请" in task.trigger_text
    assert "procInstId=proc-1&taskId=102648910080" in task.oa_url
    assert '"source":"oa_pending_scan"' in task.trigger_message_json


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


def test_scan_pending_oa_approvals_does_not_requeue_for_own_remark(tmp_path):
    class FakeDws:
        latest_operation_time = 1
        latest_operation_user_id = "requester"

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
                            "operationType": "ADD_REMARK",
                            "operationTime": self.latest_operation_time,
                            "userId": self.latest_operation_user_id,
                        },
                    ]
                }
            }

    dws = FakeDws()
    store = AutoReplyStore(tmp_path / "task.sqlite3")
    now = datetime.fromisoformat("2026-07-31T19:00:00+08:00")

    assert scan_pending_oa_approvals(store, dws, now=now) == 1
    dws.latest_operation_time = 2
    dws.latest_operation_user_id = "principal-user-1"

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
