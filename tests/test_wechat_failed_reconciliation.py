import pytest

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
                channel="wechat", send_status="sent", trigger_message_id="trigger-1",
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


@pytest.mark.parametrize("readback_matches", [True, False])
def test_sender_recovery_settles_sent_failed_task_only_after_exact_readback(
    tmp_path, readback_matches,
):
    from datetime import datetime, timedelta, timezone

    from app import task_entity_storage
    from app.wechat import service
    from tests.wechat.test_send_message_handler import _account, _setup

    store, task, row, action, _handler, runner, prepared = _setup(tmp_path)
    delivery_id = store.create_standard_wechat_delivery(
        candidate_id=row["candidate_id"], review_id=row["review_id"],
        action_identity=action.action_identity,
    )
    store.mark_wechat_delivery_sending(delivery_id)
    store.set_wechat_delivery_status(delivery_id, "sent")
    with store._connect() as db:
        task_entity_storage.update_task_cas(
            db, task_id=task.id, expected={"status": task.status},
            changes={"status": "failed", "error": "external_action_failed"},
        )
    message = _message(
        body=prepared.final_body if readback_matches else "unrelated",
        conversation_id="u9", account_id="acct-1",
        sent_at=(datetime.now(timezone.utc) + timedelta(seconds=1)).isoformat(),
    )
    reader = _Reader([message])

    service.recover_before_sender(store, reader, account=_account())

    assert runner.calls == []
    assert store.get_reply_task(task.id).status == ("done" if readback_matches else "failed")
    assert store.get_wechat_delivery_by_id(delivery_id).status == "sent"
    evidence = store.get_wechat_reply_task_reconciliation("u9", task.trigger_message_id)
    assert bool(evidence) is readback_matches
    if readback_matches:
        assert 'out-1' in evidence.detail
        service.recover_before_sender(store, reader, account=_account())
        assert len(reader.calls) == 1


@pytest.mark.parametrize("readback,expected", [
    ("absent", "skipped"),
    ("present", "failed"),
    ("incomplete", "failed"),
    ("wrong_scope", "failed"),
])
def test_failed_task_with_formally_skipped_delivery_requires_complete_no_send_readback(
    tmp_path, monkeypatch, readback, expected,
):
    from app import task_entity_storage
    from app.action_storage import update_action
    from app.external_retry import MAX_AUTOMATIC_RETRIES
    from tests.wechat.test_send_message_handler import _account, _setup

    store, task, row, action, _handler, runner, prepared = _setup(tmp_path)
    attempt_id = store.record_reply_attempt(
        conversation_id=task.conversation_id, conversation_title=task.conversation_title,
        trigger_message_id=task.trigger_message_id, trigger_sender=task.trigger_sender,
        trigger_text=task.trigger_text, action="send_reply", sensitivity_kind="normal",
        send_status="pending", channel="wechat",
    )
    delivery_id = store.create_standard_wechat_delivery(
        candidate_id=row["candidate_id"], review_id=row["review_id"],
        action_identity=action.action_identity,
    )
    store.mark_wechat_delivery_sending(delivery_id, now="2026-10-26 19:53:52")
    store.set_wechat_delivery_status(delivery_id, "failed", error="target_open_failed",
                                     pre_action_failure=True)
    with store._connect() as db:
        update_action(db, "reply_attempts", attempt_id, {
            "retry_count": MAX_AUTOMATIC_RETRIES, "agent_run_id": row["audit_run_id"],
        })
        task_entity_storage.update_task_cas(
            db, task_id=task.id, expected={"status": task.status},
            changes={"status": "failed", "error": "external_action_failed"},
        )
    store.skip_exhausted_stale_wechat_delivery(
        delivery_id, expected_execution_generation=task.execution_generation,
        reason="stale target opening failure", inactive_before="2026-10-26 20:00:00",
    )
    messages = []
    if readback == "present":
        messages.append(_message(
            body=prepared.final_body, conversation_id=task.conversation_id, account_id="acct-1",
        ))
    if readback == "incomplete":
        messages.extend(
            _message(body="unrelated", message_id=str(i), conversation_id=task.conversation_id,
                     account_id="acct-1")
            for i in range(200)
        )
    if readback == "wrong_scope":
        messages.append(_message(body="unrelated", conversation_id="another"))
    reader = _Reader(messages)
    monkeypatch.setattr(
        wechat_reconciliation, "WechatSendMessageHandler",
        lambda store: WechatSendMessageHandler(
            store, reader_factory=lambda: reader, account_factory=_account,
            sender_factory=lambda: pytest.fail("settlement must never call Sender"),
        ),
    )

    result = wechat_reconciliation.reconcile_failed_wechat_attempt(store, attempt_id)

    assert result["outcome"] == expected
    assert store.get_reply_task(task.id).status == expected
    assert store.get_reply_attempt(attempt_id).send_status == "skipped"
    assert store.get_wechat_delivery_by_id(delivery_id).status == "skipped"
    assert not runner.calls
    assert len(reader.calls) == 1


