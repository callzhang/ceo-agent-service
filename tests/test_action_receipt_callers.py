"""Real runtime callers retain their contracts after the receipt-table cutover."""

from tests.sent_fixtures import historical_sent_delivery

import hashlib
import json

import pytest

from app.task8_storage import task8_set_parts
from app.action_storage import EXTERNAL_ACTION_RESULT_SQL, SENT_REPLY_SQL, insert_action, update_action
from app.run_storage import AGENT_RUN_SQL
from app.store import AgentRole, AutoReplyStore
from tests.test_action_receipt_source import source_values
from tests.test_agent_turn_store import _claim_consumer, _task
from tests.test_reviewed_candidate_store import _reviewed


def _update_reply_task_fixture(db, task_id, values):
    parts = task8_set_parts("reply_tasks", values)
    return db.execute(
        f"update tasks set {parts.sql} where record_type='reply_tasks' and id=?",
        (*parts.parameters, task_id),
    ).rowcount


@pytest.fixture
def receipt_store(tmp_path):
    return AutoReplyStore(tmp_path / "receipt-callers.sqlite3")


@pytest.mark.parametrize("late", [False, True])
def test_candidate_receipt_idempotency_conflicts_and_late_result(receipt_store, late):
    store = receipt_store
    task, candidate, review, _ = _reviewed(store, options=False)
    execution = store.claim_candidate_execution(candidate["id"], review["id"], "worker", 60)
    attempt = store.begin_candidate_action(execution["id"], "worker", 0, "01")
    assert attempt["status"] == "dispatched"
    if late:
        with store._connect() as db:
            update_action(db, "review_candidates", candidate["id"], {
                "invalidated_at": "2026-10-13 12:00:00", "invalidation_reason": "new source fact",
            })
            _update_reply_task_fixture(db, task.id, {
                "execution_generation": "next-generation", "status": "pending",
            })
    args = (execution["id"], "worker", 0, "01", "send", {"user_id": "x"})
    saved = store.record_candidate_external_action(*args, {"receipt": "原始"})
    assert store.record_candidate_external_action(*args, {"receipt": "原始"}) == saved
    with pytest.raises(ValueError, match="conflicting external action result"):
        store.record_candidate_external_action(*args, {"receipt": "different"})
    assert store.get_candidate_external_action("01") == saved
    assert store.get_candidate_external_action("1") is None
    assert store.get_verified_action_source("01")["candidate_id"] == candidate["id"]
    assert store.list_verified_candidate_actions(task.id)[0]["provider_result_json"] == '{"receipt":"原始"}'
    store.record_candidate_action_outcome(execution["id"], "worker", 0, "verified", {"receipt": "原始"})
    assert store.finish_candidate_execution(execution["id"], "worker", "done", {})["status"] == "done"
    if late:
        assert store.get_reply_task(task.id).status == "pending"
    with store._connect() as db:
        assert db.execute(f"select count(*) from ({EXTERNAL_ACTION_RESULT_SQL})").fetchone()[0] == 1
        assert not db.execute("pragma foreign_key_check").fetchall()


