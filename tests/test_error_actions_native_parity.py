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
import hashlib
import io
import json
from pathlib import Path
import sqlite3
import subprocess
import tarfile
from tempfile import TemporaryDirectory

import pytest

from app import store as store_module
from app.action_error_definitions import error_projection
from app.store import AutoReplyStore
from app.task8_storage import task8_set_parts


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


def test_predecessor_all_24_ast_hashes_are_pinned():
    actual = _native_methods(PREDECESSOR)
    assert len(actual) == 24
    combined = "\n".join(ast.dump(actual[name], include_attributes=False)
                         for name in METHODS)
    assert hashlib.sha256(combined.encode()).hexdigest() == (
        "9b416a9aab666987e2b42555a4fe08aee4fa41365e215cc76eb91c51e56e86eb"
    )


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


