"""A referenced incoming message and an accepted task do not prove delivery."""

import pytest

from app.dws_client import DwsClient
from app.service_message_sender import ServiceMessageSender
from app.store import AutoReplyStore
from app.system_action_handlers import DingTalkMessageHandler
from tests.test_dws_client import SequenceRecordingDwsClient


def reply_receipt():
    return {"success": True, "contractVersion": "im.message-reply.v1",
        "conversationId": "group-1", "deliveryStatus": "unknown", "deliveryStatusKnown": False,
        "referencedMessage": {"messageId": "incoming-1"},
        "result": {"openTaskId": "send-task-1"}}


@pytest.mark.parametrize("status,state", [("SUCCESS", "sent"), ("PENDING", "ambiguous"),
                                         ("FAILED", "failed")])
def test_reply_reference_does_not_bypass_send_task_query(status, state):
    client = SequenceRecordingDwsClient([{"success": True, "result": {"sendStatus": status}}])
    verified = client.verify_message_send_result(reply_receipt())
    assert verified["state"] == state
    assert verified["open_task_id"] == "send-task-1"
    assert verified["status_result"]["result"]["sendStatus"] == status
    assert len(client.commands) == 1


def native_send_receipt(*, nested_task_id="send-task-1", receipt_task_id="send-task-1"):
    return {
        "ok": True,
        "tool": "send_personal_message",
        "result": {"success": True, "result": {"openTaskId": nested_task_id}},
        "sendReceipt": {"contractVersion": "im.message-send-receipt.v1",
                        "openTaskId": receipt_task_id},
    }


def test_native_send_envelope_queries_accepted_task_instead_of_staying_uncertain():
    client = SequenceRecordingDwsClient([{"success": True, "result": {
        "sendStatus": "SUCCESS", "openMessageId": "outgoing-1"}}])

    verified = client.verify_message_send_result(native_send_receipt())

    assert verified["state"] == "sent"
    assert verified["open_task_id"] == "send-task-1"
    assert len(client.commands) == 1


def test_system_handler_verifies_persisted_native_send_receipt():
    client = SequenceRecordingDwsClient([{"success": True, "result": {
        "sendStatus": "SUCCESS", "openMessageId": "outgoing-1"}}])

    outcome = DingTalkMessageHandler(None, client)._verified(native_send_receipt())

    assert outcome.status == "verified"
    assert outcome.provider_result["verification"]["open_task_id"] == "send-task-1"
    assert len(client.commands) == 1


def test_native_direct_and_nested_task_identity_conflict_stays_uncertain():
    client = SequenceRecordingDwsClient([])
    receipt = native_send_receipt()
    receipt["result"]["openTaskId"] = "other-task"
    assert client.verify_message_send_result(receipt)["state"] == "ambiguous"
    assert client.commands == []


def test_native_receipt_task_identity_can_be_queried_without_nested_result():
    client = SequenceRecordingDwsClient([{"success": True, "result": {
        "sendStatus": "PENDING"}}])
    receipt = native_send_receipt(nested_task_id="")

    verified = client.verify_message_send_result(receipt)

    assert verified["state"] == "ambiguous"
    assert verified["open_task_id"] == "send-task-1"
    assert len(client.commands) == 1


@pytest.mark.parametrize("receipt", [
    native_send_receipt(nested_task_id="send-task-1", receipt_task_id="other-task"),
    {"ok": True, "result": {"success": False, "result": {"openTaskId": "send-task-1"}},
     "sendReceipt": {"contractVersion": "im.message-send-receipt.v1",
                     "openTaskId": "send-task-1"}},
    {"ok": True, "result": {"success": True, "result": {}},
     "sendReceipt": {"contractVersion": "unrelated-contract",
                     "openTaskId": "send-task-1"}},
])
def test_native_untrusted_task_id_does_not_start_status_query(receipt):
    client = SequenceRecordingDwsClient([])

    assert client.verify_message_send_result(receipt)["state"] == "ambiguous"
    assert client.commands == []


@pytest.mark.parametrize("noise", [{"referencedMessage": {"messageId": "old"}},
    {"nextActions": [{"arguments": {"messageId": "old"}}]},
    {"result": {"processQueryKey": "bot-recall-key"}}])
def test_unrelated_identifier_is_not_delivery_evidence(noise):
    assert DwsClient().verify_message_send_result({"success": True, **noise})["state"] == "ambiguous"


def test_outgoing_message_id_without_readback_proof_is_still_ambiguous():
    assert DwsClient().verify_message_send_result({
        "success": True, "result": {"openMessageId": "outgoing-id"},
    })["state"] == "ambiguous"


def test_nested_status_metadata_is_not_send_task_completion():
    client = SequenceRecordingDwsClient([{"success": True, "result": {
        "sendStatus": "PENDING", "metadata": {"status": "SUCCESS"}}}])
    assert client.verify_message_send_result(reply_receipt())["state"] == "ambiguous"


def test_task_receipt_is_queried_even_when_message_id_is_already_present():
    client = SequenceRecordingDwsClient([{"success": True, "result": {"sendStatus": "PENDING"}}])
    receipt = reply_receipt()
    receipt["result"]["openMessageId"] = "outgoing-1"
    assert client.verify_message_send_result(receipt)["state"] == "ambiguous"
    assert len(client.commands) == 1


