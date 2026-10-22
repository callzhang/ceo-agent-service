from datetime import datetime, timedelta, timezone

from app.audit_web import _queue_attention_rows
from app.reviewed_sources import capture_candidate_sources
from app.store import AgentRole
from app.system_executor import SystemExecutor
from tests.test_oa_return_readback import Source, action
from tests.test_reviewed_orchestration import setup
from tests.test_system_executor import _candidate


def _failed_oa_attempt(store, *, process_id, suffix):
    from app.action_storage import update_action

    attempt_id = store.record_reply_attempt(
        conversation_id=f"oa_pending_scan:{suffix}",
        conversation_title="OA approval",
        trigger_message_id=f"scan:{suffix}",
        trigger_sender="Derek OA",
        trigger_text="Review the pending OA process",
        action="agent_run",
        sensitivity_kind="oa_approval",
        oa_process_instance_id=process_id,
        oa_task_id="original",
        send_status="failed",
    )
    with store._immediate_write_transaction() as db:
        update_action(db, "reply_attempts", attempt_id, {
            "updated_at": "2000-01-01 00:00:00",
            "send_error": "consumer_purpose_retry_exhausted",
        })
    return attempt_id


def _verified_return(store, task, context, monkeypatch):
    from app.dws_client import DINGTALK_MESSAGE_TIME_ZONE

    class Clock(datetime):
        offset = timedelta()

        @classmethod
        def now(cls, tz=None):
            return datetime.now(tz) + cls.offset

    monkeypatch.setattr("app.system_action_handlers.datetime", Clock)

    class SourceWithReviewedForm(Source):
        def read_oa_approval_detail(self, process_id):
            value = super().read_oa_approval_detail(process_id)
            value["result"]["processInstanceId"] = process_id
            value["result"]["formValueVOS"] = [{"id": "amount", "value": "900" if self.returned else "100"}]
            return value

        def revert_oa_approval_task(self, **kwargs):
            self.calls.append(kwargs)
            self.returned = True
            event = datetime.now(timezone.utc) + timedelta(seconds=2)
            self.records[0]["date"] = event.astimezone(DINGTALK_MESSAGE_TIME_ZONE).strftime("%Y-%m-%d %H:%M:%S")
            self.destination["create_time"] = (event + timedelta(seconds=1)).astimezone(
                DINGTALK_MESSAGE_TIME_ZONE
            ).strftime("%Y-%m-%d %H:%M:%S")
            Clock.offset = timedelta(seconds=4)
            return {"success": True, "dingOpenErrcode": 0, "result": True}

    source = SourceWithReviewedForm()
    proposal = capture_candidate_sources(
        _candidate(actions=[action().model_dump(mode="json")]), context, source
    )
    consumer = store.claim_agent_run(
        task.id,
        task.execution_generation,
        role=AgentRole.CONSUMER,
        proposal_revision=0,
        turn_attempt=0,
        parent_agent_run_id=None,
        operation_id="",
        owner="consumer",
    ).run
    store.complete_agent_run(consumer.id, proposal.model_dump(mode="json"), owner="consumer")
    candidate = store.persist_review_candidate(task, consumer, proposal)
    audit = store.claim_agent_run(
        task.id,
        task.execution_generation,
        role=AgentRole.AUDIT,
        proposal_revision=0,
        turn_attempt=0,
        parent_agent_run_id=consumer.id,
        operation_id="review",
        owner="audit",
    ).run
    approval = {"outcome": "approve", "candidate_digest": candidate["candidate_digest"]}
    store.complete_agent_run(audit.id, approval, owner="audit")
    review = store.record_candidate_review(candidate["id"], audit.id, approval)

    result = SystemExecutor(store, dws=source).execute(
        task, candidate["id"], review["id"], context=context
    )
    assert result.error.code == "external_action_uncertain"
    attempt_id = store.record_reply_attempt(
        conversation_id=task.conversation_id,
        conversation_title=task.conversation_title,
        trigger_message_id=task.trigger_message_id,
        trigger_sender=task.trigger_sender,
        trigger_text=task.trigger_text,
        action="agent_run",
        sensitivity_kind="oa_approval",
        oa_process_instance_id="process",
        oa_task_id="original",
        oa_remark="Need exact handover facts",
        send_status="uncertain",
    )
    from app.action_storage import update_action

    with store._immediate_write_transaction() as db:
        update_action(db, "reply_attempts", attempt_id, {"agent_run_id": audit.id})

    from app.oa_reconciliation import reconcile_oa_return_attempt

    reconciliation = reconcile_oa_return_attempt(
        store,
        source,
        attempt_id=attempt_id,
        candidate_id=candidate["id"],
        review_id=review["id"],
    )
    assert reconciliation.outcome == "executed"


def test_older_failed_oa_attention_is_hidden_after_later_verified_return(tmp_path, monkeypatch):
    store, task, context = setup(tmp_path)
    old_a = _failed_oa_attempt(store, process_id="process", suffix="old-a")
    old_b = _failed_oa_attempt(store, process_id="process", suffix="old-b")

    assert {old_a, old_b} <= {
        int(row["id"]) for row in store.list_current_unresolved_problem_attempt_summaries()
    }
    before_return = {
        int(row["id"])
        for row in _queue_attention_rows(store)
        if row["category"] == "Reply"
    }
    assert {old_a, old_b} <= before_return
    _verified_return(store, task, context, monkeypatch)

    visible_ids = {int(row["id"]) for row in _queue_attention_rows(store) if row["category"] == "Reply"}
    assert old_a not in visible_ids
    assert old_b not in visible_ids
    # The original failed records remain in the immutable attempt store.
    from app.action_storage import REPLY_ATTEMPT_COMMON_SQL

    with store._connect() as db:
        retained = db.execute(
            f"select count(*) from ({REPLY_ATTEMPT_COMMON_SQL}) "
            "where id in (?,?) and send_status='failed'",
            (old_a, old_b),
        ).fetchone()[0]
    assert retained == 2


def test_verified_return_for_another_process_does_not_hide_failed_attention(tmp_path, monkeypatch):
    store, task, context = setup(tmp_path)
    old = _failed_oa_attempt(store, process_id="different-process", suffix="different")
    _verified_return(store, task, context, monkeypatch)

    visible_ids = {int(row["id"]) for row in _queue_attention_rows(store) if row["category"] == "Reply"}
    assert old in visible_ids
