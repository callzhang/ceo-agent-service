from app.service_message_sender import ServiceMessageSender
from app.store import AutoReplyStore
import pytest


class AcceptedOnceThenReadback:
    def __init__(self):
        self.sends = 0
        self.reads = 0
        self.readback = None

    def send_reply_to_trigger(self, conversation, trigger, text):
        self.sends += 1
        raise TimeoutError("provider accepted but local completion was interrupted")

    def reconcile_reply_to_trigger(self, conversation, trigger, text):
        self.reads += 1
        return self.readback

    def verify_message_send_result(self, result):
        return {"state": "sent"}


def test_prepared_message_caller_uses_common_action_and_owned_receipts(tmp_path):
    store = AutoReplyStore(tmp_path / "prepared.sqlite")
    sender = ServiceMessageSender(store=store, dingtalk=AcceptedOnceThenReadback())
    prepared = sender.prepare(
        channel="dingtalk", delivery_key="prepared-action", body="Status",
    )

    with store._connect() as db:
        owner = db.execute(
            "select id,record_type,action_operation,action_delivery_channel,"
            "action_delivery_key,action_prepared_body,action_feedback_token,"
            "action_postfix_version,action_prepared_at "
            "from actions where record_type='action' and action_delivery_key=?",
            (prepared.delivery_key,),
        ).fetchone()
        assert owner is not None
        assert owner["record_type"] == "action"
        assert owner["action_operation"] is None
        assert owner["action_delivery_channel"] == prepared.channel
        assert owner["action_prepared_body"] == prepared.final_body
        assert owner["action_feedback_token"] == prepared.feedback_token
        assert owner["action_postfix_version"] == prepared.postfix_version
        assert owner["action_prepared_at"] is not None
        assert db.execute(
            "select count(*) from action_receipts where action_id=?",
            (owner["id"],),
        ).fetchone()[0] == 0
        assert db.execute(
            "select count(*) from actions where record_type='outbound_postfixes' "
            "and delivery_key=?", (prepared.delivery_key,),
        ).fetchone()[0] == 0


def test_restart_after_provider_acceptance_does_not_resend(tmp_path):
    path = tmp_path / "prepared-recovery.sqlite"
    adapter = AcceptedOnceThenReadback()
    sender = ServiceMessageSender(store=AutoReplyStore(path), dingtalk=adapter)
    prepared = sender.prepare(
        channel="dingtalk", delivery_key="interrupted-action", body="Status",
    )
    with pytest.raises(TimeoutError, match="local completion was interrupted"):
        sender.send_dingtalk_reply_to_trigger_prepared(
            prepared, conversation="cid", trigger="msg",
        )

    adapter.readback = {"result": {"processQueryKey": "readback-msg"}}
    restarted = ServiceMessageSender(store=AutoReplyStore(path), dingtalk=adapter)
    receipt = restarted.send_dingtalk_reply_to_trigger_prepared(
        prepared, conversation="cid", trigger="msg",
    )

    assert receipt.provider_result == adapter.readback
    assert adapter.sends == 1
    assert adapter.reads == 1
    assert AutoReplyStore(path).get_outbound_postfix_receipt(
        "dingtalk", prepared.delivery_key,
    ) == adapter.readback
    with AutoReplyStore(path)._connect() as db:
        dispatch, receipt = db.execute(
            "select operation,target,dispatched_at,observed_at,provider_result_json "
            "from action_receipts "
            "where action_id=(select id from actions where action_delivery_key=?) "
            "order by id",
            (prepared.delivery_key,),
        ).fetchall()
    assert tuple(dispatch[:2]) == tuple(receipt[:2]) == (
        "send_reply_to_trigger", prepared.delivery_key,
    )
    assert dispatch[2] is not None and dispatch[3] is None and dispatch[4] is None
    assert receipt[2] is None and receipt[3] is not None and receipt[4] is not None


