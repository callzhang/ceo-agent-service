import sqlite3
from multiprocessing import get_context
from pathlib import Path

import pytest

from app.store import AgentRunLeaseLostError, AutoReplyStore
from app.wechat.models import WechatReplyScope


def _store(tmp_path):
    return AutoReplyStore(tmp_path / "worker.sqlite3")


def _seed_exhausted_target_open_failure(tmp_path, *, retry_count=2):
    store = _store(tmp_path)
    store.enqueue_reply_task(
        channel="wechat", conversation_id="u1", conversation_title="Alex",
        single_chat=True, trigger_message_id="m1",
        trigger_create_time="2026-07-30T10:00:00",
        trigger_sender="Alex", trigger_text="hi",
    )
    attempt_id = store.record_reply_attempt(
        conversation_id="u1", conversation_title="Alex",
        trigger_message_id="m1", trigger_sender="Alex", trigger_text="hi",
        action="send_reply", sensitivity_kind="normal",
        send_status="pending", channel="wechat",
    )
    delivery_id = store.create_wechat_delivery(
        reply_task_id=1, account_id="acct-1", target_type="direct",
        target_id="u1", conversation_id="u1", reply_text="reply",
    )
    delivery = store.get_wechat_delivery_by_id(delivery_id)
    store.mark_wechat_delivery_sending(
        delivery_id,
        now="2026-07-30 10:00:00",
    )
    store.set_wechat_delivery_status(
        delivery_id,
        "failed",
        error="target_open_failed",
        pre_action_failure=True,
    )
    with store._connect() as db:
        db.execute(
            "update reply_attempts set retry_count=? where id=?",
            (retry_count, attempt_id),
        )
    return store, delivery_id, attempt_id, delivery.execution_generation


def _wechat_delivery_and_attempt_state(store, delivery_id):
    with store._connect() as db:
        delivery = dict(
            db.execute(
                "select * from wechat_deliveries where id=?",
                (delivery_id,),
            ).fetchone()
        )
        attempts = [
            dict(row)
            for row in db.execute(
                """
                select attempts.*
                from reply_attempts as attempts
                join reply_tasks as tasks
                  on tasks.conversation_id=attempts.conversation_id
                 and tasks.trigger_message_id=attempts.trigger_message_id
                where tasks.id=? and attempts.channel='wechat'
                order by attempts.id
                """,
                (delivery["reply_task_id"],),
            ).fetchall()
        ]
    return delivery, attempts


def _open_legacy_store_in_process(db_path, barrier, results):
    try:
        barrier.wait()
        store = AutoReplyStore(Path(db_path))
        with store._connect() as db:
            results.put(
                (
                    "ok",
                    db.execute("pragma foreign_keys").fetchone()[0],
                    db.execute("pragma foreign_key_check").fetchall(),
                    db.execute(
                        "select client_request_id from workbench_attachments"
                    ).fetchone()[0],
                )
            )
    except BaseException as exc:
        results.put(("error", type(exc).__name__, str(exc)))


def test_store_round_trips_wechat_scope(tmp_path):
    store = _store(tmp_path)
    scope = WechatReplyScope(
        account_id="acct-1",
        target_type="group",
        target_id="group-1",
        conversation_id="cid-1",
        display_name="CEO group",
        trigger_mode="mention_current_account",
    )
    store.replace_wechat_reply_scopes("acct-1", [scope])
    loaded = store.list_wechat_reply_scopes("acct-1")
    assert len(loaded) == 1
    assert loaded[0].model_copy(update={"last_active_at": ""}) == scope
    assert loaded[0].last_active_at


def test_scope_account_mismatch_rejected(tmp_path):
    store = _store(tmp_path)
    scope = WechatReplyScope(
        account_id="other", target_type="direct", target_id="u",
        display_name="X", trigger_mode="every_inbound_text",
    )
    try:
        store.replace_wechat_reply_scopes("acct-1", [scope])
    except ValueError:
        return
    raise AssertionError("expected ValueError for account mismatch")


def test_replace_disables_omitted_scopes(tmp_path):
    store = _store(tmp_path)
    a = WechatReplyScope(account_id="acct-1", target_type="direct", target_id="u1",
                         display_name="A", trigger_mode="every_inbound_text")
    b = WechatReplyScope(account_id="acct-1", target_type="direct", target_id="u2",
                         display_name="B", trigger_mode="every_inbound_text")
    store.replace_wechat_reply_scopes("acct-1", [a, b])
    store.replace_wechat_reply_scopes("acct-1", [a])
    enabled = store.list_wechat_reply_scopes("acct-1", enabled_only=True)
    assert [s.target_id for s in enabled] == ["u1"]


def test_dingtalk_claim_does_not_claim_wechat_task(tmp_path):
    store = _store(tmp_path)
    store.enqueue_reply_task(
        channel="wechat", conversation_id="cid-1", conversation_title="Friend",
        single_chat=True, trigger_message_id="msg-1",
        trigger_create_time="2026-07-17 10:00:00", trigger_sender="Friend",
        trigger_text="hello",
    )
    assert store.claim_reply_tasks(10, channel="dingtalk") == []
    claimed = store.claim_reply_tasks(10, channel="wechat")
    assert len(claimed) == 1
    assert claimed[0].channel == "wechat"
    assert store.count_reply_tasks(channel="wechat") == 1
    assert store.count_reply_tasks(channel="dingtalk") == 0


def test_read_state_ready_account_scopes(tmp_path):
    store = _store(tmp_path)
    store.upsert_wechat_read_state(
        account_id="acct-1", account_dir="/d", db_dir="/d/db_storage",
        app_version="4.1.10", self_user_id="self-1", capability_status="ready",
    )
    store.replace_wechat_reply_scopes("acct-1", [
        WechatReplyScope(account_id="acct-1", target_type="direct", target_id="u1",
                         display_name="A", trigger_mode="every_inbound_text"),
    ])
    scopes = store.list_wechat_reply_scopes_for_ready_account()
    assert [s.target_id for s in scopes] == ["u1"]


