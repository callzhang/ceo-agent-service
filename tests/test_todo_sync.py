import json
from pathlib import Path

import pytest

from app.dws_client import DwsError
from app.store import AutoReplyStore
from app.todo_sync import (
    maybe_create_dingtalk_todo,
    pull_dingtalk_todo_statuses,
    refresh_dingtalk_todo_before_follow_up,
    retry_failed_dingtalk_todo_links,
    sync_completed_todo_to_dingtalk,
)


class FakeTodoDws:
    def __init__(self):
        self.created = []
        self.create_payload = {"todoTaskId": "dt-task-1"}
        self.get_calls = []
        self.get_payloads = {}
        self.get_errors = {}
        self.done_calls = []
        self.done_error = None

    def create_todo_task(
        self,
        *,
        title,
        executor_user_id,
        due,
        priority,
        description="",
        tags=None,
        participants=None,
        files=None,
    ):
        self.created.append(
            {
                "title": title,
                "executor_user_id": executor_user_id,
                "due": due,
                "priority": priority,
                "description": description,
                "tags": tags or [],
                "participants": participants or [],
                "files": files or [],
            }
        )
        return self.create_payload

    def get_todo_task(self, task_id):
        self.get_calls.append(task_id)
        error = self.get_errors.get(task_id)
        if error is not None:
            raise error
        return self.get_payloads.get(task_id, {"id": task_id, "done": False})

    def mark_todo_task_done(self, task_id, *, done=True):
        if self.done_error is not None:
            raise self.done_error
        self.done_calls.append({"task_id": task_id, "done": done})
        return {"id": task_id, "done": done}


def _store(tmp_path: Path) -> AutoReplyStore:
    return AutoReplyStore(tmp_path / "task.sqlite3")


def _project_and_todo(store: AutoReplyStore, **todo_values):
    project_id = store.create_work_project(
        title="客户交付",
        category="projects",
        tags_json=json.dumps(["客户交付"], ensure_ascii=False),
        status="active",
        priority="P1",
        risk_level="medium",
        background="客户交付项目需要持续确认验收安排。",
        related_people_json=json.dumps(
            [{"user_id": "owner-1", "name": "Alex", "role": "owner"}],
            ensure_ascii=False,
        ),
    )
    defaults = {
        "project_id": project_id,
        "title": "给客户同步验收 ETA",
        "owner_user_id": "owner-1",
        "owner_name": "Alex",
        "status": "open",
        "priority": "P1",
        "deadline_at": "2026-07-01 18:00:00",
    }
    defaults.update(todo_values)
    return project_id, store.create_work_todo(**defaults)


def test_maybe_create_dingtalk_todo_creates_high_confidence_link(tmp_path):
    store = _store(tmp_path)
    _, todo_id = _project_and_todo(store)
    dws = FakeTodoDws()

    link = maybe_create_dingtalk_todo(
        store,
        dws,
        work_todo_id=todo_id,
        now="2026-06-27 10:00:00",
    )

    assert link is not None
    assert dws.created == [
        {
            "title": "给客户同步验收 ETA：项目：客户交付",
            "executor_user_id": "owner-1",
            "due": "2026-07-01T18:00:00+08:00",
            "priority": 30,
            "description": (
                "项目：客户交付\n"
                "项目背景：客户交付项目需要持续确认验收安排。\n"
                "待办：给客户同步验收 ETA\n"
                "截止时间：2026-07-01 18:00:00\n"
                "负责人：Alex\n"
                "优先级：P1"
            ),
            "tags": ["客户交付", "projects", "risk:medium", "P1"],
            "participants": [
                {"user_id": "owner-1", "name": "Alex", "role": "owner"}
            ],
            "files": [],
        }
    ]
    stored = store.get_work_todo_dingtalk_link(link.id)
    assert stored.dingtalk_task_id == "dt-task-1"
    assert stored.status == "active"


