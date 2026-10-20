"""Actual reviewed Store19.7 source -> current facts -> strict old cold open."""
from contextlib import closing
import inspect
import io
from pathlib import Path
import sqlite3
import subprocess
import tarfile

import pytest

from app.store_schema_release import prepare_database_schema_release
from app.task_lifecycle_schema_release import prepare_task_lifecycle_schema_release
from tests.test_shared_action_release import _old_process
from tests import task_lifecycle_caller_fixture as calls

ROOT = Path(__file__).resolve().parents[1]
SOURCE_HEAD = "1aed63ec3c9abb345b685749e70852958bf22776"


@pytest.fixture(scope="module")
def archived_source(tmp_path_factory):
    root = tmp_path_factory.mktemp("actual-store19-7")
    archive = subprocess.check_output(["git", "archive", SOURCE_HEAD, "app", "tests", "scripts"], cwd=ROOT)
    with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
        tar.extractall(root, filter="data")
    return root


def seed(root, path):
    _old_process(root, """
from pathlib import Path
import sys
from app.store import AutoReplyStore
from app.email_store import EmailStore
from app.state_storage import update_state_rows
store=AutoReplyStore(Path(sys.argv[1])); EmailStore(store.path)
with store._immediate_write_transaction() as db:
 update_state_rows(db,'service_state',{'updated_at':b'original-source-clock'},
  where_sql='key=?',parameters=('store_schema_version',))
""", path)


def cold(root, path):
    _old_process(root, """
from pathlib import Path
import sys
from app.store import AutoReplyStore
from app.email_store import EmailStore
from app.workbench.store import WorkbenchStore
p=Path(sys.argv[1]); AutoReplyStore(p); EmailStore(p); WorkbenchStore(p)
""", path)


def execute(root, path):
    return _old_process(root, inspect.getsource(calls) + """
import json,sys
print(json.dumps(exercise(sys.argv[1]),ensure_ascii=False))
""", path)


def test_all_nine_actual_dao_paths_current_inverse_cold_and_reforward(archived_source, tmp_path):
    source, target = tmp_path / "source.sqlite3", tmp_path / "target.sqlite3"
    seed(archived_source, source)
    with closing(sqlite3.connect(source)) as db, closing(sqlite3.connect(target)) as other:
        db.backup(other)  # Independent test branch, never used by the inverse.
    before = calls.facts(source)
    release = prepare_database_schema_release(ROOT, target)
    assert type(release).__name__ == "TaskLifecycleSchemaRelease"
    release.publish()
    assert calls.facts(target) == before
    expected = execute(archived_source, source)
    actual = execute(ROOT, target)
    assert actual == expected
    current = calls.facts(target)
    assert current == calls.facts(source)
    release.verify_loaded()
    release.rollback()
    assert calls.facts(target) == current
    cold(archived_source, target)
    assert calls.facts(target) == current
    next_release = prepare_database_schema_release(ROOT, target)
    next_release.publish()
    next_release.finalize()
    assert calls.facts(target) == current
    assert prepare_database_schema_release(ROOT, target) is None


def test_unknown_source_and_wrong_pair_refuse_before_write(archived_source, tmp_path):
    from app.store import AutoReplyStore
    from app.email_store import EmailStore
    path = tmp_path / "source.sqlite3"
    seed(archived_source, path)
    before = path.read_bytes()
    for constructor in (AutoReplyStore, EmailStore):
        with pytest.raises(sqlite3.IntegrityError):
            constructor(path)
        assert path.read_bytes() == before
    with closing(sqlite3.connect(path)) as db:
        db.execute("create index unknown_task_index on tasks(legacy_key)")
        db.commit()
    before = path.read_bytes()
    with pytest.raises(ValueError, match="declaration"):
        prepare_task_lifecycle_schema_release(ROOT, path)
    assert path.read_bytes() == before