def test_capability_probe_does_not_clear_existing_read_state_watermarks(tmp_path):
    store = _store(tmp_path)
    common = dict(
        account_id="acct-1", account_dir="/d", db_dir="/d/db_storage",
        app_version="4.1.10", self_user_id="self-1", capability_status="ready",
    )
    store.upsert_wechat_read_state(
        **common, watermark_sent_at="2026-07-20T10:00:00+08:00",
        watermark_message_id="m1", last_scan_at="2026-07-20T10:01:00+08:00",
    )

    store.upsert_wechat_read_state(
        **{**common, "self_user_id": ""}, capability_reason="probe_ok"
    )

    state = store.get_wechat_read_state("acct-1")
    assert state["self_user_id"] == "self-1"
    assert state["watermark_sent_at"] == "2026-07-20T10:00:00+08:00"
    assert state["watermark_message_id"] == "m1"
    assert state["last_scan_at"] == "2026-07-20T10:01:00+08:00"


def test_reply_task_identity_is_isolated_by_channel(tmp_path):
    store = _store(tmp_path)
    common = dict(
        conversation_id="same-conversation", conversation_title="Same",
        single_chat=True, trigger_message_id="same-message",
        trigger_create_time="2026-07-20T10:00:00+08:00",
        trigger_sender="Sender", trigger_text="hello",
    )

    assert store.enqueue_reply_task(channel="dingtalk", **common)
    assert store.enqueue_reply_task(channel="wechat", **common)
    assert store.get_reply_task_for_message(
        "same-conversation", "same-message", channel="dingtalk"
    ).channel == "dingtalk"
    assert store.get_reply_task_for_message(
        "same-conversation", "same-message", channel="wechat"
    ).channel == "wechat"


def test_recreating_pre_action_failed_delivery_makes_it_retryable(tmp_path):
    store = _store(tmp_path)
    store.enqueue_reply_task(
        channel="wechat",
        conversation_id="u1",
        conversation_title="Alex",
        single_chat=True,
        trigger_message_id="m1",
        trigger_create_time="2026-07-28T10:00:00",
        trigger_sender="Alex",
        trigger_text="hi",
    )
    delivery_id = store.create_wechat_delivery(
        reply_task_id=1,
        account_id="acct-1",
        target_type="direct",
        target_id="u1",
        conversation_id="u1",
        reply_text="first",
    )
    store.set_wechat_delivery_status(
        delivery_id,
        "failed",
        error="target_binding_unverified",
        pre_action_failure=True,
    )

    store.create_wechat_delivery(
        reply_task_id=1,
        account_id="acct-1",
        target_type="direct",
        target_id="u1",
        conversation_id="u1",
        reply_text="second",
    )

    delivery = store.get_wechat_delivery_for_task(1)
    assert delivery.status == "ready_to_send"
    assert delivery.error == ""
    assert delivery.reply_text == "second"


def test_generation_rotation_supersedes_ready_delivery_atomically(tmp_path):
    store = _store(tmp_path)
    store.enqueue_reply_task(
        channel="wechat", conversation_id="u1", conversation_title="Alex",
        single_chat=True, trigger_message_id="m1",
        trigger_create_time="2026-07-30T10:00:00",
        trigger_sender="Alex", trigger_text="hi",
    )
    delivery_id = store.create_wechat_delivery(
        reply_task_id=1, account_id="acct-1", target_type="direct",
        target_id="u1", conversation_id="u1", reply_text="old reply",
    )

    new_generation = store.rotate_reply_task_execution_generation(1)

    delivery = store.get_wechat_delivery_for_task(1)
    assert delivery is not None
    assert delivery.id == delivery_id
    assert delivery.status == "superseded"
    assert delivery.error == f"superseded_by_generation:{new_generation}"
    assert store.list_wechat_deliveries_by_status("ready_to_send") == []


def test_new_generation_replaces_superseded_delivery_with_corrected_reply(tmp_path):
    store = _store(tmp_path)
    store.enqueue_reply_task(
        channel="wechat", conversation_id="u1", conversation_title="Alex",
        single_chat=True, trigger_message_id="m1",
        trigger_create_time="2026-07-30T10:00:00",
        trigger_sender="Alex", trigger_text="hi",
    )
    task = store.claim_reply_tasks(1, channel="wechat")[0]
    store.create_wechat_delivery(
        reply_task_id=task.id, account_id="acct-1", target_type="direct",
        target_id="u1", conversation_id="u1", reply_text="old reply",
    )
    new_generation = store.rotate_reply_task_execution_generation(task.id)
    claimed = store.claim_reply_task(task.id)
    assert claimed is not None
    assert claimed.execution_generation == new_generation

    store.finalize_wechat_reply_task(
        task_id=task.id,
        expected_execution_generation=new_generation,
        action="send_reply",
        sensitivity_kind="general",
        codex_reason="reviewed correction",
        draft_reply_text="corrected reply",
        audit_summary="corrected reply queued",
        send_status="pending",
        account_id="acct-1",
        target_type="direct",
        target_id="u1",
        conversation_id="u1",
        reply_text="corrected reply",
    )

    delivery = store.get_wechat_delivery_for_task(task.id)
    assert delivery.status == "ready_to_send"
    assert delivery.execution_generation == new_generation
    assert delivery.reply_text == "corrected reply"
    assert delivery.error == ""


