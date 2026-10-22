"""Actual predecessor Errors methods versus the selected Actions component.

Only the Errors store methods are restored from production predecessor 883c7c9f.
Both databases have the same current typed Tasks/Runs/Business parents and
task delivery facts. The native database is obtained by inversing only the
Errors component from a paired current release, using its captured source
metadata. The differing Actions declarations and Config triggers are exactly
those required by the Errors component. This is an Errors-only method seam,
not a whole old-service runtime.
"""

from __future__ import annotations

import ast
from contextlib import closing
from contextvars import ContextVar
from datetime import datetime, timezone
import hashlib
import io
import json
from pathlib import Path
import sqlite3
import subprocess
import tarfile
from tempfile import TemporaryDirectory
from types import SimpleNamespace

import pytest

from app import store as store_module
from app.action_error_definitions import error_projection
from app.action_error_storage import restore_action_error_schema
from app.action_storage import insert_error_action
from app.store import AutoReplyStore
from app.task8_storage import task8_set_parts
import test_action_task_delivery_storage as paired_tests
import test_followup_sync_schema_release as release_tests


PREDECESSOR = "883c7c9f0798b962ad6675eaf00cb2dab0c78114"
EARLIER_PREDECESSOR = "8a3262b921d370d8d9cd90fab33f026ddafc3345"
METHODS = (
    "_compact_settled_reply_task_input",
    "close_unresolved_reply_task_already_settled",
    "reconcile_unresolved_reply_tasks_with_settlement_evidence",
    "reconcile_done_reply_tasks_with_failed_current_run",
    "has_unresolved_error", "record_error", "list_errors", "get_error",
    "list_errors_after", "resolve_errors",
    "resolve_errors_before_successful_component_tick",
    "resolve_unresolved_errors_by_kind",
    "resolve_unresolved_errors_for_conversation_kind",
    "resolve_errors_recovered_by_wechat_reader",
    "resolve_errors_recovered_by_scheduled_service_command",
    "resolve_errors_recovered_by_scheduled_reply_task",
    "resolve_errors_recovered_by_scheduled_task_seed",
    "redact_and_resolve_error",
    "resolve_errors_recovered_by_reply_attempts",
    "resolve_errors_recovered_by_completed_reply_tasks",
    "resolve_errors_recovered_by_terminal_work_summary_inputs",
    "max_error_id", "count_errors", "_operation_logs_base_query",
)
CLOCK = "2026-10-17 12:00:00"


def _native_methods(revision: str) -> dict[str, ast.FunctionDef]:
    source = subprocess.check_output(
        ["git", "show", f"{revision}:app/store.py"], text=True,
        cwd=Path(__file__).parents[1],
    )
    cls = next(node for node in ast.parse(source).body
               if isinstance(node, ast.ClassDef) and node.name == "AutoReplyStore")
    methods = {node.name: node for node in cls.body if isinstance(node, ast.FunctionDef)
               and node.name in METHODS}
    assert set(methods) == set(METHODS)
    return methods


def _native_type() -> type[AutoReplyStore]:
    actual = _native_methods(PREDECESSOR)
    earlier = _native_methods(EARLIER_PREDECESSOR)
    for name in METHODS:
        assert ast.dump(actual[name], include_attributes=False) == ast.dump(
            earlier[name], include_attributes=False,
        ), name
    namespace = dict(vars(store_module))
    source = subprocess.check_output(
        ["git", "show", f"{PREDECESSOR}:app/store.py"], text=True,
        cwd=Path(__file__).parents[1],
    )
    source_ast = ast.parse(source)
    native_history_constant = next(
        node.value.value for node in source_ast.body
        if isinstance(node, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id == "TERMINAL_OA_SOURCE_SKIP_SQL"
                for target in node.targets)
    )
    namespace["TERMINAL_OA_SOURCE_SKIP_SQL"] = native_history_constant
    for name, node in actual.items():
        code = compile(ast.fix_missing_locations(ast.Module(body=[node], type_ignores=[])),
                       f"{PREDECESSOR}:app/store.py:{name}", "exec")
        exec(code, namespace)  # noqa: S102 - exact checked-in git predecessor AST
    return type("NativeErrorStore", (AutoReplyStore,),
                {name: namespace[name] for name in METHODS})


