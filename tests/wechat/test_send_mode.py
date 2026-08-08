from app.store import AutoReplyStore
from app.wechat import service
from app.wechat.accessibility import AccessibilityResult, SendOutcome, WechatSender
from app.wechat.models import WechatAccount, WechatMessage, WechatReplyScope


def _seed(store, *, binding="verified", task_id=1):
    store.replace_wechat_reply_scopes("acct-1", [WechatReplyScope(
        account_id="acct-1", target_type="direct", target_id="u9",
        conversation_id="u9", display_name="Alex",
        trigger_mode="every_inbound_text", binding_status=binding)])
    store.enqueue_reply_task(
        channel="wechat", conversation_id="u9", conversation_title="Alex",
        single_chat=True, trigger_message_id=f"m{task_id}",
        trigger_create_time="2026-07-18T10:00:00", trigger_sender="Alex", trigger_text="hi")
    store.create_wechat_delivery(
        reply_task_id=task_id, account_id="acct-1", target_type="direct",
        target_id="u9", conversation_id="u9", reply_text="收到",
        evidence={"trigger_text": "hi"})
    return store.get_wechat_delivery_for_task(task_id)


def _seed_with_attempt(store, *, task_id=1):
    delivery = _seed(store, task_id=task_id)
    attempt_id = store.record_reply_attempt(
        conversation_id="u9",
        conversation_title="Alex",
        trigger_message_id=f"m{task_id}",
        trigger_sender="Alex",
        trigger_text="hi",
        action="send_reply",
        sensitivity_kind="normal",
        send_status="pending",
        channel="wechat",
    )
    return delivery, attempt_id


class FakeSender:
    def __init__(self):
        self.sent = []

    def send(self, delivery, scope):
        self.sent.append(delivery.id)
        return SendOutcome("sent")


def test_confirm_mode_holds_deliveries(tmp_path):
    store = AutoReplyStore(tmp_path / "w.sqlite3"); _seed(store)
    sender = FakeSender()
    assert service.process_ready_wechat_deliveries(store, sender, mode="confirm", sender_enabled=True) == 0
    assert sender.sent == []
    assert len(service.pending_wechat_deliveries(store)) == 1


def test_sender_disabled_holds_even_in_auto(tmp_path):
    store = AutoReplyStore(tmp_path / "w.sqlite3"); _seed(store)
    sender = FakeSender()
    assert service.process_ready_wechat_deliveries(store, sender, mode="auto", sender_enabled=False) == 0
    assert sender.sent == []


def test_auto_mode_sends(tmp_path):
    store = AutoReplyStore(tmp_path / "w.sqlite3"); _seed(store)
    sender = FakeSender()
    assert service.process_ready_wechat_deliveries(store, sender, mode="auto", sender_enabled=True) == 1
    assert sender.sent == [1]


def test_auto_mode_refreshes_direct_binding_text_before_send(tmp_path):
    store = AutoReplyStore(tmp_path / "w.sqlite3")
    _seed(store)
    calls = []
    account = WechatAccount(
        account_id="acct-1",
        display_name="Derek",
        self_user_id="self",
        account_dir="/account",
        db_dir="/db",
        app_version="4.0",
    )

    class Reader:
        def read_messages(self, requested_account, **kwargs):
            assert requested_account == account
            assert kwargs["conversation_id"] == "u9"
            assert kwargs["conversation_type"] == "direct"
            assert kwargs["order"] == "newest"
            return [WechatMessage(
                account_id="acct-1",
                conversation_id="u9",
                message_id="m2",
                sender_id="u9",
                sender_display_name="Alex",
                conversation_type="direct",
                direction="inbound",
                sent_at="2026-07-18T10:01:00",
                kind="text",
                text="new message during reply delay",
                source_version="4.0",
            )]

    class Runner:
        @staticmethod
        def send(label, reply_text, *, search_query=None, expected_recent_text=None):
            calls.append((label, reply_text, search_query, expected_recent_text))
            return AccessibilityResult(True, True)

    sender = WechatSender(store, Runner())

    assert service.process_ready_wechat_deliveries(
        store,
        sender,
        mode="auto",
        sender_enabled=True,
        reader=Reader(),
        account=account,
    ) == 1
    assert calls == [("Alex", "收到", None, "new message during reply delay")]


