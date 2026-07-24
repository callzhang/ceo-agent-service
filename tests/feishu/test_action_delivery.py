import asyncio
import hashlib
import importlib
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest

from app.feishu.action_delivery import (
    FeishuMessageActionSender,
    plan_message_action_reconciliation,
    recover_orphaned_message_actions,
)
from app.feishu.actions import build_message_action
from app.feishu.client import FeishuChannelClient, FeishuSendResult
from app.feishu.delivery import delivery_idempotency_key
from app.feishu.models import FeishuInboundMessage
from app.store import AutoReplyStore


def _seed(tmp_path, *, app_id="cli_a", chat_id="oc_a"):
    store = AutoReplyStore(tmp_path / "actions.sqlite3")
    event = store.record_feishu_event(
        FeishuInboundMessage(
            event_id="evt_action",
            app_id=app_id,
            message_id="om_trigger",
            chat_id=chat_id,
            chat_type="group",
            sender_open_id="ou_sender",
            message_type="text",
            mentioned_bot=True,
            body_text="please help",
            event_create_time="2026-07-22T03:20:00+00:00",
            received_at="2026-07-22T03:20:01+00:00",
        ),
        eligibility_status="eligible",
        store_body=True,
    )
    task = next(
        task
        for task in store.list_reply_tasks(channel="feishu")
        if task.id == event.reply_task_id
    )
    attempt_id = store.record_reply_attempt(
        conversation_id=task.conversation_id,
        conversation_title=task.conversation_title,
        trigger_message_id=event.message_id,
        trigger_sender="sender",
        trigger_text=event.body_text,
        action="send_reply",
        sensitivity_kind="general",
        draft_reply_text="reply",
        send_status="pending",
        channel="feishu",
    )
    delivery = store.create_feishu_delivery(
        reply_task_id=task.id,
        attempt_id=attempt_id,
        app_id=app_id,
        chat_id=chat_id,
        reply_to_message_id=event.message_id,
        reply_in_thread=False,
        # Build a real two-chunk delivery so both owned message receipts obey
        # the immutable local chunk plan used by the Stage 2 sender.
        reply_text="R" * 3501,
        idempotency_key=delivery_idempotency_key(
            app_id=app_id,
            reply_task_id=task.id,
            trigger_message_id=event.message_id,
        ),
    )
    claimed = store.claim_feishu_delivery(delivery.id, app_id=app_id)
    store.transition_feishu_delivery(
        delivery.id,
        from_statuses=("sending",),
        to_status="sent",
        app_id=app_id,
        expected_lease_token=claimed.lease_token,
        feishu_message_id="om_bot_1",
        message_ids=("om_bot_1", "om_bot_2"),
    )
    return store, task, attempt_id, delivery


def _action(store, task, attempt_id, *, kind, key, target="", open_id="", payload=None):
    built = build_message_action(
        reply_task_id=task.id,
        attempt_id=attempt_id,
        app_id="cli_a",
        chat_id="oc_a",
        action_key=key,
        kind=kind,
        target_message_id=target,
        target_open_id=open_id,
        payload=payload,
    )
    return store.create_feishu_message_action(
        built,
        handoff_target_allowlist=("ou_owner",),
    )


def _pending_delivery(store, *, number: int, reply_text: str = "next reply"):
    event = store.record_feishu_event(
        FeishuInboundMessage(
            event_id=f"evt_delivery_{number}",
            app_id="cli_a",
            message_id=f"om_delivery_{number}",
            chat_id="oc_a",
            chat_type="group",
            sender_open_id="ou_sender",
            message_type="text",
            mentioned_bot=True,
            body_text="next",
            event_create_time=f"2026-07-22T03:21:{number:02d}+00:00",
            received_at=f"2026-07-22T03:21:{number + 1:02d}+00:00",
        ),
        eligibility_status="eligible",
        store_body=True,
    )
    task = next(
        item
        for item in store.list_reply_tasks(channel="feishu")
        if item.id == event.reply_task_id
    )
    attempt_id = store.record_reply_attempt(
        conversation_id=task.conversation_id,
        conversation_title=task.conversation_title,
        trigger_message_id=event.message_id,
        trigger_sender="sender",
        trigger_text=event.body_text,
        action="send_reply",
        sensitivity_kind="general",
        draft_reply_text=reply_text,
        send_status="pending",
        channel="feishu",
    )
    return store.create_feishu_delivery(
        reply_task_id=task.id,
        attempt_id=attempt_id,
        app_id="cli_a",
        chat_id="oc_a",
        reply_to_message_id=event.message_id,
        reply_in_thread=False,
        reply_text=reply_text,
        idempotency_key=delivery_idempotency_key(
            app_id="cli_a",
            reply_task_id=task.id,
            trigger_message_id=event.message_id,
        ),
    )


def _record_later_trigger(
    store,
    *,
    app_id="cli_a",
    chat_id="oc_a",
    message_id="om_later",
    reference_root="om_trigger",
    enqueue_eligible=True,
):
    return store.record_feishu_event(
        FeishuInboundMessage(
            event_id=f"evt_{app_id}_{message_id}",
            app_id=app_id,
            message_id=message_id,
            chat_id=chat_id,
            chat_type="group",
            root_message_id=reference_root,
            sender_open_id="ou_later",
            message_type="text",
            mentioned_bot=True,
            body_text="new trigger",
            event_create_time="2026-07-22T03:20:02+00:00",
            received_at="2026-07-22T03:20:03+00:00",
        ),
        eligibility_status="eligible",
        store_body=True,
        enqueue_eligible=enqueue_eligible,
    )


class FakeActionClient:
    app_id = "cli_a"

    def __init__(self):
        self.message_state = SimpleNamespace(state="exists")
        self.reaction_result = FeishuSendResult(
            True, reaction_id="omr_1", request_log_id="log_reaction"
        )
        self.recall_result = FeishuSendResult(True, request_log_id="log_recall")
        self.handoff_result = FeishuSendResult(True, message_id="om_handoff")
        self.calls = []
        self.probe_calls = []

    async def fetch_message_state(self, app_id, message_id):
        self.probe_calls.append((app_id, message_id))
        return self.message_state

    async def add_reaction(self, app_id, message_id, emoji_type):
        self.calls.append(("reaction", app_id, message_id, emoji_type))
        return self.reaction_result

    async def recall_message(self, app_id, message_id):
        self.calls.append(("recall", app_id, message_id))
        return self.recall_result

    async def send_handoff(self, action):
        self.calls.append(("handoff", action.app_id, action.target_open_id))
        return self.handoff_result


def _sender(store, client, **kwargs):
    kwargs.setdefault("handoff_target_allowlist", ("ou_owner",))
    return FeishuMessageActionSender(
        store,
        client,
        sender_enabled=True,
        live_send_allowed=True,
        reactions_enabled=True,
        recalls_enabled=True,
        handoff_enabled=True,
        **kwargs,
    )


def _reviewed_action_send(sender, action, *, approved_by="operator"):
    return sender.approve_and_send(
        action.id,
        expected_approval_hash=action.approval_hash,
        approved_by=approved_by,
    )


def test_reaction_probes_persisted_trigger_before_remote_mutation(tmp_path):
    store, task, attempt_id, _ = _seed(tmp_path)
    action = _action(
        store,
        task,
        attempt_id,
        kind="add_reaction",
        key="reaction:probe-exists",
        target="om_trigger",
        payload={"emoji_type": "OK"},
    )
    client = FakeActionClient()

    outcome = asyncio.run(
        _reviewed_action_send(
            _sender(store, client, send_mode="auto"), action
        )
    )

    assert outcome.status == "sent"
    assert client.probe_calls == [("cli_a", "om_trigger")]
    assert client.calls == [("reaction", "cli_a", "om_trigger", "OK")]


