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
    assert store.list_wechat_reply_scopes("acct-1") == [scope]


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
