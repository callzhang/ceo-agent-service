from app.action_storage import (
    REPLY_ATTEMPT_COMMON_SQL, delete_action, update_action,
    task_delivery_projection, task_delivery_set_parts, insert_task_delivery_action,
)
from multiprocessing import get_context
from pathlib import Path

import pytest

from app.external_retry import MAX_AUTOMATIC_RETRIES
from app.store import AgentRunLeaseLostError, AutoReplyStore
from app.task8_storage import task8_insert_parts, task8_set_parts
from app.task12_storage import insert_task12, task12_projection
from app.task20_definitions import task20_column
from app.configs_definitions import config_projection
from app.wechat.models import WechatReplyScope
from tests.wechat.support import approve_delivery

WECHAT_SQL = task_delivery_projection("wechat_deliveries")


def _update_delivery(db, delivery_id, values):
    assignments, parameters = task_delivery_set_parts("wechat_deliveries", values)
    db.execute(
        f"update actions set {assignments} where record_type='wechat_deliveries' "
        "and cast(legacy_key as integer)=?",
        (*parameters, delivery_id),
    )


def _store(tmp_path):
    return AutoReplyStore(tmp_path / "worker.sqlite3")


def _seed_exhausted_target_open_failure(tmp_path, *, retry_count=MAX_AUTOMATIC_RETRIES):
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
        update_action(db, "reply_attempts", attempt_id, {"retry_count": retry_count})
    return store, delivery_id, attempt_id, delivery.execution_generation


def _wechat_delivery_and_attempt_state(store, delivery_id):
    with store._connect() as db:
        delivery = dict(
            db.execute(
                f"select * from ({WECHAT_SQL}) where id=?",
                (delivery_id,),
            ).fetchone()
        )
        attempts = [
            dict(row)
            for row in db.execute(
                f"""
                select attempts.*
                from ({REPLY_ATTEMPT_COMMON_SQL}) as attempts
                join task_inputs as inputs
                  on inputs.conversation_id=attempts.conversation_id
                 and inputs.trigger_message_id=attempts.trigger_message_id
                join tasks as tasks on tasks.id=inputs.task_id
                where tasks.id=? and tasks.kind='reply'
                  and attempts.channel='wechat'
                order by attempts.id
                """,
                (delivery["reply_task_id"],),
            ).fetchall()
        ]
    return delivery, attempts