def test_maybe_create_dingtalk_todo_uses_project_context_when_description_has_no_source(
    tmp_path,
):
    store = _store(tmp_path)
    _, todo_id = _project_and_todo(
        store,
        description="确认交付验收时间和阻塞。",
    )
    dws = FakeTodoDws()

    maybe_create_dingtalk_todo(
        store,
        dws,
        work_todo_id=todo_id,
        now="2026-06-27 10:00:00",
    )

    assert dws.created[0]["title"] == (
        "给客户同步验收 ETA：项目：客户交付；确认交付验收时间和阻塞"
    )


def test_maybe_create_dingtalk_todo_pushes_context_in_external_title(tmp_path):
    store = _store(tmp_path)
    _, todo_id = _project_and_todo(
        store,
        title="准备宝马专家邀请材料",
        description=(
            "来源：宝马项目周末攻坚与客户Demo推进；交付：补齐专家背景、"
            "邀请理由、Demo议程和客户确认口径。完成标准：可直接发给客户。"
        ),
    )
    dws = FakeTodoDws()

    link = maybe_create_dingtalk_todo(
        store,
        dws,
        work_todo_id=todo_id,
        now="2026-06-27 10:00:00",
    )

    assert link is not None
    created_title = dws.created[0]["title"]
    assert created_title.startswith("准备宝马专家邀请材料：来源：宝马项目周末攻坚")
    assert len(created_title) <= 80
    stored = store.get_work_todo_dingtalk_link(link.id)
    assert stored.title_snapshot == created_title


def test_maybe_create_dingtalk_todo_accepts_create_todo_task_id(tmp_path):
    store = _store(tmp_path)
    _, todo_id = _project_and_todo(store)
    dws = FakeTodoDws()
    dws.create_payload = {"todoTaskId": "dt-task-from-create"}

    link = maybe_create_dingtalk_todo(
        store,
        dws,
        work_todo_id=todo_id,
        now="2026-06-27 10:00:00",
    )

    assert link is not None
    assert link.dingtalk_task_id == "dt-task-from-create"
    assert link.status == "active"
    assert dws.get_calls == ["dt-task-from-create"]


def test_maybe_create_dingtalk_todo_skips_missing_deadline(tmp_path):
    store = _store(tmp_path)
    _, todo_id = _project_and_todo(store, deadline_at="")
    dws = FakeTodoDws()

    link = maybe_create_dingtalk_todo(
        store,
        dws,
        work_todo_id=todo_id,
        now="2026-06-27 10:00:00",
    )

    assert link is None
    assert dws.created == []


@pytest.mark.parametrize(
    "todo_values",
    [
        {"title": "跟进一下"},
        {"owner_user_id": ""},
        {"completion_evidence_json": json.dumps({"source": "reply_attempt:1"})},
        {"status": "done"},
    ],
)
def test_maybe_create_dingtalk_todo_skips_ineligible_todos(
    tmp_path,
    todo_values,
):
    store = _store(tmp_path)
    _, todo_id = _project_and_todo(store, **todo_values)
    dws = FakeTodoDws()

    link = maybe_create_dingtalk_todo(
        store,
        dws,
        work_todo_id=todo_id,
        now="2026-06-27 10:00:00",
    )

    assert link is None
    assert dws.created == []


def test_maybe_create_dingtalk_todo_skips_hr_project(tmp_path):
    store = _store(tmp_path)
    project_id = store.create_work_project(
        title="HR 事项",
        category="HR",
        status="active",
        priority="P1",
        risk_level="medium",
    )
    todo_id = store.create_work_todo(
        project_id=project_id,
        title="确认候选人面试安排",
        owner_user_id="owner-1",
        owner_name="Alex",
        status="open",
        priority="P1",
        deadline_at="2026-07-01 18:00:00",
    )
    dws = FakeTodoDws()

    link = maybe_create_dingtalk_todo(
        store,
        dws,
        work_todo_id=todo_id,
        now="2026-06-27 10:00:00",
    )

    assert link is None
    assert dws.created == []