def test_reaction_absent_target_fails_before_budget_fence_or_mutation(tmp_path):
    store, task, attempt_id, _ = _seed(tmp_path)
    action = _action(
        store,
        task,
        attempt_id,
        kind="add_reaction",
        key="reaction:probe-absent",
        target="om_trigger",
        payload={"emoji_type": "OK"},
    )
    client = FakeActionClient()
    client.message_state = SimpleNamespace(state="absent")
    sender = _sender(store, client, send_mode="auto")

    outcome = asyncio.run(_reviewed_action_send(sender, action))

    saved = store.get_feishu_message_action(action.id)
    assert outcome.status == "failed"
    assert outcome.error_code == "target_revoked"
    assert saved.status == "failed"
    assert saved.mutation_started_at == ""
    assert client.calls == []
    assert list(sender._sent_times) == []


@pytest.mark.parametrize(
    ("max_attempts", "expected_status"),
    [(3, "retry"), (1, "failed")],
)
def test_reaction_unknown_target_never_mutates(
    tmp_path, max_attempts, expected_status
):
    store, task, attempt_id, _ = _seed(tmp_path)
    action = _action(
        store,
        task,
        attempt_id,
        kind="add_reaction",
        key=f"reaction:probe-unknown:{max_attempts}",
        target="om_trigger",
        payload={"emoji_type": "OK"},
    )
    client = FakeActionClient()
    client.message_state = SimpleNamespace(state="unknown")
    sender = _sender(
        store,
        client,
        send_mode="auto",
        max_attempts=max_attempts,
    )

    outcome = asyncio.run(_reviewed_action_send(sender, action))

    saved = store.get_feishu_message_action(action.id)
    assert outcome.status == expected_status
    assert outcome.error_code == "target_probe_unknown"
    assert saved.status == expected_status
    assert saved.mutation_started_at == ""
    assert client.calls == []
    assert list(sender._sent_times) == []


def test_reaction_probe_timeout_is_retryable_not_result_unknown(tmp_path):
    store, task, attempt_id, _ = _seed(tmp_path)
    action = _action(
        store,
        task,
        attempt_id,
        kind="add_reaction",
        key="reaction:probe-timeout",
        target="om_trigger",
        payload={"emoji_type": "OK"},
    )

    class SlowProbeClient(FakeActionClient):
        async def fetch_message_state(self, app_id, message_id):
            await asyncio.sleep(1)

    client = SlowProbeClient()
    sender = _sender(
        store,
        client,
        send_mode="auto",
        action_timeout_seconds=0.01,
    )

    outcome = asyncio.run(_reviewed_action_send(sender, action))

    saved = store.get_feishu_message_action(action.id)
    assert outcome.status == "retry"
    assert saved.status == "retry"
    assert saved.error_code == "target_probe_unknown"
    assert saved.mutation_started_at == ""
    assert client.calls == []
    assert list(sender._sent_times) == []


def test_claimed_handoff_rechecks_current_allowlist_before_network(tmp_path):
    store, task, attempt_id, _ = _seed(tmp_path)
    action = _action(
        store,
        task,
        attempt_id,
        kind="handoff_notify",
        key="handoff:revoked-after-create",
        open_id="ou_owner",
        payload={"text": "take over"},
    )
    client = FakeActionClient()
    sender = _sender(
        store,
        client,
        send_mode="auto",
        handoff_target_allowlist=(),
    )

    assert asyncio.run(sender.process_once()) == 1

    saved = store.get_feishu_message_action(action.id)
    assert saved.status == "failed"
    assert saved.error_code == "target_revoked"
    assert client.calls == []
    assert "handoff_target_revoked" in {
        event.event_type
        for event in store.list_feishu_audit_events(
            entity_type="message_action", entity_id=action.id
        )
    }


def test_handoff_sender_rejects_untrusted_allowlist_shape(tmp_path):
    store, _, _, _ = _seed(tmp_path)
    client = FakeActionClient()

    with pytest.raises(ValueError, match="local sequence"):
        _sender(store, client, handoff_target_allowlist="ou_owner")
    with pytest.raises(ValueError, match="invalid target"):
        _sender(store, client, handoff_target_allowlist=(" user@example.com ",))


def test_action_unknown_reconciliation_contract_is_kind_specific():
    reaction = SimpleNamespace(status="result_unknown", kind="add_reaction")
    recall = SimpleNamespace(status="result_unknown", kind="recall_message")
    handoff = SimpleNamespace(status="result_unknown", kind="handoff_notify")

    reaction_applied = plan_message_action_reconciliation(
        reaction,
        outcome="applied",
        verified_by="operator",
        evidence_kind="message_lookup",
        remote_id="omr_verified",
        request_log_id="log_1",
    )
    assert reaction_applied.final_status == "sent"
    assert reaction_applied.remote_id == "omr_verified"
    assert reaction_applied.evidence_kind == "message_lookup"

    recall_applied = plan_message_action_reconciliation(
        recall,
        outcome="applied",
        verified_by="operator",
        evidence_kind="feishu_ui",
    )
    assert recall_applied.final_status == "sent"
    assert recall_applied.remote_id == ""
    assert recall_applied.recall_receipt_status == "recalled"

    recall_not_applied = plan_message_action_reconciliation(
        recall,
        outcome="not-applied",
        verified_by="operator",
        evidence_kind="admin_audit",
    )
    assert recall_not_applied.final_status == "failed"
    assert recall_not_applied.error_code == "verified_not_applied"
    assert recall_not_applied.recall_receipt_status == "active"

    handoff_applied = plan_message_action_reconciliation(
        handoff,
        outcome="applied",
        verified_by="operator",
        evidence_kind="feishu_ui",
        remote_id="om_handoff_verified",
    )
    assert handoff_applied.final_status == "sent"
    assert handoff_applied.remote_id == "om_handoff_verified"


def test_action_unknown_reconciliation_contract_rejects_weak_evidence():
    reaction = SimpleNamespace(status="result_unknown", kind="add_reaction")

    with pytest.raises(ValueError, match="requires verified_by"):
        plan_message_action_reconciliation(
            reaction,
            outcome="applied",
            verified_by="",
            evidence_kind="message_lookup",
            remote_id="omr_verified",
        )
    with pytest.raises(ValueError, match="evidence kind"):
        plan_message_action_reconciliation(
            reaction,
            outcome="applied",
            verified_by="operator",
            evidence_kind="free_form_note",
            remote_id="omr_verified",
        )
    with pytest.raises(ValueError, match="requires Feishu reaction ID"):
        plan_message_action_reconciliation(
            reaction,
            outcome="applied",
            verified_by="operator",
            evidence_kind="message_lookup",
        )
    with pytest.raises(ValueError, match="requires result_unknown"):
        plan_message_action_reconciliation(
            SimpleNamespace(status="sent", kind="add_reaction"),
            outcome="applied",
            verified_by="operator",
            evidence_kind="message_lookup",
            remote_id="omr_verified",
        )


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("outcome", 123),
        ("verified_by", 123),
        ("evidence_kind", 123),
        ("remote_id", 123),
        ("request_log_id", 123),
    ),
)
def test_action_unknown_reconciliation_rejects_non_string_inputs(field, value):
    action = SimpleNamespace(status="result_unknown", kind="add_reaction")
    arguments = {
        "outcome": "not_applied",
        "verified_by": "operator",
        "evidence_kind": "feishu_ui",
        "remote_id": "",
        "request_log_id": "",
    }
    arguments[field] = value
    with pytest.raises(ValueError):
        plan_message_action_reconciliation(action, **arguments)


