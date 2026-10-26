from tests.test_console_web_api import _client
from app.store import AutoReplyStore


def test_meeting_history_rerun_reopens_only_latest_failed_attempt(tmp_path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    task_id = store.upsert_meeting_alignment_job(
        meeting_id="meeting-history-rerun",
        title="项目验收会",
        source_json='{"summary":"核对验收标准"}',
        participants_json="[]",
        ended_at="2026-10-22T10:00:00Z",
        eligible_at="2026-10-22T10:05:00Z",
        status="failed",
    )
    earlier_run = store.create_professional_run(task_id, status="failed", error="old")
    latest_run = store.create_professional_run(task_id, status="failed", error="latest")

    with _client(tmp_path) as client:
        stale = client.post(f"/api/console/meeting-attempts/{earlier_run}/rerun")
        accepted = client.post(f"/api/console/meeting-attempts/{latest_run}/rerun")

    assert stale.status_code == 409
    assert stale.json()["code"] == "attempt_not_latest"
    assert accepted.status_code == 200
    assert store.get_meeting_alignment_job(task_id).status == "retry"
