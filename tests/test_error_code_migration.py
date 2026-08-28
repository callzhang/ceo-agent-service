import json

import pytest

from app.error_code_migration import migrate_legacy_meeting_projections
from app.store import AutoReplyStore


@pytest.fixture
def store(tmp_path):
    return AutoReplyStore(tmp_path / "migration.sqlite3")


def test_migration_updates_projection_and_preserves_run_history(store):
    with store._connect() as db:
        db.execute(
            """insert into meeting_alignment_jobs
               (meeting_id,title,source_json,participants_json,status,attempts,error)
               values ('meeting-1','Test','{}','[]','failed',1,?)""",
            (json.dumps({"kind": "meeting_agent", "message": "runtime_effect_policy_violation"}),),
        )
        job_id = db.execute("select last_insert_rowid()").fetchone()[0]
        db.execute(
            """insert into meeting_alignment_runs
               (job_id,status,error) values (?, 'failed', ?)""",
            (job_id, "runtime_effect_policy_violation"),
        )

    preview = migrate_legacy_meeting_projections(store)
    assert preview == preview.__class__(scanned=1, changed=1, dry_run=True)
    with store._connect() as db:
        assert json.loads(
            db.execute("select error from meeting_alignment_jobs where id=?", (job_id,)).fetchone()[0]
        )["message"] == "runtime_effect_policy_violation"

    result = migrate_legacy_meeting_projections(store, dry_run=False)
    assert result == result.__class__(scanned=1, changed=1, dry_run=False)
    with store._connect() as db:
        projection = json.loads(
            db.execute("select error from meeting_alignment_jobs where id=?", (job_id,)).fetchone()[0]
        )
        history = db.execute(
            "select error from meeting_alignment_runs where job_id=?", (job_id,)
        ).fetchone()[0]
    assert projection["code"] == "runtime_execution_failed"
    assert projection["legacy_code"] == "meeting_agent"
    assert history == "runtime_effect_policy_violation"


def test_migration_maps_meeting_target_failure(store):
    with store._connect() as db:
        db.execute(
            """insert into meeting_alignment_jobs
               (meeting_id,title,source_json,participants_json,status,attempts,error)
               values ('meeting-2','Test','{}','[]','failed',3,?)""",
            (json.dumps({"kind": "meeting_send", "message": "multi-party meeting has no sendable group"}),),
        )
    result = migrate_legacy_meeting_projections(store, dry_run=False)
    assert result.changed == 1
    with store._connect() as db:
        payload = json.loads(db.execute("select error from meeting_alignment_jobs order by id desc limit 1").fetchone()[0])
    assert payload["code"] == "provider_target_failed"
    assert payload["source_code"] == "multi-party meeting has no sendable group"
