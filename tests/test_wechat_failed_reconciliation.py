from types import SimpleNamespace

from app import wechat_reconciliation
from app.system_executor import ActionOutcome
from app.system_action_handlers import WechatSendMessageHandler


def _delivery(*, status="sent", body="reply", started="2026-10-26 19:53:52"):
    return SimpleNamespace(
        id=146,
        task_id=417557,
        account_id="account-1",
        target_type="direct",
        target_id="melody115",
        conversation_id="melody115",
        reply_text=body,
        action_started_at=started,
        execution_generation="initial",
        status=status,
        evidence={
            "standard_candidate_id": "399693",
            "standard_review_id": "1616",
            "standard_action_identity": "wechat:melody115:clarify-codex-error:2026-10-26",
        },
        error="",
        pre_action_failure=False,
    )


class _Store:
    def __init__(self, delivery):
        self.delivery = delivery

    def get_wechat_delivery_for_task(self, task_id):
        assert task_id == self.delivery.task_id
        return self.delivery


class _Reader:
    def __init__(self, messages):
        self.messages = messages
        self.calls = []

    def read_messages(self, account, **kwargs):
        self.calls.append((account.account_id, kwargs))
        return self.messages


class _PreparedHandler(WechatSendMessageHandler):
    def _task_and_prepared(self, action, candidate):
        return SimpleNamespace(), SimpleNamespace(final_body="reply")


def _message(*, body="reply", message_id="out-1", sent_at="2026-10-26T19:54:10+00:00",
             conversation_id="melody115", direction="outbound", account_id="account-1"):
    return SimpleNamespace(
        account_id=account_id,
        conversation_id=conversation_id,
        conversation_type="direct",
        message_id=message_id,
        direction=direction,
        sent_at=sent_at,
        text=body,
    )


def test_sent_wechat_delivery_reconciliation_requires_exact_reader_match_without_sender():
    delivery = _delivery()
    reader = _Reader([_message()])
    account = SimpleNamespace(account_id="account-1", self_user_id="self")

    def forbidden_sender():
        raise AssertionError("reconciliation must never construct or call Sender")

    handler = _PreparedHandler(
        _Store(delivery),
        reader_factory=lambda: reader,
        account_factory=lambda: account,
        sender_factory=forbidden_sender,
    )
    result = handler.reconcile(
        SimpleNamespace(
            payload={"text": "reply"},
            action_identity="wechat:melody115:clarify-codex-error:2026-10-26",
        ),
        action_key="wechat:melody115:clarify-codex-error:2026-10-26",
        candidate={
            "id": 399693, "candidate_id": 399693, "review_id": 1616,
            "task_id": 417557, "execution_generation": "initial",
        },
    )

    assert result.status == "verified"
    assert result.provider_result["delivery_id"] == 146
    assert result.provider_result["readback"]["message_id"] == "out-1"
    assert reader.calls == [(
        "account-1",
        {
            "conversation_id": "melody115",
            "conversation_type": "direct",
            "since": "2026-10-26T19:53:52+00:00",
            "limit": 200,
            "order": "newest",
        },
    )]


def test_sent_wechat_delivery_reconciliation_rejects_nonunique_or_mismatched_readback():
    delivery = _delivery()
    reader = _Reader([_message(message_id="out-1"), _message(message_id="out-2")])
    handler = _PreparedHandler(
        _Store(delivery),
        reader_factory=lambda: reader,
        account_factory=lambda: SimpleNamespace(account_id="account-1", self_user_id="self"),
        sender_factory=lambda: (_ for _ in ()).throw(
            AssertionError("reconciliation must not send")
        ),
    )

    result = handler.reconcile(
        SimpleNamespace(payload={"text": "reply"}, action_identity="wechat:melody115:clarify-codex-error:2026-10-26"), action_key="key",
        candidate={
            "id": 399693, "candidate_id": 399693, "review_id": 1616,
            "task_id": 417557, "execution_generation": "initial",
        },
    )

    assert result.status == "uncertain"
    assert result.provider_result["reason"] == "wechat_readback_not_unique"