def test_new_generation_replaces_confirmed_unperformed_delivery(tmp_path):
    store = _store(tmp_path)
    store.enqueue_reply_task(
        channel="wechat", conversation_id="u1", conversation_title="Alex",
        single_chat=True, trigger_message_id="m1",
        trigger_create_time="2026-07-30T10:00:00",
        trigger_sender="Alex", trigger_text="hi",
    )
    task = store.claim_reply_tasks(1, channel="wechat")[0]
    delivery_id = store.create_wechat_delivery(
        reply_task_id=task.id, account_id="acct-1", target_type="direct",
        target_id="u1", conversation_id="u1", reply_text="old reply",
    )
    store.mark_wechat_delivery_sending(delivery_id)
    store.set_wechat_delivery_status(
        delivery_id,
        "failed",
        error="action_not_performed",
        pre_action_failure=True,
    )
    new_generation = store.rotate_reply_task_execution_generation(task.id)
    claimed = store.claim_reply_task(task.id)
    assert claimed is not None

    store.finalize_wechat_reply_task(
        task_id=task.id,
        expected_execution_generation=new_generation,
        action="send_reply",
        sensitivity_kind="general",
        codex_reason="fresh decision",
        draft_reply_text="new reply",
        audit_summary="fresh reply queued",
        send_status="pending",
        account_id="acct-1",
        target_type="direct",
        target_id="u1",
        conversation_id="u1",
        reply_text="new reply",
    )

    delivery = store.get_wechat_delivery_for_task(task.id)
    assert delivery is not None
    assert delivery.status == "ready_to_send"
    assert delivery.execution_generation == new_generation
    assert delivery.reply_text == "new reply"
    with store._connect() as db:
        row = db.execute(
            "select action_started_at from wechat_deliveries where id=?",
            (delivery_id,),
        ).fetchone()
    assert row["action_started_at"] == ""


def test_new_generation_does_not_replace_started_or_uncertain_delivery(tmp_path):
    for status in ("sending", "send_unknown"):
        store = AutoReplyStore(tmp_path / f"{status}.sqlite3")
        store.enqueue_reply_task(
            channel="wechat", conversation_id="u1", conversation_title="Alex",
            single_chat=True, trigger_message_id="m1",
            trigger_create_time="2026-07-30T10:00:00",
            trigger_sender="Alex", trigger_text="hi",
        )
        task = store.claim_reply_tasks(1, channel="wechat")[0]
        delivery_id = store.create_wechat_delivery(
            reply_task_id=task.id, account_id="acct-1", target_type="direct",
            target_id="u1", conversation_id="u1", reply_text="old reply",
        )
        store.mark_wechat_delivery_sending(delivery_id)
        if status == "send_unknown":
            store.set_wechat_delivery_status(delivery_id, status)
        with pytest.raises(
            ValueError,
            match="WeChat delivery reconciliation required before rotation",
        ):
            store.rotate_reply_task_execution_generation(task.id)


def test_normalize_user_rejected_wechat_deliveries_is_terminal_and_idempotent(tmp_path):
    store = _store(tmp_path)
    store.enqueue_reply_task(
        channel="wechat",
        conversation_id="cid-1",
        conversation_title="Friday",
        single_chat=True,
        trigger_message_id="msg-1",
        trigger_create_time="2026-08-12 00:00:00",
        trigger_sender="Mina",
        trigger_text="Please do not send this.",
    )
    delivery_id = store.create_wechat_delivery(
        reply_task_id=1,
        account_id="acct-1",
        target_type="direct",
        target_id="user-1",
        conversation_id="cid-1",
        reply_text="This will not be sent.",
    )
    store.set_wechat_delivery_status(delivery_id, "failed", error="user_rejected")

    assert store.normalize_user_rejected_wechat_deliveries() == 1
    assert store.get_wechat_delivery_by_id(delivery_id).status == "skipped"
    assert store.normalize_user_rejected_wechat_deliveries() == 0


def test_stale_wechat_delivery_cannot_record_completion_after_rotation(tmp_path):
    store = _store(tmp_path)
    store.enqueue_reply_task(
        channel="wechat", conversation_id="u1", conversation_title="Alex",
        single_chat=True, trigger_message_id="m1",
        trigger_create_time="2026-07-30T10:00:00",
        trigger_sender="Alex", trigger_text="hi",
    )
    task = store.claim_reply_tasks(1, channel="wechat")[0]
    delivery_id = store.create_wechat_delivery(
        reply_task_id=task.id, account_id="acct-1", target_type="direct",
        target_id="u1", conversation_id="u1", reply_text="old reply",
    )

    store.rotate_reply_task_execution_generation(task.id)

    with pytest.raises(
        AgentRunLeaseLostError,
        match="WeChat delivery superseded",
    ):
        store.set_wechat_delivery_status(delivery_id, "sent")
    assert store.get_wechat_delivery_by_id(delivery_id).status == "superseded"


def test_new_delivery_supersedes_older_unsent_delivery_for_same_conversation(
    tmp_path,
):
    store = _store(tmp_path)
    for task_id, message_id in ((1, "m1"), (2, "m2")):
        store.enqueue_reply_task(
            channel="wechat",
            conversation_id="u1",
            conversation_title="Alex",
            single_chat=True,
            trigger_message_id=message_id,
            trigger_create_time=f"2026-07-28T10:0{task_id}:00",
            trigger_sender="Alex",
            trigger_text=f"message {task_id}",
        )
        store.record_reply_attempt(
            conversation_id="u1",
            conversation_title="Alex",
            trigger_message_id=message_id,
            trigger_sender="Alex",
            trigger_text=f"message {task_id}",
            action="send_reply",
            sensitivity_kind="normal",
            send_status="pending",
            channel="wechat",
        )
        store.create_wechat_delivery(
            reply_task_id=task_id,
            account_id="acct-1",
            target_type="direct",
            target_id="u1",
            conversation_id="u1",
            reply_text=f"reply {task_id}",
        )

    old_delivery = store.get_wechat_delivery_for_task(1)
    new_delivery = store.get_wechat_delivery_for_task(2)
    old_attempt = store.get_latest_reply_attempt_for_trigger("u1", "m1")
    new_attempt = store.get_latest_reply_attempt_for_trigger("u1", "m2")

    assert old_delivery.status == "superseded"
    assert old_delivery.error == "superseded_by_newer_wechat_trigger:2"
    assert old_attempt.send_status == "skipped"
    assert old_attempt.send_error == "superseded_by_newer_wechat_trigger:2"
    assert new_delivery.status == "ready_to_send"
    assert new_attempt.send_status == "pending"