def test_local_action_reject_remains_available_with_outbound_gates_closed(
    tmp_path,
):
    store, task, attempt_id, _ = _seed(tmp_path)
    action = _action(
        store,
        task,
        attempt_id,
        kind="add_reaction",
        key="reaction:reject-while-disabled",
        target="om_trigger",
        payload={"emoji_type": "OK"},
    )
    client = FakeActionClient()
    sender = FeishuMessageActionSender(
        store,
        client,
        sender_enabled=False,
        live_send_allowed=False,
        reactions_enabled=False,
    )

    sender.reject(action.id, rejected_by="operator")

    assert store.get_feishu_message_action(action.id).status == "rejected"
    assert client.calls == []


def test_receipts_preserve_every_chunk_and_primary_compatibility(tmp_path):
    store, _, _, delivery = _seed(tmp_path)

    receipts = store.list_feishu_delivery_receipts(delivery_id=delivery.id)

    assert [receipt.ordinal for receipt in receipts] == [0, 1]
    assert [receipt.message_id for receipt in receipts] == ["om_bot_1", "om_bot_2"]
    assert store.get_feishu_delivery(delivery.id).feishu_message_id == "om_bot_1"


def test_action_targets_are_strictly_app_owned_and_handoff_allowlisted(tmp_path):
    store, task, attempt_id, _ = _seed(tmp_path)
    reaction = _action(
        store,
        task,
        attempt_id,
        kind="add_reaction",
        key="reaction:trigger",
        target="om_trigger",
        payload={"emoji_type": "OK"},
    )
    recall = _action(
        store,
        task,
        attempt_id,
        kind="recall_message",
        key="recall:bot",
        target="om_bot_2",
    )

    assert reaction.target_message_id == "om_trigger"
    assert recall.target_message_id == "om_bot_2"
    with pytest.raises(PermissionError, match="terminal receipt"):
        _action(
            store,
            task,
            attempt_id,
            kind="recall_message",
            key="recall:foreign",
            target="om_foreign",
        )
    with pytest.raises(PermissionError, match="persisted trigger"):
        _action(
            store,
            task,
            attempt_id,
            kind="add_reaction",
            key="reaction:owned-but-not-trigger",
            target="om_bot_1",
            payload={"emoji_type": "OK"},
        )
    blocked = build_message_action(
        reply_task_id=task.id,
        attempt_id=attempt_id,
        app_id="cli_a",
        chat_id="oc_a",
        action_key="handoff:blocked",
        kind="handoff_notify",
        target_open_id="ou_blocked",
        payload={"text": "take over"},
    )
    with pytest.raises(PermissionError, match="allowlisted"):
        store.create_feishu_message_action(
            blocked, handoff_target_allowlist=("ou_owner",)
        )


def test_partial_receipt_recall_requires_terminal_owner_and_blocks_requeue(
    tmp_path,
):
    store, _, _, _ = _seed(tmp_path)
    delivery = _pending_delivery(
        store, number=2, reply_text="x" * 6000
    )
    task = next(
        item
        for item in store.list_reply_tasks(channel="feishu")
        if item.id == delivery.reply_task_id
    )
    claimed = store.claim_feishu_delivery(delivery.id, app_id="cli_a")
    store.record_feishu_delivery_chunk(
        delivery.id,
        app_id="cli_a",
        lease_token=claimed.lease_token,
        ordinal=0,
        expected_chunks=2,
        message_id="om_partial_terminal",
    )
    store.mark_feishu_delivery_send_unknown(
        delivery.id,
        error_code="send_timeout",
        error="second chunk unknown",
    )
    with pytest.raises(PermissionError, match="terminal receipt"):
        _action(
            store,
            task,
            delivery.attempt_id,
            kind="recall_message",
            key="recall:while-unknown",
            target="om_partial_terminal",
        )
    failed = store.reconcile_feishu_delivery_unknown(
        delivery.id,
        app_id="cli_a",
        outcome="not_sent",
        verified_by="operator",
        evidence_kind="message_lookup",
    )
    recall = _action(
        store,
        task,
        delivery.attempt_id,
        kind="recall_message",
        key="recall:failed-prefix",
        target="om_partial_terminal",
    )
    with pytest.raises(ValueError, match="open recall action"):
        store.requeue_feishu_delivery_after_verification(
            delivery.id,
            app_id="cli_a",
            verified_by="operator",
            evidence_kind="admin_audit",
        )
    outcome = asyncio.run(
        _reviewed_action_send(
            _sender(store, FakeActionClient(), send_mode="auto"), recall
        )
    )
    assert failed.status == "failed"
    assert outcome.status == "sent"
    assert store.get_feishu_delivery_receipt(
        app_id="cli_a", message_id="om_partial_terminal"
    ).status == "recalled"


def test_verified_delivery_requeue_rechecks_recall_atomically(
    tmp_path, monkeypatch
):
    store, _, _, _ = _seed(tmp_path)
    delivery = _pending_delivery(
        store, number=2, reply_text="z" * 6000
    )
    task = next(
        item
        for item in store.list_reply_tasks(channel="feishu")
        if item.id == delivery.reply_task_id
    )
    claimed = store.claim_feishu_delivery(delivery.id, app_id="cli_a")
    store.record_feishu_delivery_chunk(
        delivery.id,
        app_id="cli_a",
        lease_token=claimed.lease_token,
        ordinal=0,
        expected_chunks=2,
        message_id="om_recalled_during_requeue",
    )
    store.mark_feishu_delivery_send_unknown(
        delivery.id,
        error_code="send_timeout",
        error="second chunk unknown",
    )
    store.reconcile_feishu_delivery_unknown(
        delivery.id,
        app_id="cli_a",
        outcome="not_sent",
        verified_by="operator",
        evidence_kind="message_lookup",
    )
    recall = _action(
        store,
        task,
        delivery.attempt_id,
        kind="recall_message",
        key="recall:during-verified-requeue",
        target="om_recalled_during_requeue",
    )

    reached_transition = threading.Event()
    finish_transition = threading.Event()
    original_transition = store.transition_feishu_delivery

    def pause_after_requeue_prechecks(*args, **kwargs):
        if kwargs.get("audit_event_type") == "requeued_after_verification":
            reached_transition.set()
            assert finish_transition.wait(timeout=5)
        return original_transition(*args, **kwargs)

    monkeypatch.setattr(
        store, "transition_feishu_delivery", pause_after_requeue_prechecks
    )

    with ThreadPoolExecutor(max_workers=1) as executor:
        pending_requeue = executor.submit(
            store.requeue_feishu_delivery_after_verification,
            delivery.id,
            app_id="cli_a",
            verified_by="operator",
            evidence_kind="admin_audit",
        )
        assert reached_transition.wait(timeout=5)
        try:
            outcome = asyncio.run(
                _reviewed_action_send(
                    _sender(store, FakeActionClient(), send_mode="auto"),
                    recall,
                )
            )
        finally:
            finish_transition.set()

        assert outcome.status == "sent"
        with pytest.raises(ValueError, match="recall|receipt"):
            pending_requeue.result(timeout=5)

    assert store.get_feishu_delivery(delivery.id).status == "failed"
    assert store.get_feishu_delivery_receipt(
        app_id="cli_a", message_id="om_recalled_during_requeue"
    ).status == "recalled"


