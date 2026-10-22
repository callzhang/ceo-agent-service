from app.service_message_sender import ServiceMessageSender
from app.store import AutoReplyStore
import pytest


class AcceptedOnceThenReadback:
    def __init__(self):
        self.sends = 0
        self.reads = 0
        self.readback = None

    def send_reply_to_trigger(self, conversation, trigger, text):
        self.sends += 1
        raise TimeoutError("provider accepted but local completion was interrupted")

    def reconcile_reply_to_trigger(self, conversation, trigger, text):
        self.reads += 1
        return self.readback

    def verify_message_send_result(self, result):
        return {"state": "sent"}


def test_prepared_message_caller_uses_common_action_and_owned_receipts(tmp_path):
    store = AutoReplyStore(tmp_path / "prepared.sqlite")
    sender = ServiceMessageSender(store=store, dingtalk=AcceptedOnceThenReadback())
    prepared = sender.prepare(
        channel="dingtalk", delivery_key="prepared-action", body="Status",
    )

    with store._connect() as db:
        owner = db.execute(
            "select id,record_type,action_operation,action_delivery_channel,"
            "action_delivery_key,action_prepared_body,action_feedback_token,"
            "action_postfix_version,action_prepared_at "
            "from actions where record_type='action' and action_delivery_key=?",
            (prepared.delivery_key,),
        ).fetchone()
        assert owner is not None
        assert owner["record_type"] == "action"
        assert owner["action_operation"] is None
        assert owner["action_delivery_channel"] == prepared.channel
        assert owner["action_prepared_body"] == prepared.final_body
        assert owner["action_feedback_token"] == prepared.feedback_token
        assert owner["action_postfix_version"] == prepared.postfix_version
        assert owner["action_prepared_at"] is not None
        assert db.execute(
            "select count(*) from action_receipts where action_id=?",
            (owner["id"],),
        ).fetchone()[0] == 0
        assert db.execute(
            "select count(*) from actions where record_type='outbound_postfixes' "
            "and delivery_key=?", (prepared.delivery_key,),
        ).fetchone()[0] == 0


def test_restart_after_provider_acceptance_does_not_resend(tmp_path):
    path = tmp_path / "prepared-recovery.sqlite"
    adapter = AcceptedOnceThenReadback()
    sender = ServiceMessageSender(store=AutoReplyStore(path), dingtalk=adapter)
    prepared = sender.prepare(
        channel="dingtalk", delivery_key="interrupted-action", body="Status",
    )
    with pytest.raises(TimeoutError, match="local completion was interrupted"):
        sender.send_dingtalk_reply_to_trigger_prepared(
            prepared, conversation="cid", trigger="msg",
        )

    adapter.readback = {"result": {"processQueryKey": "readback-msg"}}
    restarted = ServiceMessageSender(store=AutoReplyStore(path), dingtalk=adapter)
    receipt = restarted.send_dingtalk_reply_to_trigger_prepared(
        prepared, conversation="cid", trigger="msg",
    )

    assert receipt.provider_result == adapter.readback
    assert adapter.sends == 1
    assert adapter.reads == 1
    assert AutoReplyStore(path).get_outbound_postfix_receipt(
        "dingtalk", prepared.delivery_key,
    ) == adapter.readback
    with AutoReplyStore(path)._connect() as db:
        dispatch, receipt = db.execute(
            "select operation,target,dispatched_at,observed_at,provider_result_json "
            "from action_receipts "
            "where action_id=(select id from actions where action_delivery_key=?) "
            "order by id",
            (prepared.delivery_key,),
        ).fetchall()
    assert tuple(dispatch[:2]) == tuple(receipt[:2]) == (
        "send_reply_to_trigger", prepared.delivery_key,
    )
    assert dispatch[2] is not None and dispatch[3] is None and dispatch[4] is None
    assert receipt[2] is None and receipt[3] is not None and receipt[4] is not None