def test_finalize_reply_supersedes_older_unsent_delivery_for_same_conversation(
    tmp_path,
):
    store = _store(tmp_path)
    attempts = []
    for message_id in ("m1", "m2"):
        store.enqueue_reply_task(
            channel="wechat",
            conversation_id="u1",
            conversation_title="Alex",
            single_chat=True,
            trigger_message_id=message_id,
            trigger_create_time=f"2026-07-28T10:0{len(attempts) + 1}:00",
            trigger_sender="Alex",
            trigger_text=message_id,
        )
        task = store.claim_reply_tasks(1, channel="wechat")[0]
        attempts.append(
            store.finalize_wechat_reply_task(
                task_id=task.id,
                expected_execution_generation=task.execution_generation,
                action="send_reply",
                sensitivity_kind="normal",
                codex_reason="reply requested",
                draft_reply_text=f"reply {message_id}",
                audit_summary="reply queued",
                send_status="pending",
                account_id="acct-1",
                target_type="direct",
                target_id="u1",
                conversation_id="u1",
                reply_text=f"reply {message_id}",
            )
        )

    old_delivery = store.get_wechat_delivery_for_task(1)
    new_delivery = store.get_wechat_delivery_for_task(2)
    old_attempt = store.get_reply_attempt(attempts[0])

    assert old_delivery.status == "superseded"
    assert old_delivery.error == "superseded_by_newer_wechat_trigger:2"
    assert old_attempt.send_status == "skipped"
    assert old_attempt.send_error == "superseded_by_newer_wechat_trigger:2"
    assert new_delivery.status == "ready_to_send"


def test_stale_ready_delivery_can_be_superseded_without_send(tmp_path):
    store = _store(tmp_path)
    store.enqueue_reply_task(
        channel="wechat", conversation_id="u1", conversation_title="Alex",
        single_chat=True, trigger_message_id="m1",
        trigger_create_time="2026-07-30T10:00:00",
        trigger_sender="Alex", trigger_text="hi",
    )
    attempt_id = store.record_reply_attempt(
        conversation_id="u1", conversation_title="Alex",
        trigger_message_id="m1", trigger_sender="Alex", trigger_text="hi",
        action="send_reply", sensitivity_kind="normal",
        send_status="pending", channel="wechat",
    )
    delivery_id = store.create_wechat_delivery(
        reply_task_id=1, account_id="acct-1", target_type="direct",
        target_id="u1", conversation_id="u1", reply_text="stale reply",
    )
    delivery = store.get_wechat_delivery_by_id(delivery_id)
    with store._connect() as db:
        db.execute(
            "update wechat_deliveries set created_at='2026-07-30 10:00:00' "
            "where id=?",
            (delivery_id,),
        )

    store.supersede_stale_ready_wechat_delivery(
        delivery_id,
        expected_execution_generation=delivery.execution_generation,
        reason="expired_time_sensitive_reply",
        inactive_before="2026-07-30 10:10:00",
    )

    refreshed = store.get_wechat_delivery_by_id(delivery_id)
    attempt = store.get_reply_attempt(attempt_id)
    assert refreshed.status == "superseded"
    assert refreshed.error == "expired_time_sensitive_reply"
    assert attempt.send_status == "skipped"
    assert attempt.send_error == "expired_time_sensitive_reply"


def test_stale_ready_cleanup_cannot_overwrite_started_delivery(tmp_path):
    store = _store(tmp_path)
    store.enqueue_reply_task(
        channel="wechat", conversation_id="u1", conversation_title="Alex",
        single_chat=True, trigger_message_id="m1",
        trigger_create_time="2026-07-30T10:00:00",
        trigger_sender="Alex", trigger_text="hi",
    )
    delivery_id = store.create_wechat_delivery(
        reply_task_id=1, account_id="acct-1", target_type="direct",
        target_id="u1", conversation_id="u1", reply_text="reply",
    )
    delivery = store.get_wechat_delivery_by_id(delivery_id)
    store.mark_wechat_delivery_sending(delivery_id, now="2026-07-30 10:00:00")

    with pytest.raises(AgentRunLeaseLostError, match="not in expected state"):
        store.supersede_stale_ready_wechat_delivery(
            delivery_id,
            expected_execution_generation=delivery.execution_generation,
            reason="expired_time_sensitive_reply",
            inactive_before="2026-07-30 10:10:00",
        )

    assert store.get_wechat_delivery_by_id(delivery_id).status == "sending"