def test_partial_receipt_recall_is_denied_while_retrying_or_sending(tmp_path):
    store, _, _, _ = _seed(tmp_path)
    delivery = _pending_delivery(
        store, number=2, reply_text="y" * 6000
    )
    task = next(
        item
        for item in store.list_reply_tasks(channel="feishu")
        if item.id == delivery.reply_task_id
    )
    claimed = store.claim_feishu_delivery(delivery.id, app_id="cli_a")
    store.record_feishu_delivery_chunk(
        delivery.id,
        app_id="cli_a",
        lease_token=claimed.lease_token,
        ordinal=0,
        expected_chunks=2,
        message_id="om_partial_retry",
    )
    store.mark_feishu_delivery_send_unknown(
        delivery.id,
        error_code="send_timeout",
        error="second chunk unknown",
    )
    store.reconcile_feishu_delivery_unknown(
        delivery.id,
        app_id="cli_a",
        outcome="not_sent",
        verified_by="operator",
        evidence_kind="message_lookup",
    )
    retry = store.requeue_feishu_delivery_after_verification(
        delivery.id,
        app_id="cli_a",
        verified_by="operator",
        evidence_kind="admin_audit",
    )
    with pytest.raises(PermissionError, match="terminal receipt"):
        _action(
            store,
            task,
            delivery.attempt_id,
            kind="recall_message",
            key="recall:while-retry",
            target="om_partial_retry",
        )
    store.approve_feishu_delivery(
        delivery.id,
        app_id="cli_a",
        approved_by="operator",
        expected_approval_hash=retry.approval_hash,
    )
    assert store.claim_feishu_delivery(
        delivery.id, app_id="cli_a", approved_only=True
    ) is not None
    with pytest.raises(PermissionError, match="terminal receipt"):
        _action(
            store,
            task,
            delivery.attempt_id,
            kind="recall_message",
            key="recall:while-sending",
            target="om_partial_retry",
        )


def test_r4_recall_requires_approval_even_in_auto_mode(tmp_path):
    store, task, attempt_id, _ = _seed(tmp_path)
    recall = _action(
        store,
        task,
        attempt_id,
        kind="recall_message",
        key="recall:r4",
        target="om_bot_1",
    )

    assert store.claim_feishu_message_action(
        recall.id,
        app_id="cli_a",
        kinds=("recall_message",),
        send_mode="auto",
    ) is None
    store.approve_feishu_message_action(
        recall.id,
        app_id="cli_a",
        approved_by="owner",
        expected_approval_hash=recall.approval_hash,
    )
    claimed = store.claim_feishu_message_action(
        recall.id,
        app_id="cli_a",
        kinds=("recall_message",),
        send_mode="auto",
    )
    assert claimed is not None and claimed.status == "sending"


def test_action_runtime_helper_cannot_self_source_approval_hash(tmp_path):
    store, task, attempt_id, _ = _seed(tmp_path)
    action = _action(
        store,
        task,
        attempt_id,
        kind="recall_message",
        key="recall:no-blind-runtime-approval",
        target="om_bot_1",
    )
    client = FakeActionClient()
    sender = _sender(store, client, send_mode="auto")

    with pytest.raises(TypeError, match="expected_approval_hash"):
        sender.approve_and_send(action.id, approved_by="operator")
    with pytest.raises(ValueError, match="approval hash changed"):
        asyncio.run(
            sender.approve_and_send(
                action.id,
                expected_approval_hash="0" * 64,
                approved_by="operator",
            )
        )

    assert store.get_feishu_message_action(action.id).approved_at == ""
    assert client.calls == []


def test_action_approval_hash_cannot_be_replayed_across_identical_effects(
    tmp_path,
):
    store, task, attempt_id, _ = _seed(tmp_path)
    first = _action(
        store,
        task,
        attempt_id,
        kind="add_reaction",
        key="reaction:approval-row-a",
        target="om_trigger",
        payload={"emoji_type": "OK"},
    )
    second = _action(
        store,
        task,
        attempt_id,
        kind="add_reaction",
        key="reaction:approval-row-b",
        target="om_trigger",
        payload={"emoji_type": "OK"},
    )

    assert first.payload_sha256 == second.payload_sha256
    assert first.approval_hash != second.approval_hash
    with pytest.raises(ValueError, match="approval hash changed"):
        store.approve_feishu_message_action(
            second.id,
            app_id="cli_a",
            approved_by="operator",
            expected_approval_hash=first.approval_hash,
        )
    assert store.get_feishu_message_action(second.id).approved_at == ""


def test_legacy_effect_only_action_approval_is_rotated_and_invalidated(
    tmp_path,
):
    store, task, attempt_id, _ = _seed(tmp_path)
    action = _action(
        store,
        task,
        attempt_id,
        kind="add_reaction",
        key="reaction:legacy-approval",
        target="om_trigger",
        payload={"emoji_type": "OK"},
    )
    legacy_fields = (
        "2",
        action.app_id,
        action.kind,
        action.target_message_id,
        action.payload_sha256,
        action.risk,
        str(action.review_generation),
    )
    legacy_hash = hashlib.sha256(
        "\0".join(legacy_fields).encode("utf-8")
    ).hexdigest()
    with sqlite3.connect(store.path) as db:
        db.execute(
            "drop trigger if exists feishu_message_actions_identity_immutable"
        )
        db.execute(
            "update feishu_message_actions set approval_hash=?, "
            "approved_at='2026-01-01T00:00:00+00:00', approved_by='legacy' "
            "where id=?",
            (legacy_hash, action.id),
        )

    # Re-run startup initialization to model the first open in a new process;
    # the store intentionally caches initialized paths within one process.
    store._initialize()
    migrated_store = AutoReplyStore(store.path)
    migrated = migrated_store.get_feishu_message_action(action.id)
    assert migrated.approval_hash == action.approval_hash
    assert migrated.approved_at == ""
    assert migrated.approved_by == ""
    assert "approval_snapshot_migrated" in {
        event.event_type
        for event in migrated_store.list_feishu_audit_events(
            entity_type="message_action", entity_id=action.id
        )
    }


def test_reaction_requires_reaction_id_and_unknown_is_never_reclaimed(tmp_path):
    store, task, attempt_id, _ = _seed(tmp_path)
    action = _action(
        store,
        task,
        attempt_id,
        kind="add_reaction",
        key="reaction:no-id",
        target="om_trigger",
        payload={"emoji_type": "OK"},
    )
    client = FakeActionClient()
    client.reaction_result = FeishuSendResult(True)
    sender = _sender(store, client, send_mode="auto")

    assert asyncio.run(sender.process_once()) == 1
    assert store.get_feishu_message_action(action.id).status == "result_unknown"
    assert asyncio.run(sender.process_once()) == 0
    assert len(client.calls) == 1


@pytest.mark.parametrize(
    "provider_code", ["target_state_unknown", "target_probe_unknown"]
)
def test_probe_named_reaction_exception_after_mutation_is_not_replayed(
    tmp_path, provider_code
):
    store, task, attempt_id, _ = _seed(tmp_path)
    action = _action(
        store,
        task,
        attempt_id,
        kind="add_reaction",
        key=f"reaction:provider-error:{provider_code}",
        target="om_trigger",
        payload={"emoji_type": "OK"},
    )
    provider_error = RuntimeError("provider state was indeterminate")
    provider_error.code = provider_code

    class MutatingErrorClient(FakeActionClient):
        async def add_reaction(self, app_id, message_id, emoji_type):
            self.calls.append(("reaction", app_id, message_id, emoji_type))
            raise provider_error

    client = MutatingErrorClient()
    sender = _sender(store, client, send_mode="auto")

    outcome = asyncio.run(_reviewed_action_send(sender, action))

    saved = store.get_feishu_message_action(action.id)
    assert outcome.status == "result_unknown"
    assert outcome.error_code == "unknown"
    assert saved.status == "result_unknown"
    assert saved.error_code == "unknown"
    assert saved.mutation_started_at
    assert client.calls == [("reaction", "cli_a", "om_trigger", "OK")]

    assert asyncio.run(sender.process_once()) == 0
    assert client.calls == [("reaction", "cli_a", "om_trigger", "OK")]