NATIVE_TYPE = _native_type()


class ClockedCurrentStore(AutoReplyStore):
    def _email_actions_history_available(self):
        # The paired release fixture has a staged publication marker. History
        # is filtered to Errors below; prevent unrelated email branch probing.
        return False

    def _email_unsubscribe_receipts_table_exists(self, db):
        # Paired release has not published current version markers. This
        # scenario has no unsubscribe receipts on either side.
        assert not db.execute(
            "select 1 from actions where record_type='email_unsubscribe_receipts' limit 1"
        ).fetchone()
        return False

    def _open_connection(self):
        db = super()._open_connection()
        db.create_function("current_timestamp", 0, lambda: CLOCK)
        return db


class ClockedNativeStore(NATIVE_TYPE):
    def _email_actions_history_available(self):
        return False

    def _open_connection(self):
        db = super()._open_connection()
        db.create_function("current_timestamp", 0, lambda: CLOCK)
        return db


def _store(cls, path: Path):
    # Constructors would migrate the native half. The source declaration has
    # already been verified by release._prepare, so initialize connection
    # state only, then exercise the real Store connection and method bodies.
    obj = object.__new__(cls)
    obj.path = path
    obj.busy_timeout_seconds = 5
    obj.busy_timeout_milliseconds = 5000
    obj._read_snapshot_connection = ContextVar(f"native_error_parity_{id(obj)}", default=None)
    return obj


def _copy(source: Path, dest: Path) -> None:
    with closing(sqlite3.connect(source)) as old, closing(sqlite3.connect(dest)) as new:
        old.backup(new)


@pytest.fixture(scope="module", params=("actual", "fresh_candidate"))
def baseline(request):
    with TemporaryDirectory(prefix="error-native-parity-") as root:
        folder = Path(root)
        source = paired_tests.delivery_paired_source.__wrapped__(
            folder, SimpleNamespace(param=request.param),
        )
        release = release_tests._prepare(source)
        with closing(sqlite3.connect(source)) as db:
            assert db.execute("select 1 from sqlite_schema where name='errors'").fetchone()
        release.apply()
        assert release.source_schema["errors"]["source_table"]
        native = folder / "native-baseline.db"
        _copy(source, native)
        with closing(sqlite3.connect(native)) as db:
            db.execute("pragma foreign_keys=off")
            db.execute("begin immediate")
            restore_action_error_schema(db, release.source_schema["errors"])
            db.commit()
        with closing(sqlite3.connect(source)) as db:
            assert db.execute("select 1 from sqlite_schema where name='errors'").fetchone() is None
        yield native, source, folder


@pytest.fixture
def pair(baseline, request):
    native, current, folder = baseline
    with TemporaryDirectory(prefix=f"{request.node.name}-", dir=folder) as root:
        src = Path(root) / "native.db"
        dst = Path(root) / "current.db"
        _copy(native, src)
        _copy(current, dst)
        yield _store(ClockedNativeStore, src), _store(ClockedCurrentStore, dst)


def _rows(store, *, current: bool):
    with store._connect() as db:
        source = f"({error_projection()})" if current else "errors"
        return [tuple(row) for row in db.execute(
            "select id,conversation_id,message_id,kind,detail,created_at,"
            f"resolved_at,resolution from {source} order by id")]


def _types_and_water(store, *, current: bool):
    with store._connect() as db:
        source = f"({error_projection()})" if current else "errors"
        fields = ("id", "conversation_id", "message_id", "kind", "detail",
                  "created_at", "resolved_at", "resolution")
        types = [tuple(row) for row in db.execute(
            "select " + ",".join(f"typeof({field})" for field in fields)
            + f" from {source} order by id"
        )]
        sequence = db.execute(
            "select seq from sqlite_sequence where name=?",
            ("actions:errors" if current else "errors",),
        ).fetchone()
        return types, None if sequence is None else sequence[0]


def _same(pair):
    native, current = pair
    assert _rows(native, current=False) == _rows(current, current=True)
    assert _types_and_water(native, current=False) == _types_and_water(current, current=True)
    assert native.max_error_id() == current.max_error_id()
    assert native.count_errors() == current.count_errors()