def test_exhausted_stale_wechat_delivery_is_skipped_with_attempt_sync(tmp_path):
    store = _store(tmp_path)
    store.enqueue_reply_task(
        channel="wechat", conversation_id="u1", conversation_title="Alex",
        single_chat=True, trigger_message_id="m1",
        trigger_create_time="2026-07-30T10:00:00",
        trigger_sender="Alex", trigger_text="hi",
    )
    attempt_id = store.record_reply_attempt(
        conversation_id="u1", conversation_title="Alex",
        trigger_message_id="m1", trigger_sender="Alex", trigger_text="hi",
        action="send_reply", sensitivity_kind="normal",
        send_status="pending", channel="wechat",
    )
    delivery_id = store.create_wechat_delivery(
        reply_task_id=1, account_id="acct-1", target_type="direct",
        target_id="u1", conversation_id="u1", reply_text="reply",
    )
    delivery = store.get_wechat_delivery_by_id(delivery_id)
    store.mark_wechat_delivery_sending(
        delivery_id,
        now="2026-07-30 10:00:00",
    )
    store.set_wechat_delivery_status(
        delivery_id,
        "failed",
        error="target_open_failed",
        pre_action_failure=True,
    )
    with store._connect() as db:
        db.execute(
            "update reply_attempts set retry_count=2 where id=?",
            (attempt_id,),
        )

    store.skip_exhausted_stale_wechat_delivery(
        delivery_id,
        expected_execution_generation=delivery.execution_generation,
        reason="stale_target_open_retry_exhausted",
        inactive_before="2026-07-30 10:10:00",
    )

    refreshed = store.get_wechat_delivery_by_id(delivery_id)
    attempt = store.get_reply_attempt(attempt_id)
    assert refreshed.status == "skipped"
    assert refreshed.error == "stale_target_open_retry_exhausted"
    assert refreshed.pre_action_failure is False
    assert attempt.send_status == "skipped"
    assert attempt.send_error == "stale_target_open_retry_exhausted"


def test_exhausted_stale_wechat_delivery_second_call_is_rejected_unchanged(tmp_path):
    store, delivery_id, _, generation = _seed_exhausted_target_open_failure(tmp_path)
    arguments = {
        "delivery_id": delivery_id,
        "expected_execution_generation": generation,
        "reason": "stale_target_open_retry_exhausted",
        "inactive_before": "2026-07-30 10:10:00",
    }
    store.skip_exhausted_stale_wechat_delivery(**arguments)
    after_first_call = _wechat_delivery_and_attempt_state(store, delivery_id)

    with pytest.raises(AgentRunLeaseLostError, match="not in expected state"):
        store.skip_exhausted_stale_wechat_delivery(**arguments)

    assert _wechat_delivery_and_attempt_state(store, delivery_id) == after_first_call


def test_exhausted_stale_wechat_delivery_rejects_unknown_positive_id_unchanged(
    tmp_path,
):
    store, delivery_id, _, generation = _seed_exhausted_target_open_failure(tmp_path)
    before = _wechat_delivery_and_attempt_state(store, delivery_id)

    with pytest.raises(AgentRunLeaseLostError, match="not in expected state"):
        store.skip_exhausted_stale_wechat_delivery(
            delivery_id + 999,
            expected_execution_generation=generation,
            reason="stale_target_open_retry_exhausted",
            inactive_before="2026-07-30 10:10:00",
        )

    assert _wechat_delivery_and_attempt_state(store, delivery_id) == before


def test_exhausted_stale_wechat_delivery_uses_latest_attempt_retry_count(tmp_path):
    store, delivery_id, _, generation = _seed_exhausted_target_open_failure(
        tmp_path,
        retry_count=9,
    )
    latest_attempt_id = store.record_reply_attempt(
        conversation_id="u1", conversation_title="Alex",
        trigger_message_id="m1", trigger_sender="Alex", trigger_text="hi",
        action="send_reply", sensitivity_kind="normal",
        send_status="failed", channel="wechat",
    )
    with store._connect() as db:
        db.execute(
            "update reply_attempts set retry_count=1 where id=?",
            (latest_attempt_id,),
        )
    before = _wechat_delivery_and_attempt_state(store, delivery_id)

    with pytest.raises(AgentRunLeaseLostError, match="not in expected state"):
        store.skip_exhausted_stale_wechat_delivery(
            delivery_id,
            expected_execution_generation=generation,
            reason="stale_target_open_retry_exhausted",
            inactive_before="2026-07-30 10:10:00",
        )

    assert _wechat_delivery_and_attempt_state(store, delivery_id) == before


@pytest.mark.parametrize(
    "invalid_state",
    [
        "wrong_failure_code",
        "pre_action_failure_false",
        "newer_than_cutoff",
        "non_failed_status",
        "blank_action_started_at",
        "delivery_generation_mismatch",
        "task_generation_mismatch",
        "missing_matching_attempt",
    ],
)
def test_exhausted_stale_wechat_delivery_rejects_ineligible_state_unchanged(
    tmp_path,
    invalid_state,
):
    store, delivery_id, attempt_id, generation = (
        _seed_exhausted_target_open_failure(tmp_path)
    )
    expected_generation = generation
    with store._connect() as db:
        if invalid_state == "wrong_failure_code":
            db.execute(
                "update wechat_deliveries set error='target_binding_unverified' "
                "where id=?",
                (delivery_id,),
            )
        elif invalid_state == "pre_action_failure_false":
            db.execute(
                "update wechat_deliveries set pre_action_failure=0 where id=?",
                (delivery_id,),
            )
        elif invalid_state == "newer_than_cutoff":
            db.execute(
                "update wechat_deliveries "
                "set action_started_at='2026-07-30 10:20:00' where id=?",
                (delivery_id,),
            )
        elif invalid_state == "non_failed_status":
            db.execute(
                "update wechat_deliveries set status='send_unknown' where id=?",
                (delivery_id,),
            )
        elif invalid_state == "blank_action_started_at":
            db.execute(
                "update wechat_deliveries set action_started_at='' where id=?",
                (delivery_id,),
            )
        elif invalid_state == "delivery_generation_mismatch":
            expected_generation = "different-generation"
        elif invalid_state == "task_generation_mismatch":
            db.execute(
                "update reply_tasks set execution_generation='new-generation' "
                "where id=(select reply_task_id from wechat_deliveries where id=?)",
                (delivery_id,),
            )
        elif invalid_state == "missing_matching_attempt":
            db.execute("delete from reply_attempts where id=?", (attempt_id,))
    before = _wechat_delivery_and_attempt_state(store, delivery_id)

    with pytest.raises(AgentRunLeaseLostError, match="not in expected state"):
        store.skip_exhausted_stale_wechat_delivery(
            delivery_id,
            expected_execution_generation=expected_generation,
            reason="stale_target_open_retry_exhausted",
            inactive_before="2026-07-30 10:10:00",
        )

    assert _wechat_delivery_and_attempt_state(store, delivery_id) == before


