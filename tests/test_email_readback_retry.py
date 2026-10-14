from pathlib import Path
from dataclasses import replace

import pytest
import sqlite3

from app.action_storage import update_email_action
from app.email_classifier_contracts import EmailAction
from app.email_store import EMAIL_DIRECT_ACTION_LOCATOR_SQL, EmailStore
from app.email_action_reconcile import ExistingReadOnlyEmailStore, release_source_verified_retry
from tests.test_email_action_reconcile import (
    ACCOUNT, ReadOnlyFakeSession, _action_row, _factory, _seed_failed as _seed_original,
)
from tests.test_email_store import _task2_account_values
from app.email_store import email_imap_configuration_snapshot


def _release(store, failed, **kwargs):
    return store.release_source_verified_direct_action(
        failed, **kwargs,
        account_configuration_snapshot=email_imap_configuration_snapshot(store.get_account(failed.action.account_id)),
    )


def _attested_factory(store, session):
    factory = _factory(session)
    factory.account_configuration_snapshot = email_imap_configuration_snapshot(store.get_account(ACCOUNT))
    return factory


def test_missing_configuration_evidence_cannot_release(tmp_path):
    store = EmailStore(tmp_path / "mail.sqlite3")
    action_id = _seed_failed(store, "unattested", uid=9)
    factory = _factory(ReadOnlyFakeSession(messages={"INBOX": {9: ("<unattested@example.com>", set())}}))
    report = release_source_verified_retry(
        store, account_id=ACCOUNT, action_id=action_id, provider_factory=factory, apply=True,
    )
    assert not report["released"]
    failed = store.list_failed_direct_actions(account_id=ACCOUNT, action_types=(EmailAction.MOVE,), limit=1)[0]
    with pytest.raises(TypeError, match="account_configuration_snapshot"):
        EmailStore.release_source_verified_direct_action(store,
            failed, source_locator=failed.action.locator, source_revision="source-v1",
            requested_at="2026-10-12T06:00:00+00:00", apply=True,
        )


def _seed_failed(store, *args, **kwargs):
    if store.get_account(ACCOUNT) is None:
        store.create_account(_task2_account_values(ACCOUNT))
    return _seed_original(store, *args, **kwargs)


def test_account_changed_during_readback_cannot_release(tmp_path):
    store = EmailStore(tmp_path / "mail.sqlite3")
    action_id = _seed_failed(store, "account-race", uid=9)
    session = ReadOnlyFakeSession(messages={"INBOX": {9: ("<account-race@example.com>", set())}})

    def changed_connection():
        with store._connect() as db:
            db.execute("update email_accounts set imap_username='different' where account_id=?", (ACCOUNT,))
        return _factory(session)()

    changed_connection.account_configuration_snapshot = email_imap_configuration_snapshot(store.get_account(ACCOUNT))

    report = release_source_verified_retry(
        store, account_id=ACCOUNT, action_id=action_id,
        provider_factory=changed_connection, apply=True,
    )
    assert not report["released"]
    assert not _action_row(store.path, action_id)["next_attempt_at"]


def test_factory_configuration_changed_before_command_cannot_release(tmp_path):
    from app.email_store import email_imap_configuration_snapshot

    store = EmailStore(tmp_path / "mail.sqlite3")
    action_id = _seed_failed(store, "old-factory", uid=9)
    session = ReadOnlyFakeSession(messages={"INBOX": {9: ("<old-factory@example.com>", set())}})
    factory = _factory(session)
    factory.account_configuration_snapshot = email_imap_configuration_snapshot(store.get_account(ACCOUNT))
    with store._connect() as db:
        db.execute("update email_accounts set imap_username='different' where account_id=?", (ACCOUNT,))
    report = release_source_verified_retry(
        store, account_id=ACCOUNT, action_id=action_id, provider_factory=factory, apply=True,
    )
    assert not report["released"]