def _seed_both(pair, operation):
    for store in pair:
        with store._connect() as db:
            operation(db)


def _task_set(db, kind, task_id, **values):
    assignment = task8_set_parts(kind, values, payload_source="tasks.payload_json")
    db.execute(
        f"update tasks set {assignment.sql} where record_type=? and id=?",
        (*assignment.parameters, kind, task_id),
    )


def _result(fn, *args, **kwargs):
    try:
        value = fn(*args, **kwargs)
    except Exception as error:
        return ("error", type(error), str(error))
    if isinstance(value, list):
        value = [item.model_dump(mode="python") if hasattr(item, "model_dump") else item
                 for item in value]
    elif hasattr(value, "model_dump"):
        value = value.model_dump(mode="python")
    return ("value", value)


def _call(pair, name, *args, expected_error=None, **kwargs):
    native, current = pair
    left = _result(getattr(native, name), *args, **kwargs)
    right = _result(getattr(current, name), *args, **kwargs)
    assert left == right, (name, left, right)
    if expected_error is None:
        assert left[0] == "value", (name, left)
    else:
        assert left[0] == "error" and left[1] is expected_error, (name, left)
    _same(pair)
    return left[1]


def test_predecessor_all_24_ast_hashes_are_pinned():
    actual = _native_methods(PREDECESSOR)
    assert len(actual) == 24
    combined = "\n".join(ast.dump(actual[name], include_attributes=False)
                         for name in METHODS)
    assert hashlib.sha256(combined.encode()).hexdigest() == (
        "9b416a9aab666987e2b42555a4fe08aee4fa41365e215cc76eb91c51e56e86eb"
    )


def test_public_record_read_resolve_and_validation(pair):
    _same(pair)
    assert _call(pair, "has_unresolved_error", "paired-chat", "worker") is True
    assert _call(pair, "has_unresolved_error", "missing", "worker") is False
    assert _call(pair, "list_errors")
    _call(pair, "list_errors", 1, 0)
    _call(pair, "list_errors", 1, 1)
    _call(pair, "get_error", 7)
    assert _call(pair, "get_error", 999) is None
    _call(pair, "record_error", None, None, "component", "detail")
    assert pair[0].max_error_id() == 13
    _call(pair, "record_error", "cid", "mid", "component", "detail")
    assert [item["id"] for item in _call(pair, "list_errors_after", 7)] == [13, 14]
    assert [item["id"] for item in _call(pair, "list_errors_after", 13)] == [14]
    assert _call(pair, "list_errors_after", 999) == []
    _call(pair, "resolve_errors", [0, -1], resolution="ignored")
    assert _call(pair, "resolve_errors", [7, 7, 999], resolution=" resolved ") == 1
    assert _call(pair, "resolve_errors", [7], resolution="repeat") == 0
    _call(pair, "resolve_errors", [1], resolution=" ", expected_error=ValueError)
    _call(pair, "redact_and_resolve_error", -1, detail="safe", resolution="closed")
    _call(pair, "redact_and_resolve_error", 999, detail="safe", resolution="closed")
    assert _call(pair, "redact_and_resolve_error", 13,
                 detail=" safe ", resolution=" closed ") is True
    _call(pair, "redact_and_resolve_error", 14, detail=" ", resolution="closed",
          expected_error=ValueError)


def test_transaction_rollback_restores_error_rows_types_and_water(pair):
    _same(pair)
    for store, current in ((pair[0], False), (pair[1], True)):
        with pytest.raises(RuntimeError, match="rollback witness"):
            with store._connect() as db:
                db.execute("begin immediate")
                if current:
                    insert_error_action(db, {
                        "conversation_id": "rolled-back", "message_id": None,
                        "kind": "worker", "detail": "transient",
                    })
                else:
                    db.execute(
                        "insert into errors(conversation_id,message_id,kind,detail) "
                        "values('rolled-back',null,'worker','transient')"
                    )
                raise RuntimeError("rollback witness")
    _same(pair)
    _call(pair, "record_error", "after-rollback", None, "worker", "durable")
    assert pair[0].max_error_id() == 13


