import json
from pathlib import Path

import pytest

from app.store import AutoReplyStore
from app.task_deadline_backfill import (
    TodoDeadlineDecision,
    backfill_todo_deadlines,
    list_open_todos_without_deadline,
    validate_todo_deadline,
)

NOW = "2026-09-17T09:00:00+08:00"


class FakeRunner:
    def __init__(self, deadline_by_title: dict[str, str]):
        self.deadline_by_title = deadline_by_title
        self.calls: list[int] = []

    def infer(self, *, todo, project, now):
        self.calls.append(todo.id)
        deadline = self.deadline_by_title[todo.title]
        if deadline == "RAISE":
            raise RuntimeError("runtime unavailable")
        return TodoDeadlineDecision(deadline_at=deadline, reason="按工作量推断")


def _store(tmp_path: Path) -> AutoReplyStore:
    return AutoReplyStore(tmp_path / "deadline.sqlite3")


def _project(store: AutoReplyStore) -> int:
    return store.create_work_project(
        title="客户交付",
        category="projects",
        tags_json=json.dumps(["客户交付"], ensure_ascii=False),
        status="active",
        priority="P1",
        risk_level="medium",
    )


def _todo(store, project_id, title, *, owner="owner-1", deadline="", status="open"):
    return store.create_work_todo(
        project_id=project_id,
        title=title,
        owner_user_id=owner,
        owner_name="Alex" if owner else "",
        status=status,
        priority="P1",
        deadline_at=deadline,
    )


def _outbox(store):
    return {row["work_todo_id"]: row for row in store.list_task_todo_sync_outbox()}


def test_only_open_todos_without_a_usable_deadline_are_selected(tmp_path):
    store = _store(tmp_path)
    project = _project(store)
    missing = _todo(store, project, "缺截止日期")
    unparsable = _todo(store, project, "日期不可解析", deadline="尽快")
    _todo(store, project, "已有截止日期", deadline="2026-09-30 18:00:00")
    _todo(store, project, "已完成", status="done")

    ids = [todo.id for todo in list_open_todos_without_deadline(store)]

    assert ids == [missing, unparsable]


def test_dry_run_decides_but_writes_nothing_and_queues_nothing(tmp_path):
    store = _store(tmp_path)
    project = _project(store)
    todo_id = _todo(store, project, "补交报价")
    runner = FakeRunner({"补交报价": "2026-09-25T18:00:00+08:00"})

    result = backfill_todo_deadlines(store, runner, dry_run=True, now=NOW)

    assert result.inspected == 1
    assert result.deadlines_set == 0 and result.mirrors_queued == 0
    assert result.decisions[0]["deadline_at"] == "2026-09-25T18:00:00+08:00"
    assert store.get_work_todo(todo_id).deadline_at == ""
    assert _outbox(store) == {}


def test_apply_writes_the_deadline_and_mirrors_only_owned_todos(tmp_path):
    store = _store(tmp_path)
    project = _project(store)
    owned = _todo(store, project, "有负责人")
    ownerless = _todo(store, project, "无负责人", owner="")
    runner = FakeRunner({
        "有负责人": "2026-09-25T18:00:00+08:00",
        "无负责人": "2026-09-26T18:00:00+08:00",
    })

    result = backfill_todo_deadlines(store, runner, dry_run=False, now=NOW)

    assert result.deadlines_set == 2
    assert result.mirrors_queued == 1
    assert store.get_work_todo(owned).deadline_at == "2026-09-25T18:00:00+08:00"
    assert store.get_work_todo(ownerless).deadline_at == "2026-09-26T18:00:00+08:00"
    outbox = _outbox(store)
    assert set(outbox) == {owned}
    assert outbox[owned]["operation"] == "create"
    assert outbox[owned]["operation_key"] == f"deadline-backfill:{owned}:create"


def test_a_second_apply_does_not_queue_a_duplicate_mirror(tmp_path):
    store = _store(tmp_path)
    project = _project(store)
    _todo(store, project, "补交报价")
    runner = FakeRunner({"补交报价": "2026-09-25T18:00:00+08:00"})

    backfill_todo_deadlines(store, runner, dry_run=False, now=NOW)
    second = backfill_todo_deadlines(store, runner, dry_run=False, now=NOW)

    assert second.inspected == 0
    assert len(store.list_task_todo_sync_outbox()) == 1