def test_sent_wechat_delivery_reconciliation_requires_a_complete_readback_window():
    delivery = _delivery()
    reader = _Reader([
        _message(),
        *[_message(body=f"unrelated-{index}", message_id=f"other-{index}")
          for index in range(199)],
    ])
    handler = _PreparedHandler(
        _Store(delivery),
        reader_factory=lambda: reader,
        account_factory=lambda: SimpleNamespace(account_id="account-1", self_user_id="self"),
        sender_factory=lambda: (_ for _ in ()).throw(
            AssertionError("reconciliation must not send")
        ),
    )

    result = handler.reconcile(
        SimpleNamespace(payload={"text": "reply"}, action_identity="wechat:melody115:clarify-codex-error:2026-10-26"),
        action_key="key",
        candidate={
            "id": 399693, "candidate_id": 399693, "review_id": 1616,
            "task_id": 417557, "execution_generation": "initial",
        },
    )

    assert result.status == "uncertain"
    assert result.provider_result["reason"] == "wechat_readback_window_incomplete"
    assert result.provider_result["match_count"] == 1
    assert result.provider_result["window_complete"] is False


def test_retry_api_path_settles_current_sent_task_without_constructing_sender(monkeypatch):
    action = SimpleNamespace(
        capability="wechat", operation="send_message",
        action_identity="wechat:melody115:clarify-codex-error:2026-10-26",
        payload={"text": "reply"},
        target={
            "account_id": "account-1", "target_type": "direct",
            "target_id": "melody115", "conversation_id": "melody115",
        },
    )
    task = SimpleNamespace(
        id=417557, channel="wechat", status="failed", execution_generation="initial",
        conversation_id="melody115",
    )
    review = {
        "id": 399693, "candidate_id": 399693, "review_id": 1616,
        "task_id": 417557, "execution_generation": "initial",
        "audit_run_id": 1616, "decision": "approve", "candidate_json": "{}",
    }
    delivery = _delivery()

    class Store(_Store):
        def get_reply_attempt(self, attempt_id):
            assert attempt_id == 16128
            return SimpleNamespace(
                channel="wechat", send_status="failed", trigger_message_id="trigger-1",
                conversation_id="melody115", agent_run_id=1616,
            )

        def get_reply_task_by_trigger_message_id(self, trigger_message_id, *, channel):
            assert trigger_message_id == "trigger-1" and channel == "wechat"
            return task

        def get_reply_task(self, task_id):
            assert task_id == 417557
            return task

        def current_reviewed_candidate(self, task_id, generation):
            assert (task_id, generation) == (417557, "initial")
            return review

        def wechat_delivery_review_approved(self, delivery_id):
            return delivery_id == 146

        def settle_failed_wechat_task_from_delivery(self, *args, **kwargs):
            self.settlement = (args, kwargs)
            return True

    store = Store(delivery)
    monkeypatch.setattr(
        wechat_reconciliation, "parse_persisted_consumer_result",
        lambda _body: SimpleNamespace(proposal=SimpleNamespace(actions=(action,))),
    )

    class ReaderOnlyHandler:
        def __init__(self, _store):
            pass

        def reconcile(self, _action, *, action_key, candidate):
            assert action_key == ""
            assert candidate["review_id"] == 1616
            return ActionOutcome("verified", {
                "delivery_id": 146,
                "readback": {
                    "message_id": "out-1", "account_id": "account-1",
                    "conversation_id": "melody115", "direction": "outbound",
                    "sent_at": "2026-10-26T19:54:10+00:00", "text": "reply",
                },
            })

    monkeypatch.setattr(wechat_reconciliation, "WechatSendMessageHandler", ReaderOnlyHandler)
    result = wechat_reconciliation.reconcile_failed_wechat_attempt(store, 16128)

    assert result["outcome"] == "done"
    assert store.settlement[1]["task_status"] == "done"
    assert store.settlement[1]["delivery_id"] == 146