def test_kind_conversation_tick_and_simple_recovery(pair):
    _call(pair, "record_error", "wechat", "reader", "wechat_reader_unavailable", "offline")
    _call(pair, "record_error", "scheduled-task:1", None, "scheduled_task_seed_failed", "seed")
    _call(pair, "record_error", "cid", "mid", "foo-bar", "one")
    _call(pair, "record_error", "other", "mid", "foo_bar", "two")
    _call(pair, "record_error", "component", "tick", "component", "incident")
    assert _call(pair, "resolve_unresolved_errors_for_conversation_kind", "cid", "foo_bar",
                 resolution=" closed ") == 1
    assert _call(pair, "resolve_unresolved_errors_by_kind", "foo-bar",
                 resolution="closed") == 1
    _call(pair, "resolve_unresolved_errors_by_kind", " ", resolution="closed",
          expected_error=ValueError)
    _call(pair, "resolve_unresolved_errors_for_conversation_kind", " ", "foo",
          resolution="closed", expected_error=ValueError)
    assert _call(pair, "resolve_errors_recovered_by_wechat_reader") == 1
    assert _call(pair, "resolve_errors_recovered_by_wechat_reader") == 0
    assert _call(pair, "resolve_errors_recovered_by_scheduled_task_seed") == 1
    assert _call(pair, "resolve_errors_recovered_by_scheduled_task_seed") == 0
    assert _call(pair, "resolve_errors_before_successful_component_tick", "component",
                 tick_at=datetime(2026, 10, 17, 12, 0, 1, tzinfo=timezone.utc)) == 1
    assert _call(pair, "resolve_errors_before_successful_component_tick", "component",
                 tick_at=datetime(2026, 10, 17, 12, 0, 0, tzinfo=timezone.utc)) == 0
    _call(pair, "resolve_errors_before_successful_component_tick", " ",
          tick_at=datetime.now(timezone.utc), expected_error=ValueError)


def test_parent_recovery_and_history_query(pair):
    native, current = pair
    _call(pair, "record_error", "chat", "message", "reply_task", "task issue")
    _call(pair, "record_error", "attempt-chat", "attempt-msg", "reply_task", "attempt issue")
    input_ids = [store.enqueue_work_summary_input("local_file", "work", "{}")
                 for store in pair]
    assert input_ids[0] == input_ids[1]
    _call(pair, "record_error", "work_summary_input", str(input_ids[0]),
          "task_agent", "work issue")
    for store in pair:
        store.record_reply_attempt(
            conversation_id="attempt-chat", conversation_title="Attempt",
            trigger_message_id="attempt-msg", trigger_sender="sender",
            trigger_text="text", action="agent_run", sensitivity_kind="general",
            send_status="completed",
        )
    _seed_both(pair, lambda db: _task_set(
        db, "reply_tasks", 22, status="done", updated_at="2026-10-17 12:01:00",
    ))
    _seed_both(pair, lambda db: _task_set(
        db, "work_summary_inputs", input_ids[0], status="skipped",
        updated_at="2026-10-17 12:01:00",
    ))
    assert _call(pair, "resolve_errors_recovered_by_reply_attempts") == 1
    assert _call(pair, "resolve_errors_recovered_by_reply_attempts") == 0
    assert _call(pair, "resolve_errors_recovered_by_completed_reply_tasks") == 1
    assert _call(pair, "resolve_errors_recovered_by_completed_reply_tasks") == 0
    assert _call(pair, "resolve_errors_recovered_by_terminal_work_summary_inputs") == 1
    assert _call(pair, "resolve_errors_recovered_by_terminal_work_summary_inputs") == 0
    for sources in (("errors",),):
        left = native._operation_logs_base_query(sources)
        right = current._operation_logs_base_query(sources)
        with native._connect() as db:
            original = [tuple(row) for row in db.execute(
                f"select * from ({left}) where source_table='errors' order by source_id")]
        with current._connect() as db:
            selected = [tuple(row) for row in db.execute(
                f"select * from ({right}) where source_table='errors' order by source_id")]
        assert original == selected