def test_maybe_create_dingtalk_todo_skips_existing_active_link(tmp_path):
    store = _store(tmp_path)
    _, todo_id = _project_and_todo(store)
    link_id = store.create_work_todo_dingtalk_link(
        work_todo_id=todo_id,
        dingtalk_task_id="dt-task-existing",
        executor_user_id="owner-1",
        title_snapshot="给客户同步验收 ETA",
        deadline_at_snapshot="2026-07-01 18:00:00",
        priority_snapshot="P1",
        status="active",
    )
    dws = FakeTodoDws()

    link = maybe_create_dingtalk_todo(
        store,
        dws,
        work_todo_id=todo_id,
        now="2026-06-27 10:00:00",
    )

    assert link.id == link_id
    assert link.dingtalk_task_id == "dt-task-existing"
    assert dws.created == []


def test_maybe_create_dingtalk_todo_keeps_task_id_when_get_fails(tmp_path):
    store = _store(tmp_path)
    _, todo_id = _project_and_todo(store)
    dws = FakeTodoDws()
    dws.get_errors["dt-task-1"] = DwsError("todo get failed")

    link = maybe_create_dingtalk_todo(
        store,
        dws,
        work_todo_id=todo_id,
        now="2026-06-27 10:00:00",
    )

    assert link is not None
    assert link.status == "failed"
    assert link.dingtalk_task_id == "dt-task-1"
    assert "todo get failed" in link.last_error


def test_maybe_create_dingtalk_todo_recovers_failed_link_without_duplicate_create(
    tmp_path,
):
    store = _store(tmp_path)
    _, todo_id = _project_and_todo(store)
    dws = FakeTodoDws()
    dws.get_errors["dt-task-1"] = DwsError("first get failed")

    first_link = maybe_create_dingtalk_todo(
        store,
        dws,
        work_todo_id=todo_id,
        now="2026-06-27 10:00:00",
    )
    dws.get_errors = {}
    dws.get_payloads["dt-task-1"] = {"id": "dt-task-1", "done": False}

    second_link = maybe_create_dingtalk_todo(
        store,
        dws,
        work_todo_id=todo_id,
        now="2026-06-27 10:05:00",
    )

    assert len(dws.created) == 1
    assert second_link.id == first_link.id
    assert second_link.dingtalk_task_id == "dt-task-1"
    assert second_link.status == "active"
    stored = store.get_work_todo_dingtalk_link(first_link.id)
    assert stored.dingtalk_task_id == "dt-task-1"
    assert stored.status == "active"
    assert stored.last_error == ""


def test_maybe_create_dingtalk_todo_retries_token_failed_create_link(tmp_path):
    store = _store(tmp_path)
    _, todo_id = _project_and_todo(store)
    link_id = store.create_work_todo_dingtalk_link(
        work_todo_id=todo_id,
        dingtalk_task_id="",
        status="failed",
        last_error="code=TOKEN_VERIFIED_FAILED",
    )
    dws = FakeTodoDws()

    link = maybe_create_dingtalk_todo(
        store,
        dws,
        work_todo_id=todo_id,
        now="2026-06-27 10:05:00",
    )

    assert link.id == link_id
    assert len(dws.created) == 1
    assert link.dingtalk_task_id == "dt-task-1"
    assert link.status == "active"
    assert link.last_error == ""