def test_two_direct_email_claimants_only_one_provider_call(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    from app.email_classifier_contracts import EmailClassificationStatus
    from app.email_provider_actions import DeterministicEmailActionExecutor, ProviderMessageState
    from app.email_store import EmailStore
    from app.email_worker import _run_next_direct_action
    from tests.test_email_store import _classification

    class ProviderSubstitute:
        def __init__(self):
            self.entered = Event()
            self.release = Event()
            self.apply_calls = 0

        def read_state(self, locator, *, action_type):
            self.entered.set()
            assert self.release.wait(5)
            return ProviderMessageState(
                revision="before", labels=frozenset(), is_read=False,
                archived=False, folder=locator.folder, trashed=False,
            )

        def apply(self, locator, action_type, parameters, *, observed_state):
            self.apply_calls += 1
            return None

    store = EmailStore(tmp_path / "concurrent-direct.sqlite3")
    classification = _classification(status=EmailClassificationStatus.PROCESSED)
    store.persist_scan_result(classification, model_text="fixture features")
    provider = ProviderSubstitute()

    def executor_factory(_account_id):
        return DeterministicEmailActionExecutor(provider)

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(_run_next_direct_action, store, executor_factory)
        assert provider.entered.wait(5)
        second = pool.submit(_run_next_direct_action, store, executor_factory)
        second_result = second.result(timeout=5)
        provider.release.set()
        first_result = first.result(timeout=5)

    assert (first_result is not None) != (second_result is not None)
    assert provider.apply_calls == 1


def test_reviewed_email_prepared_share_owner(tmp_path):
    from app.email_classifier_contracts import EmailClassificationStatus
    from app.email_store import EmailStore
    from app.action_entity_definitions import ACTION_FIELDS
    from tests.test_email_store import _classification, _task2_account_values
    from tests.test_reviewed_candidate_store import _reviewed

    path = tmp_path / "shared-action-owner.sqlite3"
    store = AutoReplyStore(path)
    sender = ServiceMessageSender(store=store, dingtalk=AcceptedOnceThenReadback())
    prepared = sender.prepare(channel="dingtalk", delivery_key="prepared", body="Prepared")

    _, candidate, review, _ = _reviewed(store, options=False)
    execution = store.claim_candidate_execution(candidate["id"], review["id"], "worker", 60)
    reviewed = store.begin_candidate_action(execution["id"], "worker", 0, "reviewed-effect")

    email = EmailStore(path)
    email.create_account(_task2_account_values("account", enabled=False))
    classification = _classification(
        status=EmailClassificationStatus.PROCESSED, classification_id=902,
    )
    email.persist_scan_result(classification, model_text="fixture features")
    direct_id = email.direct_action_ids_for_plan(
        classification.action_plan.action_plan_id,
    )[0]
    assert email.claim_direct_action(
        action_id=direct_id, claimed_at="2026-10-19T10:00:00+00:00",
    ) is not None

    with store._connect() as db:
        prepared_id = db.execute(
            "select id from actions where action_delivery_channel='dingtalk' and action_delivery_key='prepared'",
        ).fetchone()[0]
        direct_owner_id = db.execute(
            "select id from actions where id=?",
            (int(direct_id),),
        ).fetchone()[0]
        owners = db.execute(
            "select * from actions where record_type='action' and id in (?, ?, ?) order by id",
            (prepared_id, reviewed["action_owner_id"], direct_owner_id),
        ).fetchall()
        assert len(owners) == 3
        assert all({f"action_{field}" for field in ACTION_FIELDS} <= set(owner.keys()) for owner in owners)
        assert all(owner["record_type"] == "action" for owner in owners)
        assert db.execute("select count(*) from actions where record_type in ('outbound_postfixes','candidate_action_attempts','email_actions')").fetchone()[0] == 0


def test_retry_keeps_original_external_identity(tmp_path):
    from app.email_classifier_contracts import EmailClassificationStatus
    from app.email_store import EmailStore
    from tests.test_email_store import _classification, _task2_account_values

    store = EmailStore(tmp_path / "direct-retry-identity.sqlite3")
    store.create_account(_task2_account_values("account", enabled=False))
    classification = _classification(
        status=EmailClassificationStatus.PROCESSED, classification_id=903,
    )
    store.persist_scan_result(classification, model_text="fixture features")
    action_id = store.direct_action_ids_for_plan(
        classification.action_plan.action_plan_id,
    )[0]
    first = store.claim_direct_action(
        action_id=action_id, claimed_at="2026-10-19T10:00:00+00:00",
    )
    assert first is not None
    store.complete_direct_action_attempt(
        first, status="failed", provider_operation="archive",
        provider_target=first.locator.stable_message_identity,
        provider_result_id="", error="transient provider failure",
        finished_at="2026-10-19T10:01:00+00:00",
    )
    retry = store.claim_direct_action(
        action_id=action_id, claimed_at="2026-10-19T11:00:00+00:00",
    )
    assert retry is not None
    assert retry.action_id == first.action_id == action_id
    assert retry.attempt_number == 2
    assert [attempt["attempt_number"] for attempt in store.list_action_attempts(action_id)] == [1]
    store.complete_direct_action_attempt(
        retry, status="done", provider_operation="archive",
        provider_target=retry.locator.stable_message_identity,
        provider_result_id="verified-provider-receipt", error="",
        finished_at="2026-10-19T11:01:00+00:00",
    )
    assert [attempt["attempt_number"] for attempt in store.list_action_attempts(action_id)] == [1, 2]
    with store._connect() as db:
        assert tuple(db.execute(
            "select action_external_action_key,action_action_identity from actions "
            "where record_type='action' and id=?", (int(action_id),),
        ).fetchone()) == (None, None)