@pytest.mark.parametrize(
    "deadline",
    ["", "尽快", "2026-09-10T18:00:00+08:00", "2026-09-17T09:00:00+08:00"],
)
def test_a_missing_unparsable_or_past_deadline_is_rejected(deadline):
    with pytest.raises(ValueError):
        validate_todo_deadline(
            TodoDeadlineDecision.model_construct(deadline_at=deadline, reason="x"),
            now=NOW,
        )


def test_one_failing_todo_does_not_stop_the_batch(tmp_path):
    store = _store(tmp_path)
    project = _project(store)
    bad = _todo(store, project, "推断失败")
    good = _todo(store, project, "推断成功")
    runner = FakeRunner({
        "推断失败": "RAISE",
        "推断成功": "2026-09-25T18:00:00+08:00",
    })

    result = backfill_todo_deadlines(store, runner, dry_run=False, now=NOW)

    assert result.failed == 1 and result.deadlines_set == 1
    assert store.get_work_todo(bad).deadline_at == ""
    assert store.get_work_todo(good).deadline_at == "2026-09-25T18:00:00+08:00"


def test_a_past_deadline_from_the_agent_is_not_written(tmp_path):
    store = _store(tmp_path)
    project = _project(store)
    todo_id = _todo(store, project, "过去的日期")
    runner = FakeRunner({"过去的日期": "2026-06-30T18:00:00+08:00"})

    result = backfill_todo_deadlines(store, runner, dry_run=False, now=NOW)

    assert result.failed == 1 and result.deadlines_set == 0
    assert store.get_work_todo(todo_id).deadline_at == ""
    assert _outbox(store) == {}


def test_a_dry_run_failure_records_no_error(tmp_path):
    store = _store(tmp_path)
    project = _project(store)
    _todo(store, project, "推断失败")
    runner = FakeRunner({"推断失败": "RAISE"})

    result = backfill_todo_deadlines(store, runner, dry_run=True, now=NOW)

    assert result.failed == 1
    with store._connect() as db:
        count = db.execute(
            "select count(*) from errors where kind='todo_deadline_backfill'"
        ).fetchone()[0]
    assert count == 0


def test_the_deadline_backfill_workload_key_is_accepted_by_the_store():
    AutoReplyStore._validate_runtime_operation_workload("task", "27:deadline_backfill")
    with pytest.raises(ValueError, match="unsupported suffix"):
        AutoReplyStore._validate_runtime_operation_workload("task", "27:something_else")


def test_the_parser_reads_a_codex_jsonl_stream():
    from app.task_deadline_backfill import parse_todo_deadline_decision

    decision_text = '{"deadline_at": "2026-09-25T18:00:00+08:00", "reason": "原文写明"}'
    raw = "\n".join([
        json.dumps({"type": "turn.started"}),
        json.dumps({"type": "item.completed", "item": {"type": "agent_message", "text": decision_text}}),
        json.dumps({"type": "turn.completed"}),
    ])

    assert parse_todo_deadline_decision(raw).deadline_at == "2026-09-25T18:00:00+08:00"


def test_the_parser_reads_a_plain_text_reply_with_a_think_block():
    """Friday Runtime returns plain text, often with a reasoning block first."""
    from app.task_deadline_backfill import parse_todo_deadline_decision

    raw = (
        "<think>\\n这个 TODO 没写日期，按工作量估两周。\\n</think>\\n"
        "```json\\n"
        '{"deadline_at": "2026-10-01T18:00:00+08:00", "reason": "按工作量推断"}\\n'
        "```"
    )

    assert parse_todo_deadline_decision(raw).deadline_at == "2026-10-01T18:00:00+08:00"


def test_the_parser_prefers_the_final_answer_over_an_earlier_draft():
    from app.task_deadline_backfill import parse_todo_deadline_decision

    raw = (
        '草稿 {"deadline_at": "2026-09-20T18:00:00+08:00", "reason": "草稿"} '
        '最终 {"deadline_at": "2026-09-28T18:00:00+08:00", "reason": "最终"}'
    )

    assert parse_todo_deadline_decision(raw).reason == "最终"