@pytest.mark.parametrize(
    "invalid_argument",
    [
        {"delivery_id": 0},
        {"delivery_id": -1},
        {"expected_execution_generation": ""},
        {"expected_execution_generation": "   "},
        {"reason": ""},
        {"reason": "   "},
        {"inactive_before": ""},
        {"inactive_before": "   "},
        {"max_retries": 0},
        {"max_retries": -1},
    ],
)
def test_exhausted_stale_wechat_delivery_rejects_invalid_scalar_unchanged(
    tmp_path,
    invalid_argument,
):
    store, delivery_id, _, generation = _seed_exhausted_target_open_failure(tmp_path)
    arguments = {
        "delivery_id": delivery_id,
        "expected_execution_generation": generation,
        "reason": "stale_target_open_retry_exhausted",
        "inactive_before": "2026-07-30 10:10:00",
        "max_retries": 2,
    }
    arguments.update(invalid_argument)
    before = _wechat_delivery_and_attempt_state(store, delivery_id)

    with pytest.raises(ValueError):
        store.skip_exhausted_stale_wechat_delivery(**arguments)

    assert _wechat_delivery_and_attempt_state(store, delivery_id) == before


def test_newer_sent_delivery_supersedes_older_action_not_performed(tmp_path):
    store = _store(tmp_path)
    for task_id, message_id in ((1, "m1"), (2, "m2")):
        store.enqueue_reply_task(
            channel="wechat",
            conversation_id="u1",
            conversation_title="Alex",
            single_chat=True,
            trigger_message_id=message_id,
            trigger_create_time=f"2026-07-28T10:0{task_id}:00",
            trigger_sender="Alex",
            trigger_text=f"message {task_id}",
        )
        store.create_wechat_delivery(
            reply_task_id=task_id,
            account_id="acct-1",
            target_type="direct",
            target_id="u1",
            conversation_id="u1",
            reply_text=f"reply {task_id}",
        )

    old = store.get_wechat_delivery_for_task(1)
    newer = store.get_wechat_delivery_for_task(2)
    # Simulate an explicit pre-action failure that existed before a later delivery
    # was recorded; normal creation now supersedes this case immediately.
    with store._connect() as db:
        db.execute(
            "update wechat_deliveries set status='failed', error='composer_input_unconfirmed', pre_action_failure=1 where id=?",
            (old.id,),
        )
    store.mark_wechat_delivery_sending(newer.id)
    store.set_wechat_delivery_status(newer.id, "sent")

    refreshed_old = store.get_wechat_delivery_for_task(1)
    assert refreshed_old.status == "superseded"
    assert refreshed_old.error == f"superseded_by_newer_wechat_delivery:{newer.id}"


@pytest.mark.parametrize("status", ["sending", "send_unknown"])
def test_newer_wechat_trigger_waits_for_uncertain_delivery_reconciliation(
    tmp_path,
    status,
):
    store = _store(tmp_path)
    for task_id, message_id in ((1, "m1"), (2, "m2")):
        store.enqueue_reply_task(
            channel="wechat",
            conversation_id="u1",
            conversation_title="Alex",
            single_chat=True,
            trigger_message_id=message_id,
            trigger_create_time=f"2026-07-28T10:0{task_id}:00",
            trigger_sender="Alex",
            trigger_text=f"message {task_id}",
        )
    old_delivery_id = store.create_wechat_delivery(
        reply_task_id=1,
        account_id="acct-1",
        target_type="direct",
        target_id="u1",
        conversation_id="u1",
        reply_text="old reply",
    )
    store.mark_wechat_delivery_sending(old_delivery_id)
    if status == "send_unknown":
        store.set_wechat_delivery_status(old_delivery_id, status)

    with pytest.raises(
        ValueError,
        match="WeChat delivery reconciliation required before a newer trigger",
    ):
        store.create_wechat_delivery(
            reply_task_id=2,
            account_id="acct-1",
            target_type="direct",
            target_id="u1",
            conversation_id="u1",
            reply_text="new reply",
        )

    assert store.get_wechat_delivery_by_id(old_delivery_id).status == status
    assert store.get_wechat_delivery_for_task(2) is None


def test_reconciled_unknown_delivery_can_be_superseded_without_replay(tmp_path):
    store = _store(tmp_path)
    store.enqueue_reply_task(
        channel="wechat", conversation_id="u1", conversation_title="Alex",
        single_chat=True, trigger_message_id="m1",
        trigger_create_time="2026-07-30T10:00:00",
        trigger_sender="Alex", trigger_text="hi",
    )
    attempt_id = store.record_reply_attempt(
        conversation_id="u1", conversation_title="Alex",
        trigger_message_id="m1", trigger_sender="Alex", trigger_text="hi",
        action="send_reply", sensitivity_kind="normal",
        send_status="pending", channel="wechat",
    )
    delivery_id = store.create_wechat_delivery(
        reply_task_id=1, account_id="acct-1", target_type="direct",
        target_id="u1", conversation_id="u1", reply_text="stale reply",
    )
    delivery = store.get_wechat_delivery_by_id(delivery_id)
    store.mark_wechat_delivery_sending(delivery_id)
    store.set_wechat_delivery_status(
        delivery_id,
        "send_unknown",
        error="read_only_reconciliation_inconclusive",
    )

    with store._connect() as db:
        db.execute(
            "update wechat_deliveries "
            "set action_started_at='2026-07-30 10:05:00' "
            "where id=?",
            (delivery_id,),
        )
    store.set_wechat_delivery_status(
        delivery_id,
        "send_unknown",
        error="read_only_reconciliation_inconclusive",
    )

    store.supersede_reconciled_wechat_delivery(
        delivery_id,
        expected_execution_generation=delivery.execution_generation,
        reason="stale_after_read_only_reconciliation",
        inactive_before="2026-07-30 10:10:00",
    )

    refreshed = store.get_wechat_delivery_by_id(delivery_id)
    attempt = store.get_reply_attempt(attempt_id)
    assert refreshed.status == "superseded"
    assert refreshed.error == "stale_after_read_only_reconciliation"
    assert attempt is not None
    assert attempt.send_status == "skipped"
    assert attempt.send_error == "stale_after_read_only_reconciliation"


