import json

import pytest

from app.error_code_migration import migrate_legacy_meeting_projections
from app.store import AutoReplyStore
from app.error_code_migration import MEETING_JOB_SQL
from app.store import MEETING_LOGICAL_RUN_SQL
from app.task_entity_storage import update_task_cas


def failed_meeting(store, public_id, error):
    task_id = store.upsert_meeting_alignment_job(
        meeting_id=f"meeting-{public_id}", title="Test", source_json="{}",
        participants_json="[]", ended_at="2026-10-11 00:00:00",
        eligible_at="2026-10-11 00:00:00", status="failed",
    )
    with store._connect() as db:
        update_task_cas(db, task_id=task_id, expected={"status": "failed"},
                        changes={"error": json.dumps(error)})
    return task_id


@pytest.fixture
def store(tmp_path):
    return AutoReplyStore(tmp_path / "migration.sqlite3")


def test_migration_updates_projection_and_preserves_run_history(store):
    job_id = failed_meeting(store, 700, {"kind": "meeting_agent", "message": "runtime_effect_policy_violation"})
    run_id = store.create_professional_run(job_id, status="failed", error="runtime_effect_policy_violation")

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
    job_id = failed_meeting(store, 701, {"kind": "meeting_send", "message": "multi-party meeting has no sendable group"})
    result = migrate_legacy_meeting_projections(store, dry_run=False)
    assert result.changed == 1
    with store._connect() as db:
        payload = json.loads(db.execute(f"select error from ({MEETING_JOB_SQL}) where id=?", (job_id,)).fetchone()[0])
    assert payload["code"] == "provider_target_failed"
    assert payload["source_code"] == "multi-party meeting has no sendable group"