def test_auto_mode_keeps_delivery_pending_when_binding_refresh_fails(tmp_path):
    store = AutoReplyStore(tmp_path / "w.sqlite3")
    delivery = _seed(store)
    account = WechatAccount(
        account_id="acct-1", display_name="Derek", self_user_id="self",
        account_dir="/account", db_dir="/db", app_version="4.0",
    )

    class Reader:
        @staticmethod
        def read_messages(*_args, **_kwargs):
            raise RuntimeError("reader temporarily unavailable")

    sender = FakeSender()

    assert service.process_ready_wechat_deliveries(
        store, sender, mode="auto", sender_enabled=True,
        reader=Reader(), account=account,
    ) == 0
    assert sender.sent == []
    assert store.get_wechat_delivery_by_id(delivery.id).status == "ready_to_send"


def test_auto_mode_keeps_delivery_pending_when_latest_message_has_no_text(tmp_path):
    store = AutoReplyStore(tmp_path / "w.sqlite3")
    delivery = _seed(store)
    account = WechatAccount(
        account_id="acct-1", display_name="Derek", self_user_id="self",
        account_dir="/account", db_dir="/db", app_version="4.0",
    )

    class Reader:
        @staticmethod
        def read_messages(*_args, **_kwargs):
            return [WechatMessage(
                account_id="acct-1", conversation_id="u9", message_id="image-1",
                sender_id="u9", sender_display_name="Alex",
                conversation_type="direct", direction="inbound",
                sent_at="2026-07-18T10:01:00", kind="image", text="",
                source_version="4.0",
            )]

    sender = FakeSender()

    assert service.process_ready_wechat_deliveries(
        store, sender, mode="auto", sender_enabled=True,
        reader=Reader(), account=account,
    ) == 0
    assert sender.sent == []
    assert store.get_wechat_delivery_by_id(delivery.id).status == "ready_to_send"


def test_auto_mode_skips_invisible_records_when_refreshing_binding_text(tmp_path):
    store = AutoReplyStore(tmp_path / "w.sqlite3")
    _seed(store)
    account = WechatAccount(
        account_id="acct-1", display_name="Derek", self_user_id="self",
        account_dir="/account", db_dir="/db", app_version="4.0",
    )

    class Reader:
        @staticmethod
        def read_messages(*_args, **kwargs):
            assert kwargs["limit"] > 1
            common = {
                "account_id": "acct-1", "conversation_id": "u9",
                "sender_id": "u9", "sender_display_name": "Alex",
                "conversation_type": "direct", "direction": "inbound",
                "source_version": "4.0",
            }
            return [
                WechatMessage(
                    **common, message_id="internal-1",
                    sent_at="2026-07-18T10:01:01", kind="unknown", text="",
                ),
                WechatMessage(
                    **common, message_id="m2",
                    sent_at="2026-07-18T10:01:00", kind="text",
                    text="newest visible message",
                ),
            ]

    class Runner:
        calls = []

        @classmethod
        def send(cls, label, reply_text, *, search_query=None, expected_recent_text=None):
            cls.calls.append(expected_recent_text)
            return AccessibilityResult(True, True)

    assert service.process_ready_wechat_deliveries(
        store, WechatSender(store, Runner()), mode="auto", sender_enabled=True,
        reader=Reader(), account=account,
    ) == 1
    assert Runner.calls == ["newest visible message"]


def test_sender_round_does_not_retry_when_send_result_is_unknown(tmp_path):
    store = AutoReplyStore(tmp_path / "w.sqlite3")
    delivery = _seed(store)
    store.mark_wechat_delivery_sending(delivery.id)
    store.set_wechat_delivery_status(
        delivery.id,
        "send_unknown",
        error="sender_execution_interrupted",
    )
    events = []

    class Reader:
        account = object()

        def read_messages(self, *_args, **_kwargs):
            events.append("reconciled")
            return []

    sender = FakeSender()

    assert service.process_ready_wechat_deliveries(
        store,
        sender,
        mode="auto",
        sender_enabled=True,
        reader=Reader(),
    ) == 0
    assert events == ["reconciled"]
    assert sender.sent == []
    assert store.get_wechat_delivery_by_id(delivery.id).status == "send_unknown"


def test_auto_mode_holds_delivery_while_sender_session_is_locked(tmp_path):
    store = AutoReplyStore(tmp_path / "w.sqlite3")
    delivery = _seed(store)

    class Runner:
        @staticmethod
        def preflight():
            return "screen_locked"

    sender = FakeSender()
    sender.runner = Runner()

    assert service.process_ready_wechat_deliveries(
        store, sender, mode="auto", sender_enabled=True
    ) == 0
    assert sender.sent == []
    assert store.get_wechat_delivery_for_task(delivery.task_id).status == "ready_to_send"