def test_retry_failed_dingtalk_todo_links_recovers_token_failures(tmp_path):
    store = _store(tmp_path)
    _, existing_todo_id = _project_and_todo(store, title="确认验收 ETA")
    existing_link_id = store.create_work_todo_dingtalk_link(
        work_todo_id=existing_todo_id,
        dingtalk_task_id="dt-existing",
        status="failed",
        last_error="code=TOKEN_VERIFIED_FAILED",
    )
    _, create_todo_id = _project_and_todo(store, title="归档验收材料")
    create_link_id = store.create_work_todo_dingtalk_link(
        work_todo_id=create_todo_id,
        dingtalk_task_id="",
        status="failed",
        last_error="code=TOKEN_VERIFIED_FAILED",
    )
    dws = FakeTodoDws()
    dws.get_payloads["dt-existing"] = {"id": "dt-existing", "done": False}

    recovered = retry_failed_dingtalk_todo_links(
        store,
        dws,
        now="2026-06-27 10:10:00",
    )

    assert recovered == 2
    existing_link = store.get_work_todo_dingtalk_link(existing_link_id)
    create_link = store.get_work_todo_dingtalk_link(create_link_id)
    assert existing_link.status == "active"
    assert existing_link.last_error == ""
    assert create_link.status == "active"
    assert create_link.dingtalk_task_id == "dt-task-1"
    assert len(dws.created) == 1


def test_maybe_create_dingtalk_todo_keeps_failed_link_when_recovery_get_fails(
    tmp_path,
):
    store = _store(tmp_path)
    _, todo_id = _project_and_todo(store)
    dws = FakeTodoDws()
    dws.get_errors["dt-task-1"] = DwsError("first get failed")

    first_link = maybe_create_dingtalk_todo(
        store,
        dws,
        work_todo_id=todo_id,
        now="2026-06-27 10:00:00",
    )
    dws.get_errors["dt-task-1"] = DwsError("second get failed")

    second_link = maybe_create_dingtalk_todo(
        store,
        dws,
        work_todo_id=todo_id,
        now="2026-06-27 10:05:00",
    )

    assert len(dws.created) == 1
    assert second_link.id == first_link.id
    assert second_link.status == "failed"
    assert second_link.dingtalk_task_id == "dt-task-1"
    assert "second get failed" in second_link.last_error


def test_maybe_create_dingtalk_todo_prefers_active_link_over_failed_recovery(
    tmp_path,
):
    store = _store(tmp_path)
    _, todo_id = _project_and_todo(store)
    failed_link_id = store.create_work_todo_dingtalk_link(
        work_todo_id=todo_id,
        dingtalk_task_id="dt-task-failed",
        executor_user_id="owner-1",
        title_snapshot="给客户同步验收 ETA",
        deadline_at_snapshot="2026-07-01 18:00:00",
        priority_snapshot="P1",
        status="failed",
    )
    active_link_id = store.create_work_todo_dingtalk_link(
        work_todo_id=todo_id,
        dingtalk_task_id="dt-task-active",
        executor_user_id="owner-1",
        title_snapshot="给客户同步验收 ETA",
        deadline_at_snapshot="2026-07-01 18:00:00",
        priority_snapshot="P1",
        status="active",
    )
    dws = FakeTodoDws()
    dws.get_payloads["dt-task-failed"] = {"id": "dt-task-failed", "done": False}

    link = maybe_create_dingtalk_todo(
        store,
        dws,
        work_todo_id=todo_id,
        now="2026-06-27 10:10:00",
    )

    assert dws.created == []
    assert link.id == active_link_id
    assert link.dingtalk_task_id == "dt-task-active"
    assert store.get_work_todo_dingtalk_link(failed_link_id).status == "failed"
    assert store.get_work_todo_dingtalk_link(active_link_id).status == "active"