@pytest.mark.parametrize("claim_exact", [True, False])
def test_account_changed_after_release_cannot_claim(tmp_path, claim_exact):
    store = EmailStore(tmp_path / "mail.sqlite3")
    action_id = _seed_failed(store, "account-change", uid=9)
    failed = store.list_failed_direct_actions(account_id=ACCOUNT, action_types=(EmailAction.MOVE,), limit=1)[0]
    assert _release(store,
        failed, source_locator=failed.action.locator, source_revision="source-v1",
        requested_at="2026-10-12T06:00:00+00:00", apply=True,
    )
    with store._connect() as db:
        db.execute("update email_accounts set imap_host='other.example.com' where account_id=?", (ACCOUNT,))
    if claim_exact:
        claim = store.claim_direct_action(action_id=action_id, claimed_at="2026-10-12T07:00:00+00:00")
    else:
        claim = store.claim_next_direct_action(claimed_at="2026-10-12T07:00:00+00:00")
    assert claim is None
    states = store.list_mailbox_action_states([failed.action.classification_id])
    assert states[failed.action.classification_id][0]["retriable"] is False
    from app.email_store import _failed_action_retriable_sql
    with store._connect() as db:
        count = db.execute(
            f"select count(*) from ({EMAIL_DIRECT_ACTION_LOCATOR_SQL}) a where a.action_id=? and "
            + _failed_action_retriable_sql("a"), (action_id,),
        ).fetchone()[0]
    assert count == 0


def test_readback_error_closes_connection_created_in_worker(monkeypatch):
    from app import email_action_reconcile as reconcile

    class Connection:
        closed = False

        def close(self):
            self.closed = True

    connection = Connection()

    def fail_read(_provider, _failed):
        raise OSError("read unavailable")

    monkeypatch.setattr(reconcile, "_examine", fail_read)
    with pytest.raises(OSError, match="read unavailable"):
        reconcile._examine_with_deadline(None, lambda: connection, None, 1)
    assert connection.closed


def test_source_verified_retry_preserves_history_and_is_single_use(tmp_path: Path):
    store = EmailStore(tmp_path / "mail.sqlite3")
    action_id = _seed_failed(store, "retry-source", uid=7)
    failed = store.list_failed_direct_actions(
        account_id=ACCOUNT, action_types=(EmailAction.MOVE,), limit=1,
    )[0]
    before = _action_row(store.path, action_id)
    assert _release(store,
        failed, source_locator=failed.action.locator, source_revision="source-v1",
        requested_at="2026-10-12T06:00:00+00:00", apply=False,
    )
    assert _action_row(store.path, action_id)["status"] == "failed"
    assert _release(store,
        failed, source_locator=failed.action.locator, source_revision="source-v1",
        requested_at="2026-10-12T06:00:00+00:00", apply=True,
    )
    after = _action_row(store.path, action_id)
    assert after["status"] == "failed"
    assert after["next_attempt_at"] == "2026-10-12T06:00:00+00:00"
    assert after["error"] == before["error"]
    assert after["attempt_count"] == before["attempt_count"]
    assert after["provider_result_id"] == before["provider_result_id"]
    EmailStore(store.path)
    assert not _release(store,
        failed, source_locator=failed.action.locator, source_revision="source-v1",
        requested_at="2026-10-12T06:00:00+00:00", apply=True,
    )


def test_source_verified_retry_rejects_changed_failure(tmp_path: Path):
    store = EmailStore(tmp_path / "mail.sqlite3")
    action_id = _seed_failed(store, "retry-changed", uid=7)
    failed = store.list_failed_direct_actions(
        account_id=ACCOUNT, action_types=(EmailAction.MOVE,), limit=1,
    )[0]
    with store._connect() as db:
        action_rows = db.execute(
            f"select rowid from ({EMAIL_DIRECT_ACTION_LOCATOR_SQL}) where action_id=?",
            (action_id,),
        ).fetchall()
        for action_row in action_rows:
            update_email_action(db, "email_actions", action_row["rowid"], {"error": "new failure"})
    assert not _release(store,
        failed, source_locator=failed.action.locator, source_revision="source-v1",
        requested_at="2026-10-12T06:00:00+00:00", apply=True,
    )
    assert _action_row(store.path, action_id)["status"] == "failed"


def test_retry_entry_reads_only_the_exact_source_before_release(tmp_path: Path):
    store = EmailStore(tmp_path / "mail.sqlite3")
    action_id = _seed_failed(store, "still", uid=9)
    other = _seed_failed(store, "other", uid=10)
    session = ReadOnlyFakeSession(messages={"INBOX": {9: ("<still@example.com>", set())}})
    report = release_source_verified_retry(
        store, account_id=ACCOUNT, action_id=action_id,
        provider_factory=_attested_factory(store, session), apply=True,
    )
    assert report == {"outcome": "in_source", "eligible": True, "released": True}
    assert _action_row(store.path, action_id)["status"] == "failed"
    assert _action_row(store.path, action_id)["next_attempt_at"]
    assert _action_row(store.path, other)["status"] == "failed"
    assert [row["status"] for row in store.list_action_attempts(action_id)] == ["failed"]


