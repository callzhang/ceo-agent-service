"""End-to-end regression for the user-feedback processing workflow.

This fixture models the historical feedback reported for attempt #8308.  It
uses the real local Console API and persists only the deterministic summary
and references; no model invocation is involved in the import step.
"""

import subprocess
from pathlib import Path
from types import SimpleNamespace

from fastapi.testclient import TestClient

import app.web_api.registration as registration_module
from app.audit_web import create_audit_app
from app.store import AutoReplyStore


class _NonExecutingExecutor:
    def __init__(self, workspace: Path):
        self.workspace = workspace

    def recover(self):
        return 0

    def run_once(self):
        return []

    def stop(self, turn_id):
        del turn_id
        return None

    def confirm(self, confirmation_id):
        raise AssertionError(f"unexpected confirmation: {confirmation_id}")

    def cancel(self, confirmation_id):
        raise AssertionError(f"unexpected cancellation: {confirmation_id}")

    def close(self):
        return True


def _client(tmp_path: Path) -> TestClient:
    assets = tmp_path / "assets"
    assets.mkdir()
    (assets / "index.html").write_text("<html>workbench</html>", encoding="utf-8")
    return TestClient(
        create_audit_app(
            tmp_path / "worker.sqlite3",
            workbench_asset_dir=assets,
            workbench_workspace=tmp_path,
            workbench_executor=_NonExecutingExecutor(tmp_path),
            spa_enabled=False,
        ),
        client=("127.0.0.1", 50000),
        headers={"Host": "127.0.0.1:8765"},
    )


def _seed_attempt_8308(store: AutoReplyStore) -> tuple[int, str]:
    """Seed a sent reply and feedback event with the historical attempt id."""

    token = "feedback-token-8308"
    store.upsert_conversation(
        "conversation-8308",
        title="用户反馈回归",
        single_chat=False,
        codex_session_id="session-8308",
    )
    # A fresh test database has no reply attempts.  Setting the AUTOINCREMENT
    # sequence makes the fixture exercise the exact historical attempt route.
    with store._connect() as db:
        db.execute(
            "insert or replace into sqlite_sequence(name, seq) values('reply_attempts', 8307)"
        )
    attempt_id = store.record_reply_attempt(
        conversation_id="conversation-8308",
        conversation_title="用户反馈回归",
        trigger_message_id="message-8308",
        trigger_sender="Derek",
        trigger_text="请处理 attempt#8308 的反馈",
        action="send_reply",
        sensitivity_kind="general",
        codex_reason="历史服务反馈回归",
        draft_reply_text="先复现问题，再修复并验证。",
        codex_session_id="session-8308",
        audit_summary="已有摘要：反馈入口需要纳入用户反馈处理闭环。",
        send_status="sent",
    )
    assert attempt_id == 8308
    store.update_reply_attempt(
        attempt_id,
        final_reply_text="已发送的原始回复，不应因处理反馈而改写。",
        permission_action="allow",
        send_status="sent",
    )
    store.record_sent_reply(
        "conversation-8308",
        "message-8308",
        "已发送的原始回复，不应因处理反馈而改写。",
        feedback_token=token,
    )
    with store._connect() as db:
        # Resolution requires a durable positive run id.  The run is not
        # needed for this API fixture; its detail route remains conservative
        # unless an actual agent-run role is present.
        db.execute("update reply_attempts set agent_run_id=445 where id=?", (attempt_id,))
    store.upsert_feedback_event(
        key="feedback-8308",
        feedback_token=token,
        rating="negative",
        rating_label="不满意",
        comment="原始用户反馈：这个反馈没有处理。",
        original_text="请修复 attempt#8308 的反馈入口。",
        reply_text="已发送的原始回复，不应因处理反馈而改写。",
        source="dingtalk",
        received_at="2026-08-29 10:00:00",
    )
    return attempt_id, token