def test_pull_done_dingtalk_todo_closes_internal_todo(tmp_path):
    store = _store(tmp_path)
    project_id, todo_id = _project_and_todo(store)
    follow_up_id = store.create_follow_up_draft(
        project_id=project_id,
        todo_id=todo_id,
        owner_user_id="owner-1",
        owner_name="Alex",
        target_kind="direct",
        question_text="请确认验收 ETA。",
        scheduled_at="2026-06-27 09:00:00",
        status="sent",
        sent_at="2026-06-27 09:05:00",
    )
    link_id = store.create_work_todo_dingtalk_link(
        work_todo_id=todo_id,
        dingtalk_task_id="dt-task-1",
        executor_user_id="owner-1",
        title_snapshot="给客户同步验收 ETA",
        deadline_at_snapshot="2026-07-01 18:00:00",
        priority_snapshot="P1",
        status="active",
    )
    dws = FakeTodoDws()
    dws.get_payloads["dt-task-1"] = {"id": "dt-task-1", "done": True}

    updated = pull_dingtalk_todo_statuses(
        store,
        dws,
        now="2026-06-27 11:00:00",
    )

    assert updated == 1
    todo = store.get_work_todo(todo_id)
    assert todo.status == "done"
    assert "dingtalk_todo:dt-task-1" in todo.completion_evidence_json
    assert store.get_work_todo_dingtalk_link(link_id).status == "done"
    follow_up = store.get_follow_up_draft(follow_up_id)
    assert follow_up is not None
    assert follow_up.status == "completed"
    check = json.loads(follow_up.evidence_check_json)
    assert check["source"] == "dingtalk_todo:dt-task-1"
    assert check["reason"] == "DingTalk Todo marked done by owner"


def test_pull_done_dingtalk_todo_closes_from_detail_model_done(tmp_path):
    store = _store(tmp_path)
    _, todo_id = _project_and_todo(store)
    link_id = store.create_work_todo_dingtalk_link(
        work_todo_id=todo_id,
        dingtalk_task_id="dt-task-1",
        executor_user_id="owner-1",
        title_snapshot="给客户同步验收 ETA",
        deadline_at_snapshot="2026-07-01 18:00:00",
        priority_snapshot="P1",
        status="active",
    )
    dws = FakeTodoDws()
    dws.get_payloads["dt-task-1"] = {
        "result": {"todoDetailModel": {"taskId": "dt-task-1", "done": True}}
    }

    updated = pull_dingtalk_todo_statuses(
        store,
        dws,
        now="2026-06-27 11:00:00",
    )

    assert updated == 1
    assert store.get_work_todo(todo_id).status == "done"
    assert store.get_work_todo_dingtalk_link(link_id).status == "done"


def test_pull_dingtalk_todo_skips_cancelled_backfill_link(tmp_path):
    store = _store(tmp_path)
    _, todo_id = _project_and_todo(
        store,
        status="cancelled",
        blocker="routine HR offer-flow step",
    )
    link_id = store.create_work_todo_dingtalk_link(
        work_todo_id=todo_id,
        dingtalk_task_id="dt-task-1",
        executor_user_id="owner-1",
        title_snapshot="给客户同步验收 ETA",
        deadline_at_snapshot="2026-07-01 18:00:00",
        priority_snapshot="P1",
        status="cancelled",
        last_error=(
            "Internal TODO cancelled as routine process; external "
            "cancellation is not part of this change."
        ),
    )
    dws = FakeTodoDws()
    dws.get_payloads["dt-task-1"] = {"id": "dt-task-1", "done": True}

    updated = pull_dingtalk_todo_statuses(
        store,
        dws,
        now="2026-06-27 11:00:00",
    )

    todo = store.get_work_todo(todo_id)
    link = store.get_work_todo_dingtalk_link(link_id)

    assert updated == 0
    assert dws.get_calls == []
    assert todo.status == "cancelled"
    assert todo.blocker == "routine HR offer-flow step"
    assert link.status == "cancelled"
    assert "external cancellation is not part of this change" in link.last_error


def test_refresh_link_before_follow_up_closes_when_dingtalk_done(tmp_path):
    store = _store(tmp_path)
    _, todo_id = _project_and_todo(store)
    link_id = store.create_work_todo_dingtalk_link(
        work_todo_id=todo_id,
        dingtalk_task_id="dt-task-1",
        executor_user_id="owner-1",
        title_snapshot="给客户同步验收 ETA",
        deadline_at_snapshot="2026-07-01 18:00:00",
        priority_snapshot="P1",
        status="active",
    )
    dws = FakeTodoDws()
    dws.get_payloads["dt-task-1"] = {"id": "dt-task-1", "done": True}

    completed, reason = refresh_dingtalk_todo_before_follow_up(
        store,
        dws,
        work_todo_id=todo_id,
        now="2026-06-27 10:00:00",
    )

    assert completed is True
    assert reason == "dingtalk_todo_done"
    assert store.get_work_todo(todo_id).status == "done"
    assert store.get_work_todo_dingtalk_link(link_id).status == "done"
    assert store.get_active_work_todo_dingtalk_link(todo_id) is None