def test_duplicate_acceptance_without_delivery_receipt_is_unknown():
    verified = DwsClient().verify_message_send_result({"success": True, "idempotency": {
        "state": "duplicate_confirmed", "uuid": "original-uuid",
    }})
    assert verified["state"] == "ambiguous"


def test_message_identifier_without_provider_delivery_receipt_is_not_success():
    verified = DwsClient().verify_message_send_result({
        "success": True,
        "result": {"openMessageId": "outgoing-without-receipt"},
    })
    assert verified["state"] == "ambiguous"


def test_generic_finished_status_is_not_native_delivery_success():
    client = SequenceRecordingDwsClient([{"success": True, "result": {"status": "finished"}}])
    assert client.verify_message_send_result(reply_receipt())["state"] == "ambiguous"


class ReplyClient(SequenceRecordingDwsClient):
    def __init__(self):
        super().__init__([])
        self.status = "PENDING"
        self.sends = 0

    def send_reply_to_trigger(self, conversation, trigger, text):
        self.sends += 1
        return reply_receipt()

    def query_message_send_status(self, task_id):
        assert task_id == "send-task-1"
        self.commands.append(["query", task_id])
        return {"success": True, "result": {"sendStatus": self.status}}


def test_pending_reply_retains_task_and_rechecks_it_without_resending(tmp_path):
    store = AutoReplyStore(tmp_path / "state.sqlite3")
    client = ReplyClient()
    sender = ServiceMessageSender(store=store, dingtalk=client)
    prepared = sender.prepare(channel="dingtalk", delivery_key="reply-1", body="Prepared advice")
    with pytest.raises(RuntimeError, match="ambiguous"):
        sender.send_dingtalk_reply_to_trigger_prepared(prepared, conversation="group-1", trigger="incoming-1")
    assert store.get_outbound_postfix_receipt("dingtalk", prepared.delivery_key) == reply_receipt()
    # Recreating the sender models recovery with no in-memory provider result.
    sender = ServiceMessageSender(store=store, dingtalk=client)
    with pytest.raises(RuntimeError, match="ambiguous"):
        sender.send_dingtalk_reply_to_trigger_prepared(prepared, conversation="group-1", trigger="incoming-1")
    client.status = "SUCCESS"
    result = sender.send_dingtalk_reply_to_trigger_prepared(prepared, conversation="group-1", trigger="incoming-1")
    assert result.provider_result == reply_receipt()
    assert client.sends == 1
    assert len(client.commands) == 3


def test_system_handler_does_not_promote_pending_reply_to_verified():
    client = ReplyClient()
    outcome = DingTalkMessageHandler(None, client)._verified(reply_receipt())
    assert outcome.status == "uncertain"
    assert outcome.provider_result["verification"]["open_task_id"] == "send-task-1"


def test_native_reply_process_query_key_is_not_completed_delivery(tmp_path):
    class ProcessKeyClient(ReplyClient):
        def send_reply_to_trigger(self, conversation, trigger, text):
            self.sends += 1
            return {"success": True, "result": {"processQueryKey": "recall-key"}}
    client = ProcessKeyClient()
    sender = ServiceMessageSender(store=AutoReplyStore(tmp_path / "state.sqlite3"), dingtalk=client)
    prepared = sender.prepare(channel="dingtalk", delivery_key="reply-1", body="Prepared advice")
    with pytest.raises(RuntimeError, match="ambiguous"):
        sender.send_dingtalk_reply_to_trigger_prepared(prepared, conversation="group-1", trigger="incoming-1")


def test_unknown_delivery_keeps_execution_uncertain_until_same_task_succeeds(tmp_path):
    from tests.test_agent_orchestrator import (
        ScriptedAudit, ScriptedConsumer, candidate, orchestrator, task_and_context,
    )
    store, task, context = task_and_context(tmp_path)
    client = ReplyClient()

    class DeliveryHandler(DingTalkMessageHandler):
        dispatched = 0
        reconciled = 0

        def dispatch(self, action, **kwargs):
            self.dispatched += 1
            return self._verified(reply_receipt())

        def reconcile(self, action, **kwargs):
            self.reconciled += 1
            return self._verified(reply_receipt())

    handler = DeliveryHandler(store, client)
    consumer, audit = ScriptedConsumer(store, candidate()), ScriptedAudit(store, "approve")
    driver, _ = orchestrator(store, consumer, audit, handler)
    result = driver.process(task, context, refresh_context=lambda: context)
    assert result.error.code == "external_action_uncertain"
    reviewed = store.current_reviewed_candidate(task.id, task.execution_generation)
    execution = store.get_candidate_execution(reviewed["id"])
    assert execution["status"] == "uncertain"
    attempts = store.list_candidate_action_attempts(execution["id"])
    assert attempts[0]["status"] == "uncertain"
    assert store.get_candidate_external_action(attempts[0]["external_action_key"]) is None
    client.status = "SUCCESS"
    result = driver.process(task, context, refresh_context=lambda: context)
    assert result.status == "executed"
    assert (handler.dispatched, handler.reconciled) == (1, 1)
    assert len(consumer.calls) == len(audit.calls) == 1
