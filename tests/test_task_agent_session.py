import threading

import pytest

from app.store import AutoReplyStore
from app.task_agent_session import (
    TASK_AGENT_SESSION_SCOPE_ID,
    TaskAgentSessionLease,
    TaskAgentSessionLeaseLost,
)


def test_task_agent_session_lease_serializes_store_instances(tmp_path):
    db_path = tmp_path / "task-agent-session.sqlite3"
    first_store = AutoReplyStore(db_path)
    second_store = AutoReplyStore(db_path)

    first = TaskAgentSessionLease.try_acquire(first_store)
    assert first is not None
    try:
        assert TaskAgentSessionLease.try_acquire(second_store) is None
    finally:
        first.close()

    second = TaskAgentSessionLease.try_acquire(second_store)
    assert second is not None
    second.close()


def test_task_agent_runtime_prompt_keeps_current_shared_source_body_once(tmp_path, monkeypatch):
    from app.task_agent import TaskAgentRunner, process_work_item
    from app.task_models import TaskAgentDecision, WorkItem

    store = AutoReplyStore(tmp_path / "runtime-source.sqlite3")
    item = WorkItem.model_validate({
        "source": {"type": "reply_attempt", "ref": "message:one", "conversation_id": "c1"},
        "summary": "完整的本次来源只装载一次 runtime-body-8934",
        "context": {"source_conversation_kind": "group", "sender_user_id": "u1", "sender": "张三"},
    })
    task = store.create_business_task(title="完整的本次来源", stage="candidate")
    signal = store.create_business_task_signal(source_type="reply_attempt", source_ref=item.source.ref, evidence_text=item.summary, conversation_id="c1", author_kind="human", author_user_id="u1", author_name="张三", dedupe_key="current")
    store.link_business_task_evidence(task_id=task, signal_id=signal, evidence_role="discovery")
    id = store.enqueue_work_summary_input(payload_json=item.model_dump_json(), source_type=item.source.type.value, source_ref=item.source.ref)
    work_input = store.claim_work_summary_inputs(limit=1)[0]
    monkeypatch.setattr("app.task_agent.memory_connector_config_issue", lambda: "")

    class CapturingCodex:
        last_session_id = "fixture-shared-session"
        prompts = []

        def decide(self, **kwargs):
            self.prompts.append(kwargs["prompt"])
            return TaskAgentDecision(task_decisions=[], project_assessments=[], update_summary="没有新的行动项")

    codex = CapturingCodex()
    process_work_item(store, TaskAgentRunner(codex), work_input)
    assert len(codex.prompts) == 1
    assert codex.prompts[0].count(item.summary) == 1
    assert '"sender_user_id": "u1"' in codex.prompts[0]
    assert '"document_count": 1' in codex.prompts[0]
    assert store.get_work_summary_input(id).status.value != "failed"


def test_task_agent_session_lease_marks_failed_renewal_as_lost():
    class Store:
        def acquire_codex_session_lock(self, conversation_id, owner):
            return True

        def renew_codex_session_lock(self, conversation_id, owner):
            return False

        def release_codex_session_lock(self, conversation_id, owner):
            return True

    lease = TaskAgentSessionLease.try_acquire(Store(), renew_interval_seconds=60)
    assert lease is not None
    try:
        with pytest.raises(TaskAgentSessionLeaseLost, match="no longer owned"):
            lease.assert_owned()
    finally:
        lease.close()


def test_task_agent_session_lease_renews_until_closed(tmp_path):
    store = AutoReplyStore(tmp_path / "task-agent-session-renew.sqlite3")
    renewals = []
    two_renewals = threading.Event()
    original_renew = store.renew_codex_session_lock

    def record_renewal(conversation_id, owner, *, now=None):
        renewed = original_renew(conversation_id, owner, now=now)
        renewals.append(renewed)
        if len(renewals) >= 2:
            two_renewals.set()
        return renewed

    store.renew_codex_session_lock = record_renewal
    lease = TaskAgentSessionLease.try_acquire(
        store,
        renew_interval_seconds=0.01,
    )
    assert lease is not None
    assert two_renewals.wait(timeout=2)
    lease.close()

    assert renewals and all(renewals)
    assert not lease._renew_thread.is_alive()
    with store._connect() as db:
        row = db.execute(
            "select owner from codex_session_locks where conversation_id=?",
            (TASK_AGENT_SESSION_SCOPE_ID,),
        ).fetchone()
    assert row is None
