"""End-to-end regression for the user-feedback processing workflow.

This fixture models the historical feedback reported for attempt #8308.  It
uses the real local Console API and persists only the deterministic summary
and references; no model invocation is involved in the import step.
"""

import subprocess
from pathlib import Path

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


def _resolution_receipt(
    commit_sha: str,
    *,
    test_name: str,
    before_pid: int,
    after_pid: int,
) -> dict[str, object]:
    return {
        "commit_sha": commit_sha,
        "test_evidence": {test_name: {"exit_code": 0}},
        "restart_evidence": {
            "launchd_label": "com.ceo-agent-service.main",
            "before_pid": before_pid,
            "after_pid": after_pid,
        },
        "health_evidence": {
            "url": "http://127.0.0.1:8765/healthz",
            "status_code": 200,
            "ok": True,
        },
    }


def test_attempt_8308_feedback_processing_requires_complete_receipts(
    tmp_path: Path, monkeypatch
):
    db_path = tmp_path / "worker.sqlite3"
    store = AutoReplyStore(db_path)
    attempt_id, _token = _seed_attempt_8308(store)
    original_comment = store.get_feedback_event("feedback-8308").comment

    valid_commit = "a" * 40
    missing_commit = "b" * 40
    nonancestor_commit = "c" * 40
    git_calls: list[list[str]] = []
    original_subprocess_run = subprocess.run

    def checked_git(command, **kwargs):
        assert isinstance(command, list)
        if not command or command[0] != "git":
            return original_subprocess_run(command, **kwargs)
        assert kwargs.get("shell", False) is False
        git_calls.append(command)
        if command == ["git", "cat-file", "-e", f"{missing_commit}^{{commit}}"]:
            return subprocess.CompletedProcess(command, 1, "", "missing commit")
        if command == ["git", "cat-file", "-e", f"{nonancestor_commit}^{{commit}}"]:
            return subprocess.CompletedProcess(command, 0, "", "")
        if command == [
            "git",
            "merge-base",
            "--is-ancestor",
            nonancestor_commit,
            "main",
        ]:
            return subprocess.CompletedProcess(command, 1, "", "")
        if command == ["git", "cat-file", "-e", f"{valid_commit}^{{commit}}"]:
            return subprocess.CompletedProcess(command, 0, "", "")
        if command == [
            "git",
            "merge-base",
            "--is-ancestor",
            valid_commit,
            "main",
        ]:
            return subprocess.CompletedProcess(command, 0, "", "")
        raise AssertionError(f"unexpected git command: {command}")

    monkeypatch.setattr(registration_module.subprocess, "run", checked_git)

    round_one_receipt = _resolution_receipt(
        valid_commit,
        test_name="round-one",
        before_pid=100,
        after_pid=101,
    )
    round_two_receipt = _resolution_receipt(
        valid_commit,
        test_name="round-two",
        before_pid=200,
        after_pid=201,
    )

    with _client(tmp_path) as client:
        health = client.get("/healthz")
        assert health.status_code == 200
        assert health.json() == {"ok": True, "status": "ok"}
        pending = client.get("/api/console/feedback?status=pending&page_size=50")
        assert pending.status_code == 200
        row = next(item for item in pending.json()["items"] if item["feedback_key"] == "feedback-8308")
        assert row["attempt_id"] == str(attempt_id) == "8308"
        assert row["status"] == "pending"
        assert row["comment"] == original_comment
        assert row["summary"] == "已有摘要：反馈入口需要纳入用户反馈处理闭环。"
        assert {reference["label"] for reference in row["references"]} >= {"attempt#8308", "run#445"}
        assert {reference["route"] for reference in row["references"] if reference["label"] == "attempt#8308"} == {"/attempts/8308"}

        unknown = client.post(
            "/api/console/feedback/items/unknown/reopen",
            json={"reason": "The earlier resolution was premature."},
        )
        assert unknown.status_code == 404
        assert unknown.json()["code"] == "not_found"
        for invalid_body in (
            {},
            {"reason": "   "},
            {"reason": "premature", "extra": True},
        ):
            invalid = client.post(
                "/api/console/feedback/items/feedback-8308/reopen",
                json=invalid_body,
            )
            assert invalid.status_code == 422
            assert invalid.json()["code"] == "feedback_reopen_invalid"
        for invalid_request in (
            client.post(
                "/api/console/feedback/items/feedback-8308/reopen",
                json=[],
            ),
            client.post(
                "/api/console/feedback/items/feedback-8308/reopen",
                content="not-json",
                headers={"Content-Type": "application/json"},
            ),
            client.post(
                "/api/console/feedback/items/feedback-8308/reopen",
                content='{"reason":"premature"}',
                headers={"Content-Type": "text/plain"},
            ),
        ):
            assert invalid_request.status_code == 422
            assert invalid_request.json()["code"] == "feedback_reopen_invalid"

        pending_reopen = client.post(
            "/api/console/feedback/items/feedback-8308/reopen",
            json={"reason": "Pending retry is idempotent."},
        )
        assert pending_reopen.status_code == 200
        assert pending_reopen.json()["item"]["status"] == "pending"
        assert pending_reopen.json()["item"]["current_processing"] is None
        assert pending_reopen.json()["item"]["processing_history"] == []

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

        round_one_id = batch["items"][0]["id"]
        processing_reopen = client.post(
            "/api/console/feedback/items/feedback-8308/reopen",
            json={"reason": "Do not interrupt the active claim."},
        )
        assert processing_reopen.status_code == 409
        assert processing_reopen.json()["code"] == "feedback_reopen_processing"

        incomplete = client.post(
            f"/api/console/feedback/batches/{batch_id}/resolve",
            json={},
        )
        assert incomplete.status_code == 409
        assert incomplete.json()["code"] == "feedback_resolution_incomplete"

        user_backlog = client.post(
            f"/api/console/feedback/batches/{batch_id}/resolve",
            json={
                **round_one_receipt,
                "backlog_evidence": {
                    "processing": 0,
                    "failed": 0,
                    "retryable": 0,
                },
            },
        )
        assert user_backlog.status_code == 409
        assert user_backlog.json()["code"] == "feedback_resolution_invalid"

        # Even a complete-looking top-level receipt cannot resolve until the
        # item itself contains its task/turn association and matching evidence.
        blocked = client.post(
            f"/api/console/feedback/batches/{batch_id}/resolve",
            json=round_one_receipt,
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
                **round_one_receipt,
            },
        )
        assert patched.status_code == 200

        missing_commit_result = client.post(
            f"/api/console/feedback/batches/{batch_id}/resolve",
            json={**round_one_receipt, "commit_sha": missing_commit},
        )
        assert missing_commit_result.status_code == 409
        assert missing_commit_result.json()["code"] == "feedback_resolution_incomplete"
        assert store.get_feedback_processing_item("feedback-8308").status == "processing"

        nonancestor_result = client.post(
            f"/api/console/feedback/batches/{batch_id}/resolve",
            json={**round_one_receipt, "commit_sha": nonancestor_commit},
        )
        assert nonancestor_result.status_code == 409
        assert nonancestor_result.json()["code"] == "feedback_resolution_incomplete"
        assert store.get_feedback_processing_item("feedback-8308").status == "processing"

        resolved = client.post(
            f"/api/console/feedback/batches/{batch_id}/resolve",
            json=round_one_receipt,
        )
        assert resolved.status_code == 200
        assert resolved.json()["item"] == {"batch_id": batch_id, "status": "resolved"}

        detail = client.get(f"/api/console/feedback/batches/{batch_id}")
        assert detail.status_code == 200
        assert detail.json()["item"]["status"] == "resolved"
        assert detail.json()["item"]["items"][0]["status"] == "resolved"
        assert detail.json()["item"]["items"][0]["round_number"] == 1

        resolved_item = client.get("/api/console/feedback/feedback-8308")
        assert resolved_item.status_code == 200
        assert resolved_item.json()["item"]["current_processing"]["id"] == round_one_id
        assert [
            item["round_number"]
            for item in resolved_item.json()["item"]["processing_history"]
        ] == [1]
        assert resolved_item.json()["item"]["processing_history"][0][
            "backlog_evidence"
        ] == {"processing": 0, "failed": 0, "retryable": 0}

        reopen_reason = "  The earlier resolution preceded the completed repair.  "
        reopened = client.post(
            "/api/console/feedback/items/feedback-8308/reopen",
            json={"reason": reopen_reason},
        )
        assert reopened.status_code == 200
        reopened_item = reopened.json()["item"]
        assert reopened_item["status"] == "pending"
        assert reopened_item["current_processing"] is None
        assert reopened_item["processing_history"][0]["reopen_reason"] == reopen_reason
        assert reopened_item["processing_history"][0]["id"] == round_one_id

        pending_again = client.post(
            "/api/console/feedback/items/feedback-8308/reopen",
            json={"reason": "This must not replace the first reason."},
        )
        assert pending_again.status_code == 200
        assert pending_again.json()["item"]["processing_history"][0][
            "reopen_reason"
        ] == reopen_reason

        old_batch = client.get(f"/api/console/feedback/batches/{batch_id}")
        assert old_batch.status_code == 200
        old_batch_item = old_batch.json()["item"]
        assert old_batch_item["status"] == "resolved"
        assert old_batch_item["items"][0]["id"] == round_one_id
        assert old_batch_item["items"][0]["round_number"] == 1
        assert old_batch_item["items"][0]["commit_sha"] == valid_commit

        pending_after_reopen = client.get("/api/console/feedback?status=pending")
        pending_rows = [
            item
            for item in pending_after_reopen.json()["items"]
            if item["feedback_key"] == "feedback-8308"
        ]
        assert len(pending_rows) == 1
        assert pending_rows[0]["batch_id"] == ""

        claimed_again = client.post(
            "/api/console/feedback/batches",
            json={"feedback_keys": ["feedback-8308"]},
        )
        assert claimed_again.status_code == 200
        batch_two = claimed_again.json()["item"]
        batch_two_id = batch_two["batch_id"]
        round_two = batch_two["items"][0]
        assert batch_two_id != batch_id
        assert round_two["round_number"] == 2
        assert round_two["id"] != round_one_id
        assert round_two["workbench_task_id"] == ""
        assert round_two["workbench_turn_id"] == ""
        assert round_two["commit_sha"] == ""
        assert round_two["test_evidence"] == {}
        assert round_two["restart_evidence"] == {}
        assert round_two["health_evidence"] == {}

        patched_again = client.patch(
            "/api/console/feedback/items/feedback-8308",
            json={
                "workbench_task_id": "task-224",
                "workbench_turn_id": "turn-545",
                "attempt_id": 8308,
                "agent_run_id": 545,
                **round_two_receipt,
            },
        )
        assert patched_again.status_code == 200

        stale_round_one = client.post(
            f"/api/console/feedback/batches/{batch_two_id}/resolve",
            json=round_one_receipt,
        )
        assert stale_round_one.status_code == 409
        assert stale_round_one.json()["code"] == "feedback_resolution_incomplete"
        assert store.get_feedback_processing_item("feedback-8308").status == "processing"

        resolved_again = client.post(
            f"/api/console/feedback/batches/{batch_two_id}/resolve",
            json=round_two_receipt,
        )
        assert resolved_again.status_code == 200
        assert resolved_again.json()["item"] == {
            "batch_id": batch_two_id,
            "status": "resolved",
        }

        final_detail = client.get("/api/console/feedback/feedback-8308").json()["item"]
        assert final_detail["current_processing"]["id"] == round_two["id"]
        assert [item["round_number"] for item in final_detail["processing_history"]] == [2, 1]
        assert final_detail["processing_history"][0]["test_evidence"] == round_two_receipt["test_evidence"]
        assert final_detail["processing_history"][1]["test_evidence"] == round_one_receipt["test_evidence"]

        new_batch = client.get(f"/api/console/feedback/batches/{batch_two_id}")
        assert new_batch.status_code == 200
        assert new_batch.json()["item"]["items"][0]["round_number"] == 2
        old_batch_after = client.get(f"/api/console/feedback/batches/{batch_id}")
        assert old_batch_after.json()["item"]["items"][0]["round_number"] == 1

        with store._connect() as db:
            db.execute(
                "update feedback_processing_items set current_round_id=999999 "
                "where feedback_key='feedback-8308'"
            )
        history_error = client.post(
            "/api/console/feedback/items/feedback-8308/reopen",
            json={"reason": "The history pointer is intentionally damaged."},
        )
        assert history_error.status_code == 409
        assert history_error.json()["code"] == "feedback_reopen_history_incomplete"
        assert store.get_feedback_event("feedback-8308").resolved_at
        with store._connect() as db:
            db.execute(
                "update feedback_processing_items set current_round_id=? "
                "where feedback_key='feedback-8308'",
                (round_two["id"],),
            )

        resolved_after = client.get("/api/console/feedback?status=resolved")
        resolved_rows = [
            item
            for item in resolved_after.json()["items"]
            if item["feedback_key"] == "feedback-8308"
        ]
        assert len(resolved_rows) == 1
        resolved_row = resolved_rows[0]
        assert resolved_row["status"] == "resolved"

    assert [call[1] for call in git_calls] == [
        "cat-file",
        "merge-base",
        "cat-file",
        "cat-file",
        "merge-base",
        "cat-file",
        "merge-base",
        "cat-file",
        "merge-base",
        "cat-file",
        "merge-base",
    ]
    assert all("rev-parse" not in call for call in git_calls)

    assert store.get_feedback_event("feedback-8308").comment == original_comment
    assert store.get_feedback_processing_item("feedback-8308").status == "resolved"