def test_store_rejects_target_probe_retry_after_action_mutation_fence(tmp_path):
    store, task, attempt_id, _ = _seed(tmp_path)
    action = _action(
        store,
        task,
        attempt_id,
        kind="add_reaction",
        key="reaction:probe-retry-after-fence",
        target="om_trigger",
        payload={"emoji_type": "OK"},
    )
    claimed = store.claim_feishu_message_action(
        action.id,
        app_id="cli_a",
        kinds=("add_reaction",),
        send_mode="auto",
    )
    assert claimed is not None
    fenced = store.begin_feishu_message_action_mutation(
        action.id,
        app_id="cli_a",
        lease_token=claimed.lease_token,
    )
    assert fenced is not None and fenced.mutation_started_at

    with pytest.raises(ValueError, match="target probe retry"):
        store.transition_feishu_message_action(
            action.id,
            from_statuses=("sending",),
            to_status="retry",
            app_id="cli_a",
            expected_lease_token=claimed.lease_token,
            error_code="target_probe_unknown",
            error="unsafe forged probe outcome",
        )

    saved = store.get_feishu_message_action(action.id)
    assert saved.status == "sending"
    assert saved.mutation_started_at == fenced.mutation_started_at


def test_recall_success_atomically_marks_owned_receipt(tmp_path):
    store, task, attempt_id, _ = _seed(tmp_path)
    action = _action(
        store,
        task,
        attempt_id,
        kind="recall_message",
        key="recall:success",
        target="om_bot_1",
    )
    sender = _sender(store, FakeActionClient(), send_mode="auto")

    outcome = asyncio.run(
        _reviewed_action_send(sender, action, approved_by="owner")
    )

    receipt = store.get_feishu_delivery_receipt(
        app_id="cli_a", message_id="om_bot_1"
    )
    assert outcome.status == "sent"
    assert receipt.status == "recalled"
    assert receipt.recall_action_id == action.id


def test_recall_already_absent_is_idempotent_success_but_permission_is_not(tmp_path):
    store, task, attempt_id, _ = _seed(tmp_path)
    action = _action(
        store,
        task,
        attempt_id,
        kind="recall_message",
        key="recall:already-absent",
        target="om_bot_1",
    )
    client = FakeActionClient()
    client.recall_result = FeishuSendResult(False, error_code="target_revoked")
    outcome = asyncio.run(
        _reviewed_action_send(
            _sender(store, client, send_mode="auto"),
            action,
            approved_by="owner",
        )
    )
    assert outcome.status == "sent"
    assert store.get_feishu_delivery_receipt(
        app_id="cli_a", message_id="om_bot_1"
    ).status == "recalled"
    assert "already_absent" in {
        event.event_type
        for event in store.list_feishu_audit_events(
            entity_type="message_action", entity_id=action.id
        )
    }

    # Use the other active chunk so the second action has a valid owned target.
    denied = _action(
        store,
        task,
        attempt_id,
        kind="recall_message",
        key="recall:denied",
        target="om_bot_2",
    )
    client.recall_result = FeishuSendResult(False, error_code="permission_denied")
    denied_outcome = asyncio.run(
        _reviewed_action_send(
            _sender(store, client, send_mode="auto"),
            denied,
            approved_by="owner",
        )
    )
    assert denied_outcome.status == "failed"
    assert store.get_feishu_delivery_receipt(
        app_id="cli_a", message_id="om_bot_2"
    ).status == "active"


def test_recall_timeout_marks_action_and_receipt_unknown(tmp_path):
    store, task, attempt_id, _ = _seed(tmp_path)
    action = _action(
        store,
        task,
        attempt_id,
        kind="recall_message",
        key="recall:timeout",
        target="om_bot_1",
    )

    class SlowClient(FakeActionClient):
        async def recall_message(self, app_id, message_id):
            self.calls.append(("recall", app_id, message_id))
            await asyncio.sleep(1)
            return self.recall_result

    sender = _sender(
        store,
        SlowClient(),
        send_mode="auto",
        action_timeout_seconds=0.01,
        action_lease_stale_seconds=1,
    )

    outcome = asyncio.run(
        _reviewed_action_send(sender, action, approved_by="owner")
    )

    receipt = store.get_feishu_delivery_receipt(
        app_id="cli_a", message_id="om_bot_1"
    )
    assert outcome.status == "result_unknown"
    assert receipt.status == "recall_unknown"


@pytest.mark.parametrize(
    ("kind", "result"),
    [
        (
            "add_reaction",
            FeishuSendResult(
                False,
                reaction_id="omr_uncertain",
                error_code="rate_limited",
            ),
        ),
        (
            "handoff_notify",
            FeishuSendResult(
                False,
                message_id="om_handoff_uncertain",
                error_code="permission_denied",
            ),
        ),
        (
            "handoff_notify",
            FeishuSendResult(
                True,
                message_ids=("om_handoff_1", "om_handoff_2"),
            ),
        ),
    ],
)
def test_action_result_shapes_with_extra_remote_effects_are_unknown(
    tmp_path, kind, result
):
    store, task, attempt_id, _ = _seed(tmp_path)
    action = _action(
        store,
        task,
        attempt_id,
        kind=kind,
        key=f"{kind}:result-shape",
        target=("om_trigger" if kind == "add_reaction" else ""),
        open_id=("ou_owner" if kind == "handoff_notify" else ""),
        payload=(
            {"emoji_type": "OK"}
            if kind == "add_reaction"
            else {"text": "handoff"}
        ),
    )
    client = FakeActionClient()
    if kind == "add_reaction":
        client.reaction_result = result
    else:
        client.handoff_result = result

    outcome = asyncio.run(
        _reviewed_action_send(
            _sender(store, client, send_mode="auto"), action
        )
    )

    assert outcome.status == "result_unknown"
    saved = store.get_feishu_message_action(action.id)
    assert saved.status == "result_unknown"
    assert saved.remote_id == ""


@pytest.mark.parametrize("kind", ["add_reaction", "recall_message"])
def test_pinned_sdk_target_echo_succeeds_end_to_end(tmp_path, kind):
    coerce = importlib.import_module("lark_channel.channel._coerce")

    class PinnedSdkChannel:
        async def fetch_message(self, message_id):
            return {"code": 0, "data": {"items": [{"message_id": message_id}]}}

        async def add_reaction(self, message_id, emoji_type):
            assert emoji_type == "OK"
            return coerce.result_from_raw(
                {"code": 0, "data": {"reaction_id": "omr_sdk"}},
                message_id=message_id,
            )

        async def recall_message(self, message_id):
            return coerce.result_from_raw(
                {"code": 0, "data": {"deleted": True}},
                message_id=message_id,
            )

    store, task, attempt_id, _ = _seed(tmp_path)
    action = _action(
        store,
        task,
        attempt_id,
        kind=kind,
        key=f"{kind}:sdk-target-echo",
        target="om_trigger" if kind == "add_reaction" else "om_bot_1",
        payload={"emoji_type": "OK"} if kind == "add_reaction" else {},
    )
    client = FeishuChannelClient(PinnedSdkChannel(), app_id="cli_a")

    outcome = asyncio.run(
        _reviewed_action_send(
            _sender(store, client, send_mode="auto"), action
        )
    )

    assert outcome.status == "sent"
    if kind == "add_reaction":
        assert outcome.remote_id == "omr_sdk"
    else:
        assert outcome.remote_id == ""
        assert store.get_feishu_delivery_receipt(
            app_id="cli_a", message_id="om_bot_1"
        ).status == "recalled"


