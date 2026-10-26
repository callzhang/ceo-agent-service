import json
import pytest

from app.audit_web import _queue_attention_rows
from app.store import AutoReplyStore
from app.dws_client import DwsOaApprovalCandidate
from app.task_scanners import (
    _pending_oa_task_ids_for_current_user,
    scan_pending_oa_approvals,
)


def test_current_oa_pending_node_remains_attention_after_comment_done(tmp_path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    task = store.ensure_reply_task(
        conversation_id="oa_pending_scan:instance", conversation_title="OA",
        single_chat=True, trigger_message_id="message", trigger_sender="OA",
        trigger_text="Review leave", trigger_create_time="2026-10-13T00:00:00Z",
        business_object_key="oa:instance:node",
    )
    with store._connect() as db:
        db.execute("update tasks set status='done', updated_at='2026-10-13 00:00:00' where id=?", (task.id,))
    store.set_daily_scan_state("oa_pending", last_success_at="2026-10-13T01:00:00Z",
        cursor_json=json.dumps({"pending_business_object_keys": ["oa:instance:node"]}),
        last_error="")
    rows = _queue_attention_rows(store)
    assert [(r["category"], r["id"]) for r in rows] == [("OA approval", str(task.id))]
    assert rows[0]["status"] == "pending"
    assert rows[0]["error"] == (
        "DingTalk still reports this OA process as pending; the tracked task has had no update for over one hour."
    )
    assert "no final approval or return receipt" not in rows[0]["error"]
    assert "principal OA node is still pending" not in rows[0]["error"]
    store.set_daily_scan_state("oa_pending", last_success_at="2026-10-13T02:00:00Z",
        cursor_json=json.dumps({"pending_business_object_keys": []}), last_error="")
    assert _queue_attention_rows(store) == []


def test_completed_oa_from_previous_cursor_is_removed_from_attention(tmp_path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    key = "oa:instance:node"
    store.set_daily_scan_state(
        "oa_pending",
        last_success_at="2026-10-13T01:00:00Z",
        cursor_json=json.dumps({"pending_business_object_keys": [key]}),
        last_error="",
    )

    class Provider:
        def list_pending_oa_approvals(self, **kwargs):
            return []

        def read_oa_approval_tasks(self, instance):
            raise AssertionError("no task-list read is needed for an empty pending page")

        def read_oa_process_instance_openapi(self, instance):
            assert instance == "instance"
            return {
                "result": {
                    "status": "COMPLETED",
                    "processInstanceResult": "agree",
                    "tasks": [{"taskId": "node", "status": "COMPLETED"}],
                }
            }

        def get_current_user_id(self):
            return "principal"

    scan_pending_oa_approvals(store, Provider())
    assert _queue_attention_rows(store) == []
    cursor = json.loads(store.get_daily_scan_state("oa_pending")["cursor_json"])
    assert cursor["pending_business_object_keys"] == []


def test_verified_pending_node_without_a_local_task_is_not_attention_yet(tmp_path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    store.set_daily_scan_state("oa_pending", last_success_at="2026-10-13T01:00:00Z",
        cursor_json=json.dumps({"pending_business_object_keys": ["oa:instance:second"]}),
        last_error="")
    # Attention lists a pending node through its tracked Task, and only once that
    # Task has been quiet for an hour (fe50e1176); no Task yet means nothing to show.
    assert _queue_attention_rows(store) == []


def test_scanner_retains_pending_nodes_when_detail_read_fails_after_pending_disappears(tmp_path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    store.set_daily_scan_state("oa_pending", last_success_at="2026-10-13T01:00:00Z",
        cursor_json=json.dumps({"pending_business_object_keys": ["oa:instance:node"]}),
        last_error="")
    class Provider:
        present = True
        def list_pending_oa_approvals(self, **kwargs):
            return [DwsOaApprovalCandidate(process_instance_id="instance", title="Leave")] if self.present else []
        def read_oa_approval_tasks(self, instance):
            raise ValueError("source unavailable")
        def read_oa_process_instance_openapi(self, instance):
            raise ValueError("source unavailable")
        def get_current_user_id(self):
            return "principal"
    provider = Provider()
    scan_pending_oa_approvals(store, provider)
    cursor = json.loads(store.get_daily_scan_state("oa_pending")["cursor_json"])
    assert cursor["pending_business_object_keys"] == ["oa:instance:node"]
    provider.present = False
    scan_pending_oa_approvals(store, provider)
    cursor = json.loads(store.get_daily_scan_state("oa_pending")["cursor_json"])
    assert cursor["pending_business_object_keys"] == ["oa:instance:node"]
    assert not any(row["category"] == "OA approval" for row in _queue_attention_rows(store))


def test_pending_list_failure_cannot_erase_last_verified_pending_nodes(tmp_path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    cursor = json.dumps({"pending_business_object_keys": ["oa:instance:node"]})
    store.set_daily_scan_state("oa_pending", last_success_at="2026-10-13T01:00:00Z",
        cursor_json=cursor, last_error="")
    class Provider:
        def list_pending_oa_approvals(self, **kwargs):
            raise ValueError("provider read failed")
        def read_oa_approval_tasks(self, instance):
            raise AssertionError("not reached")
        def read_oa_process_instance_openapi(self, instance):
            raise AssertionError("not reached")
    assert scan_pending_oa_approvals(store, Provider()) == 0
    state = store.get_daily_scan_state("oa_pending")
    assert state["cursor_json"] == cursor
    assert state["last_error"] == "provider read failed"


def test_scanner_captures_all_verified_nodes_and_never_guesses_principal(tmp_path):
    class Provider:
        principal = "principal"
        def list_pending_oa_approvals(self, **kwargs):
            return [DwsOaApprovalCandidate(process_instance_id="instance", title="Leave")]
        def get_current_user_id(self):
            if not self.principal:
                raise ValueError("identity unavailable")
            return self.principal
        def read_oa_approval_tasks(self, instance):
            return {"result": {"tasks": []}}
        def read_oa_process_instance_openapi(self, instance):
            return {"result": {"status": "RUNNING", "tasks": [
                {"taskId": "first", "userId": "principal", "taskStatus": "RUNNING"},
                {"taskId": "second", "userId": "principal", "taskStatus": "RUNNING"},
                {"taskId": "unconfirmed", "userId": "principal"},
                {"taskId": "other", "userId": "other-person", "taskStatus": "RUNNING"},
            ]}}
        def read_oa_approval_records(self, instance):
            return {"result": {"operationRecords": []}}
    provider = Provider()
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    scan_pending_oa_approvals(store, provider, max_new_items=0)
    cursor = json.loads(store.get_daily_scan_state("oa_pending")["cursor_json"])
    assert cursor["pending_business_object_keys"] == [
        "oa:instance:first", "oa:instance:second", "oa:instance:unconfirmed"
    ]
    assert _pending_oa_task_ids_for_current_user(
        {"result": {"tasks": [{"taskId": "other", "status": "RUNNING"}]}},
        current_user_id="",
    ) == []
    provider.principal = ""
    store = AutoReplyStore(tmp_path / "unknown.sqlite3")
    scan_pending_oa_approvals(store, provider, max_new_items=0)
    cursor = json.loads(store.get_daily_scan_state("oa_pending")["cursor_json"])
    assert cursor["pending_business_object_keys"] == []


@pytest.mark.parametrize("identity_known,status", [(False, "RUNNING"), (True, "")])
def test_unverified_ownership_or_status_cannot_close_human_attempt(tmp_path, identity_known, status):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    attempt_id = store.record_reply_attempt(conversation_id="oa_pending_scan:instance",
        conversation_title="OA", trigger_message_id="message", trigger_sender="OA",
        trigger_text="Review leave", action="agent_run", sensitivity_kind="general",
        oa_process_instance_id="instance", oa_task_id="node", send_status="needs_human")
    store.set_daily_scan_state("oa_pending", last_success_at="2026-10-13T01:00:00Z",
        cursor_json=json.dumps({"pending_business_object_keys": ["oa:instance:node"]}), last_error="")
    class Provider:
        def list_pending_oa_approvals(self, **kwargs):
            return [DwsOaApprovalCandidate(process_instance_id="instance", title="Leave")]
        def get_current_user_id(self):
            if not identity_known:
                raise ValueError("identity unavailable")
            return "principal"
        def read_oa_approval_tasks(self, instance):
            return {"result": {"tasks": []}}
        def read_oa_process_instance_openapi(self, instance):
            return {"result": {"status": "RUNNING", "tasks": [
                {"taskId": "node", "userId": "principal", "taskStatus": status}]}}
        def read_oa_approval_records(self, instance):
            return {"result": {"operationRecords": []}}
    scan_pending_oa_approvals(store, Provider(), max_new_items=0)
    assert store.get_reply_attempt(attempt_id).send_status == "needs_human"
    cursor = json.loads(store.get_daily_scan_state("oa_pending")["cursor_json"])
    assert cursor["pending_business_object_keys"] == ["oa:instance:node"]