def test_attempt_8308_feedback_processing_requires_complete_receipts(
    tmp_path: Path, monkeypatch
):
    db_path = tmp_path / "worker.sqlite3"
    store = AutoReplyStore(db_path)
    attempt_id, _token = _seed_attempt_8308(store)
    original_comment = store.get_feedback_event("feedback-8308").comment

    with _client(tmp_path) as client:
        pending = client.get("/api/console/feedback?status=pending&page_size=50")
        assert pending.status_code == 200
        row = next(item for item in pending.json()["items"] if item["feedback_key"] == "feedback-8308")
        assert row["attempt_id"] == str(attempt_id) == "8308"
        assert row["status"] == "pending"
        assert row["comment"] == original_comment
        assert row["summary"] == "已有摘要：反馈入口需要纳入用户反馈处理闭环。"
        assert {reference["label"] for reference in row["references"]} >= {"attempt#8308", "run#445"}
        assert {reference["route"] for reference in row["references"] if reference["label"] == "attempt#8308"} == {"/attempts/8308"}

        claimed = client.post(
            "/api/console/feedback/batches",
            json={"feedback_keys": ["feedback-8308"]},
        )
        assert claimed.status_code == 200
        batch = claimed.json()["item"]
        batch_id = batch["batch_id"]
        assert batch["status"] == "processing"
        assert "attempt#8308 (/attempts/8308)" in batch["start_message"]
        assert "原始用户反馈" not in batch["start_message"]
        assert "已有摘要：反馈入口需要纳入用户反馈处理闭环。" in batch["start_message"]

        incomplete = client.post(
            f"/api/console/feedback/batches/{batch_id}/resolve",
            json={},
        )
        assert incomplete.status_code == 409
        assert incomplete.json()["code"] == "feedback_resolution_incomplete"

        head = subprocess.run(
            ["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=True
        ).stdout.strip()

        # Even a complete-looking top-level receipt cannot resolve until the
        # item itself contains its task/turn association and matching evidence.
        blocked = client.post(
            f"/api/console/feedback/batches/{batch_id}/resolve",
            json={
                "commit_sha": head,
                "test_evidence": {"pytest": {"exit_code": 0}},
                "restart_evidence": {
                    "launchd_label": "com.ceo-agent-service.main",
                    "before_pid": 100,
                    "after_pid": 101,
                },
                "health_evidence": {
                    "url": "http://127.0.0.1:8765/healthz",
                    "status_code": 200,
                    "ok": True,
                },
            },
        )
        assert blocked.status_code == 409
        assert blocked.json()["code"] == "feedback_resolution_incomplete"
        assert store.get_feedback_processing_batch(batch_id).status == "processing"

        patched = client.patch(
            "/api/console/feedback/items/feedback-8308",
            json={
                "workbench_task_id": "task-124",
                "workbench_turn_id": "turn-445",
                "attempt_id": 8308,
                "agent_run_id": 445,
                "commit_sha": head,
                "test_evidence": {"pytest": {"exit_code": 0}},
                "restart_evidence": {
                    "launchd_label": "com.ceo-agent-service.main",
                    "before_pid": 100,
                    "after_pid": 101,
                },
                "health_evidence": {
                    "url": "http://127.0.0.1:8765/healthz",
                    "status_code": 200,
                    "ok": True,
                },
            },
        )
        assert patched.status_code == 200

        # The route obtains the current HEAD from git.  Mock that receipt so
        # this test remains deterministic even when run from another checkout.
        monkeypatch.setattr(
            registration_module.subprocess,
            "run",
            lambda *args, **kwargs: SimpleNamespace(stdout=f"{head}\n"),
        )
        resolved = client.post(
            f"/api/console/feedback/batches/{batch_id}/resolve",
            json={
                "commit_sha": head,
                "test_evidence": {"pytest": {"exit_code": 0}},
                "restart_evidence": {
                    "launchd_label": "com.ceo-agent-service.main",
                    "before_pid": 100,
                    "after_pid": 101,
                },
                "health_evidence": {
                    "url": "http://127.0.0.1:8765/healthz",
                    "status_code": 200,
                    "ok": True,
                },
            },
        )
        assert resolved.status_code == 200
        assert resolved.json()["item"] == {"batch_id": batch_id, "status": "resolved"}

        detail = client.get(f"/api/console/feedback/batches/{batch_id}")
        assert detail.status_code == 200
        assert detail.json()["item"]["status"] == "resolved"
        assert detail.json()["item"]["items"][0]["status"] == "resolved"

        pending_after = client.get("/api/console/feedback?status=pending")
        assert all(item["feedback_key"] != "feedback-8308" for item in pending_after.json()["items"])
        resolved_after = client.get("/api/console/feedback?status=resolved")
        resolved_row = next(item for item in resolved_after.json()["items"] if item["feedback_key"] == "feedback-8308")
        assert resolved_row["status"] == "resolved"

    assert store.get_feedback_event("feedback-8308").comment == original_comment
    assert store.get_feedback_processing_item("feedback-8308").status == "resolved"
