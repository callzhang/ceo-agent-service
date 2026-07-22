import asyncio
import importlib.metadata
import sqlite3

import pytest

import app.store as store_module
from app.feishu import delivery as delivery_module
from app.feishu.client import (
    FeishuChannelClient,
    FeishuMessageState,
    FeishuSendResult,
)
from app.feishu.delivery import (
    FeishuDeliverySender,
    delivery_idempotency_key,
    recover_orphaned_sending,
)
from app.feishu.models import FeishuInboundMessage
from app.feishu.payloads import delivery_chunk_idempotency_key
from app.store import AutoReplyStore
from tests.feishu.fakes import FakeDeliveryClient


def _seed(tmp_path, *, reply_text="收到", reply_format="text"):
    store = AutoReplyStore(tmp_path / "feishu.sqlite3")
    event = store.record_feishu_event(
        FeishuInboundMessage(
            event_id="evt_1",
            app_id="cli_test",
            message_id="om_1",
            chat_id="oc_1",
            chat_type="group",
            chat_title="Group",
            sender_open_id="ou_1",
            sender_name="Alex",
            message_type="text",
            mentioned_bot=True,
            body_text="hi",
            event_create_time="2026-07-22T03:20:00+00:00",
            received_at="2026-07-22T03:20:01+00:00",
        ),
        eligibility_status="eligible",
        store_body=True,
    )
    task = next(
        row
        for row in store.list_reply_tasks(channel="feishu")
        if row.id == event.reply_task_id
    )
    attempt_id = store.record_reply_attempt(
        conversation_id=task.conversation_id,
        conversation_title=task.conversation_title,
        trigger_message_id=event.message_id,
        trigger_sender=event.sender_name,
        trigger_text=event.body_text,
        action="send_reply",
        sensitivity_kind="general",
        draft_reply_text=reply_text,
        send_status="pending",
        channel="feishu",
    )
    delivery = store.create_feishu_delivery(
        reply_task_id=event.reply_task_id,
        attempt_id=attempt_id,
        app_id="cli_test",
        chat_id="oc_1",
        reply_to_message_id="om_1",
        reply_in_thread=False,
        reply_text=reply_text,
        reply_format=reply_format,
        idempotency_key=delivery_idempotency_key(
            app_id="cli_test",
            reply_task_id=event.reply_task_id,
            trigger_message_id="om_1",
        ),
    )
    return store, delivery


def test_direct_store_rejects_multi_chunk_post(tmp_path):
    with pytest.raises(ValueError, match="must use text format"):
        _seed(tmp_path, reply_text="x" * 3501, reply_format="post")


def _sender(store, client, **kwargs):
    return FeishuDeliverySender(
        store,
        client,
        sender_enabled=True,
        live_send_allowed=True,
        **kwargs,
    )


def _reviewed_send(sender, delivery, *, approved_by="test-reviewer"):
    return sender.approve_and_send(
        delivery.id,
        expected_approval_hash=delivery.approval_hash,
        approved_by=approved_by,
    )


