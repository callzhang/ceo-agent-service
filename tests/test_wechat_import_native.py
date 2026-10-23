import sqlite3
from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from app.store import AutoReplyStore
from app.task_entity_storage import create_task, get_task
from app.wechat.memory_import import CodexMemoryExtractionRunner, CodexMemoryRecallMatcher
from app.task_run_entity_assembly import task_run_entity_assembly_objects


class AssembledTaskStore(AutoReplyStore):
    def __init__(self, path):
        self.path = path
        self.db = sqlite3.connect(":memory:")
        self.db.row_factory = sqlite3.Row
        self.db.execute("pragma foreign_keys=on")
        from app.task8_storage import register_task8_functions

        register_task8_functions(self.db)
        objects = task_run_entity_assembly_objects()
        for ddl in objects.values():
            if ddl.lstrip().lower().startswith("create table"):
                self.db.execute(ddl)
        for ddl in objects.values():
            if not ddl.lstrip().lower().startswith("create table"):
                self.db.execute(ddl)

    @contextmanager
    def _connect(self):
        owned = not self.db.in_transaction
        try:
            yield self.db
        except BaseException:
            if owned and self.db.in_transaction:
                self.db.rollback()
            raise
        else:
            if owned and self.db.in_transaction:
                self.db.commit()

    @contextmanager
    def _immediate_write_transaction(self):
        self.db.execute("begin immediate")
        try:
            yield self.db
        except BaseException:
            self.db.rollback()
            raise
        else:
            self.db.commit()

    @contextmanager
    def _agent_run_write_transaction(self, now):
        from datetime import datetime

        value = datetime.fromisoformat(now) if isinstance(now, str) else now or datetime(2026, 10, 23, 12)
        text = value.strftime("%Y-%m-%d %H:%M:%S") if value else "2026-10-23 12:00:00"
        self.db.execute("begin immediate")
        try:
            yield self.db, (value, text)
        except BaseException:
            self.db.rollback()
            raise
        else:
            self.db.commit()


@pytest.fixture
def store(tmp_path):
    return AssembledTaskStore(tmp_path / "native-wechat-import.sqlite3")


def test_wechat_import_begin_uses_native_task_owner(store):
    with pytest.raises(ValueError, match="import_run_id must be non-empty"):
        store.begin_wechat_memory_import_job(import_run_id=" ", account_id="acct")
    with store._connect() as db:
        create_task(db, values={
            "kind": "reply", "status": "pending", "created_at": "now", "updated_at": "now",
        })
    task_id = store.begin_wechat_memory_import_job(import_run_id=" scope ", account_id=" acct ")
    assert task_id == 2
    task = get_task(store.db, task_id)
    assert task["kind"] == "wechat_import" and task["status"] == "running"
    assert task["parameters"] == {"import_run_id": "scope", "account_id": "acct"}
    assert not store.db.execute("select 1 from runs").fetchone()


def test_wechat_import_terminal_write_is_native_and_idempotent(store):
    first = store.begin_wechat_memory_import_job(import_run_id="scope", account_id="acct")
    second = store.begin_wechat_memory_import_job(import_run_id="scope", account_id="acct")
    assert first != second
    attempt = store.claim_runtime_operation_attempt(
        "memory", f"wechat_memory_import_job:{first}", "codex_oauth",
        "codex_cli", "local_oauth", "gpt-5.5",
    )
    independent = store.claim_runtime_operation_attempt(
        "memory", f"wechat_memory_import_job:{second}", "codex_oauth",
        "codex_cli", "local_oauth", "gpt-5.5",
    )
    assert attempt.workload_key == f"wechat_memory_import_job:{first}"
    assert independent.workload_key == f"wechat_memory_import_job:{second}"
    assert independent.id != attempt.id
    assert attempt.parent_id is None
    store.mark_agent_runtime_attempt_running(attempt.id)
    store.complete_agent_runtime_attempt(attempt.id, "session", "", 0, 0)
    store.finish_wechat_memory_import_job(first, status="completed")
    prior = tuple(store.db.execute(
        "select updated_at from tasks where id=?", (first,)
    ).fetchone()) + tuple(store.db.execute(
        "select finished_at,result_json from task_results where task_id=? and result_kind='wechat_import'",
        (first,),
    ).fetchone())
    store.finish_wechat_memory_import_job(first, status="completed")
    current = tuple(store.db.execute(
        "select updated_at from tasks where id=?", (first,)
    ).fetchone()) + tuple(store.db.execute(
        "select finished_at,result_json from task_results where task_id=? and result_kind='wechat_import'",
        (first,),
    ).fetchone())
    assert current == prior
    assert current[2] is None
    assert current[0] == current[1]
    with pytest.raises(ValueError, match="conflicting WeChat Memory import terminal"):
        store.finish_wechat_memory_import_job(first, status="failed", error="changed")


