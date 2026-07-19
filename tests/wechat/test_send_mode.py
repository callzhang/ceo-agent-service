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
        target_id="u9", conversation_id="u9", reply_text="收到")
    return store.get_wechat_delivery_for_task(task_id)


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


def test_approve_sends_specific_pending(tmp_path):
    store = AutoReplyStore(tmp_path / "w.sqlite3"); d = _seed(store)
    sender = FakeSender()
    assert service.approve_wechat_delivery(store, sender, d.id) == "sent"
    assert sender.sent == [d.id]


def test_reject_marks_failed_without_send(tmp_path):
    store = AutoReplyStore(tmp_path / "w.sqlite3"); d = _seed(store)
    service.reject_wechat_delivery(store, d.id)
    assert store.get_wechat_delivery_for_task(1).status == "failed"
    assert service.pending_wechat_deliveries(store) == []


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
