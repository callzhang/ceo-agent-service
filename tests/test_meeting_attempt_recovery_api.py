from tests.test_console_web_api import _client
from app.store import AutoReplyStore


def test_meeting_history_rerun_refreshes_source_only_for_latest_attempt(
    tmp_path, monkeypatch
):
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
    calls = []

    def refresh_source(store_arg, dws, *, now, limit, meeting_id, feature_registry):
        calls.append((store_arg, dws, limit, meeting_id))
        return [{"outcome": "queued", "job_id": task_id, "meeting_id": meeting_id}]

    class FakeDws:
        pass

    monkeypatch.setattr(
        "app.web_api.registration.queue_recent_meeting_alignment_replay",
        refresh_source,
        raising=False,
    )
    monkeypatch.setattr("app.audit_web.DwsClient", lambda **_kwargs: FakeDws())

    with _client(tmp_path) as client:
        stale = client.post(f"/api/console/meeting-attempts/{earlier_run}/rerun")
        accepted = client.post(f"/api/console/meeting-attempts/{latest_run}/rerun")

    assert stale.status_code == 409
    assert stale.json()["code"] == "attempt_not_latest"
    assert accepted.status_code == 200
    assert len(calls) == 1
    assert calls[0][0].path == store.path
    assert calls[0][2:] == (1, "meeting-history-rerun")
    assert store.get_meeting_alignment_job(task_id).status == "failed"