def test_auto_mode_verifies_exact_direct_target_before_send(tmp_path):
    store = AutoReplyStore(tmp_path / "w.sqlite3"); _seed(store, binding="unverified")

    class Runner:
        def open_and_identify(
            self, label, *, search_query=None, expected_recent_text=None,
        ):
            assert (label, search_query, expected_recent_text) == (
                "Alex", None, "hi",
            )
            return "Alex"

    class Sender(FakeSender):
        def __init__(self):
            super().__init__()
            self.runner = Runner()

        def send(self, delivery, scope):
            assert scope.binding_status == "verified"
            return super().send(delivery, scope)

    sender = Sender()

    assert service.process_ready_wechat_deliveries(
        store, sender, mode="auto", sender_enabled=True
    ) == 1
    assert sender.sent == [1]
    assert store.get_wechat_reply_scope(
        "acct-1", "direct", "u9"
    ).binding_status == "verified"


def test_approve_sends_specific_pending(tmp_path):
    store = AutoReplyStore(tmp_path / "w.sqlite3"); d = _seed(store)
    sender = FakeSender()
    assert service.approve_wechat_delivery(store, sender, d.id) == "sent"
    assert sender.sent == [d.id]


def test_reject_marks_failed_without_send(tmp_path):
    store = AutoReplyStore(tmp_path / "w.sqlite3"); d, attempt_id = _seed_with_attempt(store)
    service.reject_wechat_delivery(store, d.id)
    assert store.get_wechat_delivery_for_task(1).status == "failed"
    attempt = store.get_reply_attempt(attempt_id)
    assert attempt is not None
    assert attempt.send_status == "skipped"
    assert attempt.send_error == "user_rejected"
    assert service.pending_wechat_deliveries(store) == []


def test_delivery_status_updates_history_attempt(tmp_path):
    store = AutoReplyStore(tmp_path / "w.sqlite3"); d, attempt_id = _seed_with_attempt(store)

    store.mark_wechat_delivery_sending(d.id)
    store.set_wechat_delivery_status(d.id, "sent")

    attempt = store.get_reply_attempt(attempt_id)
    assert attempt is not None
    assert attempt.send_status == "sent"


def test_unknown_delivery_status_fails_history_attempt(tmp_path):
    store = AutoReplyStore(tmp_path / "w.sqlite3"); d, attempt_id = _seed_with_attempt(store)

    store.mark_wechat_delivery_sending(d.id)
    store.set_wechat_delivery_status(d.id, "send_unknown", error="no_visible_confirmation")

    attempt = store.get_reply_attempt(attempt_id)
    assert attempt is not None
    assert attempt.send_status == "failed"
    assert attempt.send_error == "no_visible_confirmation"


def test_auto_mode_retries_delivery_when_no_wechat_action_was_performed(tmp_path):
    store = AutoReplyStore(tmp_path / "w.sqlite3"); d, attempt_id = _seed_with_attempt(store)
    store.mark_wechat_delivery_sending(d.id)
    store.set_wechat_delivery_status(d.id, "failed", error="action_not_performed")

    class RetryingSender(FakeSender):
        def send(self, delivery, scope):
            self.sent.append(delivery.id)
            store.mark_wechat_delivery_sending(delivery.id)
            store.set_wechat_delivery_status(delivery.id, "sent")
            return SendOutcome("sent")

    sender = RetryingSender()

    assert service.process_ready_wechat_deliveries(
        store, sender, mode="auto", sender_enabled=True
    ) == 1
    assert sender.sent == [d.id]
    assert store.get_wechat_delivery_for_task(1).status == "sent"
    assert store.get_reply_attempt(attempt_id).send_status == "sent"


def test_unperformed_wechat_delivery_is_requeued_only_once(tmp_path):
    store = AutoReplyStore(tmp_path / "w.sqlite3"); d, attempt_id = _seed_with_attempt(store)
    store.mark_wechat_delivery_sending(d.id)
    store.set_wechat_delivery_status(d.id, "failed", error="action_not_performed")

    assert store.requeue_unperformed_wechat_deliveries() == 1
    assert store.get_reply_attempt(attempt_id).retry_count == 1

    store.mark_wechat_delivery_sending(d.id)
    store.set_wechat_delivery_status(d.id, "failed", error="action_not_performed")

    assert store.requeue_unperformed_wechat_deliveries() == 0
    assert store.get_wechat_delivery_for_task(1).status == "failed"
    assert store.get_reply_attempt(attempt_id).retry_count == 1