@pytest.mark.parametrize("location", ["Projects", "Archive", "absent"])
def test_retry_entry_does_not_release_non_source_results(tmp_path: Path, location: str):
    store = EmailStore(tmp_path / "mail.sqlite3")
    action_id = _seed_failed(store, "elsewhere", uid=9)
    messages = {} if location == "absent" else {location: {31: ("<elsewhere@example.com>", set())}}
    report = release_source_verified_retry(
        store, account_id=ACCOUNT, action_id=action_id,
        provider_factory=_attested_factory(store, ReadOnlyFakeSession(messages=messages)), apply=True,
    )
    assert not report["released"]
    assert _action_row(store.path, action_id)["status"] == "failed"


def test_retry_rejects_a_different_source_identity(tmp_path: Path):
    store = EmailStore(tmp_path / "mail.sqlite3")
    action_id = _seed_failed(store, "identity", uid=7)
    failed = store.list_failed_direct_actions(
        account_id=ACCOUNT, action_types=(EmailAction.MOVE,), limit=1,
    )[0]
    with pytest.raises(ValueError, match="exact move/trash source"):
        _release(store,
            failed, source_locator=replace(failed.action.locator, uid=8),
            source_revision="source-v1", requested_at="2026-10-12T06:00:00+00:00",
            apply=True,
        )
    assert _action_row(store.path, action_id)["status"] == "failed"


def test_exhausted_retry_is_consumed_once_without_resetting_budget(tmp_path: Path):
    store = EmailStore(tmp_path / "mail.sqlite3")
    action_id = _seed_failed(store, "exhausted", uid=7)
    for index in range(3):
        failed = store.list_failed_direct_actions(
            account_id=ACCOUNT, action_types=(EmailAction.MOVE,), limit=1,
        )[0]
        assert _release(store,
            failed, source_locator=failed.action.locator, source_revision="source-v1",
            requested_at=f"2026-10-12T06:00:0{index}+00:00", apply=True,
        )
        claimed = store.claim_direct_action(
            action_id=action_id, claimed_at=f"2026-10-12T06:00:0{index}+00:00",
        )
        assert claimed is not None
        assert not _action_row(store.path, action_id)["next_attempt_at"]
        store.complete_direct_action_attempt(
            claimed, status="failed", provider_operation="MOVE",
            provider_target=claimed.locator.stable_message_identity,
            provider_result_id="", error="provider_apply_failed:TimeoutError",
            finished_at=f"2026-10-12T06:00:0{index}+00:00", retryable=False,
        )
    assert _action_row(store.path, action_id)["attempt_count"] == 4
    assert store.claim_direct_action(
        action_id=action_id, claimed_at="2026-10-12T07:00:00+00:00",
    ) is None
    assert len(store.list_action_attempts(action_id)) == 4
    EmailStore(store.path)


def test_read_only_preview_never_initializes_or_writes(tmp_path: Path, monkeypatch):
    store = EmailStore(tmp_path / "mail.sqlite3")
    _seed_failed(store, "readonly", uid=7)

    def no_initialization(*args, **kwargs):
        raise AssertionError("preview initialized the schema")

    monkeypatch.setattr(EmailStore, "_initialize", no_initialization)
    preview = ExistingReadOnlyEmailStore(store.path)
    failed = preview.list_failed_direct_actions(
        account_id=ACCOUNT, action_types=(EmailAction.MOVE,), limit=1,
    )[0]
    assert _release(preview,
        failed, source_locator=failed.action.locator, source_revision="source-v1",
        requested_at="2026-10-12T06:00:00+00:00", apply=False,
    )
    with pytest.raises(sqlite3.OperationalError, match="readonly"):
        with preview._connect() as db:
            action_rows = db.execute(
                f"select rowid from ({EMAIL_DIRECT_ACTION_LOCATOR_SQL})"
            ).fetchall()
            for action_row in action_rows:
                update_email_action(db, "email_actions", action_row["rowid"], {
                    "next_attempt_at": "changed",
                })


@pytest.mark.parametrize("change", [{"action_plan_id": "changed-plan"}, {"config_version": "changed-config"}, {"parameters": {"target_folder": "Other"}}])
def test_retry_rejects_changed_plan_config_or_parameters(tmp_path: Path, change):
    store = EmailStore(tmp_path / "mail.sqlite3")
    action_id = _seed_failed(store, "scope", uid=7)
    failed = store.list_failed_direct_actions(
        account_id=ACCOUNT, action_types=(EmailAction.MOVE,), limit=1,
    )[0]
    stale = replace(failed, action=replace(failed.action, **change))
    assert not _release(store,
        stale, source_locator=failed.action.locator, source_revision="source-v1",
        requested_at="2026-10-12T06:00:00+00:00", apply=True,
    )
    assert not _action_row(store.path, action_id)["next_attempt_at"]