@pytest.mark.parametrize("kind", ["add_reaction", "recall_message"])
def test_sdk_mismatched_or_multiple_action_ids_are_unknown_end_to_end(
    tmp_path, kind
):
    class AmbiguousSdkChannel:
        async def fetch_message(self, message_id):
            return {"code": 0, "data": {"items": [{"message_id": message_id}]}}

        async def add_reaction(self, message_id, emoji_type):
            return SimpleNamespace(
                success=True,
                message_id="om_wrong_target",
                chunk_ids=None,
                error=None,
                raw={"data": {"reaction_id": "omr_sdk"}},
            )

        async def recall_message(self, message_id):
            return SimpleNamespace(
                success=True,
                message_id=message_id,
                chunk_ids=["om_unplanned_extra"],
                error=None,
                raw={},
            )

    store, task, attempt_id, _ = _seed(tmp_path)
    action = _action(
        store,
        task,
        attempt_id,
        kind=kind,
        key=f"{kind}:sdk-ambiguous-result",
        target="om_trigger" if kind == "add_reaction" else "om_bot_1",
        payload={"emoji_type": "OK"} if kind == "add_reaction" else {},
    )
    client = FeishuChannelClient(AmbiguousSdkChannel(), app_id="cli_a")

    outcome = asyncio.run(
        _reviewed_action_send(
            _sender(store, client, send_mode="auto"), action
        )
    )

    assert outcome.status == "result_unknown"
    assert store.get_feishu_message_action(action.id).status == "result_unknown"


def test_verified_recall_requeue_rotates_review_generation_and_stale_hash(
    tmp_path,
):
    store, task, attempt_id, _ = _seed(tmp_path)
    action = _action(
        store,
        task,
        attempt_id,
        kind="recall_message",
        key="recall:fresh-review-generation",
        target="om_bot_1",
    )
    stale_hash = action.approval_hash
    store.approve_feishu_message_action(
        action.id,
        app_id="cli_a",
        approved_by="operator",
        expected_approval_hash=stale_hash,
    )
    claimed = store.claim_feishu_message_action(
        action.id,
        app_id="cli_a",
        kinds=("recall_message",),
        send_mode="auto",
    )
    store.begin_feishu_message_action_mutation(
        action.id,
        app_id="cli_a",
        lease_token=claimed.lease_token,
    )
    store.transition_feishu_message_action(
        action.id,
        from_statuses=("sending",),
        to_status="result_unknown",
        app_id="cli_a",
        expected_lease_token=claimed.lease_token,
        error_code="unknown",
        error="recall_result_unknown",
    )
    failed = store.reconcile_feishu_message_action_unknown(
        action.id,
        app_id="cli_a",
        outcome="not_applied",
        verified_by="operator",
        evidence_kind="message_lookup",
    )
    retry = store.requeue_feishu_message_action_after_verification(
        action.id,
        app_id="cli_a",
        verified_by="operator",
        evidence_kind="admin_audit",
    )

    assert failed.error_code == "verified_not_applied"
    assert retry.review_generation == action.review_generation + 1
    assert retry.approval_hash != stale_hash
    assert retry.approved_at == ""
    assert store.claim_feishu_message_action(
        action.id,
        app_id="cli_a",
        kinds=("recall_message",),
        send_mode="auto",
    ) is None
    with pytest.raises(ValueError, match="approval hash changed"):
        store.approve_feishu_message_action(
            action.id,
            app_id="cli_a",
            approved_by="operator",
            expected_approval_hash=stale_hash,
        )


def test_same_chat_fifo_and_pre_mutation_stale_recovery_are_retryable(tmp_path):
    store, task, attempt_id, _ = _seed(tmp_path)
    first = _action(
        store,
        task,
        attempt_id,
        kind="add_reaction",
        key="reaction:1",
        target="om_trigger",
        payload={"emoji_type": "OK"},
    )
    second = _action(
        store,
        task,
        attempt_id,
        kind="handoff_notify",
        key="handoff:2",
        open_id="ou_owner",
        payload={"text": "take over"},
    )
    claimed = store.claim_feishu_message_actions(
        10,
        app_id="cli_a",
        kinds=("add_reaction", "handoff_notify"),
        send_mode="auto",
        now="2026-07-22T10:00:00+00:00",
    )
    assert [row.id for row in claimed] == [first.id]
    with sqlite3.connect(store.path) as db:
        db.execute(
            "update feishu_message_actions set locked_at=? where id=?",
            ("2026-07-22T09:50:00+00:00", first.id),
        )
    assert recover_orphaned_message_actions(
        store,
        app_id="cli_a",
        max_age_seconds=60,
        now=__import__("datetime").datetime.fromisoformat(
            "2026-07-22T10:00:00+00:00"
        ),
    ) == 1
    recovered = store.get_feishu_message_action(first.id)
    assert recovered.status == "retry"
    assert recovered.mutation_started_at == ""
    assert store.claim_feishu_message_action(
        second.id,
        app_id="cli_a",
        kinds=("handoff_notify",),
        send_mode="auto",
    ) is None


def test_new_same_root_trigger_at_action_fence_prevents_sdk_call(tmp_path):
    store, task, attempt_id, _ = _seed(tmp_path)
    action = _action(
        store,
        task,
        attempt_id,
        kind="add_reaction",
        key="reaction:fence-race",
        target="om_trigger",
        payload={"emoji_type": "OK"},
    )
    claimed = store.claim_feishu_message_action(
        action.id,
        app_id="cli_a",
        kinds=("add_reaction",),
        send_mode="auto",
    )
    client = FakeActionClient()
    sender = _sender(store, client, send_mode="auto")
    fence_started = threading.Event()
    release_fence = threading.Event()
    real_begin = store.begin_feishu_message_action_mutation

    def blocking_begin(*args, **kwargs):
        fence_started.set()
        assert release_fence.wait(timeout=2)
        return real_begin(*args, **kwargs)

    store.begin_feishu_message_action_mutation = blocking_begin
    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(
            lambda: asyncio.run(sender.send_claimed(claimed))
        )
        assert fence_started.wait(timeout=2)
        _record_later_trigger(store)
        rejected = store.get_feishu_message_action(action.id)
        assert rejected.status == "rejected"
        assert rejected.error_code == "superseded"
        assert rejected.mutation_started_at == ""
        release_fence.set()
        outcome = future.result(timeout=2)

    assert outcome.status == "rejected"
    assert outcome.error_code == "superseded"
    assert client.calls == []
    assert {
        event.event_type
        for event in store.list_feishu_audit_events(
            entity_type="message_action", entity_id=action.id
        )
    } >= {"claimed", "trigger_superseded"}


def test_unattached_eligible_event_supersedes_before_action_fence(tmp_path):
    store, task, attempt_id, _ = _seed(tmp_path)
    action = _action(
        store,
        task,
        attempt_id,
        kind="add_reaction",
        key="reaction:unattached-newer",
        target="om_trigger",
        payload={"emoji_type": "OK"},
    )
    claimed = store.claim_feishu_message_action(
        action.id,
        app_id="cli_a",
        kinds=("add_reaction",),
        send_mode="auto",
    )
    newer = _record_later_trigger(store, enqueue_eligible=False)
    assert newer.reply_task_id == 0

    assert store.begin_feishu_message_action_mutation(
        action.id,
        app_id="cli_a",
        lease_token=claimed.lease_token,
    ) is None
    rejected = store.get_feishu_message_action(action.id)
    assert rejected.status == "rejected"
    assert rejected.error_code == "superseded"