def test_two_direct_email_claimants_only_one_provider_call(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    from app.email_classifier_contracts import EmailClassificationStatus
    from app.email_provider_actions import DeterministicEmailActionExecutor, ProviderMessageState
    from app.email_store import EmailStore
    from app.email_worker import _run_next_direct_action
    from tests.test_email_store import _classification

    class ProviderSubstitute:
        def __init__(self):
            self.entered = Event()
            self.release = Event()
            self.apply_calls = 0

        def read_state(self, locator, *, action_type):
            self.entered.set()
            assert self.release.wait(5)
            return ProviderMessageState(
                revision="before", labels=frozenset(), is_read=False,
                archived=False, folder=locator.folder, trashed=False,
            )

        def apply(self, locator, action_type, parameters, *, observed_state):
            self.apply_calls += 1
            return None

    store = EmailStore(tmp_path / "concurrent-direct.sqlite3")
    classification = _classification(status=EmailClassificationStatus.PROCESSED)
    store.persist_scan_result(classification, model_text="fixture features")
    provider = ProviderSubstitute()

    def executor_factory(_account_id):
        return DeterministicEmailActionExecutor(provider)

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(_run_next_direct_action, store, executor_factory)
        assert provider.entered.wait(5)
        second = pool.submit(_run_next_direct_action, store, executor_factory)
        second_result = second.result(timeout=5)
        provider.release.set()
        first_result = first.result(timeout=5)

    assert (first_result is not None) != (second_result is not None)
    assert provider.apply_calls == 1


def test_reviewed_email_prepared_share_owner(tmp_path):
    from app.email_classifier_contracts import EmailClassificationStatus
    from app.email_store import EmailStore
    from app.action_entity_definitions import ACTION_FIELDS
    from tests.test_email_store import _classification, _task2_account_values
    from tests.test_reviewed_candidate_store import _reviewed

    path = tmp_path / "shared-action-owner.sqlite3"
    store = AutoReplyStore(path)
    sender = ServiceMessageSender(store=store, dingtalk=AcceptedOnceThenReadback())
    prepared = sender.prepare(channel="dingtalk", delivery_key="prepared", body="Prepared")

    _, candidate, review, _ = _reviewed(store, options=False)
    execution = store.claim_candidate_execution(candidate["id"], review["id"], "worker", 60)
    reviewed = store.begin_candidate_action(execution["id"], "worker", 0, "reviewed-effect")

    email = EmailStore(path)
    email.create_account(_task2_account_values("account", enabled=False))
    classification = _classification(
        status=EmailClassificationStatus.PROCESSED, classification_id=902,
    )
    email.persist_scan_result(classification, model_text="fixture features")
    direct_id = email.direct_action_ids_for_plan(
        classification.action_plan.action_plan_id,
    )[0]
    assert email.claim_direct_action(
        action_id=direct_id, claimed_at="2026-10-19T10:00:00+00:00",
    ) is not None

    with store._connect() as db:
        prepared_id = db.execute(
            "select id from actions where action_delivery_channel='dingtalk' and action_delivery_key='prepared'",
        ).fetchone()[0]
        direct_owner_id = db.execute(
            "select id from actions where id=?",
            (int(direct_id),),
        ).fetchone()[0]
        owners = db.execute(
            "select * from actions where record_type='action' and id in (?, ?, ?) order by id",
            (prepared_id, reviewed["action_owner_id"], direct_owner_id),
        ).fetchall()
        assert len(owners) == 3
        assert all({f"action_{field}" for field in ACTION_FIELDS} <= set(owner.keys()) for owner in owners)
        assert all(owner["record_type"] == "action" for owner in owners)
        assert db.execute("select count(*) from actions where record_type in ('outbound_postfixes','candidate_action_attempts','email_actions')").fetchone()[0] == 0


def test_selected_reviewed_message_uses_prepared_action_through_sent(tmp_path):
    from app.agent_context import AgentTaskContext
    from app.agent_contracts import ConsumerAgentResult
    from app.consumer_agent import _prepare_outgoing_dingtalk_messages
    from app.action_storage import SENT_REPLY_SQL
    from app.service_message_sender import agent_message_delivery_key
    from app.store import AgentRole
    from app.system_action_handlers import native_dingtalk_message_handlers
    from app.system_executor import SystemExecutor

    class StrictProvider:
        def __init__(self):
            self.calls = []

        def send_message(self, conversation_id, body, **target):
            self.calls.append((conversation_id, body, target))
            return {"success": True, "data": {"messageId": "provider-message-1"}}

        def verify_message_send_result(self, result):
            assert result == {"success": True, "data": {"messageId": "provider-message-1"}}
            return {"state": "sent", "message_id": "provider-message-1"}

    store = AutoReplyStore(tmp_path / "selected-prepared.sqlite3")
    store.enqueue_reply_task(
        channel="dingtalk", conversation_id="target-cid", conversation_title="Group",
        single_chat=False, trigger_message_id="trigger-1", trigger_sender="Derek",
        trigger_create_time="2026-10-20T10:00:00Z", trigger_text="Please send the notice",
    )
    task = store.claim_reply_tasks(1)[0]
    context = AgentTaskContext(
        task_id=task.id, channel=task.channel, conversation_id=task.conversation_id,
        conversation_title=task.conversation_title, single_chat=task.single_chat,
        trigger_message_id=task.trigger_message_id, trigger_sender=task.trigger_sender,
        trigger_text=task.trigger_text, trigger_create_time=task.trigger_create_time,
        messages=(), materials=(), prior_receipts=(),
    )
    result = ConsumerAgentResult.model_validate({
        "outcome": "needs_human", "summary": "Choose the reviewed notice",
        "proposal": None,
        "decision_options": [
            {"key": "send_notice", "label": "Send the notice", "applies_to": "task_class",
             "instruction": "Send the approved notice", "consequence": "The group receives the notice",
             "plan": {"objective": "Send the notice", "actions": [{
                 "description": "Send notice", "action_identity": "notice",
                 "capability": "dingtalk-chat", "operation": "send_group_message",
                 "target": {"conversation_id": "target-cid"},
                 "payload": {"content": "Approved notice"},
             }], "sourced_facts": [], "authored_judgment": "The selected notice is ready"}},
            {"key": "stop", "label": "Stop", "instruction": "Do not send",
             "consequence": "No notice is sent", "terminal_outcome": "skipped",
             "reason": "The notice is no longer needed", "applies_to": "task_class"},
        ],
        "error": {"code": "", "retryable": False, "authorization_required": False},
        "risk": "medium", "confidence": 0.8, "rule_coverage": 0.4,
        "information_completeness": 1.0,
        "needs_human_reason": "Only Derek can choose this instance's notice",
        "decision_basis": {
            "verified_facts": [{"assertion": "The group requested a notice", "references": ["message:trigger-1"]}],
            "rule_evidence": [{"assertion": "No rule selects an option", "references": ["message:trigger-1"]}],
            "quality_explanation": "The choice is specific to this notice",
            "no_external_action_evidence": [{"assertion": "No notice has been sent", "references": ["message:trigger-1"]}],
            "conclusion": "Ask Derek to select the current notice",
        },
    })
    result = _prepare_outgoing_dingtalk_messages(
        result, store=store, task=task, context=context, proposal_revision=0,
    )
    consumer = store.claim_agent_run(
        task.id, task.execution_generation, role=AgentRole.CONSUMER,
        proposal_revision=0, turn_attempt=0, parent_agent_run_id=None,
        operation_id="", owner="consumer",
    ).run
    store.complete_agent_run(consumer.id, result.model_dump(mode="json"), owner="consumer")
    candidate = store.persist_review_candidate(task, consumer, result)
    audit = store.claim_agent_run(
        task.id, task.execution_generation, role=AgentRole.AUDIT,
        proposal_revision=0, turn_attempt=0, parent_agent_run_id=consumer.id,
        operation_id="review", owner="audit",
    ).run
    approval = {"outcome": "approve", "candidate_digest": candidate["candidate_digest"]}
    store.complete_agent_run(audit.id, approval, owner="audit")
    review = store.record_candidate_review(candidate["id"], audit.id, approval)
    store.select_candidate_option(candidate["id"], review["id"], "send_notice")

    source_candidate = result
    provider = StrictProvider()
    result = SystemExecutor(
        store, native_dingtalk_message_handlers(store, provider), owner="system",
    ).execute(task, candidate["id"], review["id"])

    assert result.outcome == "executed"
    assert len(provider.calls) == 1
    delivery_key = agent_message_delivery_key(
        business_object_key=task.business_object_key,
        action_identity="notice:send_notice",
        execution_generation=task.execution_generation,
        proposal_revision=0,
    )
    external_action = store.list_candidate_action_attempts(
        store.get_candidate_execution(candidate["id"])["id"]
    )[0]
    with store._connect() as db:
        owners = db.execute(
            "select id,action_delivery_key,action_external_action_key,action_prepared_receipt_id,"
            "action_delivery_receipt_id from actions where record_type='action' "
            "and (action_delivery_key is not null or action_external_action_key is not null) order by id",
        ).fetchall()
        owner = db.execute(
            "select * from actions where record_type='action' and action_delivery_key=?",
            (delivery_key,),
        ).fetchone()
        assert owner is not None
        prepared_receipt = db.execute(
            "select * from action_receipts where id=? and action_id=?",
            (owner["action_prepared_receipt_id"], owner["id"]),
        ).fetchone()
        observation = db.execute(
            "select * from action_receipts where action_id=? and execution_id=? and action_index=0",
            (owner["id"], store.get_candidate_execution(candidate["id"])["id"]),
        ).fetchone()
        canonical_receipt = db.execute(
            "select * from action_receipts where id=? and action_id=?",
            (observation["provider_receipt_id"], owner["id"]),
        ).fetchone()
        sent_fact = db.execute(
            "select * from action_receipts where id=? and action_id=?",
            (owner["action_delivery_receipt_id"], owner["id"]),
        ).fetchone()
        observer = db.execute(
            "select * from action_observers where action_id=? and agent_run_id=? and task_id=?",
            (owner["id"], audit.id, task.id),
        ).fetchone()
        sent_projection = db.execute(
            f"select reply_text,agent_run_id,id from ({SENT_REPLY_SQL}) where id=?",
            (owner["id"],),
        ).fetchone()
    assert len(owners) == 1
    assert owners[0]["id"] == owner["id"]
    assert owner["action_delivery_key"] == delivery_key
    assert owner["action_external_action_key"] == external_action["external_action_key"]
    assert owner["action_prepared_receipt_id"] == prepared_receipt["id"]
    assert owner["action_delivery_receipt_id"] == sent_fact["id"]
    assert prepared_receipt["operation"] == "send_message"
    assert prepared_receipt["target"] == delivery_key
    assert prepared_receipt["observed_at"] is not None
    assert observation["status"] == "verified"
    assert observation["provider_receipt_id"] == canonical_receipt["id"]
    assert canonical_receipt["operation"] == "send_group_message"
    assert canonical_receipt["target"] == '{"conversation_id":"target-cid"}'
    assert canonical_receipt["provider_result_json"] is not None
    assert sent_fact["provider_receipt_id"] == canonical_receipt["id"]
    assert sent_fact["observed_at"] is not None
    assert observer is not None
    assert sent_projection["id"] == owner["id"]
    assert sent_projection["agent_run_id"] == audit.id
    assert sent_projection["reply_text"] == provider.calls[0][1]
    assert owner["action_prepared_at"] is not None

    # The next revision has a distinct delivery preparation but the same
    # canonical external key. Exercise the persisted owner recovery branch
    # directly; SystemExecutor's existing exact-content check runs earlier.
    later_result = _prepare_outgoing_dingtalk_messages(
        source_candidate,
        store=store, task=task, context=context, proposal_revision=1,
    )
    later_consumer = store.claim_agent_run(
        task.id, task.execution_generation, role=AgentRole.CONSUMER,
        proposal_revision=1, turn_attempt=1, parent_agent_run_id=audit.id,
        operation_id="", owner="consumer-later",
    ).run
    store.complete_agent_run(
        later_consumer.id, later_result.model_dump(mode="json"), owner="consumer-later",
    )
    later_candidate = store.persist_review_candidate(task, later_consumer, later_result)
    later_audit = store.claim_agent_run(
        task.id, task.execution_generation, role=AgentRole.AUDIT,
        proposal_revision=1, turn_attempt=1, parent_agent_run_id=later_consumer.id,
        operation_id="review-later", owner="audit-later",
    ).run
    later_approval = {"outcome": "approve", "candidate_digest": later_candidate["candidate_digest"]}
    store.complete_agent_run(later_audit.id, later_approval, owner="audit-later")
    later_review = store.record_candidate_review(later_candidate["id"], later_audit.id, later_approval)
    store.select_candidate_option(later_candidate["id"], later_review["id"], "send_notice")
    later_execution = store.claim_candidate_execution(
        later_candidate["id"], later_review["id"], "system-later", 1800,
    )
    assert later_execution is not None
    later_delivery_key = agent_message_delivery_key(
        business_object_key=task.business_object_key,
        action_identity="notice:send_notice",
        execution_generation=task.execution_generation,
        proposal_revision=1,
    )
    recovered_attempt = store.begin_candidate_action(
        later_execution["id"], "system-later", 0, external_action["external_action_key"],
        preparation_owner={"channel": "dingtalk", "delivery_key": later_delivery_key},
    )
    assert recovered_attempt["status"] == "verified"
    assert len(provider.calls) == 1
    with store._connect() as db:
        recovered_observation = db.execute(
            "select action_id,status,provider_receipt_id from action_receipts "
            "where execution_id=? and action_index=0",
            (later_execution["id"],),
        ).fetchone()
        later_preparation = db.execute(
            "select id from actions where record_type='action' and action_delivery_key=?",
            (later_delivery_key,),
        ).fetchone()
    assert later_preparation is not None
    assert later_preparation["id"] != owner["id"]
    assert recovered_observation["action_id"] == owner["id"]
    assert recovered_observation["status"] == "verified"
    assert recovered_observation["provider_receipt_id"] == canonical_receipt["id"]



def test_retry_keeps_original_external_identity(tmp_path):
    from app.email_classifier_contracts import EmailClassificationStatus
    from app.email_store import EmailStore
    from tests.test_email_store import _classification, _task2_account_values

    store = EmailStore(tmp_path / "direct-retry-identity.sqlite3")
    store.create_account(_task2_account_values("account", enabled=False))
    classification = _classification(
        status=EmailClassificationStatus.PROCESSED, classification_id=903,
    )
    store.persist_scan_result(classification, model_text="fixture features")
    action_id = store.direct_action_ids_for_plan(
        classification.action_plan.action_plan_id,
    )[0]
    first = store.claim_direct_action(
        action_id=action_id, claimed_at="2026-10-19T10:00:00+00:00",
    )
    assert first is not None
    store.complete_direct_action_attempt(
        first, status="failed", provider_operation="archive",
        provider_target=first.locator.stable_message_identity,
        provider_result_id="", error="transient provider failure",
        finished_at="2026-10-19T10:01:00+00:00",
    )
    retry = store.claim_direct_action(
        action_id=action_id, claimed_at="2026-10-19T11:00:00+00:00",
    )
    assert retry is not None
    assert retry.action_id == first.action_id == action_id
    assert retry.attempt_number == 2
    assert [attempt["attempt_number"] for attempt in store.list_action_attempts(action_id)] == [1]
    store.complete_direct_action_attempt(
        retry, status="done", provider_operation="archive",
        provider_target=retry.locator.stable_message_identity,
        provider_result_id="verified-provider-receipt", error="",
        finished_at="2026-10-19T11:01:00+00:00",
    )
    assert [attempt["attempt_number"] for attempt in store.list_action_attempts(action_id)] == [1, 2]
    with store._connect() as db:
        assert tuple(db.execute(
            "select action_external_action_key,action_action_identity from actions "
            "where record_type='action' and id=?", (int(action_id),),
        ).fetchone()) == (None, None)
