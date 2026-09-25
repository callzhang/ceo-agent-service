import json
import sqlite3
from types import SimpleNamespace

from app.email_classifier_agent import AgentClassificationResult
from app.email_classifier_contracts import EmailProviderLocator
from app.email_classifier_scan import AgentScanContext
from app.email_store import EmailStore
from app.email_training_labeler import LabelCandidate, label_candidate, select_candidates


def _observation(identity, subject, folder="INBOX", sender="bill@vendor.example"):
    return {
        "stable_message_identity": identity,
        "provider_folder_name": folder,
        "subject": subject,
        "sender": sender,
    }


def _cache(entries):
    return {
        "accounts": {
            "account-1": {
                "folders": {
                    "folder-inbox": {
                        "uidvalidity": 2,
                        "observations": {
                            identity: {"uid": uid, "observation": observation}
                            for uid, (identity, observation) in enumerate(entries, start=1)
                        },
                    }
                }
            }
        }
    }


def test_candidates_are_unclassified_inbox_mail_matching_a_thin_category():
    cache = _cache(
        [
            ("m1", _observation("m1", "Your invoice for September")),
            ("m2", _observation("m2", "Monthly billing statement")),
            ("m3", _observation("m3", "Lunch on Friday?")),
            ("m4", _observation("m4", "Invoice attached", folder="已删除邮件")),
            ("m5", _observation("m5", "Payment receipt")),
        ]
    )

    chosen = select_candidates(
        cache,
        classified_identities=frozenset({"m5"}),
        categories=["external_billing"],
        limit_per_category=1,
    )

    assert [item.stable_message_identity for item in chosen] == ["m1"]
    assert chosen[0].targeted_category == "external_billing"
    assert (chosen[0].uidvalidity, chosen[0].uid) == (2, 1)


# A message with an RFC Message-ID gets its stable identity from that id.
IDENTITY = EmailProviderLocator(
    account_id="account-1",
    folder="INBOX",
    uidvalidity=42,
    uid=7,
    rfc_message_id="<label-only@example.com>",
).stable_message_identity


def _message():
    return {
        "messageId": "<label-only@example.com>",
        "stableMessageIdentity": IDENTITY,
        "accountId": "account-1",
        "folder": "INBOX",
        "uidValidity": 42,
        "uid": 7,
        "providerUnread": False,
        "from": {"email": "bill@vendor.example"},
        "subject": "Your invoice",
        "textBody": "Amount due for September.",
        "date": "2026-09-07T12:00:00+00:00",
    }


def _context():
    return AgentScanContext(
        allowed_category_keys=("work", "junk"),
        category_descriptions={"work": {}, "junk": {}},
        folder_targets={"work": "Work"},
        config_version="config-v1",
    )


def _candidate():
    return LabelCandidate(
        account_id="account-1",
        folder="INBOX",
        uidvalidity=42,
        uid=7,
        stable_message_identity=IDENTITY,
        subject="Your invoice",
        sender="bill@vendor.example",
        targeted_category="external_billing",
    )


def _agent(certainty):
    calls = []

    def classify(task, **kwargs):
        calls.append(json.loads(task.input_json))
        return AgentClassificationResult(
            category="work" if certainty == "certain" else None,
            important=True,
            certainty=certainty,
            confidence=0.91,
            reason="invoice from a vendor",
        )

    return SimpleNamespace(classify=classify, calls=calls)


def test_a_certain_label_is_persisted_without_any_mailbox_action(tmp_path):
    """The whole point of the job: a training label, and nothing that can move mail."""

    database = tmp_path / "label.sqlite3"
    store = EmailStore(database)
    agent = _agent("certain")

    outcome = label_candidate(
        _candidate(),
        email_store=store,
        agent=agent,
        read_current_message=lambda _candidate: _message(),
        context=_context(),
    )

    assert outcome["outcome"] == "labelled"
    persisted = store.get_classification_by_stable_identity(IDENTITY)
    assert persisted["classification_source"] == "agent"
    assert persisted["status"] == "processed"
    assert persisted["action_plan"]["actions"] == []
    with sqlite3.connect(database) as db:
        assert db.execute("select count(*) from email_actions").fetchone()[0] == 0
        assert db.execute(
            "select count(*) from email_agent_classification_tasks"
        ).fetchone()[0] == 0
    assert store.claim_next_direct_action(
        claimed_at="2026-09-07T12:00:01+00:00", account_ids=("account-1",)
    ) is None
    labels = [
        row for row in store.list_selected_training_records()
        if row["stable_message_identity"] == IDENTITY
    ]
    assert len(labels) == 1