def test_refresh_link_before_follow_up_closes_from_detail_model_done(tmp_path):
    store = _store(tmp_path)
    _, todo_id = _project_and_todo(store)
    link_id = store.create_work_todo_dingtalk_link(
        work_todo_id=todo_id,
        dingtalk_task_id="dt-task-1",
        executor_user_id="owner-1",
        title_snapshot="给客户同步验收 ETA",
        deadline_at_snapshot="2026-07-01 18:00:00",
        priority_snapshot="P1",
        status="active",
    )
    dws = FakeTodoDws()
    dws.get_payloads["dt-task-1"] = {
        "result": {"todoDetailModel": {"taskId": "dt-task-1", "done": True}}
    }

    completed, reason = refresh_dingtalk_todo_before_follow_up(
        store,
        dws,
        work_todo_id=todo_id,
        now="2026-06-27 10:00:00",
    )

    assert completed is True
    assert reason == "dingtalk_todo_done"
    assert store.get_work_todo(todo_id).status == "done"
    assert store.get_work_todo_dingtalk_link(link_id).status == "done"
    assert store.get_active_work_todo_dingtalk_link(todo_id) is None


def test_refresh_link_before_follow_up_without_active_link_does_not_call_dws(
    tmp_path,
):
    store = _store(tmp_path)
    _, todo_id = _project_and_todo(store)
    dws = FakeTodoDws()

    completed, reason = refresh_dingtalk_todo_before_follow_up(
        store,
        dws,
        work_todo_id=todo_id,
        now="2026-06-27 10:00:00",
    )

    assert completed is False
    assert reason == ""
    assert dws.get_calls == []
    assert store.get_work_todo(todo_id).status == "open"


def test_refresh_link_before_follow_up_keeps_open_when_dingtalk_not_done(
    tmp_path,
):
    store = _store(tmp_path)
    _, todo_id = _project_and_todo(store)
    link_id = store.create_work_todo_dingtalk_link(
        work_todo_id=todo_id,
        dingtalk_task_id="dt-task-1",
        executor_user_id="owner-1",
        title_snapshot="给客户同步验收 ETA",
        deadline_at_snapshot="2026-07-01 18:00:00",
        priority_snapshot="P1",
        status="active",
    )
    dws = FakeTodoDws()
    dws.get_payloads["dt-task-1"] = {"id": "dt-task-1", "done": False}

    completed, reason = refresh_dingtalk_todo_before_follow_up(
        store,
        dws,
        work_todo_id=todo_id,
        now="2026-06-27 10:00:00",
    )

    assert completed is False
    assert reason == ""
    assert store.get_work_todo(todo_id).status == "open"
    link = store.get_work_todo_dingtalk_link(link_id)
    assert link.last_pull_at == "2026-06-27 10:00:00"
    assert link.last_dingtalk_done is False
    assert link.last_error == ""


def test_refresh_link_before_follow_up_dws_failure_records_last_error(tmp_path):
    store = _store(tmp_path)
    _, todo_id = _project_and_todo(store)
    link_id = store.create_work_todo_dingtalk_link(
        work_todo_id=todo_id,
        dingtalk_task_id="dt-task-1",
        executor_user_id="owner-1",
        title_snapshot="给客户同步验收 ETA",
        deadline_at_snapshot="2026-07-01 18:00:00",
        priority_snapshot="P1",
        status="active",
    )
    dws = FakeTodoDws()
    dws.get_errors["dt-task-1"] = DwsError("todo get failed")

    completed, reason = refresh_dingtalk_todo_before_follow_up(
        store,
        dws,
        work_todo_id=todo_id,
        now="2026-06-27 10:00:00",
    )

    assert completed is False
    assert reason == ""
    assert store.get_work_todo(todo_id).status == "open"
    assert "todo get failed" in store.get_work_todo_dingtalk_link(link_id).last_error