def _record_later_trigger(
    store,
    *,
    app_id="cli_test",
    chat_id="oc_1",
    message_id="om_2",
    reference_root="om_1",
    enqueue_eligible=True,
):
    return store.record_feishu_event(
        FeishuInboundMessage(
            event_id=f"evt_{app_id}_{message_id}",
            app_id=app_id,
            message_id=message_id,
            chat_id=chat_id,
            chat_type="group",
            chat_title="Group",
            root_message_id=reference_root,
            sender_open_id="ou_2",
            sender_name="Blair",
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


def test_confirm_mode_does_not_claim_or_send(tmp_path):
    store, delivery = _seed(tmp_path)
    client = FakeDeliveryClient()
    sender = _sender(store, client, send_mode="confirm")
    assert asyncio.run(sender.process_once()) == 0
    assert client.deliveries == []
    assert store.get_feishu_delivery(delivery.id).status == "ready_to_send"


def test_confirm_mode_claims_only_durably_approved_delivery(tmp_path):
    store, delivery = _seed(tmp_path)
    store.approve_feishu_delivery(
        delivery.id,
        app_id="cli_test",
        approved_by="cli-operator",
        expected_approval_hash=delivery.approval_hash,
    )
    client = FakeDeliveryClient()
    sender = _sender(store, client, send_mode="confirm")

    assert asyncio.run(sender.process_once()) == 1
    assert store.get_feishu_delivery(delivery.id).status == "sent"
    assert [row.id for row in client.deliveries] == [delivery.id]


def test_closed_global_gate_does_not_claim_even_in_auto(tmp_path):
    store, delivery = _seed(tmp_path)
    client = FakeDeliveryClient()
    sender = FeishuDeliverySender(
        store,
        client,
        sender_enabled=True,
        live_send_allowed=False,
        send_mode="auto",
    )
    assert asyncio.run(sender.process_once()) == 0
    assert store.get_feishu_delivery(delivery.id).status == "ready_to_send"


def test_explicit_approval_sends_and_records_message_id(tmp_path):
    store, delivery = _seed(tmp_path)
    client = FakeDeliveryClient(
        FeishuSendResult(True, message_id="om_reply", request_log_id="log-1")
    )
    outcome = asyncio.run(
        _reviewed_send(_sender(store, client, send_mode="confirm"), delivery)
    )
    saved = store.get_feishu_delivery(delivery.id)
    assert outcome.status == "sent"
    assert saved.status == "sent" and saved.feishu_message_id == "om_reply"
    assert saved.mutation_started_at
    assert saved.idempotency_key == delivery.idempotency_key
    assert saved.approved_at
    assert saved.approved_by == "test-reviewer"


def test_client_app_mismatch_fails_before_claim_or_send(tmp_path):
    store, delivery = _seed(tmp_path)
    client = FakeDeliveryClient(app_id="cli_other")
    sender = _sender(store, client, send_mode="confirm")

    with pytest.raises(PermissionError, match="App ID"):
        asyncio.run(_reviewed_send(sender, delivery))

    saved = store.get_feishu_delivery(delivery.id)
    assert saved.status == "ready_to_send"
    assert saved.approved_at == ""
    assert client.deliveries == []


def test_legacy_zero_attempt_delivery_is_never_sent(tmp_path):
    store, delivery = _seed(tmp_path)
    with sqlite3.connect(store.path) as db:
        db.execute("pragma foreign_keys=off")
        db.execute("drop trigger feishu_deliveries_identity_immutable")
        db.execute(
            "update feishu_deliveries set attempt_id=0 where id=?",
            (delivery.id,),
        )
    client = FakeDeliveryClient()
    sender = _sender(store, client, send_mode="auto")

    assert asyncio.run(sender.process_once()) == 0

    assert client.deliveries == []
    saved = store.get_feishu_delivery(delivery.id)
    assert saved.status == "failed"
    assert saved.error_code == "legacy_identity_unverifiable"
    assert {
        row.event_type
        for row in store.list_feishu_audit_events(
            entity_type="delivery", entity_id=delivery.id
        )
    } >= {"created", "invalid_binding_quarantined"}


@pytest.mark.parametrize(
    ("code", "expected"),
    [
        ("rate_limited", "retry"),
        ("not_connected", "retry"),
        ("permission_denied", "failed"),
        ("target_revoked", "failed"),
        ("format_error", "failed"),
        ("send_timeout", "send_unknown"),
        ("unknown", "send_unknown"),
    ],
)
def test_sdk_error_classification_is_fail_closed(tmp_path, code, expected):
    store, delivery = _seed(tmp_path)
    client = FakeDeliveryClient(FeishuSendResult(False, error_code=code))
    outcome = asyncio.run(
        _reviewed_send(_sender(store, client, send_mode="confirm"), delivery)
    )
    saved = store.get_feishu_delivery(delivery.id)
    assert outcome.status == expected
    assert saved.status == expected
    assert saved.idempotency_key == delivery.idempotency_key
    if expected == "retry":
        assert saved.available_at


def test_raised_timeout_is_send_unknown_not_blind_retry(tmp_path):
    store, delivery = _seed(tmp_path)
    client = FakeDeliveryClient(error=TimeoutError("may contain request details"))
    outcome = asyncio.run(
        _reviewed_send(_sender(store, client, send_mode="confirm"), delivery)
    )
    assert outcome.status == "send_unknown"
    assert "request details" not in store.get_feishu_delivery(delivery.id).error


def test_sender_enforces_bounded_network_timeout(tmp_path):
    store, delivery = _seed(tmp_path)

    class SlowClient(FakeDeliveryClient):
        async def send_reply(self, delivery):
            self.deliveries.append(delivery)
            await asyncio.sleep(1)
            return self.result

    client = SlowClient()
    sender = _sender(
        store,
        client,
        send_mode="confirm",
        send_timeout_seconds=0.01,
        send_lease_stale_seconds=1,
    )

    outcome = asyncio.run(_reviewed_send(sender, delivery))

    assert outcome.status == "send_unknown"
    assert store.get_feishu_delivery(delivery.id).status == "send_unknown"
    assert len(client.deliveries) == 1


def test_unknown_result_becomes_send_unknown_on_first_attempt(tmp_path):
    store, delivery = _seed(tmp_path)
    client = FakeDeliveryClient(FeishuSendResult(False, error_code="unknown"))
    outcome = asyncio.run(
        _reviewed_send(
            _sender(store, client, send_mode="confirm", max_attempts=1),
            delivery,
        )
    )
    assert outcome.status == "send_unknown"
    assert store.get_feishu_delivery(delivery.id).mutation_started_at


@pytest.mark.parametrize("code", ["rate_limited", "not_connected"])
def test_confirmed_non_delivery_is_failed_after_retry_budget(tmp_path, code):
    store, delivery = _seed(tmp_path)
    client = FakeDeliveryClient(FeishuSendResult(False, error_code=code))

    outcome = asyncio.run(
        _reviewed_send(
            _sender(store, client, send_mode="confirm", max_attempts=1),
            delivery,
        )
    )

    assert outcome.status == "failed"
    assert store.get_feishu_delivery(delivery.id).status == "failed"


def test_local_rate_limit_defers_with_same_uuid(tmp_path):
    store, delivery = _seed(tmp_path)
    sender = _sender(
        store,
        FakeDeliveryClient(),
        send_mode="confirm",
        max_sends_per_minute=1,
        monotonic_clock=lambda: 100.0,
    )
    sender._sent_times.append(100.0)
    outcome = asyncio.run(_reviewed_send(sender, delivery))
    saved = store.get_feishu_delivery(delivery.id)
    assert outcome.status == "retry"
    assert saved.idempotency_key == delivery.idempotency_key


def test_reject_never_calls_client(tmp_path):
    store, delivery = _seed(tmp_path)
    client = FakeDeliveryClient()
    sender = _sender(store, client, send_mode="confirm")
    sender.reject(delivery.id)
    assert store.get_feishu_delivery(delivery.id).status == "rejected"
    assert client.deliveries == []


def test_stale_pre_mutation_delivery_retries_with_fresh_fence(tmp_path):
    store, delivery = _seed(tmp_path)
    claimed = store.claim_feishu_delivery(delivery.id)
    assert recover_orphaned_sending(store) == 0
    with sqlite3.connect(store.path) as db:
        db.execute(
            "update feishu_deliveries set locked_at=datetime('now', '-6 minutes') where id=?",
            (delivery.id,),
        )
    assert recover_orphaned_sending(store) == 1
    recovered = store.get_feishu_delivery(delivery.id)
    assert recovered.status == "retry"
    assert recovered.mutation_started_at == ""

    client = FakeDeliveryClient()
    sender = _sender(store, client, send_mode="auto")
    with pytest.raises(ValueError, match="lease"):
        asyncio.run(sender.send_claimed(claimed))
    assert client.deliveries == []

    resumed = store.claim_feishu_delivery(delivery.id)
    outcome = asyncio.run(sender.send_claimed(resumed))
    assert outcome.status == "sent"
    assert len(client.deliveries) == 1
    assert store.get_feishu_delivery(delivery.id).mutation_started_at


def test_delivery_runtime_helper_cannot_self_source_approval_hash(tmp_path):
    store, delivery = _seed(tmp_path)
    client = FakeDeliveryClient()
    sender = _sender(store, client, send_mode="confirm")

    with pytest.raises(TypeError, match="expected_approval_hash"):
        sender.approve_and_send(delivery.id, approved_by="operator")
    with pytest.raises(ValueError, match="approval hash changed"):
        asyncio.run(
            sender.approve_and_send(
                delivery.id,
                expected_approval_hash="0" * 64,
                approved_by="operator",
            )
        )

    assert store.get_feishu_delivery(delivery.id).approved_at == ""
    assert client.deliveries == []


def test_stale_fenced_delivery_without_receipt_is_send_unknown(tmp_path):
    store, delivery = _seed(tmp_path)
    claimed = store.claim_feishu_delivery(delivery.id)
    fenced = store.begin_feishu_delivery_mutation(
        delivery.id,
        app_id=delivery.app_id,
        lease_token=claimed.lease_token,
    )
    with sqlite3.connect(store.path) as db:
        db.execute(
            "update feishu_deliveries "
            "set locked_at=datetime('now', '-6 minutes') where id=?",
            (delivery.id,),
        )

    assert recover_orphaned_sending(store) == 1

    saved = store.get_feishu_delivery(delivery.id)
    assert saved.status == "send_unknown"
    assert saved.mutation_started_at == fenced.mutation_started_at
    assert store.claim_feishu_delivery(delivery.id) is None
    verified = store.reconcile_feishu_delivery_unknown(
        delivery.id,
        app_id=delivery.app_id,
        outcome="sent",
        verified_by="operator",
        evidence_kind="message_lookup",
        expected_chunks=1,
        message_ids=("om_verified_after_crash",),
    )
    assert verified.status == "sent"
    assert store.get_feishu_delivery_receipt(
        app_id=delivery.app_id, message_id="om_verified_after_crash"
    ) is not None


def test_stale_fenced_delivery_verified_not_sent_can_requeue(tmp_path):
    store, delivery = _seed(tmp_path)
    claimed = store.claim_feishu_delivery(delivery.id)
    store.begin_feishu_delivery_mutation(
        delivery.id,
        app_id=delivery.app_id,
        lease_token=claimed.lease_token,
    )
    with sqlite3.connect(store.path) as db:
        db.execute(
            "update feishu_deliveries "
            "set locked_at=datetime('now', '-6 minutes') where id=?",
            (delivery.id,),
        )
    assert recover_orphaned_sending(store) == 1
    failed = store.reconcile_feishu_delivery_unknown(
        delivery.id,
        app_id=delivery.app_id,
        outcome="not_sent",
        verified_by="operator",
        evidence_kind="message_lookup",
    )
    retry = store.requeue_feishu_delivery_after_verification(
        delivery.id,
        app_id=delivery.app_id,
        verified_by="operator",
        evidence_kind="admin_audit",
    )
    assert failed.error_code == "verified_not_sent"
    assert retry.status == "retry"
    assert retry.review_generation == delivery.review_generation + 1


def test_retention_preserves_verified_not_sent_until_requeue_is_resolved(
    tmp_path,
):
    store, delivery = _seed(tmp_path)
    claimed = store.claim_feishu_delivery(delivery.id, app_id=delivery.app_id)
    store.transition_feishu_delivery(
        delivery.id,
        from_statuses=("sending",),
        to_status="send_unknown",
        app_id=delivery.app_id,
        expected_lease_token=claimed.lease_token,
        error_code="send_timeout",
        error="test uncertainty",
    )
    store.complete_reply_task(delivery.reply_task_id)
    with sqlite3.connect(store.path) as db:
        db.execute(
            "update feishu_events set created_at='2026-01-01 00:00:00' "
            "where reply_task_id=?",
            (delivery.reply_task_id,),
        )

    cutoff = "2026-07-01T00:00:00+00:00"
    assert store.purge_feishu_events_before(cutoff) == 0
    failed = store.reconcile_feishu_delivery_unknown(
        delivery.id,
        app_id=delivery.app_id,
        outcome="not_sent",
        verified_by="operator",
        evidence_kind="message_lookup",
    )
    assert failed.error_code == "verified_not_sent"
    assert store.purge_feishu_events_before(cutoff) == 0
    retry = store.requeue_feishu_delivery_after_verification(
        delivery.id,
        app_id=delivery.app_id,
        verified_by="operator",
        evidence_kind="admin_audit",
    )
    assert retry.status == "retry"
    assert store.purge_feishu_events_before(cutoff) == 0
    store.reject_feishu_delivery(delivery.id, app_id=delivery.app_id)
    assert store.purge_feishu_events_before(cutoff) == 1


def test_stale_partial_receipt_delivery_is_send_unknown(tmp_path):
    store, delivery = _seed(tmp_path, reply_text="x" * 6000)
    claimed = store.claim_feishu_delivery(delivery.id)
    store.record_feishu_delivery_chunk(
        delivery.id,
        app_id=delivery.app_id,
        lease_token=claimed.lease_token,
        ordinal=0,
        expected_chunks=delivery.expected_chunks,
        message_id="om_partial_before_crash",
    )
    with sqlite3.connect(store.path) as db:
        db.execute(
            "update feishu_deliveries "
            "set locked_at=datetime('now', '-6 minutes') where id=?",
            (delivery.id,),
        )

    assert recover_orphaned_sending(store) == 1
    saved = store.get_feishu_delivery(delivery.id)
    assert saved.status == "send_unknown"
    assert saved.mutation_started_at


def test_legacy_sending_migration_backfills_fence_before_recovery(tmp_path):
    store, delivery = _seed(tmp_path)
    store.claim_feishu_delivery(delivery.id)
    with sqlite3.connect(store.path) as db:
        db.execute(
            "alter table feishu_deliveries drop column mutation_started_at"
        )

    store._initialize()

    migrated = store.get_feishu_delivery(delivery.id)
    assert migrated.status == "sending"
    assert migrated.mutation_started_at
    with sqlite3.connect(store.path) as db:
        db.execute(
            "update feishu_deliveries "
            "set locked_at=datetime('now', '-6 minutes') where id=?",
            (delivery.id,),
        )
    assert recover_orphaned_sending(store) == 1
    assert store.get_feishu_delivery(delivery.id).status == "send_unknown"


def test_second_approval_cannot_send_already_sent_delivery(tmp_path):
    store, delivery = _seed(tmp_path)
    sender = _sender(store, FakeDeliveryClient(), send_mode="confirm")
    asyncio.run(_reviewed_send(sender, delivery))
    with pytest.raises(ValueError):
        asyncio.run(_reviewed_send(sender, delivery))


def test_absent_trigger_is_terminal_and_never_falls_back_to_new_message(tmp_path):
    store, delivery = _seed(tmp_path)
    client = FakeDeliveryClient(message_state="absent")

    outcome = asyncio.run(
        _reviewed_send(_sender(store, client, send_mode="confirm"), delivery)
    )

    assert outcome.status == "failed"
    assert outcome.error_code == "target_revoked"
    assert client.deliveries == []
    assert store.get_feishu_delivery(delivery.id).error_code == "target_revoked"


@pytest.mark.parametrize(("max_attempts", "expected"), [(2, "retry"), (1, "failed")])
def test_unknown_trigger_state_never_sends_and_respects_retry_budget(
    tmp_path, max_attempts, expected
):
    store, delivery = _seed(tmp_path)
    client = FakeDeliveryClient(message_state="unknown")

    outcome = asyncio.run(
        _reviewed_send(
            _sender(
                store,
                client,
                send_mode="confirm",
                max_attempts=max_attempts,
            ),
            delivery,
        )
    )

    assert outcome.status == expected
    assert outcome.error_code == "target_state_unknown"
    assert client.deliveries == []


def test_trigger_probe_timeout_is_safe_retry_without_send(tmp_path):
    store, delivery = _seed(tmp_path)

    class SlowProbeClient(FakeDeliveryClient):
        async def fetch_message_state(self, app_id, message_id):
            self.state_probes.append((app_id, message_id))
            await asyncio.sleep(1)

    client = SlowProbeClient()
    outcome = asyncio.run(
        _reviewed_send(
            _sender(
                store,
                client,
                send_mode="confirm",
                send_timeout_seconds=0.01,
                send_lease_stale_seconds=1,
            ),
            delivery,
        )
    )

    assert outcome.status == "retry"
    assert outcome.error_code == "target_state_unknown"
    assert client.deliveries == []


def test_new_same_root_trigger_during_probe_rejects_before_remote_send(
    tmp_path,
):
    store, delivery = _seed(tmp_path)
    probe_started = asyncio.Event()
    release_probe = asyncio.Event()

    class BlockingProbeClient(FakeDeliveryClient):
        async def fetch_message_state(self, app_id, message_id):
            self.state_probes.append((app_id, message_id))
            probe_started.set()
            await release_probe.wait()
            return FeishuMessageState("exists")

    client = BlockingProbeClient()
    sender = _sender(store, client, send_mode="confirm")

    async def race():
        send_task = asyncio.create_task(_reviewed_send(sender, delivery))
        await asyncio.wait_for(probe_started.wait(), timeout=1)
        _record_later_trigger(store)
        rejected = store.get_feishu_delivery(delivery.id)
        assert rejected.status == "rejected"
        assert rejected.error_code == "superseded"
        assert rejected.mutation_started_at == ""
        release_probe.set()
        return await asyncio.wait_for(send_task, timeout=1)

    outcome = asyncio.run(race())

    assert outcome.status == "rejected"
    assert outcome.error_code == "superseded"
    assert client.deliveries == []


def test_unattached_eligible_event_supersedes_before_delivery_fence(tmp_path):
    store, delivery = _seed(tmp_path)
    claimed = store.claim_feishu_delivery(delivery.id, app_id=delivery.app_id)
    newer = _record_later_trigger(store, enqueue_eligible=False)
    assert newer.reply_task_id == 0

    assert store.begin_feishu_delivery_mutation(
        delivery.id,
        app_id=delivery.app_id,
        lease_token=claimed.lease_token,
    ) is None
    rejected = store.get_feishu_delivery(delivery.id)
    assert rejected.status == "rejected"
    assert rejected.error_code == "superseded"
    assert {
        event.event_type
        for event in store.list_feishu_audit_events(
            entity_type="delivery", entity_id=delivery.id
        )
    } >= {"claimed", "trigger_superseded_at_send_fence"}


def test_new_trigger_after_mutation_fence_cannot_cancel_or_replay(tmp_path):
    store, delivery = _seed(tmp_path)
    claimed = store.claim_feishu_delivery(delivery.id)
    fenced = store.begin_feishu_delivery_mutation(
        delivery.id,
        app_id=delivery.app_id,
        lease_token=claimed.lease_token,
    )
    assert fenced is not None and fenced.mutation_started_at

    _record_later_trigger(store)

    still_sending = store.get_feishu_delivery(delivery.id)
    assert still_sending.status == "sending"
    assert still_sending.lease_token == claimed.lease_token
    assert still_sending.mutation_started_at == fenced.mutation_started_at

    client = FakeDeliveryClient()
    outcome = asyncio.run(
        _sender(store, client, send_mode="auto").send_claimed(claimed)
    )

    assert outcome.status == "send_unknown"
    assert outcome.error == "remote_mutation_fence_already_started"
    assert store.get_feishu_delivery(delivery.id).status == "send_unknown"
    assert client.deliveries == []
    assert client.chunk_calls == []


@pytest.mark.parametrize(
    ("app_id", "reference_root"),
    (("cli_test", "om_other_root"), ("cli_other", "om_1")),
)
def test_probe_race_different_root_or_app_does_not_block_send(
    tmp_path, app_id, reference_root
):
    store, delivery = _seed(tmp_path)
    probe_started = asyncio.Event()
    release_probe = asyncio.Event()

    class BlockingProbeClient(FakeDeliveryClient):
        async def fetch_message_state(self, runtime_app_id, message_id):
            self.state_probes.append((runtime_app_id, message_id))
            probe_started.set()
            await release_probe.wait()
            return FeishuMessageState("exists")

    client = BlockingProbeClient()
    sender = _sender(store, client, send_mode="confirm")

    async def race():
        send_task = asyncio.create_task(_reviewed_send(sender, delivery))
        await asyncio.wait_for(probe_started.wait(), timeout=1)
        _record_later_trigger(
            store,
            app_id=app_id,
            message_id=f"om_{app_id}_{reference_root}",
            reference_root=reference_root,
        )
        release_probe.set()
        return await asyncio.wait_for(send_task, timeout=1)

    outcome = asyncio.run(race())

    saved = store.get_feishu_delivery(delivery.id)
    assert outcome.status == "sent"
    assert saved.status == "sent"
    assert saved.mutation_started_at
    assert len(client.deliveries) == 1
    assert len(client.chunk_calls) == 1


def test_confirmed_partial_rate_limit_retries_only_the_suffix(tmp_path):
    store, delivery = _seed(tmp_path, reply_text="x" * 6000)

    class PartialClient(FakeDeliveryClient):
        async def send_reply(self, delivery):
            self.deliveries.append(delivery)
            if len(self.deliveries) == 1:
                return FeishuSendResult(
                    True,
                    message_id="om_partial_1",
                    request_log_id="log-chunk-1",
                )
            if len(self.deliveries) == 2:
                return FeishuSendResult(False, error_code="rate_limited")
            return FeishuSendResult(
                True,
                message_id="om_partial_2",
                request_log_id="log-chunk-2",
            )

    client = PartialClient()

    outcome = asyncio.run(
        _reviewed_send(_sender(store, client, send_mode="confirm"), delivery)
    )

    assert outcome.status == "retry"
    assert [
        receipt.message_id
        for receipt in store.list_feishu_delivery_receipts(delivery_id=delivery.id)
    ] == ["om_partial_1"]
    [receipt] = store.list_feishu_delivery_receipts(delivery_id=delivery.id)
    assert receipt.request_log_id == "log-chunk-1"
    assert [call["ordinal"] for call in client.chunk_calls] == [0, 1]

    resumed = store.claim_feishu_delivery(
        delivery.id, now="2099-01-01T00:00:00+00:00"
    )
    outcome = asyncio.run(
        _sender(store, client, send_mode="confirm").send_claimed(resumed)
    )

    assert outcome.status == "sent"
    assert [call["ordinal"] for call in client.chunk_calls] == [0, 1, 1]
    assert [
        receipt.message_id
        for receipt in store.list_feishu_delivery_receipts(
            delivery_id=delivery.id
        )
    ] == ["om_partial_1", "om_partial_2"]


@pytest.mark.parametrize("code", ["rate_limited", "not_connected"])
def test_confirmed_first_chunk_failure_is_safely_retryable(tmp_path, code):
    store, delivery = _seed(tmp_path, reply_text="x" * 3600)
    client = FakeDeliveryClient(FeishuSendResult(False, error_code=code))

    outcome = asyncio.run(
        _reviewed_send(_sender(store, client, send_mode="confirm"), delivery)
    )

    assert outcome.status == "retry"
    assert store.get_feishu_delivery(delivery.id).status == "retry"
    assert store.get_feishu_delivery(delivery.id).mutation_started_at == ""
    assert store.list_feishu_delivery_receipts(delivery_id=delivery.id) == []


def test_large_text_uses_stable_local_chunks_and_ordered_receipts(tmp_path):
    text = ("line-" + "x" * 90 + "\n") * 400
    assert len(text.encode("utf-8")) > 30 * 1024
    store, delivery = _seed(tmp_path, reply_text=text)

    class OrderedClient(FakeDeliveryClient):
        async def send_reply_chunk(self, delivery, **kwargs):
            self.chunk_calls.append(kwargs)
            ordinal = kwargs["ordinal"]
            return FeishuSendResult(
                True,
                message_id=f"om_chunk_{ordinal}",
                request_log_id=f"log-{ordinal}",
            )

    client = OrderedClient()
    outcome = asyncio.run(
        _reviewed_send(
            _sender(
                store,
                client,
                send_mode="confirm",
                max_sends_per_minute=100,
            ),
            delivery,
        )
    )

    assert outcome.status == "sent"
    assert "".join(call["text"] for call in client.chunk_calls) == text.strip()
    assert all(len(call["text"]) <= 3500 for call in client.chunk_calls)
    chunk_keys = [call["idempotency_key"] for call in client.chunk_calls]
    assert len(chunk_keys) == delivery.expected_chunks
    assert len(set(chunk_keys)) == delivery.expected_chunks
    assert all(len(key) <= 50 for key in chunk_keys)
    receipts = store.list_feishu_delivery_receipts(delivery_id=delivery.id)
    assert [row.ordinal for row in receipts] == list(
        range(delivery.expected_chunks)
    )
    assert [row.message_id for row in receipts] == [
        f"om_chunk_{index}" for index in range(delivery.expected_chunks)
    ]
    assert [row.request_log_id for row in receipts] == [
        f"log-{index}" for index in range(delivery.expected_chunks)
    ]


def test_unknown_reconciliation_accepts_only_a_strict_verified_prefix(tmp_path):
    store, delivery = _seed(tmp_path, reply_text="x" * 8000)
    claimed = store.claim_feishu_delivery(delivery.id)
    assert claimed is not None and claimed.expected_chunks == 3
    store.record_feishu_delivery_chunk(
        delivery.id,
        app_id=delivery.app_id,
        lease_token=claimed.lease_token,
        ordinal=0,
        expected_chunks=3,
        message_id="om_first",
    )
    store.mark_feishu_delivery_send_unknown(
        delivery.id,
        error_code="send_timeout",
        error="second chunk result unknown",
    )

    with pytest.raises(ValueError, match="expected chunk count"):
        store.reconcile_feishu_delivery_unknown(
            delivery.id,
            app_id=delivery.app_id,
            outcome="sent",
            verified_by="operator",
            evidence_kind="message_lookup",
            expected_chunks=2,
            message_ids=("om_first",),
        )
    with pytest.raises(ValueError, match="extend the durable receipt prefix"):
        store.reconcile_feishu_delivery_unknown(
            delivery.id,
            app_id=delivery.app_id,
            outcome="sent",
            verified_by="operator",
            evidence_kind="message_lookup",
            expected_chunks=3,
            message_ids=("om_wrong", "om_second"),
        )
    with pytest.raises(ValueError, match="extend the durable receipt prefix"):
        store.reconcile_feishu_delivery_unknown(
            delivery.id,
            app_id=delivery.app_id,
            outcome="sent",
            verified_by="operator",
            evidence_kind="message_lookup",
            expected_chunks=3,
            message_ids=("om_first",),
        )
    assert store.get_feishu_delivery(delivery.id).status == "send_unknown"

    retry = store.reconcile_feishu_delivery_unknown(
        delivery.id,
        app_id=delivery.app_id,
        outcome="sent",
        verified_by="operator",
        evidence_kind="message_lookup",
        expected_chunks=3,
        message_ids=("om_first", "om_second"),
    )
    assert retry.status == "retry"
    assert [
        row.message_id
        for row in store.list_feishu_delivery_receipts(delivery_id=delivery.id)
    ] == ["om_first", "om_second"]


def test_unknown_reconciliation_complete_verified_prefix_converges_sent(
    tmp_path,
):
    store, delivery = _seed(tmp_path, reply_text="x" * 6000)
    claimed = store.claim_feishu_delivery(delivery.id)
    store.record_feishu_delivery_chunk(
        delivery.id,
        app_id=delivery.app_id,
        lease_token=claimed.lease_token,
        ordinal=0,
        expected_chunks=2,
        message_id="om_complete_first",
    )
    store.mark_feishu_delivery_send_unknown(
        delivery.id,
        error_code="send_timeout",
        error="second chunk result unknown",
    )

    sent = store.reconcile_feishu_delivery_unknown(
        delivery.id,
        app_id=delivery.app_id,
        outcome="sent",
        verified_by="operator",
        evidence_kind="message_lookup",
        expected_chunks=2,
        message_ids=("om_complete_first", "om_complete_second"),
    )

    assert sent.status == "sent"
    assert [
        receipt.message_id
        for receipt in store.validate_feishu_delivery_receipt_prefix(
            delivery.id, app_id=delivery.app_id
        )
    ] == ["om_complete_first", "om_complete_second"]


def test_same_count_different_chunk_boundaries_quarantine_partial_resume(
    tmp_path, monkeypatch
):
    store, delivery = _seed(tmp_path, reply_text="x" * 8000)
    claimed = store.claim_feishu_delivery(delivery.id)
    store.record_feishu_delivery_chunk(
        delivery.id,
        app_id=delivery.app_id,
        lease_token=claimed.lease_token,
        ordinal=0,
        expected_chunks=3,
        message_id="om_boundary_first",
    )
    store.mark_feishu_delivery_send_unknown(
        delivery.id,
        error_code="send_timeout",
        error="second chunk result unknown",
    )
    retry = store.reconcile_feishu_delivery_unknown(
        delivery.id,
        app_id=delivery.app_id,
        outcome="sent",
        verified_by="operator",
        evidence_kind="message_lookup",
        expected_chunks=3,
        message_ids=("om_boundary_first", "om_boundary_second"),
    )
    assert retry.status == "retry"

    def changed_same_count_plan(payload):
        assert payload.text == "x" * 8000
        return (payload.text[:3000], payload.text[3000:6000], payload.text[6000:])

    monkeypatch.setattr(
        store_module, "split_reply_payload", changed_same_count_plan
    )
    assert store.claim_feishu_delivery(
        delivery.id, app_id=delivery.app_id
    ) is None
    quarantined = store.get_feishu_delivery(delivery.id)
    assert quarantined.status == "failed"
    assert quarantined.error_code == "legacy_identity_unverifiable"
    assert [
        receipt.message_id
        for receipt in store.list_feishu_delivery_receipts(
            delivery_id=delivery.id
        )
    ] == ["om_boundary_first", "om_boundary_second"]


def test_default_budget_sends_eleven_chunks_across_restart_without_replay(
    tmp_path,
):
    store, delivery = _seed(tmp_path, reply_text="x" * (3500 * 10 + 1))
    assert delivery.expected_chunks == 11

    class OrderedClient(FakeDeliveryClient):
        async def send_reply_chunk(self, delivery, **kwargs):
            self.chunk_calls.append(kwargs)
            ordinal = kwargs["ordinal"]
            return FeishuSendResult(
                True,
                message_id=f"om_restart_{ordinal}",
                request_log_id=f"log-restart-{ordinal}",
            )

    client = OrderedClient()
    first = asyncio.run(
        _reviewed_send(_sender(store, client, send_mode="confirm"), delivery)
    )

    assert first.status == "retry"
    assert [call["ordinal"] for call in client.chunk_calls] == list(range(10))
    assert len(
        store.list_feishu_delivery_receipts(delivery_id=delivery.id)
    ) == 10

    resumed = store.claim_feishu_delivery(
        delivery.id, now="2099-01-01T00:00:00+00:00"
    )
    second = asyncio.run(
        _sender(store, client, send_mode="confirm").send_claimed(resumed)
    )

    assert second.status == "sent"
    assert [call["ordinal"] for call in client.chunk_calls] == list(range(11))
    assert [
        receipt.message_id
        for receipt in store.list_feishu_delivery_receipts(
            delivery_id=delivery.id
        )
    ] == [f"om_restart_{ordinal}" for ordinal in range(11)]


def test_hundred_chunk_upper_bound_resumes_across_ten_default_windows(
    tmp_path, monkeypatch
):
    synthetic_text = "x" * 100

    def one_character_chunks(payload):
        assert payload.text == synthetic_text
        return tuple(payload.text)

    # Exercise the sender/store's declared 100-receipt upper bound without
    # weakening the production payload byte or SDK chunk-size contracts.
    monkeypatch.setattr(
        store_module, "split_reply_payload", one_character_chunks
    )
    monkeypatch.setattr(
        delivery_module, "split_reply_payload", one_character_chunks
    )
    store, delivery = _seed(tmp_path, reply_text=synthetic_text)
    assert delivery.expected_chunks == 100

    class OrderedClient(FakeDeliveryClient):
        async def send_reply_chunk(self, delivery, **kwargs):
            self.chunk_calls.append(kwargs)
            ordinal = kwargs["ordinal"]
            return FeishuSendResult(
                True,
                message_id=f"om_upper_{ordinal}",
                request_log_id=f"log-upper-{ordinal}",
            )

    current = [0.0]
    client = OrderedClient()
    sender = _sender(
        store,
        client,
        send_mode="confirm",
        monotonic_clock=lambda: current[0],
    )
    outcome = asyncio.run(_reviewed_send(sender, delivery))
    assert outcome.status == "retry"

    for window in range(1, 10):
        receipts = store.validate_feishu_delivery_receipt_prefix(
            delivery.id, app_id=delivery.app_id
        )
        assert [receipt.ordinal for receipt in receipts] == list(
            range(window * 10)
        )
        current[0] += 60
        claimed = store.claim_feishu_delivery(
            delivery.id, now="2099-01-01T00:00:00+00:00"
        )
        outcome = asyncio.run(sender.send_claimed(claimed))
        assert outcome.status == ("sent" if window == 9 else "retry")

    saved = store.get_feishu_delivery(delivery.id)
    assert saved.status == "sent"
    assert saved.attempts == 10
    assert [call["ordinal"] for call in client.chunk_calls] == list(range(100))
    assert [
        receipt.ordinal
        for receipt in store.validate_feishu_delivery_receipt_prefix(
            delivery.id, app_id=delivery.app_id
        )
    ] == list(range(100))


def test_definite_terminal_failure_after_prefix_preserves_receipts(tmp_path):
    store, delivery = _seed(tmp_path, reply_text="x" * 6000)

    class TerminalClient(FakeDeliveryClient):
        async def send_reply(self, delivery):
            self.deliveries.append(delivery)
            if len(self.deliveries) == 1:
                return FeishuSendResult(True, message_id="om_terminal_first")
            return FeishuSendResult(False, error_code="permission_denied")

    outcome = asyncio.run(
        _reviewed_send(
            _sender(store, TerminalClient(), send_mode="confirm"),
            delivery,
        )
    )
    saved = store.get_feishu_delivery(delivery.id)

    assert outcome.status == "failed"
    assert saved.status == "failed"
    assert saved.feishu_message_id == "om_terminal_first"
    assert [
        receipt.message_id
        for receipt in store.list_feishu_delivery_receipts(
            delivery_id=delivery.id
        )
    ] == ["om_terminal_first"]


def test_timeout_after_prefix_remains_send_unknown(tmp_path):
    store, delivery = _seed(tmp_path, reply_text="x" * 6000)

    class TimeoutClient(FakeDeliveryClient):
        async def send_reply(self, delivery):
            self.deliveries.append(delivery)
            if len(self.deliveries) == 1:
                return FeishuSendResult(True, message_id="om_timeout_first")
            raise TimeoutError("ambiguous second chunk")

    outcome = asyncio.run(
        _reviewed_send(
            _sender(store, TimeoutClient(), send_mode="confirm"),
            delivery,
        )
    )

    assert outcome.status == "send_unknown"
    assert store.get_feishu_delivery(delivery.id).status == "send_unknown"
    assert [
        receipt.message_id
        for receipt in store.list_feishu_delivery_receipts(
            delivery_id=delivery.id
        )
    ] == ["om_timeout_first"]


def test_unplanned_multi_id_result_cannot_be_reconciled_as_planned_send(
    tmp_path,
):
    store, delivery = _seed(tmp_path)
    result = FeishuSendResult(
        True, message_ids=("om_unplanned_1", "om_unplanned_2")
    )
    outcome = asyncio.run(
        _reviewed_send(
            _sender(store, FakeDeliveryClient(result), send_mode="auto"),
            delivery,
        )
    )

    assert outcome.status == "send_unknown"
    assert store.list_feishu_delivery_receipts(delivery_id=delivery.id) == []
    with pytest.raises(ValueError, match="unplanned Feishu wire chunks"):
        store.reconcile_feishu_delivery_unknown(
            delivery.id,
            app_id=delivery.app_id,
            outcome="sent",
            verified_by="operator",
            evidence_kind="message_lookup",
            expected_chunks=1,
            message_ids=("om_unplanned_1",),
        )
    assert store.get_feishu_delivery(delivery.id).status == "send_unknown"
    closed = store.reconcile_feishu_delivery_unknown(
        delivery.id,
        app_id=delivery.app_id,
        outcome="not_sent",
        verified_by="operator",
        evidence_kind="admin_audit",
    )
    assert closed.status == "failed"
    assert closed.error_code == "verified_unresumable_not_sent"
    with pytest.raises(ValueError, match="verification state"):
        store.requeue_feishu_delivery_after_verification(
            delivery.id,
            app_id=delivery.app_id,
            verified_by="operator",
            evidence_kind="admin_audit",
        )


def test_failed_result_id_is_unconfirmed_until_sent_or_not_sent_review(
    tmp_path,
):
    contradictory = FeishuSendResult(
        False,
        message_id="om_unconfirmed",
        request_log_id="log-contradictory",
        error_code="permission_denied",
    )
    sent_dir = tmp_path / "sent-review"
    sent_dir.mkdir()
    sent_store, sent_delivery = _seed(sent_dir)
    sent_outcome = asyncio.run(
        _reviewed_send(
            _sender(
                sent_store,
                FakeDeliveryClient(contradictory),
                send_mode="auto",
            ),
            sent_delivery,
        )
    )
    assert sent_outcome.status == "send_unknown"
    assert sent_store.list_feishu_delivery_receipts(
        delivery_id=sent_delivery.id
    ) == []
    confirmed = sent_store.reconcile_feishu_delivery_unknown(
        sent_delivery.id,
        app_id=sent_delivery.app_id,
        outcome="sent",
        verified_by="operator",
        evidence_kind="message_lookup",
        expected_chunks=1,
        message_ids=("om_unconfirmed",),
    )
    assert confirmed.status == "sent"

    not_sent_dir = tmp_path / "not-sent-review"
    not_sent_dir.mkdir()
    retry_store, retry_delivery = _seed(not_sent_dir)
    asyncio.run(
        _reviewed_send(
            _sender(
                retry_store,
                FakeDeliveryClient(contradictory),
                send_mode="auto",
            ),
            retry_delivery,
        )
    )
    failed = retry_store.reconcile_feishu_delivery_unknown(
        retry_delivery.id,
        app_id=retry_delivery.app_id,
        outcome="not_sent",
        verified_by="operator",
        evidence_kind="message_lookup",
    )
    retry = retry_store.requeue_feishu_delivery_after_verification(
        retry_delivery.id,
        app_id=retry_delivery.app_id,
        verified_by="operator",
        evidence_kind="admin_audit",
    )
    retry_store.approve_feishu_delivery(
        retry.id,
        app_id=retry.app_id,
        approved_by="operator",
        expected_approval_hash=retry.approval_hash,
    )
    client = FakeDeliveryClient(
        FeishuSendResult(True, message_id="om_retried_ordinal_zero")
    )
    resumed = retry_store.claim_feishu_delivery(
        retry.id, app_id=retry.app_id, approved_only=True
    )
    final = asyncio.run(
        _sender(retry_store, client, send_mode="confirm").send_claimed(resumed)
    )
    assert failed.error_code == "verified_not_sent"
    assert final.status == "sent"
    assert [call["ordinal"] for call in client.chunk_calls] == [0]


def test_partial_unknown_not_sent_can_be_requeued_and_resume_suffix(tmp_path):
    store, delivery = _seed(tmp_path, reply_text="x" * 6000)
    stale_approval_hash = delivery.approval_hash
    original_review_generation = delivery.review_generation
    approved = store.approve_feishu_delivery(
        delivery.id,
        app_id=delivery.app_id,
        approved_by="operator",
        expected_approval_hash=delivery.approval_hash,
    )
    claimed = store.claim_feishu_delivery(
        delivery.id, approved_only=True, app_id=delivery.app_id
    )
    store.record_feishu_delivery_chunk(
        delivery.id,
        app_id=delivery.app_id,
        lease_token=claimed.lease_token,
        ordinal=0,
        expected_chunks=2,
        message_id="om_verified_first",
    )
    store.mark_feishu_delivery_send_unknown(
        delivery.id,
        error_code="send_timeout",
        error="second chunk result unknown",
    )

    failed = store.reconcile_feishu_delivery_unknown(
        delivery.id,
        app_id=delivery.app_id,
        outcome="not_sent",
        verified_by="operator",
        evidence_kind="message_lookup",
    )
    retry = store.requeue_feishu_delivery_after_verification(
        delivery.id,
        app_id=delivery.app_id,
        verified_by="operator",
        evidence_kind="admin_audit",
    )

    assert failed.status == "failed"
    assert retry.status == "retry"
    assert retry.review_generation == original_review_generation + 1
    assert retry.approval_hash != stale_approval_hash
    assert retry.approved_at == ""
    assert store.claim_feishu_delivery(
        delivery.id, app_id=delivery.app_id, approved_only=True
    ) is None
    with pytest.raises(ValueError, match="approval hash changed"):
        store.approve_feishu_delivery(
            delivery.id,
            app_id=delivery.app_id,
            approved_by="operator",
            expected_approval_hash=stale_approval_hash,
        )
    approved_again = store.approve_feishu_delivery(
        delivery.id,
        app_id=delivery.app_id,
        approved_by="operator",
        expected_approval_hash=retry.approval_hash,
    )
    resumed = store.claim_feishu_delivery(
        delivery.id, app_id=delivery.app_id, approved_only=True
    )
    client = FakeDeliveryClient(
        FeishuSendResult(True, message_id="om_verified_second")
    )
    outcome = asyncio.run(
        _sender(store, client, send_mode="confirm").send_claimed(resumed)
    )

    assert approved.approved_at
    assert approved_again.approved_at
    assert outcome.status == "sent"
    assert [call["ordinal"] for call in client.chunk_calls] == [1]
    assert [
        receipt.message_id
        for receipt in store.list_feishu_delivery_receipts(
            delivery_id=delivery.id
        )
    ] == ["om_verified_first", "om_verified_second"]


def test_stale_complete_receipt_prefix_converges_sent_without_network(tmp_path):
    store, delivery = _seed(tmp_path, reply_text="x" * 6000)
    claimed = store.claim_feishu_delivery(delivery.id)
    for ordinal in range(delivery.expected_chunks):
        store.record_feishu_delivery_chunk(
            delivery.id,
            app_id=delivery.app_id,
            lease_token=claimed.lease_token,
            ordinal=ordinal,
            expected_chunks=delivery.expected_chunks,
            message_id=f"om_crash_{ordinal}",
        )
    with sqlite3.connect(store.path) as db:
        db.execute(
            "update feishu_deliveries set locked_at=datetime('now', '-6 minutes') "
            "where id=?",
            (delivery.id,),
        )

    assert recover_orphaned_sending(store) == 1
    saved = store.get_feishu_delivery(delivery.id)
    assert saved.status == "sent"
    assert saved.feishu_message_id == "om_crash_0"


def test_non_contiguous_receipts_are_quarantined_before_network(tmp_path):
    store, delivery = _seed(tmp_path, reply_text="x" * 6000)
    claimed = store.claim_feishu_delivery(delivery.id)
    with sqlite3.connect(store.path) as db:
        db.execute(
            "update feishu_deliveries set feishu_message_id='om_gap' where id=?",
            (delivery.id,),
        )
        db.execute(
            """
            insert into feishu_delivery_receipts (
                delivery_id, app_id, ordinal, message_id, status
            ) values (?, ?, 1, 'om_gap', 'active')
            """,
            (delivery.id, delivery.app_id),
        )
    client = FakeDeliveryClient()

    outcome = asyncio.run(
        _sender(store, client, send_mode="auto").send_claimed(claimed)
    )

    assert outcome.status == "send_unknown"
    assert outcome.error == "delivery_receipt_prefix_invalid"
    assert client.deliveries == []


def test_pinned_sdk_adapter_midstream_failure_keeps_first_receipt(tmp_path):
    lark_channel = pytest.importorskip("lark_channel")
    assert importlib.metadata.version("lark-channel-sdk") == "1.2.0"
    store, delivery = _seed(tmp_path, reply_text="x" * 6000)

    class Driver:
        def __init__(self):
            self.calls = []

        async def reply_message(self, **kwargs):
            self.calls.append(kwargs)
            if len(self.calls) == 1:
                return {"code": 0, "data": {"message_id": "om_sdk_first"}}
            raise TimeoutError("private upstream transport detail")

        async def create_message(self, **kwargs):
            raise AssertionError("every chunk must remain bound as a reply")

    driver = Driver()
    channel = lark_channel.FeishuChannel(
        app_id="cli_test", app_secret="local-test-secret"
    )
    channel._sender = lark_channel.OutboundSender(
        driver,
        lark_channel.OutboundConfig(
            retry=lark_channel.RetryConfig(max_attempts=1, base_delay_ms=0)
        ),
    )

    class PinnedSdkChannel:
        async def send(self, *args, **kwargs):
            return await channel.send(*args, **kwargs)

        async def fetch_message(self, message_id):
            return {"code": 0, "data": {"items": [{"message_id": message_id}]}}

    client = FeishuChannelClient(PinnedSdkChannel(), app_id="cli_test")

    outcome = asyncio.run(
        _reviewed_send(_sender(store, client, send_mode="confirm"), delivery)
    )

    assert outcome.status == "send_unknown"
    [receipt] = store.list_feishu_delivery_receipts(delivery_id=delivery.id)
    assert receipt.message_id == "om_sdk_first"
    first_key = delivery_chunk_idempotency_key(
        delivery_key=delivery.idempotency_key,
        ordinal=0,
        expected_chunks=delivery.expected_chunks,
        chunk_plan_sha256=delivery.chunk_plan_sha256,
        payload_sha256=delivery.payload_sha256,
    )
    second_key = delivery_chunk_idempotency_key(
        delivery_key=delivery.idempotency_key,
        ordinal=1,
        expected_chunks=delivery.expected_chunks,
        chunk_plan_sha256=delivery.chunk_plan_sha256,
        payload_sha256=delivery.payload_sha256,
    )
    assert driver.calls[0]["uuid"] == first_key
    assert all(call["uuid"] == second_key for call in driver.calls[1:])
    assert all(len(call["uuid"]) <= 50 for call in driver.calls)
    assert "private upstream" not in store.get_feishu_delivery(delivery.id).error