def test_an_uncertain_result_is_not_kept(tmp_path):
    """An uncertain label would sit in the owner's 待确认 queue for no reason."""

    database = tmp_path / "uncertain.sqlite3"
    store = EmailStore(database)

    outcome = label_candidate(
        _candidate(),
        email_store=store,
        agent=_agent("uncertain"),
        read_current_message=lambda _candidate: _message(),
        context=_context(),
    )

    assert outcome["outcome"] == "not_certain"
    with sqlite3.connect(database) as db:
        assert db.execute("select count(*) from email_classifications").fetchone()[0] == 0


class _SearchSession:
    def __init__(self):
        self.calls = []

    def select(self, mailbox, readonly=False):
        self.calls.append(("select", mailbox, readonly))
        return "OK", [b"3"]

    def response(self, name):
        return "OK", [b"42"]

    def uid(self, command, *args):
        self.calls.append(("uid", command, *args))
        return "OK", [b"5 7 9"]


class _SearchSource:
    def __init__(self):
        self.session = _SearchSession()

    def fetch_uid_batch(self, folder, *, cursor_uidvalidity, last_seen_uid, limit, unread_only):
        uid = last_seen_uid + 1
        message = {
            **_message(),
            "uid": uid,
            "messageId": f"<m{uid}@example.com>",
            "stableMessageIdentity": "",
        }
        return SimpleNamespace(uidvalidity=cursor_uidvalidity, messages=[message])


def test_gmail_search_takes_newest_unclassified_hits_over_a_readonly_select():
    from app.email_training_labeler import search_candidates

    source = _SearchSource()
    seen = EmailProviderLocator(
        account_id="account-1", folder="INBOX", uidvalidity=42, uid=9,
        rfc_message_id="<m9@example.com>",
    ).stable_message_identity

    found = search_candidates(
        source,
        account_id="account-1",
        query="category:purchases",
        targeted_category="shopping",
        classified_identities=frozenset({seen}),
        limit=1,
    )

    assert ("select", "INBOX", True) in source.session.calls
    assert ("uid", "SEARCH", "X-GM-RAW", '"category:purchases"') in source.session.calls
    assert [(candidate.uid, candidate.uidvalidity) for candidate, _ in found] == [(7, 42)]
    assert found[0][1]["uid"] == 7


def test_each_classification_runs_as_a_recorded_item_and_leaves_none_behind(tmp_path):
    """The router runs only recorded work, so a batch records one item per call."""

    from app.email_store import EmailStore
    from app.email_task_adapter import EmailClassificationTaskAdapter
    from app.email_training_labeler import OFFLINE_TASK_OWNER, _OfflineTaskBackend

    email_store = EmailStore(tmp_path / "labeler.sqlite3")
    tasks = EmailClassificationTaskAdapter(email_store)
    task_id = "email-classification:" + "a" * 64
    seen = []

    class Backend:
        def classify(self, **kwargs):
            seen.append(tasks.get_task(kwargs["task_id"]))
            return "{}"

    assert _OfflineTaskBackend(Backend(), tasks).classify(task_id=task_id) == "{}"

    assert seen[0].status == "running" and seen[0].owner == OFFLINE_TASK_OWNER
    assert tasks.get_task(task_id) is None


def test_a_failed_classification_still_removes_its_recorded_item(tmp_path):
    import pytest

    from app.email_store import EmailStore
    from app.email_task_adapter import EmailClassificationTaskAdapter
    from app.email_training_labeler import _OfflineTaskBackend

    email_store = EmailStore(tmp_path / "labeler.sqlite3")
    tasks = EmailClassificationTaskAdapter(email_store)
    task_id = "email-classification:" + "b" * 64

    class Backend:
        def classify(self, **_kwargs):
            raise RuntimeError("route failed")

    with pytest.raises(RuntimeError):
        _OfflineTaskBackend(Backend(), tasks).classify(task_id=task_id)

    assert tasks.get_task(task_id) is None


def test_recorded_items_never_reach_the_scan_worker_or_uid_protection(tmp_path):
    from app.email_store import EmailStore
    from app.email_task_adapter import EmailClassificationTaskAdapter

    email_store = EmailStore(tmp_path / "labeler.sqlite3")
    tasks = EmailClassificationTaskAdapter(email_store)
    tasks.open_offline_task(task_id="email-classification:" + "c" * 64, owner="labeler")

    assert tasks.claim_next(owner="worker") is None
    assert tasks.stable_provider_uids(account_id="a", folder="INBOX", uidvalidity=1) == frozenset()
    assert tasks.purge_offline_tasks("labeler") == 1
