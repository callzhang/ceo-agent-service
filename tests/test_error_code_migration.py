import json

import pytest

from app.error_code_migration import migrate_legacy_meeting_projections
from app.store import AutoReplyStore
from app.task_storage import MEETING_JOB_SQL, insert_task
from app.run_storage import insert_run
from app.store import MEETING_LOGICAL_RUN_SQL


@pytest.fixture
def store(tmp_path):
    return AutoReplyStore(tmp_path / "migration.sqlite3")


def test_migration_updates_projection_and_preserves_run_history(store):
    with store._connect() as db:
        job_id = 700
        physical = insert_task(db, "meeting_alignment_jobs", {
            "legacy_key": job_id, "meeting_id": "meeting-1", "title": "Test",
            "source_json": "{}", "participants_json": "[]", "status": "failed",
            "attempts": 1, "error": json.dumps({"kind": "meeting_agent", "message": "runtime_effect_policy_violation"}),
        }).lastrowid
        insert_run(db, "meeting_alignment_runs", {
            "legacy_key": 900, "task_id": physical, "status": "failed",
            "error": "runtime_effect_policy_violation",
        })

    preview = migrate_legacy_meeting_projections(store)
    assert preview == preview.__class__(scanned=1, changed=1, dry_run=True)
    with store._connect() as db:
        assert json.loads(
            db.execute(f"select error from ({MEETING_JOB_SQL}) where id=?", (job_id,)).fetchone()[0]
        )["message"] == "runtime_effect_policy_violation"

    result = migrate_legacy_meeting_projections(store, dry_run=False)
    assert result == result.__class__(scanned=1, changed=1, dry_run=False)
    with store._connect() as db:
        projection = json.loads(
            db.execute(f"select error from ({MEETING_JOB_SQL}) where id=?", (job_id,)).fetchone()[0]
        )
        history = db.execute(
            f"select error from ({MEETING_LOGICAL_RUN_SQL}) where job_id=?", (job_id,)
        ).fetchone()[0]
    assert projection["code"] == "runtime_execution_failed"
    assert projection["legacy_code"] == "meeting_agent"
    assert history == "runtime_effect_policy_violation"


def test_migration_maps_meeting_target_failure(store):
    with store._connect() as db:
        insert_task(db, "meeting_alignment_jobs", {
            "legacy_key": 701, "meeting_id": "meeting-2", "title": "Test",
            "source_json": "{}", "participants_json": "[]", "status": "failed",
            "attempts": 3, "error": json.dumps({"kind": "meeting_send", "message": "multi-party meeting has no sendable group"}),
        })
    result = migrate_legacy_meeting_projections(store, dry_run=False)
    assert result.changed == 1
    with store._connect() as db:
        payload = json.loads(db.execute(f"select error from ({MEETING_JOB_SQL}) order by id desc limit 1").fetchone()[0])
    assert payload["code"] == "provider_target_failed"
    assert payload["source_code"] == "multi-party meeting has no sendable group"