@pytest.fixture(scope="module")
def predecessor_tree():
    with TemporaryDirectory(prefix="error-full-predecessor-") as root:
        directory = Path(root)
        archived = subprocess.check_output(
            ["git", "archive", PREDECESSOR], cwd=Path(__file__).parents[1],
        )
        with tarfile.open(fileobj=io.BytesIO(archived)) as tar:
            tar.extractall(directory, filter="data")
        yield directory


_OLD_START = """
import json, sys
from pathlib import Path
from app.store import AutoReplyStore
from app.email_store import EmailStore
class ClockedStore(AutoReplyStore):
    def _open_connection(self):
        db = super()._open_connection()
        db.create_function('current_timestamp', 0, lambda: '2026-10-17 12:00:00')
        return db
store = ClockedStore(Path(sys.argv[1]))
EmailStore(Path(sys.argv[1]))
"""


def _old_call(tree, path, body):
    process = subprocess.run(
        ["python", "-c", _OLD_START + body, str(path)], cwd=tree,
        capture_output=True, text=True, timeout=180, check=True,
    )
    return process.stdout.strip().splitlines()[-1]


def _task_status(store, task_id, *, native=False):
    with store._connect() as db:
        source = "tasks" if native else f"({store_module.REPLY_TASK_SQL})"
        return tuple(db.execute(
            f"select status,error,execution_generation from {source} where id=?",
            (task_id,),
        ).fetchone())


_INPUT_FIELDS = (
    "id", "reply_task_id", "trigger_text", "trigger_message_json",
    "input_provenance_json", "input_compacted",
)
_TASK_INPUT_FIELDS = (
    "current_input_id", "claimed_input_id", "input_version",
    "claimed_input_version", "trigger_text", "trigger_message_json",
    "input_provenance_json", "input_compacted",
)


def _current_input_snapshot(store, task_id):
    with store._connect() as db:
        task = dict(db.execute(
            "select " + ",".join(_TASK_INPUT_FIELDS)
            + f" from ({store_module.REPLY_TASK_CURRENT_SQL}) where id=?", (task_id,),
        ).fetchone())
        inputs = [dict(row) for row in db.execute(
            "select " + ",".join(_INPUT_FIELDS)
            + f" from ({store_module.REPLY_TASK_INPUT_SQL}) where reply_task_id=? order by id",
            (task_id,),
        )]
    return {"task": task, "inputs": inputs}


def _old_input_snapshot(tree, source, task_id):
    body = f"""
from app.store import REPLY_TASK_CURRENT_SQL
from app.task_storage import REPLY_TASK_INPUT_SQL
with store._connect() as db:
    task = dict(db.execute('select {','.join(_TASK_INPUT_FIELDS)} '
                           f'from ({{REPLY_TASK_CURRENT_SQL}}) where id=?', ({task_id},)).fetchone())
    inputs = [dict(row) for row in db.execute('select {','.join(_INPUT_FIELDS)} '
                f'from ({{REPLY_TASK_INPUT_SQL}}) where reply_task_id=? order by id',
                ({task_id},))]
print(json.dumps({{'task': task, 'inputs': inputs}}, sort_keys=True))
"""
    return json.loads(_old_call(tree, source, body))


def _assert_compacted(before, after):
    assert before != after  # A no-op compactor must fail this witness.
    assert before["inputs"] and after["inputs"]
    assert [row["id"] for row in before["inputs"]] == [
        row["id"] for row in after["inputs"]
    ]
    for field in ("current_input_id", "claimed_input_id", "input_version",
                  "claimed_input_version"):
        assert after["task"][field] == before["task"][field]
    for original, compacted in zip(before["inputs"], after["inputs"], strict=True):
        assert original["trigger_text"]
        assert original["trigger_message_json"]
        assert original["input_compacted"] == 0
        assert compacted["input_compacted"] == 1
        assert compacted["trigger_text"] == ""
        assert compacted["trigger_message_json"] == ""
        provenance = json.loads(compacted["input_provenance_json"])
        for field in ("trigger_text", "trigger_message_json"):
            raw = original[field].encode("utf-8")
            assert provenance[field] == {
                "sha256": hashlib.sha256(raw).hexdigest(), "bytes": len(raw),
            }
    assert after["task"]["input_compacted"] == 1
    assert after["task"]["trigger_text"] == ""
    assert after["task"]["input_provenance_json"] == after["inputs"][0][
        "input_provenance_json"
    ]