def test_wechat_delivery_claim_records_action_start_time(tmp_path):
    store = _store(tmp_path)
    store.enqueue_reply_task(
        channel="wechat", conversation_id="u1", conversation_title="Alex",
        single_chat=True, trigger_message_id="m1",
        trigger_create_time="2026-07-30T10:00:00",
        trigger_sender="Alex", trigger_text="hi",
    )
    delivery_id = store.create_wechat_delivery(
        reply_task_id=1, account_id="acct-1", target_type="direct",
        target_id="u1", conversation_id="u1", reply_text="reply",
    )

    delivery = store.get_wechat_delivery_by_id(delivery_id)
    assert store.claim_wechat_delivery(
        delivery_id,
        expected_execution_generation=delivery.execution_generation,
    ) is not None

    with store._connect() as db:
        row = db.execute(
            "select action_started_at from wechat_deliveries where id=?",
            (delivery_id,),
        ).fetchone()
    assert row["action_started_at"]


def test_active_unknown_delivery_cannot_be_superseded(tmp_path):
    store = _store(tmp_path)
    store.enqueue_reply_task(
        channel="wechat", conversation_id="u1", conversation_title="Alex",
        single_chat=True, trigger_message_id="m1",
        trigger_create_time="2026-07-30T10:00:00",
        trigger_sender="Alex", trigger_text="hi",
    )
    delivery_id = store.create_wechat_delivery(
        reply_task_id=1, account_id="acct-1", target_type="direct",
        target_id="u1", conversation_id="u1", reply_text="stale reply",
    )
    delivery = store.get_wechat_delivery_by_id(delivery_id)
    store.mark_wechat_delivery_sending(delivery_id)
    store.set_wechat_delivery_status(
        delivery_id,
        "send_unknown",
        error="read_only_reconciliation_inconclusive",
    )

    with pytest.raises(AgentRunLeaseLostError, match="not in expected state"):
        store.supersede_reconciled_wechat_delivery(
            delivery_id,
            expected_execution_generation=delivery.execution_generation,
            reason="stale_after_read_only_reconciliation",
            inactive_before="2000-01-01 00:00:00",
        )

    assert store.get_wechat_delivery_by_id(delivery_id).status == "send_unknown"


def test_reject_cannot_overwrite_delivery_claimed_by_sender(tmp_path):
    store = _store(tmp_path)
    store.enqueue_reply_task(
        channel="wechat", conversation_id="u1", conversation_title="Alex",
        single_chat=True, trigger_message_id="m1",
        trigger_create_time="2026-07-30T10:00:00",
        trigger_sender="Alex", trigger_text="hi",
    )
    delivery_id = store.create_wechat_delivery(
        reply_task_id=1, account_id="acct-1", target_type="direct",
        target_id="u1", conversation_id="u1", reply_text="reply",
    )
    store.mark_wechat_delivery_sending(delivery_id)

    with pytest.raises(AgentRunLeaseLostError, match="not in expected state"):
        store.set_wechat_delivery_status(
            delivery_id, "failed", error="user_rejected"
        )

    store.set_wechat_delivery_status(delivery_id, "sent")
    assert store.get_wechat_delivery_by_id(delivery_id).status == "sent"


def test_replacing_enabled_scope_does_not_clear_its_watermark(tmp_path):
    store = _store(tmp_path)
    scope = WechatReplyScope(
        account_id="acct-1", target_type="direct", target_id="u1",
        display_name="A", trigger_mode="every_inbound_text",
    )
    store.replace_wechat_reply_scopes("acct-1", [scope])
    baseline = store.get_wechat_reply_scope("acct-1", "direct", "u1").last_active_at

    store.replace_wechat_reply_scopes("acct-1", [scope])

    assert baseline
    assert store.get_wechat_reply_scope(
        "acct-1", "direct", "u1"
    ).last_active_at == baseline