def test_failed_wechat_predecessor_settles_after_its_successor_is_formally_skipped(
    tmp_path, monkeypatch,
):
    from app import task_entity_storage
    from app.action_storage import update_action
    from app.external_retry import MAX_AUTOMATIC_RETRIES
    from app.store import AutoReplyStore
    from tests.wechat.support import approve_delivery, create_wechat_delivery
    from tests.wechat.test_send_message_handler import _account

    store = AutoReplyStore(tmp_path / "chain.sqlite3")
    chain = []
    for n in (1, 2):
        task = store.ensure_reply_task(
            channel="wechat", conversation_id="u9", conversation_title="Alex",
            single_chat=True, trigger_message_id=f"trigger-{n}",
            trigger_create_time=f"2026-10-26T19:50:0{n}+00:00",
            trigger_sender="Alex", trigger_text=f"Question {n}",
        )
        attempt_id = store.record_reply_attempt(
            conversation_id=task.conversation_id, conversation_title=task.conversation_title,
            trigger_message_id=task.trigger_message_id, trigger_sender=task.trigger_sender,
            trigger_text=task.trigger_text, action="send_reply", sensitivity_kind="normal",
            send_status="pending", channel="wechat",
        )
        delivery_id = create_wechat_delivery(
            store, reply_task_id=task.id, account_id="acct-1", target_type="direct",
            target_id="u9", conversation_id="u9", reply_text=f"Answer {n}",
        )
        approve_delivery(store, delivery_id)
        review = store.current_reviewed_candidate(task.id, task.execution_generation)
        store.mark_wechat_delivery_sending(delivery_id, now=f"2026-10-26 19:53:5{n}")
        store.set_wechat_delivery_status(
            delivery_id, "failed", error="target_open_failed", pre_action_failure=True,
        )
        with store._connect() as db:
            update_action(db, "reply_attempts", attempt_id, {
                "retry_count": MAX_AUTOMATIC_RETRIES, "agent_run_id": review["audit_run_id"],
            })
            task_entity_storage.update_task_cas(
                db, task_id=task.id, expected={"status": task.status},
                changes={"status": "failed", "error": "external_action_failed"},
            )
        chain.append((task, attempt_id, delivery_id))
    old_task, old_attempt, old_delivery = chain[0]
    next_task, _, next_delivery = chain[1]
    store.skip_exhausted_stale_wechat_delivery(
        next_delivery, expected_execution_generation=next_task.execution_generation,
        reason="stale target opening failure", inactive_before="2026-10-26 20:00:00",
    )
    reader = _Reader([])
    monkeypatch.setattr(
        wechat_reconciliation, "WechatSendMessageHandler",
        lambda store: WechatSendMessageHandler(
            store, reader_factory=lambda: reader, account_factory=_account,
            sender_factory=lambda: pytest.fail("settlement must never call Sender"),
        ),
    )
    settle = store.settle_failed_wechat_task_from_delivery

    def reject_changed_generation(*args, **kwargs):
        from copy import deepcopy

        evidence = deepcopy(kwargs["evidence"])
        evidence["no_send_readbacks"][1]["execution_generation"] = "changed-after-readback"
        with pytest.raises(ValueError, match="current skipped chain"):
            settle(*args, **{**kwargs, "evidence": evidence})
        assert store.get_reply_task(old_task.id).status == "failed"
        return settle(*args, **kwargs)

    monkeypatch.setattr(store, "settle_failed_wechat_task_from_delivery", reject_changed_generation)
    result = wechat_reconciliation.reconcile_failed_wechat_attempt(store, old_attempt)

    assert result["outcome"] == "skipped"
    assert store.get_reply_task(old_task.id).status == "skipped"
    assert store.get_reply_task(next_task.id).status == "failed"
    assert store.get_wechat_delivery_by_id(old_delivery).status == "superseded"
    assert store.get_wechat_delivery_by_id(next_delivery).status == "skipped"
    assert len(reader.calls) == 2