def test_retry_rejects_a_now_claimed_action(tmp_path: Path):
    store = EmailStore(tmp_path / "mail.sqlite3")
    action_id = _seed_failed(store, "claimed", uid=7)
    failed = store.list_failed_direct_actions(
        account_id=ACCOUNT, action_types=(EmailAction.MOVE,), limit=1,
    )[0]
    assert _release(store,
        failed, source_locator=failed.action.locator, source_revision="source-v1",
        requested_at="2026-10-12T06:00:00+00:00", apply=True,
    )
    assert store.claim_direct_action(
        action_id=action_id, claimed_at="2026-10-12T06:00:00+00:00",
    ) is not None
    assert not _release(store,
        failed, source_locator=failed.action.locator, source_revision="source-v1",
        requested_at="2026-10-12T06:00:00+00:00", apply=True,
    )


@pytest.mark.parametrize("initial_attempts", [1, 3])
@pytest.mark.parametrize("operation,error", [
    ("MOVE", "provider_apply_failed:TimeoutError"),
    ("provider_factory", "provider_factory_failed:TimeoutError"),
    ("startup_recovery", "stale_processing_recovered"),
])
def test_explicit_attempt_does_not_renew_even_for_retryable_errors(tmp_path: Path, operation, error, initial_attempts):
    store = EmailStore(tmp_path / "mail.sqlite3")
    action_id = _seed_failed(store, "one-shot", uid=7)
    for _ in range(initial_attempts - 1):
        previous = store.list_failed_direct_actions(
            account_id=ACCOUNT, action_types=(EmailAction.MOVE,), limit=1,
        )[0]
        assert _release(store,
            previous, source_locator=previous.action.locator, source_revision="source-v1",
            requested_at="2026-10-12T06:00:00+00:00", apply=True,
        )
        claim = store.claim_direct_action(action_id=action_id, claimed_at="2026-10-12T06:00:00+00:00")
        assert claim is not None
        store.complete_direct_action_attempt(
            claim, status="failed", provider_operation="MOVE",
            provider_target=claim.locator.stable_message_identity, provider_result_id="",
            error="provider_apply_failed:TimeoutError", finished_at="2026-10-12T06:00:00+00:00", retryable=False,
        )
    failed = store.list_failed_direct_actions(
        account_id=ACCOUNT, action_types=(EmailAction.MOVE,), limit=1,
    )[0]
    assert _release(store,
        failed, source_locator=failed.action.locator, source_revision="source-v1",
        requested_at="2026-10-12T06:00:00+00:00", apply=True,
    )
    claimed = store.claim_direct_action(action_id=action_id, claimed_at="2026-10-12T06:00:00+00:00")
    assert claimed is not None
    store.complete_direct_action_attempt(
        claimed, status="failed", provider_operation=operation,
        provider_target=claimed.locator.stable_message_identity, provider_result_id="",
        error=error, finished_at="2026-10-12T06:00:01+00:00", retryable=True,
    )
    assert not _action_row(store.path, action_id)["next_attempt_at"]
    assert store.claim_direct_action(action_id=action_id, claimed_at="2026-10-12T08:00:00+00:00") is None
    assert not store.list_mailbox_action_states([failed.action.classification_id])[failed.action.classification_id][0]["retriable"]
    EmailStore(store.path)


def test_interrupted_explicit_claim_does_not_renew(tmp_path: Path):
    store = EmailStore(tmp_path / "mail.sqlite3")
    action_id = _seed_failed(store, "interrupted", uid=7)
    failed = store.list_failed_direct_actions(
        account_id=ACCOUNT, action_types=(EmailAction.MOVE,), limit=1,
    )[0]
    assert _release(store,
        failed, source_locator=failed.action.locator, source_revision="source-v1",
        requested_at="2026-10-12T06:00:00+00:00", apply=True,
    )
    assert store.claim_direct_action(action_id=action_id, claimed_at="2026-10-12T06:00:00+00:00") is not None
    assert store.recover_stale_processing_actions(
        stale_before="2026-10-12T07:00:00+00:00", recovered_at="2026-10-12T07:00:00+00:00",
    ) == 1
    assert not _action_row(store.path, action_id)["next_attempt_at"]
    assert store.claim_direct_action(action_id=action_id, claimed_at="2026-10-12T09:00:00+00:00") is None
    EmailStore(store.path)