def test_full_predecessor_settlement_closure_and_reconciliation(predecessor_tree):
    # This case uses genuine old Tasks scalars because the predecessor methods
    # update them directly. It is separate from the Errors-only hybrid above.
    with TemporaryDirectory(prefix="error-native-settlement-") as root:
        source, target = Path(root) / "source.db", Path(root) / "target.db"
        task_id = int(_old_call(predecessor_tree, source, """
store.enqueue_reply_task(conversation_id='chat', conversation_title='Chat',
    single_chat=True, trigger_message_id='message',
    trigger_create_time='2026-10-17 11:00:00', trigger_sender='sender',
    trigger_text='handle this')
with store._connect() as db:
    task_id = db.execute("select id from tasks where record_type='reply_tasks'").fetchone()[0]
    db.execute("update tasks set status='failed',execution_generation='generation',error='failed' "
               "where record_type='reply_tasks' and id=?", (task_id,))
print(task_id)
"""))
        _copy(source, target)
        release = release_tests._prepare(target)
        release.apply()
        current = _store(ClockedCurrentStore, target)
        before = _old_input_snapshot(predecessor_tree, source, task_id)
        assert before == _current_input_snapshot(current, task_id)
        assert before["inputs"] and any(row["trigger_text"] for row in before["inputs"])
        assert _old_call(predecessor_tree, source, f"""
print(store.close_unresolved_reply_task_already_settled({task_id},
    expected_execution_generation='stale', settled_evidence='receipt'))
""") == "False"
        assert current.close_unresolved_reply_task_already_settled(
            task_id, expected_execution_generation="stale", settled_evidence="receipt",
        ) is False
        assert _old_call(predecessor_tree, source, f"""
print(store.close_unresolved_reply_task_already_settled({task_id},
    expected_execution_generation='generation', settled_evidence='receipt'))
""") == "True"
        assert current.close_unresolved_reply_task_already_settled(
            task_id, expected_execution_generation="generation", settled_evidence="receipt",
        ) is True
        old_errors = _store(ClockedNativeStore, source)
        _same((old_errors, current))
        assert _task_status(old_errors, task_id, native=True) == _task_status(current, task_id)
        after = _old_input_snapshot(predecessor_tree, source, task_id)
        assert after == _current_input_snapshot(current, task_id)
        _assert_compacted(before, after)
        with pytest.raises(AssertionError):
            _assert_compacted(before, before)
        with old_errors._connect() as db:
            db.execute("update tasks set status='failed',error='failed' where id=?", (task_id,))
        with current._connect() as db:
            _task_set(db, "reply_tasks", task_id, status="failed", error="failed")
        assert _old_call(predecessor_tree, source,
                         "print(store.reconcile_unresolved_reply_tasks_with_settlement_evidence())") == "1"
        assert current.reconcile_unresolved_reply_tasks_with_settlement_evidence() == 1
        _same((old_errors, current))
        assert _task_status(old_errors, task_id, native=True) == _task_status(current, task_id)


def test_full_predecessor_done_task_with_failed_current_run(predecessor_tree):
    with TemporaryDirectory(prefix="error-native-failed-run-") as root:
        source, target = Path(root) / "source.db", Path(root) / "target.db"
        task_id = int(_old_call(predecessor_tree, source, """
from app.store import AgentRole
store.enqueue_reply_task(conversation_id='failed-chat', conversation_title='Chat',
    single_chat=True, trigger_message_id='failed-message',
    trigger_create_time='2026-10-17 11:00:00', trigger_sender='sender',
    trigger_text='handle this')
task = store.claim_reply_tasks(limit=1)[0]
run = store.claim_agent_run(task.id, task.execution_generation,
    role=AgentRole.AUDIT, proposal_revision=0, turn_attempt=0,
    parent_agent_run_id=None, operation_id='native-parity-audit', owner='audit').run
store.fail_agent_run(run.id, {'code':'codex_process_failed'}, owner='audit')
with store._connect() as db:
    db.execute("update tasks set status='done',error='' where record_type='reply_tasks' and id=?",
               (task.id,))
print(task.id)
"""))
        _copy(source, target)
        release = release_tests._prepare(target)
        release.apply()
        current = _store(ClockedCurrentStore, target)
        before = _task_status(current, task_id)
        assert before[0] == "done"
        assert _old_call(predecessor_tree, source,
                         "print(store.reconcile_done_reply_tasks_with_failed_current_run())") == "1"
        assert current.reconcile_done_reply_tasks_with_failed_current_run() == 1
        native = _store(ClockedNativeStore, source)
        assert _task_status(native, task_id, native=True) == _task_status(current, task_id)
        _same((native, current))
        assert _old_call(predecessor_tree, source,
                         "print(store.reconcile_done_reply_tasks_with_failed_current_run())") == "0"
        assert current.reconcile_done_reply_tasks_with_failed_current_run() == 0