@pytest.mark.parametrize("case", [
    "verified", "wrong_scope", "before_trigger", "incomplete", "dispatched",
    "missing_reply", "automated_reply",
])
def test_readback_of_principal_reply_resolves_the_task_without_a_send(
    tmp_path, monkeypatch, case,
):
    from fastapi.testclient import TestClient

    from app import task_entity_storage
    from app.action_storage import update_action
    from app.audit_web import create_audit_app
    from app.system_executor import SystemExecutor
    from app.wechat.models import WechatMessage
    from tests.wechat.support import approve_delivery, create_wechat_delivery
    from tests.wechat.test_send_message_handler import _account, _setup

    store, task, review, _action, handler, runner, _prepared = _setup(tmp_path)
    previous = store.ensure_reply_task(
        channel="wechat", conversation_id=task.conversation_id, conversation_title="Alex",
        single_chat=True, trigger_message_id="older", trigger_create_time="2026-10-24T09:55:00",
        trigger_sender="Alex", trigger_text="Unrelated prior question",
    )
    previous_delivery = create_wechat_delivery(
        store, reply_task_id=previous.id, account_id="acct-1", target_type="direct",
        target_id=task.conversation_id, conversation_id=task.conversation_id,
        reply_text="The previous automated message",
    )
    approve_delivery(store, previous_delivery)
    store.mark_wechat_delivery_sending(previous_delivery, now="2026-10-24 10:00:01")
    result = SystemExecutor(store, handlers={("wechat", "send_message"): handler}).execute(
        task, review["candidate_id"], review["review_id"],
    )
    assert result.error.code == "external_action_failed"
    assert store.get_wechat_delivery_for_task(task.id) is None
    store.set_wechat_delivery_status(previous_delivery, "sent")

    with store._connect() as db:
        task_entity_storage.update_task_cas(
            db, task_id=task.id, expected={"status": "processing"},
            changes={"status": "failed", "error": "external_action_failed"},
        )
        if case == "dispatched":
            db.execute(
                "update action_receipts set dispatched_at=current_timestamp where execution_run_id=?",
                (review["candidate_id"],),
            )
    attempt_id = store.record_reply_attempt(
        channel="wechat", conversation_id=task.conversation_id, conversation_title="Alex",
        trigger_message_id=task.trigger_message_id, trigger_sender="Alex", trigger_text="hi",
        action="send_reply", sensitivity_kind="normal", send_status="failed",
    )
    with store._connect() as db:
        update_action(db, "reply_attempts", attempt_id, {
            "agent_run_id": review["audit_run_id"], "send_error": "external_action_failed",
        })

    trigger = WechatMessage(
        account_id="acct-1", conversation_id=task.conversation_id, conversation_type="direct",
        message_id=task.trigger_message_id, sender_id="u9", sender_display_name="Alex",
        direction="inbound", sent_at="2026-10-24T10:00:00+00:00", kind="text",
        text="hi", source_version="4.1.10",
    )
    reply = trigger.model_copy(update={
        "message_id": "manual-1", "sender_id": "self-1", "direction": "outbound",
        "sent_at": "2026-10-24T10:01:00+00:00", "text": "I replied myself",
    })
    if case == "wrong_scope":
        reply = reply.model_copy(update={"conversation_id": "another"})
    if case == "before_trigger":
        reply = reply.model_copy(update={"sent_at": "2026-10-24T09:59:59+00:00"})
    if case == "automated_reply":
        reply = reply.model_copy(update={
            "text": store.get_wechat_delivery_by_id(previous_delivery).reply_text,
        })
    messages = [trigger] if case == "missing_reply" else [trigger, reply]
    if case == "incomplete":
        messages.extend(
            trigger.model_copy(update={"message_id": f"extra-{i}"}) for i in range(198)
        )

    monkeypatch.setattr(
        wechat_reconciliation, "WechatSendMessageHandler",
        lambda store: WechatSendMessageHandler(
            store, reader_factory=lambda: _Reader(messages), account_factory=_account,
            sender_factory=lambda: pytest.fail("settlement must never construct Sender"),
        ),
    )

    expected = "done" if case == "verified" else "failed"
    client = TestClient(create_audit_app(store.path))
    response = client.post(
        f"/api/console/history/{attempt_id}/retry",
        json={"principal_reply_message_id": "manual-1"},
    )
    assert response.status_code == (200 if case == "verified" else 409)
    assert store.get_reply_task(task.id).status == expected
    assert store.get_reply_attempt(attempt_id).send_status == "failed"
    assert store.get_wechat_delivery_for_task(task.id) is None
    assert runner.calls == []
    if case == "verified":
        detail = client.get(f"/api/console/history/{attempt_id}").json()["item"]
        assert detail["status"]["raw"] == "done"
        assert "本人已回复" in detail["status"]["message"]
        assert detail["historical_attempt"]["status"] == "failed"
        assert detail["delivery"] is None