def test_independent_delivery_receipt_keeps_first_result_and_recovery_projection(receipt_store):
    store = receipt_store
    task = _task(store)
    run = _claim_consumer(store, task).run
    store.complete_agent_run(run.id, {"outcome": "proposal"}, owner="consumer")
    values = dict(agent_run_id=run.id, external_action_key="01",
                  business_object_key=task.business_object_key, action_identity="reply",
                  operation="message.send", target_identifiers={"conversation_id": task.conversation_id},
                  conversation_id=task.conversation_id, trigger_message_id=task.trigger_message_id,
                  reply_text="Already delivered.", provider_result={"receipt": "first"})
    sent = store.record_completed_agent_message_delivery(**values)
    repeated = store.record_completed_agent_message_delivery(**(values | {"provider_result": {"receipt": "later"}}))
    assert repeated.id == sent.id
    saved = store.get_candidate_external_action("01")
    assert saved["provider_result_json"] == '{"receipt":"first"}'
    assert store.get_verified_action_source("01") is None
    with pytest.raises(ValueError, match="conflicting external action result"):
        store.record_completed_agent_message_delivery(**(values | {"operation": "another-operation"}))
    with store._connect() as db:
        _update_reply_task_fixture(db, task.id, {"status": "failed", "error": "later failure"})
    assert store.resolve_reconciled_failed_reply_task(task.id, external_action_key="01")
    with store._connect() as db:
        db.execute("update runs set status='failed',structured_error_json=? "
                   "where record_type='agent_runs' and id=?", ('{"code":"later_failure"}', run.id))
    assert store.reconcile_done_reply_tasks_with_failed_current_run() == 0
    with store._connect() as db:
        _update_reply_task_fixture(db, task.id, {"status": "needs_human"})
    assert store.reconcile_failed_reply_tasks_with_recorded_deliveries() == 1
    assert store.get_reply_task(task.id).status == "done"
    assert store.get_candidate_external_action("01") == saved


def test_failed_audit_receipt_preserves_the_raw_recorded_provider_body(receipt_store, monkeypatch):
    from app.business_identity import external_action_key
    from app.dingtalk_send_evidence import DingTalkSendEvidenceDriver

    store = receipt_store
    task = _task(store)
    consumer = _claim_consumer(store, task).run
    action = {"action_identity": "send", "operation": "send", "target": {"user_id": "derek"}}
    store.complete_agent_run(consumer.id, {"outcome": "proposal", "proposal": {"actions": [action]}}, owner="consumer")
    audit = store.claim_agent_run(
        task.id, task.execution_generation, role=AgentRole.AUDIT,
        proposal_revision=0, turn_attempt=0, parent_agent_run_id=consumer.id,
        operation_id="operation", owner="audit",
    ).run
    key = external_action_key(business_object_key=task.business_object_key, action_identity="send",
                              operation="send", target_identifiers=action["target"])
    raw = ' { "verification": {"state": "sent"}, "原始": [2, 1] } '
    sent = historical_sent_delivery(store,
        agent_run_id=audit.id, external_action_key=key, conversation_id=task.conversation_id,
        trigger_message_id=task.trigger_message_id, reply_text="Delivered report",
        provider_result={"verification": {"state": "sent"}},
        provider_result_json=raw, observed_at="provider-observed-clock",
    )
    with store._connect() as db:
        before = db.execute(
            "select id,action_operation,action_action_identity,action_parameters_json,action_prepared_at "
            "from actions where id=?", (sent.id,),
        ).fetchone()
        body = db.execute(
            "select id,provider_result_json,observed_at,result_digest from action_receipts "
            "where action_id=? and provider_result_json is not null", (sent.id,),
        ).fetchone()
    assert tuple(before[1:4]) == (None, None, None)
    assert before[4] is None and sent.sent_at == "provider-observed-clock"
    assert tuple(body[1:]) == (raw, "provider-observed-clock", None)
    store.fail_agent_run(audit.id, {"code": "codex_result_invalid"}, owner="audit")
    store.fail_reply_task(task.id, "codex_result_invalid", expected_execution_generation=task.execution_generation)
    monkeypatch.setattr(DingTalkSendEvidenceDriver, "audit_run_has_execution_evidence", lambda *args, **kwargs: True)
    assert store.reconcile_failed_audit_with_verified_delivery(task.id, audit.id)
    assert store.get_candidate_external_action(key)["provider_result_json"] == raw
    assert store.get_candidate_external_action(key)["first_agent_run_id"] == audit.id
    assert store.get_agent_run(audit.id).status == "failed"
    with store._connect() as db:
        after = db.execute(
            "select id,action_operation,action_action_identity,action_parameters_json "
            "from actions where id=?", (sent.id,),
        ).fetchone()
        body_after = db.execute(
            "select id,provider_result_json,observed_at,result_digest from action_receipts "
            "where action_id=? and provider_result_json is not null", (sent.id,),
        ).fetchone()
        binding = db.execute(
            "select provider_receipt_id,operation,target,result_digest,created_at "
            "from action_receipts where action_id=? and provider_receipt_id is not null "
            "and provider_result_json is null", (sent.id,),
        ).fetchone()
    assert after[0] == sent.id and tuple(after[1:]) == ("send", "send", None)
    assert tuple(body_after[:3]) == tuple(body[:3])
    assert binding[0] == body_after[0]
    digest = hashlib.sha256(json.dumps(
        {"verification": {"state": "sent"}, "原始": [2, 1]},
        sort_keys=True, separators=(",", ":"),
    ).encode()).hexdigest()
    assert tuple(binding[1:4]) == ("send", '{"user_id":"derek"}', digest)
    assert binding[4]