def test_scheduled_parent_recovery(pair):
    from app.agent_cron.models import ScheduledTaskSnapshot
    from app.run_storage import insert_run
    from app.scheduled_config_storage import intern_scheduled_config

    command_tasks = [store.create_scheduled_task(
        name="command", command="scan-meetings-once", cron_expression="* * * * *",
        timezone_name="UTC",
    ) for store in pair]
    assert command_tasks[0].id == command_tasks[1].id
    command_id = command_tasks[0].id
    _call(pair, "record_error", f"scheduled-task:{command_id}", "command-fail",
          "scheduled_task_service_command_failed", "failed")
    _call(pair, "record_error", "scheduled-task:999", "unrelated",
          "scheduled_task_service_command_failed", "failed")
    for store, task in zip(pair, command_tasks, strict=True):
        with store._connect() as db:
            db.execute("begin immediate")
            snapshot = intern_scheduled_config(db, ScheduledTaskSnapshot.from_task(task).to_json())
            insert_run(db, "scheduled_task_runs", {
                "event_id": "command-success", "scheduled_task_id": task.id,
                "trigger_kind": "scheduled", "scheduled_for": "2026-10-17T12:01:00Z",
                "first_scheduled_for": "2026-10-17T12:01:00Z",
                "dispatch_status": "dispatched", "snapshot_id": snapshot,
                "execution_kind": "service_command", "execution_id": "scan-meetings-once",
                "dispatched_at": "2026-10-17 12:01:00",
            })
    assert _call(pair, "resolve_errors_recovered_by_scheduled_service_command") == 1
    assert _call(pair, "resolve_errors_recovered_by_scheduled_service_command") == 0


def test_scheduled_reply_task_recovery(pair):
    from app.agent_cron.models import ScheduledTaskSnapshot
    from app.run_storage import insert_run
    from app.scheduled_config_storage import intern_scheduled_config

    tasks = [store.create_scheduled_task(
        name="agent", prompt="sync", cron_expression="* * * * *",
        timezone_name="UTC", runtime_id="codex_api",
    ) for store in pair]
    assert tasks[0].id == tasks[1].id
    _call(pair, "record_error", f"scheduled-task:{tasks[0].id}", "agent-fail",
          "scheduled_task_execution_unavailable", "unavailable")
    _call(pair, "record_error", "scheduled-task:999", "other-agent",
          "scheduled_task_execution_unavailable", "unavailable")
    _seed_both(pair, lambda db: _task_set(
        db, "reply_tasks", 22, status="done", updated_at="2026-10-17 12:01:00",
    ))
    for store, task in zip(pair, tasks, strict=True):
        with store._connect() as db:
            db.execute("begin immediate")
            snapshot = intern_scheduled_config(db, ScheduledTaskSnapshot.from_task(task).to_json())
            insert_run(db, "scheduled_task_runs", {
                "event_id": "agent-success", "scheduled_task_id": task.id,
                "trigger_kind": "scheduled", "scheduled_for": "2026-10-17T12:01:00Z",
                "first_scheduled_for": "2026-10-17T12:01:00Z",
                "dispatch_status": "dispatched", "snapshot_id": snapshot,
                "execution_kind": "reply_task", "execution_id": "22",
                "dispatched_at": "2026-10-17 12:01:00",
            })
    assert _call(pair, "resolve_errors_recovered_by_scheduled_reply_task") == 1
    assert _call(pair, "resolve_errors_recovered_by_scheduled_reply_task") == 0