def test_failed_target_load_inverse_keeps_new_facts_and_original_clock(archived_source, tmp_path, monkeypatch):
    from app import store_schema_release
    from app.result_schema_release import _markers
    path = tmp_path / "source.sqlite3"
    seed(archived_source, path)
    with closing(sqlite3.connect(path)) as db:
        markers = _markers(db)
    release = prepare_task_lifecycle_schema_release(ROOT, path)
    current = None

    def fail_after_new_facts(root, database):
        nonlocal current
        execute(ROOT, database)
        current = calls.facts(database)
        raise RuntimeError("injected post-publication load failure")

    monkeypatch.setattr(store_schema_release, "_migrate_store", fail_after_new_facts)
    with pytest.raises(RuntimeError, match="injected post-publication"):
        release.publish()
    release.rollback()
    release.rollback()
    assert calls.facts(path) == current
    with closing(sqlite3.connect(path)) as db:
        assert _markers(db) == markers
    cold(archived_source, path)


def test_pruned_claim_absence_inverse_reforward_and_source_generation_reset(archived_source, tmp_path):
    source, target = tmp_path / "source.sqlite3", tmp_path / "target.sqlite3"
    seed(archived_source, source)
    with closing(sqlite3.connect(source)) as db, closing(sqlite3.connect(target)) as other:
        db.backup(other)
    release = prepare_database_schema_release(ROOT, target)
    release.publish()
    code = inspect.getsource(calls) + "\nimport json,sys\nprint(json.dumps(prune_cycle(sys.argv[1])))"
    assert _old_process(archived_source, code, source) == _old_process(ROOT, code, target)
    current = calls.facts(target)
    assert current == calls.facts(source)
    with closing(sqlite3.connect(target)) as db:
        assert db.execute("select lease_owner,owner_pid,generation,lease_expires_at,adapter_name "
                          "from tasks where record_type='work_summary_inputs'").fetchone() == (None,) * 5
    release.rollback()
    cold(archived_source, target)
    prepare_database_schema_release(ROOT, target).publish()
    assert calls.facts(target) == current
    code = inspect.getsource(calls) + "\nimport json,sys\nprint(json.dumps(prune_cycle(sys.argv[1],claim_again=True)))"
    assert _old_process(archived_source, code, source) == _old_process(ROOT, code, target) == 1


def test_actual_source_claim_cost_retains_indexed_selection_and_inherited_fk_work(archived_source, tmp_path):
    source, target = tmp_path / "source.sqlite3", tmp_path / "target.sqlite3"
    seed(archived_source, source)
    with closing(sqlite3.connect(source)) as db, closing(sqlite3.connect(target)) as other:
        db.backup(other)
    prepare_database_schema_release(ROOT, target).publish()
    code = inspect.getsource(calls) + "\nimport json,sys\nprint(json.dumps(write_cost(sys.argv[1])))"
    old = _old_process(archived_source, code, source)
    current = _old_process(ROOT, code, target)
    print("\nsource_claim_cost", old)
    print("\ncurrent_claim_cost", current)
    assert old["sqlite_version"] == current["sqlite_version"]
    for kind, sample in current["before"].items():
        crowded = current["with_3000_unrelated"][kind]
        old_sample = old["before"][kind]
        old_crowded = old["with_3000_unrelated"][kind]
        # Progress callbacks sample every 100 VM steps. The source's generated
        # parent-key FK check already scans its child containers. C1 adds only
        # fixed work; it must not add a history-dependent selection/update scan.
        extra_before = sample["vm"] - old_sample["vm"]
        extra_after = crowded["vm"] - old_crowded["vm"]
        assert abs(extra_after - extra_before) <= 200
        assert abs((crowded["vm"] - sample["vm"]) -
                   (old_crowded["vm"] - old_sample["vm"])) <= 200
        for point in (sample, crowded, old_sample, old_crowded):
            assert point["task_updates"] == 1
            assert point["selection_plan_without_fk"][0].startswith("SEARCH tasks USING ")
        for point in (sample, crowded):
            assert not any(line.startswith(("SCAN tasks", "SCAN actions"))
                           for line in point["selection_plan_without_fk"])