def test_approved_recall_survives_newer_same_root_trigger_and_mutates(tmp_path):
    store, task, attempt_id, _ = _seed(tmp_path)
    recall = _action(
        store,
        task,
        attempt_id,
        kind="recall_message",
        key="recall:newer-trigger-cleanup",
        target="om_bot_1",
    )
    store.approve_feishu_message_action(
        recall.id,
        app_id="cli_a",
        approved_by="operator",
        expected_approval_hash=recall.approval_hash,
    )

    _record_later_trigger(store)

    queued = store.get_feishu_message_action(recall.id)
    assert queued.status == "ready"
    assert queued.approved_at
    client = FakeActionClient()
    outcome = asyncio.run(
        _reviewed_action_send(
            _sender(store, client, send_mode="auto"), recall
        )
    )
    assert outcome.status == "sent"
    assert client.calls == [("recall", "cli_a", "om_bot_1")]


def test_new_trigger_after_action_fence_cannot_cancel_or_replay(tmp_path):
    store, task, attempt_id, _ = _seed(tmp_path)
    action = _action(
        store,
        task,
        attempt_id,
        kind="add_reaction",
        key="reaction:fenced",
        target="om_trigger",
        payload={"emoji_type": "OK"},
    )
    claimed = store.claim_feishu_message_action(
        action.id,
        app_id="cli_a",
        kinds=("add_reaction",),
        send_mode="auto",
    )
    fenced = store.begin_feishu_message_action_mutation(
        action.id,
        app_id="cli_a",
        lease_token=claimed.lease_token,
    )
    assert fenced is not None and fenced.mutation_started_at

    _record_later_trigger(store)

    still_sending = store.get_feishu_message_action(action.id)
    assert still_sending.status == "sending"
    assert still_sending.lease_token == claimed.lease_token
    assert still_sending.mutation_started_at == fenced.mutation_started_at

    client = FakeActionClient()
    outcome = asyncio.run(
        _sender(store, client, send_mode="auto").send_claimed(claimed)
    )

    saved = store.get_feishu_message_action(action.id)
    assert outcome.status == "result_unknown"
    assert outcome.error == "remote_mutation_fence_already_started"
    assert saved.status == "result_unknown"
    assert saved.mutation_started_at == fenced.mutation_started_at
    assert client.calls == []


@pytest.mark.parametrize(
    ("app_id", "reference_root"),
    (("cli_a", "om_other_root"), ("cli_other", "om_trigger")),
)
def test_action_fence_isolated_by_reference_root_and_app(
    tmp_path, app_id, reference_root
):
    store, task, attempt_id, _ = _seed(tmp_path)
    action = _action(
        store,
        task,
        attempt_id,
        kind="add_reaction",
        key="reaction:isolated-fence",
        target="om_trigger",
        payload={"emoji_type": "OK"},
    )
    claimed = store.claim_feishu_message_action(
        action.id,
        app_id="cli_a",
        kinds=("add_reaction",),
        send_mode="auto",
    )
    _record_later_trigger(
        store,
        app_id=app_id,
        message_id=f"om_{app_id}_{reference_root}",
        reference_root=reference_root,
    )
    client = FakeActionClient()

    outcome = asyncio.run(
        _sender(store, client, send_mode="auto").send_claimed(claimed)
    )

    saved = store.get_feishu_message_action(action.id)
    assert outcome.status == "sent"
    assert saved.status == "sent"
    assert saved.mutation_started_at
    assert len(client.calls) == 1


def test_stale_fenced_action_is_result_unknown_and_never_retried(tmp_path):
    store, task, attempt_id, _ = _seed(tmp_path)
    action = _action(
        store,
        task,
        attempt_id,
        kind="add_reaction",
        key="reaction:stale-fenced",
        target="om_trigger",
        payload={"emoji_type": "OK"},
    )
    claimed = store.claim_feishu_message_action(
        action.id,
        app_id="cli_a",
        kinds=("add_reaction",),
        send_mode="auto",
        now="2026-07-22T10:00:00+00:00",
    )
    fenced = store.begin_feishu_message_action_mutation(
        action.id,
        app_id="cli_a",
        lease_token=claimed.lease_token,
        now="2026-07-22T10:00:01+00:00",
    )
    with sqlite3.connect(store.path) as db:
        db.execute(
            "update feishu_message_actions set locked_at=? where id=?",
            ("2026-07-22T09:50:00+00:00", action.id),
        )

    assert recover_orphaned_message_actions(
        store,
        app_id="cli_a",
        max_age_seconds=60,
        now=__import__("datetime").datetime.fromisoformat(
            "2026-07-22T10:00:00+00:00"
        ),
    ) == 1

    saved = store.get_feishu_message_action(action.id)
    assert saved.status == "result_unknown"
    assert saved.mutation_started_at == fenced.mutation_started_at
    assert store.claim_feishu_message_action(
        action.id,
        app_id="cli_a",
        kinds=("add_reaction",),
        send_mode="auto",
    ) is None


def test_legacy_sending_action_migration_backfills_fence(tmp_path):
    store, task, attempt_id, _ = _seed(tmp_path)
    action = _action(
        store,
        task,
        attempt_id,
        kind="add_reaction",
        key="reaction:legacy-sending",
        target="om_trigger",
        payload={"emoji_type": "OK"},
    )
    store.claim_feishu_message_action(
        action.id,
        app_id="cli_a",
        kinds=("add_reaction",),
        send_mode="auto",
    )
    with sqlite3.connect(store.path) as db:
        db.execute(
            "alter table feishu_message_actions "
            "drop column mutation_started_at"
        )

    store._initialize()

    migrated = store.get_feishu_message_action(action.id)
    assert migrated.status == "sending"
    assert migrated.mutation_started_at
    with sqlite3.connect(store.path) as db:
        db.execute(
            "update feishu_message_actions "
            "set locked_at='2026-07-22T09:50:00+00:00' where id=?",
            (action.id,),
        )
    assert recover_orphaned_message_actions(
        store,
        app_id="cli_a",
        max_age_seconds=60,
        now=__import__("datetime").datetime.fromisoformat(
            "2026-07-22T10:00:00+00:00"
        ),
    ) == 1
    assert store.get_feishu_message_action(action.id).status == "result_unknown"


def test_event_retention_waits_for_ready_action_terminal_state(tmp_path):
    store, task, attempt_id, _ = _seed(tmp_path)
    action = _action(
        store,
        task,
        attempt_id,
        kind="add_reaction",
        key="reaction:retention-ready",
        target="om_trigger",
        payload={"emoji_type": "OK"},
    )
    store.complete_reply_task(task.id)
    with sqlite3.connect(store.path) as db:
        db.execute(
            "update feishu_events set created_at='2026-01-01 00:00:00' "
            "where reply_task_id=?",
            (task.id,),
        )

    assert store.purge_feishu_events_before(
        "2026-07-01T00:00:00+00:00"
    ) == 0
    store.reject_feishu_message_action(action.id, app_id="cli_a")
    assert store.purge_feishu_events_before(
        "2026-07-01T00:00:00+00:00"
    ) == 1


