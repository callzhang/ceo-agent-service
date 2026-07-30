from app.store import AutoReplyStore
from app.wechat import service
from app.wechat.accessibility import SendOutcome
from app.wechat.models import WechatReplyScope


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


def test_sender_round_reconciles_unknown_before_retrying(tmp_path):
    store = AutoReplyStore(tmp_path / "w.sqlite3")
    delivery = _seed(store)
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

    class Sender(FakeSender):
        def send(self, delivery, scope):
            events.append("sent")
            return super().send(delivery, scope)

    sender = Sender()

    assert service.process_ready_wechat_deliveries(
        store,
        sender,
        mode="auto",
        sender_enabled=True,
        reader=Reader(),
    ) == 1
    assert events == ["reconciled", "sent"]


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

    store.set_wechat_delivery_status(d.id, "sent")

    attempt = store.get_reply_attempt(attempt_id)
    assert attempt is not None
    assert attempt.send_status == "sent"


def test_unknown_delivery_status_fails_history_attempt(tmp_path):
    store = AutoReplyStore(tmp_path / "w.sqlite3"); d, attempt_id = _seed_with_attempt(store)

    store.set_wechat_delivery_status(d.id, "send_unknown", error="no_visible_confirmation")

    attempt = store.get_reply_attempt(attempt_id)
    assert attempt is not None
    assert attempt.send_status == "failed"
    assert attempt.send_error == "no_visible_confirmation"


def test_recall_uses_runner_capability_with_text(tmp_path):
    store = AutoReplyStore(tmp_path / "w.sqlite3"); d = _seed(store)

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