def test_the_repair_prompt_names_the_rule_the_deadline_broke():
    from app.task_deadline_backfill import _deadline_repair_prompt

    past = '{"deadline_at": "2026-06-30T18:00:00+08:00", "reason": "x"}'

    assert "is not after now" in _deadline_repair_prompt(past, now=NOW)
    assert "no TodoDeadlineDecision JSON object" in _deadline_repair_prompt("抱歉", now=NOW)


class OutageRunner:
    """Fail the way the router does when every route is paused."""

    def __init__(self) -> None:
        self.calls: list[int] = []

    def infer(self, *, todo, project, now):
        from app.agent_runtime_router import RoutedCodexExecutionError

        self.calls.append(todo.id)
        raise RoutedCodexExecutionError(
            "runtime_execution_failed", "no_eligible_route", runtime_unavailable=True
        )


def test_a_runtime_outage_stops_the_batch_without_filing_errors(tmp_path):
    """Seen live: a provider outage filed one Attention row per TODO."""
    store = _store(tmp_path)
    project = _project(store)
    first = _todo(store, project, "第一条")
    _todo(store, project, "第二条")
    runner = OutageRunner()

    result = backfill_todo_deadlines(store, runner, dry_run=False, now=NOW)

    assert runner.calls == [first]
    assert result.deferred == 1 and result.failed == 0
    with store._connect() as db:
        count = db.execute(
            "select count(*) from errors where kind='todo_deadline_backfill'"
        ).fetchone()[0]
    assert count == 0


def test_a_deadline_backfill_attempt_belongs_to_its_todo_not_a_project(tmp_path):
    """Seen live: TODO 3322 was rejected because no project had id 3322."""
    store = _store(tmp_path)
    project = _project(store)
    todo_id = _todo(store, project, "编号比所有项目都大")

    with store._connect() as db:
        assert store._runtime_operation_parent_exists(
            db, "task", f"{todo_id + 1000}:deadline_backfill"
        ) is False
        assert store._runtime_operation_parent_exists(
            db, "task", f"{todo_id}:deadline_backfill"
        ) is True
    store.update_work_todo(todo_id, status="done")
    with store._connect() as db:
        assert store._runtime_operation_parent_exists(
            db, "task", f"{todo_id}:deadline_backfill"
        ) is False


def _spend_correction(store, key: str) -> None:
    from app.agent_runtime_contracts import CredentialMode, RuntimeKind, RuntimeRoute

    route = RuntimeRoute(
        name="codex_api",
        runtime_kind=RuntimeKind.CODEX_CLI,
        credential_mode=CredentialMode.SERVICE_API,
        model="test-model",
    )
    attempt = store.claim_runtime_operation_attempt(
        "task", key, route.name, route.runtime_kind.value,
        route.credential_mode.value, route.model, owner="test-owner",
    )
    store.fail_agent_runtime_attempt(
        attempt.id, "result", "runtime_result_validation_failed", False,
        owner="test-owner",
    )


def test_a_todo_whose_correction_was_spent_gets_a_new_generation(tmp_path):
    """Seen live: TODOs 327 and 357 could never run again after one bad batch."""
    from app.task_deadline_backfill import deadline_backfill_workload_key

    store = _store(tmp_path)
    project = _project(store)
    todo_id = _todo(store, project, "上一批没返回 JSON")

    assert deadline_backfill_workload_key(store, todo_id) == f"{todo_id}:deadline_backfill"
    _spend_correction(store, f"{todo_id}:deadline_backfill")
    assert deadline_backfill_workload_key(store, todo_id) == f"{todo_id}:deadline_backfill.1"
    _spend_correction(store, f"{todo_id}:deadline_backfill.1")
    assert deadline_backfill_workload_key(store, todo_id) == f"{todo_id}:deadline_backfill.2"


def test_a_generation_key_is_accepted_and_checked_against_its_todo(tmp_path):
    store = _store(tmp_path)
    project = _project(store)
    todo_id = _todo(store, project, "第二代")

    AutoReplyStore._validate_runtime_operation_workload("task", f"{todo_id}:deadline_backfill.3")
    with pytest.raises(ValueError, match="unsupported suffix"):
        AutoReplyStore._validate_runtime_operation_workload("task", f"{todo_id}:deadline_backfill.0")
    with store._connect() as db:
        assert store._runtime_operation_parent_exists(db, "task", f"{todo_id}:deadline_backfill.3")