def test_recall_remains_claimable_after_terminal_owner_event_is_purged(
    tmp_path,
):
    store, task, attempt_id, _ = _seed(tmp_path)
    store.complete_reply_task(task.id)
    with sqlite3.connect(store.path) as db:
        db.execute(
            "update feishu_events set created_at='2026-01-01 00:00:00' "
            "where reply_task_id=?",
            (task.id,),
        )
    assert store.purge_feishu_events_before(
        "2026-07-01T00:00:00+00:00"
    ) == 1

    recall = _action(
        store,
        task,
        attempt_id,
        kind="recall_message",
        key="recall:after-event-purge",
        target="om_bot_1",
    )
    store.approve_feishu_message_action(
        recall.id,
        app_id="cli_a",
        approved_by="owner",
        expected_approval_hash=recall.approval_hash,
    )
    claimed = store.claim_feishu_message_action(
        recall.id,
        app_id="cli_a",
        kinds=("recall_message",),
        send_mode="confirm",
    )
    assert claimed is not None
    fenced = store.begin_feishu_message_action_mutation(
        recall.id,
        app_id="cli_a",
        lease_token=claimed.lease_token,
    )
    assert fenced is not None and fenced.mutation_started_at

    store.transition_feishu_message_action(
        recall.id,
        from_statuses=("sending",),
        to_status="sent",
        app_id="cli_a",
        expected_lease_token=claimed.lease_token,
    )
    reaction = _action(
        store,
        task,
        attempt_id,
        kind="add_reaction",
        key="reaction:after-event-purge",
        target="om_trigger",
        payload={"emoji_type": "OK"},
    )
    claimed_reaction = store.claim_feishu_message_action(
        reaction.id,
        app_id="cli_a",
        kinds=("add_reaction",),
        send_mode="auto",
    )
    assert claimed_reaction is not None
    with pytest.raises(ValueError, match="trigger event is unavailable"):
        store.begin_feishu_message_action_mutation(
            reaction.id,
            app_id="cli_a",
            lease_token=claimed_reaction.lease_token,
        )


def test_event_retention_waits_for_unknown_action_reconciliation(tmp_path):
    store, task, attempt_id, _ = _seed(tmp_path)
    action = _action(
        store,
        task,
        attempt_id,
        kind="add_reaction",
        key="reaction:retention-unknown",
        target="om_trigger",
        payload={"emoji_type": "OK"},
    )
    claimed = store.claim_feishu_message_action(
        action.id,
        app_id="cli_a",
        kinds=("add_reaction",),
        send_mode="auto",
    )
    store.begin_feishu_message_action_mutation(
        action.id,
        app_id="cli_a",
        lease_token=claimed.lease_token,
    )
    store.transition_feishu_message_action(
        action.id,
        from_statuses=("sending",),
        to_status="result_unknown",
        app_id="cli_a",
        expected_lease_token=claimed.lease_token,
        error_code="unknown",
        error="test_unknown",
    )
    store.complete_reply_task(task.id)
    with sqlite3.connect(store.path) as db:
        db.execute(
            "update feishu_events set created_at='2026-01-01 00:00:00' "
            "where reply_task_id=?",
            (task.id,),
        )

    assert store.purge_feishu_events_before(
        "2026-07-01T00:00:00+00:00"
    ) == 0
    failed = store.reconcile_feishu_message_action_unknown(
        action.id,
        app_id="cli_a",
        outcome="not_applied",
        verified_by="operator",
        evidence_kind="message_lookup",
    )
    assert failed.error_code == "verified_not_applied"
    assert store.purge_feishu_events_before(
        "2026-07-01T00:00:00+00:00"
    ) == 0
    retry = store.requeue_feishu_message_action_after_verification(
        action.id,
        app_id="cli_a",
        verified_by="operator",
        evidence_kind="admin_audit",
    )
    assert retry.review_generation == action.review_generation + 1
    assert store.purge_feishu_events_before(
        "2026-07-01T00:00:00+00:00"
    ) == 0
    store.reject_feishu_message_action(action.id, app_id="cli_a")
    assert store.purge_feishu_events_before(
        "2026-07-01T00:00:00+00:00"
    ) == 1


def test_delivery_unknown_blocks_same_chat_actions(tmp_path):
    store, task, attempt_id, _ = _seed(tmp_path)
    delivery = _pending_delivery(store, number=2)
    claimed_delivery = store.claim_feishu_delivery(
        delivery.id, app_id="cli_a"
    )
    store.transition_feishu_delivery(
        delivery.id,
        from_statuses=("sending",),
        to_status="send_unknown",
        app_id="cli_a",
        expected_lease_token=claimed_delivery.lease_token,
        error_code="send_timeout",
        error="bounded_timeout",
    )
    action = _action(
        store,
        task,
        attempt_id,
        kind="add_reaction",
        key="reaction:blocked-by-delivery",
        target="om_trigger",
        payload={"emoji_type": "OK"},
    )

    assert store.claim_feishu_message_action(
        action.id,
        app_id="cli_a",
        kinds=("add_reaction",),
        send_mode="auto",
    ) is None


def test_action_unknown_blocks_same_chat_specific_and_batch_delivery(tmp_path):
    store, task, attempt_id, _ = _seed(tmp_path)
    action = _action(
        store,
        task,
        attempt_id,
        kind="add_reaction",
        key="reaction:blocks-delivery",
        target="om_trigger",
        payload={"emoji_type": "OK"},
    )
    claimed_action = store.claim_feishu_message_action(
        action.id,
        app_id="cli_a",
        kinds=("add_reaction",),
        send_mode="auto",
    )
    store.transition_feishu_message_action(
        action.id,
        from_statuses=("sending",),
        to_status="result_unknown",
        app_id="cli_a",
        expected_lease_token=claimed_action.lease_token,
        error_code="send_timeout",
        error="bounded_timeout",
    )
    delivery = _pending_delivery(store, number=3)

    assert store.claim_feishu_delivery(
        delivery.id, app_id="cli_a"
    ) is None
    assert store.claim_feishu_deliveries(10, app_id="cli_a") == []


def test_action_identity_and_approval_hash_are_immutable(tmp_path):
    store, task, attempt_id, _ = _seed(tmp_path)
    action = _action(
        store,
        task,
        attempt_id,
        kind="add_reaction",
        key="reaction:immutable",
        target="om_trigger",
        payload={"emoji_type": "OK"},
    )
    with sqlite3.connect(store.path) as db:
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            db.execute(
                "update feishu_message_actions set approval_hash=? where id=?",
                ("0" * 64, action.id),
            )


def test_finalize_action_specs_roll_back_with_task_on_invalid_target(tmp_path):
    store = AutoReplyStore(tmp_path / "atomic.sqlite3")
    event = store.record_feishu_event(
        FeishuInboundMessage(
            event_id="evt_atomic",
            app_id="cli_a",
            message_id="om_atomic",
            chat_id="oc_a",
            chat_type="group",
            sender_open_id="ou_sender",
            mentioned_bot=True,
            body_text="help",
            event_create_time="2026-07-22T03:20:00+00:00",
            received_at="2026-07-22T03:20:01+00:00",
        ),
        eligibility_status="eligible",
        store_body=True,
    )
    task = store.claim_reply_tasks(1, channel="feishu")[0]

    with pytest.raises(PermissionError, match="allowlisted"):
        store.finalize_feishu_reply_task(
            event.reply_task_id,
            app_id="cli_a",
            lease_token=task.lease_token,
            action="handoff_to_human",
            sensitivity_kind="general",
            task_status="done",
            send_status="skipped",
            message_action_specs=[
                {
                    "action_key": "handoff:0",
                    "kind": "handoff_notify",
                    "target_open_id": "ou_intruder",
                    "payload": {"text": "take over"},
                }
            ],
            handoff_target_allowlist=("ou_owner",),
        )

    persisted_task = next(
        row
        for row in store.list_reply_tasks(channel="feishu")
        if row.id == event.reply_task_id
    )
    assert persisted_task.status == "processing"
    assert store.list_reply_attempts() == []
    assert store.list_feishu_message_actions() == []