def test_internal_completion_marks_dingtalk_done(tmp_path):
    store = _store(tmp_path)
    _, todo_id = _project_and_todo(store)
    store.create_work_todo_dingtalk_link(
        work_todo_id=todo_id,
        dingtalk_task_id="dt-task-1",
        executor_user_id="owner-1",
        title_snapshot="给客户同步验收 ETA",
        deadline_at_snapshot="2026-07-01 18:00:00",
        priority_snapshot="P1",
        status="active",
    )
    dws = FakeTodoDws()

    synced = sync_completed_todo_to_dingtalk(
        store,
        dws,
        work_todo_id=todo_id,
        evidence={"source": "reply_attempt:1", "summary": "已发客户"},
        now="2026-06-27 12:00:00",
    )

    assert synced is True
    assert dws.done_calls == [{"task_id": "dt-task-1", "done": True}]
    assert store.get_active_work_todo_dingtalk_link(todo_id) is None
    links = store.list_work_todo_dingtalk_links(statuses=("done",))
    assert links[0].last_push_at == "2026-06-27 12:00:00"


def test_internal_completion_without_active_link_returns_false(tmp_path):
    store = _store(tmp_path)
    _, todo_id = _project_and_todo(store)
    dws = FakeTodoDws()

    synced = sync_completed_todo_to_dingtalk(
        store,
        dws,
        work_todo_id=todo_id,
        evidence={"source": "reply_attempt:1", "summary": "已发客户"},
        now="2026-06-27 12:00:00",
    )

    assert synced is False
    assert dws.done_calls == []


def test_internal_completion_with_blank_task_id_sets_last_error(tmp_path):
    store = _store(tmp_path)
    _, todo_id = _project_and_todo(store)
    link_id = store.create_work_todo_dingtalk_link(
        work_todo_id=todo_id,
        executor_user_id="owner-1",
        title_snapshot="给客户同步验收 ETA",
        deadline_at_snapshot="2026-07-01 18:00:00",
        priority_snapshot="P1",
        status="active",
    )
    dws = FakeTodoDws()

    synced = sync_completed_todo_to_dingtalk(
        store,
        dws,
        work_todo_id=todo_id,
        evidence={"source": "reply_attempt:1", "summary": "已发客户"},
        now="2026-06-27 12:00:00",
    )

    assert synced is False
    assert dws.done_calls == []
    stored = store.get_work_todo_dingtalk_link(link_id)
    assert "no task id" in stored.last_error


def test_internal_completion_dws_failure_records_last_error(tmp_path):
    store = _store(tmp_path)
    _, todo_id = _project_and_todo(store)
    link_id = store.create_work_todo_dingtalk_link(
        work_todo_id=todo_id,
        dingtalk_task_id="dt-task-1",
        executor_user_id="owner-1",
        title_snapshot="给客户同步验收 ETA",
        deadline_at_snapshot="2026-07-01 18:00:00",
        priority_snapshot="P1",
        status="active",
    )
    dws = FakeTodoDws()
    dws.done_error = DwsError("todo done failed")

    synced = sync_completed_todo_to_dingtalk(
        store,
        dws,
        work_todo_id=todo_id,
        evidence={"source": "reply_attempt:1", "summary": "已发客户"},
        now="2026-06-27 12:00:00",
    )

    assert synced is False
    stored = store.get_work_todo_dingtalk_link(link_id)
    assert stored.status == "active"
    assert "todo done failed" in stored.last_error