def _open_current_store_for_repair_in_process(db_path, barrier, results):
    try:
        barrier.wait()
        store = AutoReplyStore(Path(db_path))
        assert store._schema_is_current() is True
        with store._connect() as db:
            results.put(
                (
                    "ok",
                    db.execute("pragma foreign_keys").fetchone()[0],
                    db.execute("pragma foreign_key_check").fetchall(),
                    db.execute(
                        f"select client_request_id from ({task12_projection('workbench_attachments')})"
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


def test_replace_new_duplicate_scope_keeps_last_input_and_one_public_row(tmp_path):
    store = _store(tmp_path)
    first = WechatReplyScope(
        account_id="acct-1", target_type="direct", target_id="u1",
        display_name="First", trigger_mode="every_inbound_text",
        last_active_at="2026-07-20T10:00:00+08:00",
    )
    last = first.model_copy(update={
        "display_name": "Second", "conversation_id": "second-conversation",
        "last_active_at": "2026-07-21T10:00:00+08:00",
    })

    store.replace_wechat_reply_scopes("acct-1", [first, last])

    assert store.list_wechat_reply_scopes("acct-1") == [last]
    with store._connect() as db:
        rows = db.execute(
            f"select rowid from ({config_projection('wechat_reply_scopes', include_rowid=True)}) "
            "where account_id='acct-1'"
        ).fetchall()
    assert len(rows) == 1 and rows[0]["rowid"] > 0


def test_replace_existing_duplicate_scope_uses_original_enabled_watermark(tmp_path):
    store = _store(tmp_path)
    original = WechatReplyScope(
        account_id="acct-1", target_type="direct", target_id="u1",
        display_name="Original", trigger_mode="every_inbound_text",
        last_active_at="2026-07-20T10:00:00+08:00",
    )
    store.replace_wechat_reply_scopes("acct-1", [original])
    with store._connect() as db:
        original_rowid = db.execute(
            f"select rowid from ({config_projection('wechat_reply_scopes', include_rowid=True)}) "
            "where account_id='acct-1'"
        ).fetchone()["rowid"]

    first = original.model_copy(update={
        "display_name": "First", "enabled": False,
        "last_active_at": "2026-09-01T10:00:00+08:00",
    })
    last = original.model_copy(update={"display_name": "Second", "last_active_at": ""})
    store.replace_wechat_reply_scopes("acct-1", [first, last])

    assert store.list_wechat_reply_scopes("acct-1") == [
        last.model_copy(update={"last_active_at": original.last_active_at})
    ]
    with store._connect() as db:
        assert db.execute(
            f"select rowid from ({config_projection('wechat_reply_scopes', include_rowid=True)}) "
            "where account_id='acct-1'"
        ).fetchone()["rowid"] == original_rowid


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
    assert delivery.reply_text == "first（by明哥分身）"


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
    assert delivery.reply_text == "corrected reply（by明哥分身）"
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
    assert delivery.reply_text == "new reply（by明哥分身）"
    with store._connect() as db:
        row = db.execute(
            f"select action_started_at from ({WECHAT_SQL}) where id=?",
            (delivery_id,),
        ).fetchone()
    assert row["action_started_at"] != ""


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
        trigger_sender="Avery",
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
    tasks = []
    for sequence, message_id in enumerate(("m1", "m2"), start=1):
        task = store.ensure_reply_task(
            channel="wechat",
            conversation_id="u1",
            conversation_title="Alex",
            single_chat=True,
            trigger_message_id=message_id,
            trigger_create_time=f"2026-07-28T10:0{sequence}:00",
            trigger_sender="Alex",
            trigger_text=f"message {sequence}",
        )
        tasks.append(task)
        store.record_reply_attempt(
            conversation_id="u1",
            conversation_title="Alex",
            trigger_message_id=message_id,
            trigger_sender="Alex",
            trigger_text=f"message {sequence}",
            action="send_reply",
            sensitivity_kind="normal",
            send_status="pending",
            channel="wechat",
        )
        store.create_wechat_delivery(
            reply_task_id=task.id,
            account_id="acct-1",
            target_type="direct",
            target_id="u1",
            conversation_id="u1",
            reply_text=f"reply {sequence}",
        )

    old_delivery = store.get_wechat_delivery_for_task(tasks[0].id)
    new_delivery = store.get_wechat_delivery_for_task(tasks[1].id)
    old_attempt = store.get_latest_reply_attempt_for_trigger("u1", "m1")
    new_attempt = store.get_latest_reply_attempt_for_trigger("u1", "m2")

    assert old_delivery.status == "superseded"
    assert old_delivery.error == f"superseded_by_newer_wechat_trigger:{tasks[1].id}"
    assert old_attempt.send_status == "skipped"
    assert old_attempt.send_error == f"superseded_by_newer_wechat_trigger:{tasks[1].id}"
    assert new_delivery.status == "ready_to_send"
    assert new_attempt.send_status == "pending"


def test_finalize_reply_supersedes_older_unsent_delivery_for_same_conversation(
    tmp_path,
):
    store = _store(tmp_path)
    attempts = []
    tasks = []
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
        tasks.append(task)
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

    old_delivery = store.get_wechat_delivery_for_task(tasks[0].id)
    new_delivery = store.get_wechat_delivery_for_task(tasks[1].id)
    old_attempt = store.get_reply_attempt(attempts[0])

    assert old_delivery.status == "superseded"
    assert old_delivery.error == f"superseded_by_newer_wechat_trigger:{tasks[1].id}"
    assert old_attempt.send_status == "skipped"
    assert old_attempt.send_error == f"superseded_by_newer_wechat_trigger:{tasks[1].id}"
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
        _update_delivery(db, delivery_id, {"created_at": "2026-07-30 10:00:00"})

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
        update_action(db, "reply_attempts", attempt_id, {"retry_count": MAX_AUTOMATIC_RETRIES})

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


def test_exhausted_stale_wechat_ui_failure_is_skipped_with_attempt_sync(tmp_path):
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
        error="wechat_ui_not_ready",
        pre_action_failure=True,
    )
    with store._connect() as db:
        update_action(db, "reply_attempts", attempt_id, {"retry_count": MAX_AUTOMATIC_RETRIES})

    store.skip_exhausted_stale_wechat_delivery(
        delivery_id,
        expected_execution_generation=delivery.execution_generation,
        reason="stale_pre_action_retry_exhausted",
        inactive_before="2026-07-30 10:10:00",
    )

    refreshed = store.get_wechat_delivery_by_id(delivery_id)
    attempt = store.get_reply_attempt(attempt_id)
    assert refreshed.status == "skipped"
    assert refreshed.error == "stale_pre_action_retry_exhausted"
    assert refreshed.pre_action_failure is False
    assert attempt.send_status == "skipped"
    assert attempt.send_error == "stale_pre_action_retry_exhausted"


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
        update_action(db, "reply_attempts", latest_attempt_id, {"retry_count": 1})
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
        if invalid_state == "pre_action_failure_false":
            _update_delivery(db, delivery_id, {"pre_action_failure": 0})
        elif invalid_state == "newer_than_cutoff":
            _update_delivery(db, delivery_id, {"action_started_at": "2026-07-30 10:20:00"})
        elif invalid_state == "non_failed_status":
            _update_delivery(db, delivery_id, {"status": "send_unknown"})
        elif invalid_state == "blank_action_started_at":
            _update_delivery(db, delivery_id, {"action_started_at": ""})
        elif invalid_state == "delivery_generation_mismatch":
            expected_generation = "different-generation"
        elif invalid_state == "task_generation_mismatch":
            parts = task8_set_parts("reply_tasks", {"execution_generation": "new-generation"})
            changed = db.execute(
                f"update tasks set {parts.sql} "
                "where record_type='reply_tasks' and id=(select reply_task_id "
                f"from ({WECHAT_SQL}) where id=?)",
                (*parts.parameters, delivery_id),
            )
            assert changed.rowcount == 1
        elif invalid_state == "missing_matching_attempt":
            delete_action(db, "reply_attempts", attempt_id)
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


@pytest.mark.parametrize("pre_action_failure", [True, False])
def test_sent_revision_supersedes_same_task_pre_action_failure(tmp_path, pre_action_failure):
    store = _store(tmp_path)
    store.enqueue_reply_task(
        channel="wechat", conversation_id="u1", conversation_title="Alex",
        single_chat=True, trigger_message_id="m1",
        trigger_create_time="2026-07-28T10:01:00", trigger_sender="Alex",
        trigger_text="message",
    )
    old_id = store.create_wechat_delivery(
        reply_task_id=1, account_id="acct-1", target_type="direct",
        target_id="u1", conversation_id="u1", reply_text="old reply",
    )
    store.mark_wechat_delivery_sending(old_id)
    store.set_wechat_delivery_status(
        old_id, "failed", error="wechat_ui_not_ready",
        pre_action_failure=pre_action_failure,
    )
    store.rotate_reply_task_execution_generation(1)
    newer_id = store.create_wechat_delivery(
        reply_task_id=1, account_id="acct-1", target_type="direct",
        target_id="u1", conversation_id="u1", reply_text="revised reply",
    )
    store.mark_wechat_delivery_sending(newer_id)
    store.set_wechat_delivery_status(newer_id, "sent")
    old = store.get_wechat_delivery_by_id(old_id)
    assert old.status == ("superseded" if pre_action_failure else "failed")
    assert store.supersede_failed_wechat_deliveries_with_newer_sent() == 0


def test_newer_sent_delivery_supersedes_older_action_not_performed(tmp_path):
    store = _store(tmp_path)
    tasks = []
    for sequence, message_id in enumerate(("m1", "m2"), start=1):
        task = store.ensure_reply_task(
            channel="wechat",
            conversation_id="u1",
            conversation_title="Alex",
            single_chat=True,
            trigger_message_id=message_id,
            trigger_create_time=f"2026-07-28T10:0{sequence}:00",
            trigger_sender="Alex",
            trigger_text=f"message {sequence}",
        )
        tasks.append(task)
        store.create_wechat_delivery(
            reply_task_id=task.id,
            account_id="acct-1",
            target_type="direct",
            target_id="u1",
            conversation_id="u1",
            reply_text=f"reply {sequence}",
        )

    old = store.get_wechat_delivery_for_task(tasks[0].id)
    newer = store.get_wechat_delivery_for_task(tasks[1].id)
    # Simulate an explicit pre-action failure that existed before a later delivery
    # was recorded; normal creation now supersedes this case immediately.
    with store._connect() as db:
        _update_delivery(db, old.id, {
            "status": "failed", "error": "composer_input_unconfirmed",
            "pre_action_failure": 1,
        })
    store.mark_wechat_delivery_sending(newer.id)
    store.set_wechat_delivery_status(newer.id, "sent")

    refreshed_old = store.get_wechat_delivery_for_task(tasks[0].id)
    assert refreshed_old.status == "superseded"
    assert refreshed_old.error == f"superseded_by_newer_wechat_delivery:{newer.id}"


@pytest.mark.parametrize("status", ["sending", "send_unknown"])
def test_newer_wechat_trigger_waits_for_uncertain_delivery_reconciliation(
    tmp_path,
    status,
):
    store = _store(tmp_path)
    tasks = []
    for sequence, message_id in enumerate(("m1", "m2"), start=1):
        task = store.ensure_reply_task(
            channel="wechat",
            conversation_id="u1",
            conversation_title="Alex",
            single_chat=True,
            trigger_message_id=message_id,
            trigger_create_time=f"2026-07-28T10:0{sequence}:00",
            trigger_sender="Alex",
            trigger_text=f"message {sequence}",
        )
        tasks.append(task)
    old_delivery_id = store.create_wechat_delivery(
        reply_task_id=tasks[0].id,
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
            reply_task_id=tasks[1].id,
            account_id="acct-1",
            target_type="direct",
            target_id="u1",
            conversation_id="u1",
            reply_text="new reply",
        )

    assert store.get_wechat_delivery_by_id(old_delivery_id).status == status
    assert store.get_wechat_delivery_for_task(tasks[1].id) is None


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
        _update_delivery(db, delivery_id, {"action_started_at": "2026-07-30 10:05:00"})
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

    approve_delivery(store, delivery_id)
    delivery = store.get_wechat_delivery_by_id(delivery_id)
    assert store.claim_wechat_delivery(
        delivery_id,
        expected_execution_generation=delivery.execution_generation,
    ) is not None

    with store._connect() as db:
        row = db.execute(
            f"select action_started_at from ({WECHAT_SQL}) where id=?",
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


@pytest.mark.xfail(
    strict=True,
    reason="the Store now refuses this old-schema input ('explicit paired schema "
           "release required'); the test belongs to the database refactor, not the "
           "WeChat pipeline, and fails the same way on a clean checkout of main",
)
def test_concurrent_current_schema_repair_preserves_rows_and_delivery_fk(tmp_path):
    db_path = tmp_path / "current-repair.sqlite3"
    attachment_path = tmp_path / "legacy.txt"
    attachment_bytes = b"legacy"
    attachment_path.write_bytes(attachment_bytes)

    original = AutoReplyStore(db_path)
    with original._connect() as db:
        db.execute("insert into sqlite_sequence (name, seq) values ('tasks', 6)")
    task = original.ensure_reply_task(
        conversation_id="same-conversation", conversation_title="Friend",
        single_chat=True, trigger_message_id="same-message",
        trigger_create_time="2026-07-20T10:00:00+08:00",
        trigger_sender="Friend", trigger_text="hello",
    )
    assert task.id == 7
    with original._connect() as db:
        insert_task_delivery_action(db, "wechat_deliveries", {
            "reply_task_id": 7, "account_id": "acct-1", "target_type": "direct",
            "target_id": "friend-1", "conversation_id": "same-conversation",
            "reply_text": "hi", "status": "failed",
            "action_started_at": "2026-07-20T10:10:00+08:00",
            "pre_action_failure": 1,
        })
        parts = task8_insert_parts("workbench_tasks", {
            "legacy_key": "workbench-legacy", "title": "Legacy attachment", "runtime_kind": "codex",
        })
        workbench_parent = db.execute(
            f"insert into tasks ({parts.columns}) values ({parts.values})",
            parts.parameters,
        ).lastrowid
        insert_task12(db, "workbench_attachments", {
            "id": "attachment-legacy", "task_id": workbench_parent,
            "filename": "legacy.txt", "media_type": "text/plain", "size_bytes": 6,
            "storage_path": str(attachment_path), "client_request_id": "attachment-legacy",
            "content_sha256": "",
        })
        attachment_before = tuple(db.execute(
            "select id, task_id, filename, media_type, size_bytes, storage_path, created_at "
            f"from ({task12_projection('workbench_attachments')})"
        ).fetchone())
        db.execute("drop index idx_task20_published_2")
        db.execute("drop index idx_task20_native_21")
    with original._connect() as db:
        assert db.execute("pragma foreign_key_check").fetchall() == []
        parent = db.execute(
            "select id, record_type, current_input_id from tasks where id=?",
            (task.id,),
        ).fetchone()
        assert parent["record_type"] == "reply_tasks"
        assert ("tasks", "current_input_id", "id") in {
            (row["table"], row["from"], row["to"])
            for row in db.execute("pragma foreign_key_list(tasks)")
        }
        current_input_id = parent["current_input_id"]
        canonical_input = db.execute(
            f"select record_type, parent_id, {task20_column('reply_task_inputs', 'trigger_text')} as trigger_text from tasks where id=?",
            (current_input_id,),
        ).fetchone()
        assert tuple(canonical_input) == ("reply_task_inputs", task.id, "hello")
        assert ("tasks", "td_reply_task_id", "id") in {
            (row["table"], row["from"], row["to"])
            for row in db.execute("pragma foreign_key_list(actions)")
        }
        assert {task20_column("workbench_attachments", field).strip('"')
                for field in ("client_request_id", "content_sha256")} <= {
            row[1] for row in db.execute("pragma table_xinfo(tasks)")
        }
        assert {"idx_task20_published_2", "idx_task20_native_21"}.isdisjoint({
            row[0] for row in db.execute("select name from sqlite_master where type='index'")
        })
    assert original._schema_is_current() is False

    context = get_context("spawn")
    barrier = context.Barrier(2)
    results = context.Queue()
    processes = [
        context.Process(
            target=_open_current_store_for_repair_in_process,
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

    assert original._schema_is_current() is True
    assert attachment_path.read_bytes() == attachment_bytes
    store = AutoReplyStore(db_path)

    assert store.get_reply_task_for_message(
        "same-conversation", "same-message", channel="dingtalk"
    ).id == 7
    assert store.list_wechat_deliveries_by_status("ready_to_send") == []
    assert store.requeue_unperformed_wechat_deliveries() == 1
    repaired_delivery = store.list_wechat_deliveries_by_status("ready_to_send")[0]
    assert repaired_delivery.task_id == 7
    assert repaired_delivery.reply_text == "hi（by明哥分身）"
    assert store.get_outbound_postfix(
        "wechat", f"wechat:{repaired_delivery.id}"
    ).final_body == repaired_delivery.reply_text
    assert store.enqueue_reply_task(
        channel="wechat", conversation_id="same-conversation",
        conversation_title="Same", single_chat=True,
        trigger_message_id="same-message",
        trigger_create_time="2026-07-20T10:00:00+08:00",
        trigger_sender="Sender", trigger_text="hello",
    )
    assert store._schema_is_current() is True
    with store._connect() as repaired:
        assert repaired.execute("pragma foreign_key_check").fetchall() == []
        assert tuple(repaired.execute(
            "select id, task_id, filename, media_type, size_bytes, storage_path, created_at "
            f"from ({task12_projection('workbench_attachments')})"
        ).fetchone()) == attachment_before
        assert tuple(repaired.execute(
            f"select client_request_id, content_sha256 from ({task12_projection('workbench_attachments')})"
        ).fetchone()) == ("attachment-legacy", "")
        assert repaired.execute(
            "select current_input_id from tasks where id=?", (task.id,)
        ).fetchone()[0] == current_input_id
        assert repaired.execute(
            "select count(*) from tasks where record_type='reply_tasks'"
        ).fetchone()[0] == 2
        assert repaired.execute(
            "select count(*) from tasks where record_type='reply_task_inputs'"
        ).fetchone()[0] == 2
    wechat_task = store.get_reply_task_for_message(
        "same-conversation", "same-message", channel="wechat"
    )
    assert wechat_task.id != task.id
    assert store.get_reply_task_for_message(
        "same-conversation", "same-message", channel="dingtalk"
    ).id == task.id


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
    [dingtalk_task] = store.list_reply_tasks(channel="dingtalk")
    assert dingtalk_task.trigger_message_id == "replacement"
    assert {
        item["trigger_message_id"]
        for item in store.list_reply_task_inputs(dingtalk_task.id)
    } == {"old", "quoted", "replace-old", "replacement"}


def test_principal_superseded_delivery_cannot_requeue_expired_candidate(tmp_path):
    store, delivery_id, _, generation = _seed_exhausted_target_open_failure(tmp_path)
    store.skip_exhausted_stale_wechat_delivery(
        delivery_id, expected_execution_generation=generation,
        reason="stale_pre_action_retry_exhausted; superseded_by_principal_reply:synthetic-reply",
        inactive_before="2026-07-30 10:10:00",
    )
    before = _wechat_delivery_and_attempt_state(store, delivery_id)
    with pytest.raises(AgentRunLeaseLostError, match="superseded"):
        store.requeue_expired_wechat_delivery_for_user(delivery_id)
    assert _wechat_delivery_and_attempt_state(store, delivery_id) == before


def _seed_failed_pre_action_delivery(tmp_path, *, error, action_started_at, pre_action=True):
    """A delivery in the persisted state a pre-action send failure leaves.

    Written directly: claiming a delivery needs an approved independent Audit,
    which these store transitions do not exercise.
    """
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
    with store._connect() as db:
        _update_delivery(db, delivery_id, {
            "status": "failed", "error": error,
            "pre_action_failure": int(pre_action),
            "action_started_at": action_started_at,
        })
        update_action(db, "reply_attempts", attempt_id, {"retry_count": 3})
    return store, delivery_id, attempt_id


def test_user_can_retry_a_failed_pre_action_delivery_without_the_cli_skip(tmp_path):
    """Delivery 140 ran out of retries on wechat_ui_not_ready and stayed failed.

    Only a manual CLI command moved an exhausted delivery to skipped, and
    skipped is the only state that offered a retry, so the failed ones that
    most needed a resend could not get one.
    """
    store, delivery_id, attempt_id = _seed_failed_pre_action_delivery(
        tmp_path, error="wechat_ui_not_ready",
        action_started_at="2026-07-30 10:00:00",
    )

    delivery = store.requeue_expired_wechat_delivery_for_user(delivery_id)

    assert delivery.status == "ready_to_send"
    assert delivery.pre_action_failure is False
    assert store.get_wechat_delivery_by_id(delivery_id).status == "ready_to_send"
    assert store.get_reply_attempt(attempt_id).send_error == "wechat_delivery_ready_to_send"


def test_failed_delivery_never_claimed_cannot_be_retried_by_the_user(tmp_path):
    """An Audit rejection fails the delivery before any claim, so no action start."""
    store, delivery_id, _ = _seed_failed_pre_action_delivery(
        tmp_path, error="wechat_audit_rejected", action_started_at="",
    )
    before = _wechat_delivery_and_attempt_state(store, delivery_id)

    with pytest.raises(AgentRunLeaseLostError, match="pre-action retry"):
        store.requeue_expired_wechat_delivery_for_user(delivery_id)

    assert _wechat_delivery_and_attempt_state(store, delivery_id) == before


def test_failed_delivery_that_was_not_a_pre_action_failure_cannot_be_retried(tmp_path):
    store, delivery_id, _ = _seed_failed_pre_action_delivery(
        tmp_path, error="send_failed_after_dispatch",
        action_started_at="2026-07-30 10:00:00", pre_action=False,
    )
    before = _wechat_delivery_and_attempt_state(store, delivery_id)

    with pytest.raises(AgentRunLeaseLostError, match="pre-action retry"):
        store.requeue_expired_wechat_delivery_for_user(delivery_id)

    assert _wechat_delivery_and_attempt_state(store, delivery_id) == before