@pytest.mark.parametrize("kind", [None, "agent_effect_intents", "agent_execution_receipts"])
def test_unstarted_run_discard_respects_independent_native_facts(receipt_store, kind):
    store = receipt_store
    task = _task(store)
    run = _claim_consumer(store, task).run
    if kind:
        with store._connect() as db:
            insert_action(db, kind, source_values(kind, run_id=run.id))
    assert store.discard_unstarted_agent_run(run.id, owner="consumer") is (kind is None)
    assert (store.get_agent_run(run.id) is None) is (kind is None)


@pytest.mark.parametrize("kind,blocker", [
    (None, None),
    ("external_action_results", "external_action_result"),
    ("agent_effect_intents", "prepared_or_dispatched_effect"),
    ("agent_execution_receipts", "prior_execution_receipt"),
])
def test_deferral_reads_each_independent_receipt_type(tmp_path, kind, blocker):
    from app.reply_task_deferral import preview_deferral
    from tests.test_reply_task_deferral import NOW, ready

    path, task_id, _ = ready(tmp_path)
    store = AutoReplyStore(path)
    task = store.get_reply_task(task_id)
    if kind:
        with store._connect() as db:
            run_id = db.execute(f"select id from ({AGENT_RUN_SQL}) where reply_task_id=?", (task_id,)).fetchone()[0]
            values = source_values(kind, run_id=run_id)
            if kind == "external_action_results":
                values["business_object_key"] = task.business_object_key
            insert_action(db, kind, values)
    preview = preview_deferral(path, task_id, now=NOW)
    if blocker:
        assert blocker in preview["blockers"]
        assert not preview["eligible"]
    else:
        assert preview["eligible"]


@pytest.mark.parametrize("kind", [None, "external_action_results", "agent_effect_intents", "agent_execution_receipts"])
def test_maintenance_reads_each_independent_receipt_type(tmp_path, kind):
    from app.deploy_maintenance import check_maintenance
    from app.email_store import EmailStore
    from app.repository_updater import UpgradePreconditionError
    from tests.test_reply_task_deferral import ready

    path, task_id, _ = ready(tmp_path)
    EmailStore(path)
    store = AutoReplyStore(path)
    task = store.get_reply_task(task_id)
    if kind:
        with store._connect() as db:
            run_id = db.execute(f"select id from ({AGENT_RUN_SQL}) where reply_task_id=?", (task_id,)).fetchone()[0]
            values = source_values(kind, run_id=run_id)
            if kind == "external_action_results":
                values["business_object_key"] = task.business_object_key
            insert_action(db, kind, values)
        reason = "prepared or completed action" if kind == "external_action_results" else "effect identity"
        with pytest.raises(UpgradePreconditionError, match=reason):
            check_maintenance(path, (task_id,))
    else:
        assert check_maintenance(path, (task_id,)).task_bindings[0][0] == task_id
