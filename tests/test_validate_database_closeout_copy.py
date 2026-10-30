from app.store import AutoReplyStore
from scripts.validate_database_closeout_copy import validate


def test_validate_uses_native_meeting_run_identity_on_sparse_copy(tmp_path):
    path = tmp_path / "copy.sqlite3"
    store = AutoReplyStore(path)
    job = store.upsert_meeting_alignment_job(
        meeting_id="validation-meeting", title="Validation meeting", source_json="{}",
        participants_json="[]", ended_at="", eligible_at="", status="pending",
    )
    run_id = store.create_professional_run(job, status="completed")

    result = validate(path)

    assert result["meeting_run_id"] == run_id
    assert result["persisted_values_unchanged"] is True
    assert result["real_sends"] == 0
    assert any(check["url"] == f"/api/console/meeting-attempts/{run_id}"
               for check in result["checks"])