def test_recovery_keeps_missing_persisted_send_receipt_unknown(tmp_path):
    store = AutoReplyStore(tmp_path / "w.sqlite3")
    delivery, attempt_id = _seed_with_attempt(store)
    store.mark_wechat_delivery_sending(delivery.id)

    class Reader:
        account = object()

        def read_messages(self, *_args, **_kwargs):
            return []

    service.recover_before_sender(store, Reader())

    refreshed = store.get_wechat_delivery_for_task(1)
    attempt = store.get_reply_attempt(attempt_id)
    assert refreshed.status == "send_unknown"
    assert attempt is not None
    assert attempt.retry_count == 0


def test_unperformed_legacy_delivery_without_attempt_is_requeued_once(tmp_path):
    store = AutoReplyStore(tmp_path / "w.sqlite3")
    delivery = _seed(store)
    store.mark_wechat_delivery_sending(delivery.id)
    store.set_wechat_delivery_status(
        delivery.id, "failed", error="action_not_performed"
    )

    assert store.requeue_unperformed_wechat_deliveries() == 1
    assert store.get_wechat_delivery_for_task(1).status == "ready_to_send"
    attempt = store.get_latest_reply_attempt_for_trigger("u9", "m1")
    assert attempt is not None
    assert attempt.channel == "wechat"
    assert attempt.retry_count == 1

    store.mark_wechat_delivery_sending(delivery.id)
    store.set_wechat_delivery_status(
        delivery.id, "failed", error="action_not_performed"
    )
    assert store.requeue_unperformed_wechat_deliveries() == 0


def test_recall_uses_runner_capability_with_text(tmp_path):
    store = AutoReplyStore(tmp_path / "w.sqlite3"); d = _seed(store)
    store.mark_wechat_delivery_sending(d.id)
    store.set_wechat_delivery_status(d.id, "sent")

    class Runner:
        def __init__(self):
            self.arg = None

        def recall_last_outbound(self, text):
            self.arg = text
            return True

    runner = Runner()
    assert service.recall_wechat_delivery(store, runner, d.id, "收到") is True
    assert runner.arg == "收到"
    assert store.get_wechat_delivery_for_task(1).status == "failed"


def test_recall_noop_when_runner_lacks_capability(tmp_path):
    store = AutoReplyStore(tmp_path / "w.sqlite3"); d = _seed(store)
    assert service.recall_wechat_delivery(store, object(), d.id, "收到") is False


def _scope(binding="unverified"):
    return WechatReplyScope(
        account_id="a", target_type="group", target_id="g@chatroom",
        conversation_id="g@chatroom", display_name="G",
        trigger_mode="mention_current_account", binding_status=binding)


class _IdRunner:
    def __init__(self, title):
        self.title = title
        self.calls = []

    def open_and_identify(
        self, label, *, search_query=None, expected_recent_text=None,
    ):
        self.calls.append((label, search_query, expected_recent_text))
        return self.title


def test_verify_binding_verified_when_unique_and_ui_matches(tmp_path):
    store = AutoReplyStore(tmp_path / "w.sqlite3"); store.replace_wechat_reply_scopes("a", [_scope()])
    st = service.verify_wechat_binding(store, _scope(), runner=_IdRunner("G"), is_unique=True)
    assert st == "verified"
    assert store.get_wechat_reply_scope("a", "group", "g@chatroom").binding_status == "verified"


def test_verify_binding_conflict_when_not_unique(tmp_path):
    store = AutoReplyStore(tmp_path / "w.sqlite3"); store.replace_wechat_reply_scopes("a", [_scope()])
    st = service.verify_wechat_binding(store, _scope(), runner=_IdRunner("G"), is_unique=False)
    assert st == "conflict"


def test_verify_binding_unverified_when_ui_mismatch(tmp_path):
    store = AutoReplyStore(tmp_path / "w.sqlite3"); store.replace_wechat_reply_scopes("a", [_scope()])
    st = service.verify_wechat_binding(store, _scope(), runner=_IdRunner("OTHER GROUP"), is_unique=True)
    assert st == "unverified"


def test_verify_duplicate_direct_name_by_unique_target_id(tmp_path):
    store = AutoReplyStore(tmp_path / "w.sqlite3")
    scope = WechatReplyScope(
        account_id="a", target_type="direct", target_id="melody115",
        conversation_id="melody115", display_name="Melody",
        trigger_mode="every_inbound_text",
    )
    store.replace_wechat_reply_scopes("a", [scope])
    runner = _IdRunner("Melody")

    status = service.verify_wechat_binding(
        store, scope, runner=runner, is_unique=False,
        expected_recent_text="latest inbound",
    )

    assert status == "verified"
    assert runner.calls == [("Melody", None, "latest inbound")]
    persisted = store.get_wechat_reply_scope("a", "direct", "melody115")
    assert persisted.binding_evidence["recent_text_sha256"]
    assert "latest inbound" not in str(persisted.binding_evidence)
