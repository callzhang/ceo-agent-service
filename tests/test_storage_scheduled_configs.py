from app.run_storage import SCHEDULED_RUN_SQL
import sqlite3
from datetime import timedelta

from app.store import AutoReplyStore
from app.configs_definitions import config_projection
from app.schedules_storage import update_schedule
from tests.test_scheduled_task_store import NOW, _create_task


def test_runs_share_exact_config_without_copying_json(tmp_path):
    store = AutoReplyStore(tmp_path / "cron.sqlite3")
    task = _create_task(store)
    first = store.create_scheduled_task_run(task.id, trigger_kind="manual", scheduled_for=NOW)
    second = store.create_scheduled_task_run(task.id, trigger_kind="manual", scheduled_for=NOW + timedelta(minutes=1))
    with sqlite3.connect(store.path) as db:
        columns = {row[1] for row in db.execute("pragma table_info(runs)")}
        assert "snapshot_json" not in columns
        refs = db.execute(f"select snapshot_id from ({SCHEDULED_RUN_SQL}) as scheduled_task_runs order by id").fetchall()
        assert refs[0] == refs[1]
        assert db.execute(f"select count(*) from ({config_projection('scheduled_task_config_versions')})").fetchone()[0] == 1
    assert store.get_scheduled_task_run(first.id).snapshot == first.snapshot
    assert store.get_scheduled_task_run(second.id).snapshot == first.snapshot


def test_config_update_keeps_queued_input(tmp_path):
    store = AutoReplyStore(tmp_path / "cron.sqlite3")
    task = _create_task(store)
    first = store.create_scheduled_task_run(task.id, trigger_kind="manual", scheduled_for=NOW)
    with sqlite3.connect(store.path) as db:
        db.execute("begin immediate")
        assert update_schedule(db, task.id, task.version, {"prompt": "new prompt"}) == 1
    second = store.create_scheduled_task_run(task.id, trigger_kind="manual", scheduled_for=NOW + timedelta(minutes=1))
    assert store.get_scheduled_task_run(first.id).snapshot.prompt == task.prompt
    assert second.snapshot.prompt == "new prompt"
    with sqlite3.connect(store.path) as db:
        assert db.execute(f"select count(*) from ({config_projection('scheduled_task_config_versions')})").fetchone()[0] == 2