def test_legacy_reply_task_identity_migration_preserves_rows_and_delivery_fk(tmp_path):
    db_path = tmp_path / "legacy.sqlite3"
    db = sqlite3.connect(db_path)
    db.executescript(
        """
        pragma foreign_keys=on;
        create table reply_tasks (
            id integer primary key autoincrement,
            conversation_id text not null,
            conversation_title text not null,
            single_chat integer not null,
            trigger_message_id text not null,
            trigger_create_time text not null,
            trigger_sender text not null,
            trigger_text text not null,
            status text not null default 'pending',
            attempts integer not null default 0,
            locked_at text,
            error text not null default '',
            created_at text not null default current_timestamp,
            updated_at text not null default current_timestamp,
            unique(conversation_id, trigger_message_id)
        );
        create table wechat_deliveries (
            id integer primary key autoincrement,
            reply_task_id integer not null unique,
            account_id text not null,
            target_type text not null,
            target_id text not null,
            conversation_id text not null default '',
            reply_text text not null,
            status text not null default 'ready_to_send',
            action_started_at text not null default '',
            evidence_json text not null default '{}',
            error text not null default '',
            created_at text not null default current_timestamp,
            updated_at text not null default current_timestamp,
            foreign key(reply_task_id) references reply_tasks(id)
        );
        create table workbench_tasks (
            id text primary key,
            title text not null,
            runtime_kind text not null,
            provider_session_ref text not null default '',
            archived_at text not null default '',
            created_at text not null default current_timestamp,
            updated_at text not null default current_timestamp
        );
        create table workbench_attachments (
            id text primary key,
            task_id text not null,
            filename text not null,
            media_type text not null,
            size_bytes integer not null check(size_bytes >= 0),
            storage_path text not null,
            created_at text not null default current_timestamp,
            foreign key(task_id) references workbench_tasks(id)
        );
        insert into reply_tasks (
            id, conversation_id, conversation_title, single_chat,
            trigger_message_id, trigger_create_time, trigger_sender, trigger_text
        ) values (
            7, 'same-conversation', 'Friend', 1,
            'same-message', '2026-07-20T10:00:00+08:00', 'Friend', 'hello'
        );
        insert into wechat_deliveries (
            reply_task_id, account_id, target_type, target_id, conversation_id,
            reply_text
        ) values (7, 'acct-1', 'direct', 'friend-1', 'same-conversation', 'hi');
        insert into workbench_tasks (id, title, runtime_kind)
        values ('workbench-legacy', 'Legacy attachment', 'codex');
        insert into workbench_attachments (
            id, task_id, filename, media_type, size_bytes, storage_path
        ) values (
            'attachment-legacy', 'workbench-legacy', 'legacy.txt',
            'text/plain', 6, '/tmp/legacy.txt'
        );
        """
    )
    db.close()

    context = get_context("spawn")
    barrier = context.Barrier(2)
    results = context.Queue()
    processes = [
        context.Process(
            target=_open_legacy_store_in_process,
            args=(str(db_path), barrier, results),
        )
        for _ in range(2)
    ]
    for process in processes:
        process.start()
    for process in processes:
        process.join(timeout=20)
        assert process.exitcode == 0

    outcomes = [results.get(timeout=2) for _ in processes]
    assert outcomes == [
        ("ok", 1, [], "attachment-legacy"),
        ("ok", 1, [], "attachment-legacy"),
    ]

    store = AutoReplyStore(db_path)

    assert store.get_reply_task_for_message(
        "same-conversation", "same-message", channel="dingtalk"
    ).id == 7
    assert store.list_wechat_deliveries_by_status("ready_to_send")[0].task_id == 7
    assert store.enqueue_reply_task(
        channel="wechat", conversation_id="same-conversation",
        conversation_title="Same", single_chat=True,
        trigger_message_id="same-message",
        trigger_create_time="2026-07-20T10:00:00+08:00",
        trigger_sender="Sender", trigger_text="hello",
    )
    with store._connect() as migrated:
        assert migrated.execute("pragma foreign_key_check").fetchall() == []


def test_dingtalk_message_operations_do_not_modify_same_identity_wechat_task(tmp_path):
    store = _store(tmp_path)
    common = dict(
        conversation_id="shared", conversation_title="Shared", single_chat=True,
        trigger_message_id="same", trigger_create_time="2026-07-20T10:00:00+08:00",
        trigger_sender="Sender", trigger_text="original",
    )
    assert store.enqueue_reply_task(channel="dingtalk", **common)
    assert store.enqueue_reply_task(channel="wechat", **common)

    assert store.update_pending_reply_task_trigger_for_message(
        "shared", "same", trigger_text="updated",
        trigger_message_json='{"updated":true}',
    ) == 1
    assert store.get_reply_task_for_message(
        "shared", "same", channel="dingtalk"
    ).trigger_text == "updated"
    assert store.get_reply_task_for_message(
        "shared", "same", channel="wechat"
    ).trigger_text == "original"

    dingtalk_task = store.claim_reply_tasks(limit=1, channel="dingtalk")[0]
    store.complete_reply_task(
        dingtalk_task.id,
        expected_execution_generation=dingtalk_task.execution_generation,
    )
    assert store.get_reply_task_for_message(
        "shared", "same", channel="dingtalk"
    ).status == "done"
    assert store.get_reply_task_for_message(
        "shared", "same", channel="wechat"
    ).status == "pending"


def test_dingtalk_pending_replacement_leaves_wechat_pending_tasks_untouched(tmp_path):
    store = _store(tmp_path)
    for channel in ("dingtalk", "wechat"):
        for message_id, created_at in (
            ("old", "2026-07-20T10:00:00+08:00"),
            ("quoted", "2026-07-20T10:01:00+08:00"),
        ):
            assert store.enqueue_reply_task(
                channel=channel, conversation_id="shared", conversation_title="Shared",
                single_chat=True, trigger_message_id=message_id,
                trigger_create_time=created_at, trigger_sender="Sender",
                trigger_text=message_id,
            )

    for channel in ("dingtalk", "wechat"):
        assert store.enqueue_reply_task(
            channel=channel, conversation_id="shared", conversation_title="Shared",
            single_chat=True, trigger_message_id="replace-old",
            trigger_create_time="2026-07-20T10:01:30+08:00",
            trigger_sender="Sender", trigger_text="replace-old",
        )

    assert store.replace_pending_single_chat_reply_task_trigger(
        conversation_id="shared", trigger_message_id="replacement",
        trigger_create_time="2026-07-20T10:02:00+08:00", trigger_sender="Sender",
        trigger_text="replacement", trigger_message_json="{}",
    ) == 1
    assert {
        task.trigger_message_id for task in store.list_reply_tasks(channel="wechat")
    } == {"old", "quoted", "replace-old"}
