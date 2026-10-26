"""A WeChat delivery from the standard pipeline is sent only on an exact approval.

The send gate accepts only the standard Consumer -> Audit review, bound to exactly
the reviewed target and text (Derek 2026-10-24 option G2; the original WeChat
review was retired 2026-10-25). Every other case below must stay unapproved.
"""

import json

import pytest

from app.agent_contracts import AuditAgentResult, ConsumerAgentResult
from app.reviewed_candidates import candidate_digest
from app.store import AgentRole, AutoReplyStore
from app.wechat.models import WechatReplyScope
from tests.wechat.support import create_wechat_delivery

IDENTITY = "send-wechat-reply"
TEXT = "收到，稍后回复你。"


def _result(text=TEXT, *, target=None, operation="send_message", capability="wechat", outcome="proposal"):
    target = target or {
        "account_id": "acct-1", "target_type": "direct", "target_id": "u9",
        "conversation_id": "u9",
    }
    plan = {"objective": "Reply", "actions": [{
        "description": "Reply on WeChat", "action_identity": IDENTITY,
        "capability": capability, "operation": operation,
        "target": target, "payload": {"text": text},
    }], "sourced_facts": [], "authored_judgment": "Short acknowledgement"}
    return ConsumerAgentResult.model_validate({
        "outcome": outcome, "summary": "Reply", "proposal": plan,
        "error": {"code": "", "retryable": False, "authorization_required": False},
        "risk": "low", "confidence": 1.0, "rule_coverage": 1.0,
        "information_completeness": 1.0,
    })


def _task(store, trigger="m1"):
    store.replace_wechat_reply_scopes("acct-1", [WechatReplyScope(
        account_id="acct-1", target_type="direct", target_id="u9", conversation_id="u9",
        display_name="Alex", trigger_mode="every_inbound_text", binding_status="verified")])
    store.enqueue_reply_task(
        channel="wechat", conversation_id="u9", conversation_title="Alex", single_chat=True,
        trigger_message_id=trigger, trigger_create_time="2026-10-24T10:00:00",
        trigger_sender="Alex", trigger_text="hi")
    return store.claim_reply_tasks(1, channel="wechat")[0]


def _reviewed(store, task, result, *, decision="approve"):
    consumer = store.claim_agent_run(
        task.id, task.execution_generation, role=AgentRole.CONSUMER, proposal_revision=0,
        turn_attempt=0, parent_agent_run_id=None, operation_id="", owner="consumer",
    ).run
    store.complete_agent_run(consumer.id, result.model_dump(mode="json"), owner="consumer")
    candidate = store.persist_review_candidate(task, consumer, result)
    audit = store.claim_agent_run(
        task.id, task.execution_generation, role=AgentRole.AUDIT, proposal_revision=0,
        turn_attempt=0, parent_agent_run_id=consumer.id, operation_id="audit", owner="audit",
    ).run
    verdict = AuditAgentResult(
        outcome=decision, summary="Reviewed", proposal_revision=0,
        candidate_digest=candidate_digest(result), evidence_refs=(),
        feedback=None if decision == "approve" else {
            "rule": "Wording", "observation": "Too curt",
            "requested_revision": "Soften the wording"},
        error={"code": "", "retryable": False}, risk="low", confidence=1.0,
        rule_coverage=1.0, information_completeness=1.0,
    ).model_dump(mode="json")
    store.complete_agent_run(audit.id, verdict, owner="audit")
    review = store.record_candidate_review(candidate["id"], audit.id, verdict)
    return candidate, review


@pytest.fixture
def approved(tmp_path):
    store = AutoReplyStore(tmp_path / "gate.sqlite3")
    task = _task(store)
    candidate, review = _reviewed(store, task, _result())
    delivery_id = store.create_standard_wechat_delivery(
        candidate_id=candidate["id"], review_id=review["id"], action_identity=IDENTITY)
    return store, task, candidate, review, delivery_id


def _set(store, delivery_id, **values):
    from app.action_storage import update_task_delivery_action
    with store._connect() as db:
        update_task_delivery_action(db, "wechat_deliveries", delivery_id, values)


def test_standard_review_approves_exactly_the_reviewed_target_and_text(approved):
    store, task, _, _, delivery_id = approved
    delivery = store.get_wechat_delivery_by_id(delivery_id)
    assert delivery.reply_text == TEXT
    assert store.wechat_delivery_review_approved(delivery_id) is True
    claimed = store.claim_wechat_delivery(
        delivery_id, expected_execution_generation=task.execution_generation)
    assert claimed is not None and claimed.status == "sending"