def test_wechat_import_attempt_parent_requires_running_native_import_task(store):
    task_id = store.begin_wechat_memory_import_job(import_run_id="scope", account_id="acct")
    key = f"wechat_memory_import_job:{task_id}"
    assert store._validate_runtime_operation_workload("memory", key) == ("memory", key)
    assert store._runtime_operation_parent_exists(store.db, "memory", key)
    store.finish_wechat_memory_import_job(task_id, status="failed", error="bad")
    assert not store._runtime_operation_parent_exists(store.db, "memory", key)
    with pytest.raises(ValueError, match="parent does not exist"):
        store.claim_runtime_operation_attempt(
            "memory", key, "codex_oauth", "codex_cli", "local_oauth", "gpt-5.5"
        )
    with store._connect() as db:
        other = create_task(db, values={
            "kind": "email_classification", "status": "running", "error": "",
            "created_at": "now", "updated_at": "now",
        })
    other_key = f"wechat_memory_import_job:{other['id']}"
    assert not store._runtime_operation_parent_exists(store.db, "memory", other_key)
    with pytest.raises(ValueError, match="parent does not exist"):
        store.claim_runtime_operation_attempt(
            "memory", other_key, "codex_oauth", "codex_cli", "local_oauth", "gpt-5.5"
        )
    for bad_key in ("wechat_memory_import_job:0", "wechat_memory_import_job:01", "wechat_memory_import_job:nope"):
        with pytest.raises(ValueError, match="native Task"):
            store._validate_runtime_operation_workload("memory", bad_key)


class PersistedRoutedExecution:
    def __init__(self, store, raw):
        self.store = store
        self.raw = raw
        self.calls = []

    def execute(self, **kwargs):
        self.calls.append(kwargs)
        attempt = self.store.claim_runtime_operation_attempt(
            kwargs["workload_kind"], kwargs["workload_key"], "codex_oauth",
            "codex_cli", "local_oauth", "gpt-5.5",
        )
        self.store.mark_agent_runtime_attempt_running_once(attempt.id)
        try:
            value = kwargs["parser"](self.raw)
        except Exception:
            self.store.fail_agent_runtime_attempt(
                attempt.id, "result", "invalid_result", False
            )
            raise
        self.store.complete_agent_runtime_attempt(attempt.id, "session", "", 0, 0)
        return SimpleNamespace(value=value, session_id="session")


@pytest.mark.parametrize("producer", ["extraction", "recall"])
@pytest.mark.parametrize("succeeds", [True, False])
def test_actual_wechat_producers_use_native_task_and_persisted_attempt(
    tmp_path, producer, succeeds
):
    store = AssembledTaskStore(tmp_path / f"{producer}-{succeeds}.sqlite3")
    with store._connect() as db:
        create_task(db, values={
            "kind": "reply", "status": "pending", "created_at": "now", "updated_at": "now",
        })
    if producer == "extraction":
        raw = '{"candidates": []}' if succeeds else "{}"
        routed = PersistedRoutedExecution(store, raw)
        runner = CodexMemoryExtractionRunner(
            tmp_path, store=store, routed_execution=routed
        )
        def invoke():
            return runner.extract([], import_run_id="scope", account_id="acct")
    else:
        raw = '{"matches":[{"statement":"fact","relation":"none","memory_id":"","evidence":"","merged_statement":""}]}' if succeeds else "{}"
        routed = PersistedRoutedExecution(store, raw)
        matcher = CodexMemoryRecallMatcher(
            tmp_path, store=store, routed_execution=routed
        )
        def invoke():
            return matcher._match_statement(
                "fact", import_run_id="scope", account_id="acct"
            )
    if succeeds:
        invoke()
    else:
        with pytest.raises(Exception):
            invoke()

    assert len(routed.calls) == 1
    task_id = int(routed.calls[0]["workload_key"].rsplit(":", 1)[1])
    assert routed.calls[0]["workload_key"] == f"wechat_memory_import_job:{task_id}"
    task = get_task(store.db, task_id)
    assert task["kind"] == "wechat_import"
    assert task["status"] == ("completed" if succeeds else "failed")
    assert task["parameters"] == {"import_run_id": "scope", "account_id": "acct"}
    result = store.db.execute(
        "select finished_at,result_json from task_results where task_id=? and result_kind='wechat_import'",
        (task_id,),
    ).fetchone()
    assert result["finished_at"] and result["result_json"] is None
    attempt = store.db.execute(
        "select parent_id,status from runtime_attempts where workload_kind='memory' and workload_key=?",
        (routed.calls[0]["workload_key"],),
    ).fetchone()
    assert attempt["parent_id"] is None
    assert attempt["status"] == ("completed" if succeeds else "failed")
    assert store.db.execute("select count(*) from runs").fetchone()[0] == 0
