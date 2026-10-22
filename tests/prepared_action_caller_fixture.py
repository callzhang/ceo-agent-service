"""Provider-free source/current DAO exercise, also run in the archived source."""
from dataclasses import asdict
from pathlib import Path
import sqlite3

from app.action_storage import OUTBOUND_POSTFIX_SQL, OUTBOUND_POSTFIX_RECEIPT_SQL, NATIVE_REPLY_DISPATCH_SQL
from app.service_message_sender import ServiceMessageSender
from app.store import AutoReplyStore


class ClockStore(AutoReplyStore):
    def _open_connection(self):
        db = super()._open_connection()
        db.create_function("current_timestamp", 0, lambda: "source-independent-clock")
        return db


def facts(path):
    with sqlite3.connect(path) as db:
        return {
            kind: [tuple(row) for row in db.execute(sql + " order by channel,delivery_key")]
            for kind, sql in (("parent", OUTBOUND_POSTFIX_SQL),
                              ("receipt", OUTBOUND_POSTFIX_RECEIPT_SQL),
                              ("claim", NATIVE_REPLY_DISPATCH_SQL))
        }


def exercise(path):
    store = ClockStore(Path(path))
    observed = []
    for key, result in (("01", None), ("1", {}), ("white space", {"z": 1, "a": "中"}),
                        ("receipt-only", ["value"])):
        prepared = store.prepare_outbound_postfix(" dingtalk ", " " + key + " ", "body\n\nfirst", "", feedback_base_url="")
        observed.append(asdict(prepared))
        assert store.prepare_outbound_postfix("dingtalk", key, "replacement", "", feedback_base_url="") == prepared
        assert store.get_outbound_postfix("dingtalk", key) == prepared
        if key != "receipt-only":
            observed.append(store.claim_native_reply_dispatch("dingtalk", key))
            observed.append(store.claim_native_reply_dispatch("dingtalk", key))
        observed.append(store.record_outbound_postfix_receipt(" dingtalk ", " " + key + " ", result))
        observed.append(store.record_outbound_postfix_receipt("dingtalk", key, {"replacement": True}))
        observed.append(store.get_outbound_postfix_receipt("dingtalk", key))
    for channel, key in (("dingtalk", "missing"), (" dingtalk ", "01"), ("dingtalk", " 01 "),
                         (None, "01"), ("dingtalk", None), (1, "01"), ("invalid", "")):
        try:
            observed.append(["claim", store.claim_native_reply_dispatch(channel, key)])
        except (ValueError, sqlite3.IntegrityError) as exc:
            observed.append([type(exc).__name__, str(exc)])
    observed.append(sorted(store.recorded_outbound_body_keys("dingtalk", "")))
    observed.append(native_cycle(path, crash=False))
    observed.append(native_cycle(path, crash=True))
    return observed


def native_cycle(path, *, crash):
    store = ClockStore(Path(path))
    key = "native-crash" if crash else "native-accepted-ambiguous"
    journal = []

    class Adapter:
        sends = 0
        reconciles = 0
        verifies = 0

        def send_reply_to_trigger(self, conversation, trigger, body):
            self.sends += 1
            # A new connection sees the committed possible-effect claim before
            # this provider substitute is invoked.
            visible = facts(path)
            assert any(row[:2] == ("dingtalk", key) for row in visible["claim"])
            assert not any(row[:2] == ("dingtalk", key) for row in visible["receipt"])
            journal.append("provider-after-committed-claim")
            if crash:
                raise RuntimeError("substitute process loss")
            return {"processQueryKey": key}

        def reconcile_reply_to_trigger(self, conversation, trigger, body):
            self.reconciles += 1
            journal.append("reconcile-existing-claim")
            return {"processQueryKey": key}

        def verify_message_send_result(self, result):
            self.verifies += 1
            assert result == {"processQueryKey": key}
            # Receipt survives before verification, including unknown delivery.
            assert store.get_outbound_postfix_receipt("dingtalk", key) == result
            journal.append("verify-after-durable-accepted-receipt")
            return {"state": "ambiguous" if self.verifies == 1 else "sent"}

    adapter = Adapter()
    sender = ServiceMessageSender(store=store, dingtalk=adapter)
    prepared = sender.prepare(channel="dingtalk", delivery_key=key, body="native body", original_text="")
    for _ in range(3 if crash else 2):
        try:
            sender.send_dingtalk_reply_to_trigger_prepared(prepared, conversation="conversation", trigger="trigger")
            journal.append("sent")
        except RuntimeError as exc:
            journal.append(str(exc))
        store = ClockStore(Path(path))
        sender = ServiceMessageSender(store=store, dingtalk=adapter)
    assert adapter.sends == 1
    assert adapter.reconciles == int(crash)
    with sqlite3.connect(path) as db:
        assert db.execute("select count(*) from runs").fetchone()[0] == 0
        assert db.execute("select count(*) from actions where record_type='external_action_results'").fetchone()[0] == 0
    return [adapter.sends, adapter.reconciles, adapter.verifies, journal]
