import sqlite3

from app.store import AutoReplyStore
from app.wechat.models import WechatReplyScope


def _store(tmp_path):
    return AutoReplyStore(tmp_path / "worker.sqlite3")


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
        """
    )
    db.close()

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

    assert store.complete_reply_task_for_message("shared", "same") == 1
    assert store.get_reply_task_for_message(
        "shared", "same", channel="dingtalk"
    ).status == "done"
    assert store.get_reply_task_for_message(
        "shared", "same", channel="wechat"
    ).status == "pending"


def test_dingtalk_supersede_operations_leave_wechat_pending_tasks_untouched(tmp_path):
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

    completed = store.complete_unfinished_reply_tasks_before_trigger(
        conversation_id="shared", trigger_create_time="2026-07-20T10:00:30+08:00",
        exclude_task_id=-1,
    )
    assert [task.channel for task in completed] == ["dingtalk"]
    completed = store.complete_unfinished_reply_tasks_for_messages(
        conversation_id="shared", trigger_message_ids=["quoted"], exclude_task_id=-1,
    )
    assert [task.channel for task in completed] == ["dingtalk"]
    assert all(
        task.status == "pending"
        for task in store.list_reply_tasks(channel="wechat")
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