@pytest.mark.parametrize("change", [
    {"reply_text": "收到，稍后回复你。 (edited)"},
    {"target_id": "someone-else"},
    {"conversation_id": "other-conversation"},
    {"account_id": "acct-2"},
    {"target_type": "group"},
])
def test_a_delivery_that_differs_from_the_reviewed_action_is_not_approved(approved, change):
    store, _, _, _, delivery_id = approved
    _set(store, delivery_id, **change)
    assert store.wechat_delivery_review_approved(delivery_id) is False


def test_an_invalidated_candidate_no_longer_approves(approved):
    store, _, candidate, _, delivery_id = approved
    store.invalidate_review_candidate(candidate["id"], "new facts arrived")
    assert store.wechat_delivery_review_approved(delivery_id) is False


@pytest.mark.parametrize("break_evidence", [
    lambda e: e.update(standard_review_id="999999"),
    lambda e: e.update(standard_candidate_id="999999"),
    lambda e: e.update(standard_action_identity="another-action"),
    lambda e: e.update(standard_review_id="not-a-number"),
    lambda e: e.pop("standard_action_identity"),
])
def test_evidence_that_does_not_point_at_the_review_is_not_approved(approved, break_evidence):
    store, _, _, _, delivery_id = approved
    with store._connect() as db:
        row = store._native_wechat_delivery(db, delivery_id)
        evidence = json.loads(row["evidence_json"])
    break_evidence(evidence)
    _set(store, delivery_id, evidence_json=json.dumps(evidence))
    assert store.wechat_delivery_review_approved(delivery_id) is False


def test_a_returned_review_cannot_create_or_release_a_delivery(tmp_path):
    store = AutoReplyStore(tmp_path / "returned.sqlite3")
    task = _task(store)
    candidate, review = _reviewed(store, task, _result(), decision="return")
    with pytest.raises(ValueError):
        store.create_standard_wechat_delivery(
            candidate_id=candidate["id"], review_id=review["id"], action_identity=IDENTITY)


@pytest.mark.parametrize("result", [
    _result(operation="reply_to_message"),
    _result(capability="dingtalk-chat"),
])
def test_only_a_complete_wechat_send_action_can_create_a_delivery(tmp_path, result):
    store = AutoReplyStore(tmp_path / "shape.sqlite3")
    task = _task(store)
    candidate, review = _reviewed(store, task, result)
    with pytest.raises(ValueError):
        store.create_standard_wechat_delivery(
            candidate_id=candidate["id"], review_id=review["id"], action_identity=IDENTITY)


def test_a_review_of_another_task_does_not_release_this_delivery(tmp_path):
    store = AutoReplyStore(tmp_path / "other.sqlite3")
    first = _task(store, "m1")
    other_candidate, other_review = _reviewed(store, first, _result())
    store.enqueue_reply_task(
        channel="wechat", conversation_id="u8", conversation_title="Sam", single_chat=True,
        trigger_message_id="m2", trigger_create_time="2026-10-24T10:05:00",
        trigger_sender="Sam", trigger_text="hello")
    second = store.claim_reply_tasks(1, channel="wechat")[0]
    assert second.id != first.id
    delivery_id = create_wechat_delivery(store,
        reply_task_id=second.id, account_id="acct-1", target_type="direct",
        target_id="u9", conversation_id="u9", reply_text=TEXT)
    evidence = {"standard_candidate_id": str(other_candidate["id"]),
                "standard_review_id": str(other_review["id"]),
                "standard_action_identity": IDENTITY}
    _set(store, delivery_id, evidence_json=json.dumps(evidence))
    assert store.wechat_delivery_review_approved(delivery_id) is False


def test_a_delivery_without_any_review_stays_unapproved(tmp_path):
    store = AutoReplyStore(tmp_path / "plain.sqlite3")
    task = _task(store)
    delivery_id = create_wechat_delivery(store,
        reply_task_id=task.id, account_id="acct-1", target_type="direct",
        target_id="u9", conversation_id="u9", reply_text=TEXT)
    assert store.wechat_delivery_review_approved(delivery_id) is False
    assert store.claim_wechat_delivery(
        delivery_id, expected_execution_generation=task.execution_generation) is None


def test_creating_twice_returns_the_same_delivery(approved):
    store, _, candidate, review, delivery_id = approved
    again = store.create_standard_wechat_delivery(
        candidate_id=candidate["id"], review_id=review["id"], action_identity=IDENTITY)
    assert again == delivery_id
