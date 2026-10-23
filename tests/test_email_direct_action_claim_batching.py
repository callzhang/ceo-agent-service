"""A direct-action claim reads its candidates' sibling actions in bulk.

With ~600 pending actions on 2026-10-23 one claim evaluation took 17 s: the
sibling lookup ran once per candidate classification and each run scanned every
direct action. The claim repeats the evaluation under the SQLite writer lock, so
every other writer in every process waited on it.
"""

from datetime import datetime, timezone

from app.email_classifier_contracts import (
    EmailAction,
    EmailCategory,
    EmailClassification,
    EmailClassificationStatus,
    EmailProviderLocator,
    build_versioned_email_action_plan,
)
from app.email_store import EmailStore
from app.email_task_producer import EmailActionTaskProducer
from app.store import AutoReplyStore

CLAIMED_AT = "2026-10-23T10:00:00+00:00"


def _store_with_pending_moves(tmp_path, count):
    database = tmp_path / "claims.sqlite3"
    email_store = EmailStore(database)
    task_store = AutoReplyStore(database)
    email_store.create_account(
        {
            "account_id": "account-batch",
            "display_name": "Batch fixture",
            "email_address": "batch@example.com",
            "imap_host": "imap.invalid",
            "imap_port": 993,
            "imap_tls": True,
            "imap_username": "batch@example.com",
            "imap_secret_reference": "keychain://unused-batch-imap",
            "smtp_host": "smtp.invalid",
            "smtp_port": 465,
            "smtp_tls": True,
            "smtp_username": "batch@example.com",
            "smtp_secret_reference": "keychain://unused-batch-smtp",
            "enabled": True,
            "scan_folders": ["INBOX"],
            "scan_interval_seconds": 60,
        }
    )
    for index in range(1, count + 1):
        classification_id = 1000 + index
        plan = build_versioned_email_action_plan(
            action_plan_version=1,
            classification_id=classification_id,
            account_id="account-batch",
            category=EmailCategory.WORK,
            classification_source="user",
            confidence=1.0,
            model_id="email-model:batch-test",
            config_version="email-config:batch-test",
            actions=(EmailAction.MOVE,),
            action_parameters={EmailAction.MOVE: {"target_folder": "Archive"}},
            created_at=datetime(2026, 9, 3, 8, 0, index, tzinfo=timezone.utc),
        )
        email_store.upsert_classification(
            EmailClassification(
                classification_id=classification_id,
                stable_message_identity=(
                    f"account-batch:message-id:<batch-{index}@example.com>"
                ),
                provider_locator=EmailProviderLocator(
                    account_id="account-batch",
                    folder="INBOX",
                    uidvalidity=9,
                    uid=index,
                    rfc_message_id=f"<batch-{index}@example.com>",
                    thread_id=f"thread-batch-{index}",
                ),
                category=EmailCategory.WORK,
                confidence=1.0,
                margin=1.0,
                probabilities={EmailCategory.WORK: 1.0},
                model_id=plan.model_id,
                config_version=plan.config_version,
                status=EmailClassificationStatus.PROCESSED,
                classification_source="user",
                action_plan=plan,
            ),
            sender="sender@example.com",
            subject=f"Batch fixture {index}",
            model_text=f"__subject__batch fixture {index}",
            received_at="2026-09-03T08:00:00+00:00",
        )
        assert EmailActionTaskProducer(task_store, email_store).produce(plan, {}) == ()
    return email_store


def _sibling_reads(store):
    statements = []
    with store._connect() as db:
        db.set_trace_callback(statements.append)
        row = store._next_direct_action_row(db, CLAIMED_AT, None)
        db.set_trace_callback(None)
    reads = [s for s in statements if "explicit_retry_scheduled" in s]
    return row, len(reads)


def test_claim_reads_sibling_actions_once_however_many_classifications_are_pending(
    tmp_path,
):
    few_row, few_reads = _sibling_reads(_store_with_pending_moves(tmp_path / "few", 2))
    many_row, many_reads = _sibling_reads(
        _store_with_pending_moves(tmp_path / "many", 12)
    )

    assert few_row is not None and many_row is not None
    assert few_reads == many_reads == 1


def test_claim_still_picks_the_oldest_pending_action_across_classifications(tmp_path):
    store = _store_with_pending_moves(tmp_path, 6)

    first = store.claim_next_direct_action(claimed_at=CLAIMED_AT)
    second = store.claim_next_direct_action(claimed_at="2026-10-23T10:00:01+00:00")

    assert first is not None and second is not None
    assert first.action_id != second.action_id
    assert store.claim_next_direct_action(claimed_at="2026-10-23T10:00:02+00:00") is not None
