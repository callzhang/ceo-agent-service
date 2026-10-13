import json

from app.audit_web import _queue_attention_rows
from app.store import AutoReplyStore
from app.dws_client import DwsOaApprovalCandidate
from app.task_scanners import scan_pending_oa_approvals


def test_current_oa_pending_node_remains_attention_after_comment_done(tmp_path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    task = store.ensure_reply_task(
        conversation_id="oa_pending_scan:instance", conversation_title="OA",
        single_chat=True, trigger_message_id="message", trigger_sender="OA",
        trigger_text="Review leave", trigger_create_time="2026-10-13T00:00:00Z",
        business_object_key="oa:instance:node",
    )
    with store._connect() as db:
        db.execute("update tasks set status='done' where id=?", (task.id,))
    store.set_daily_scan_state("oa_pending", last_success_at="2026-10-13T01:00:00Z",
        cursor_json=json.dumps({"pending_business_object_keys": ["oa:instance:node"]}),
        last_error="")
    rows = _queue_attention_rows(store)
    assert [(r["category"], r["id"]) for r in rows] == [("OA approval", str(task.id))]
    assert rows[0]["status"] == "pending"
    store.set_daily_scan_state("oa_pending", last_success_at="2026-10-13T02:00:00Z",
        cursor_json=json.dumps({"pending_business_object_keys": []}), last_error="")
    assert _queue_attention_rows(store) == []


def test_scanner_retains_pending_nodes_on_read_failure_then_clears_verified_absence(tmp_path):
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
    assert cursor["pending_business_object_keys"] == []
