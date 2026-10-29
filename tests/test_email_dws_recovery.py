"""Explicit DWS source verification; all provider operations are substitutes."""
from dataclasses import replace
from hashlib import sha256
import json
import sqlite3
from contextlib import closing
from copy import deepcopy

import pytest

from app.email_classifier_contracts import EmailAction
from app.email_store import email_imap_configuration_snapshot
from tests.test_email_dws_move_continuation import _store_with_move_and_dependents
from tests.test_email_store import _task2_account_values


def _seed_failed_dws(tmp_path):
    store, persisted, plan = _store_with_move_and_dependents(tmp_path)
    store.create_account({**_task2_account_values("dingtalk_primary"), "email_address": "derek@stardust.ai"})
    move = store.claim_next_direct_action(claimed_at="2026-10-29T11:01:00+00:00")
    store.complete_direct_action_attempt(
        move, status="done", provider_operation="reconciled_dws_readback",
        provider_target=move.locator.stable_message_identity,
        provider_result_id="dws-readback:DzzzzzzNJ0u:167:" + sha256(json.dumps({
            "account": "derek@stardust.ai", "sender": "xup@junhe.com",
            "subject": "JunHe test mail", "conversation": "junhe-thread",
            "received_at": "2026-05-07T08:56:33Z", "dws_message_id": "DzzzzzzNJ0u",
            "folder_id": "167", "action_id": str(move.action_id),
        }, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest(),
        error="", finished_at="2026-10-29T11:01:01+00:00",
    )
    flag = store.claim_next_direct_action(claimed_at="2026-10-29T11:01:02+00:00")
    context = store.verified_dws_move_context_for_action(flag)
    store.complete_direct_action_attempt(
        flag, status="failed", provider_operation="dws_mail_action",
        provider_target=flag.locator.stable_message_identity, provider_result_id="",
        error="dws_action_readback_mismatch:" + "b" * 64,
        finished_at="2026-10-29T11:01:03+00:00", retryable=False,
    )
    failed = store.list_failed_direct_actions(
        account_id=flag.account_id, action_types=(EmailAction.FLAG_IMPORTANT,),
        action_id=str(flag.action_id), limit=1,
    )[0]
    arguments = dict(
        dws_context=context, source_revision="c" * 64,
        requested_at="2026-10-29T11:02:00+00:00",
        account_configuration_snapshot=email_imap_configuration_snapshot(store.get_account(flag.account_id)),
    )
    return store, failed, arguments, move, plan


@pytest.fixture(scope="module")
def dws_baseline(tmp_path_factory):
    return _seed_failed_dws(tmp_path_factory.mktemp("dws-recovery-baseline"))


def failed_dws(tmp_path, dws_baseline):
    baseline, failed, args, move, plan = dws_baseline
    destination = tmp_path / "email.sqlite3"
    # Seed once with current public APIs; clone only a committed synthetic DB.
    with closing(sqlite3.connect(baseline.path.as_uri() + "?mode=ro", uri=True)) as source:
        with closing(sqlite3.connect(destination)) as target:
            source.backup(target)
    from app.email_store import EmailStore
    return EmailStore(destination), failed, deepcopy(args), move, plan


def test_explicit_dws_release_preserves_plan_action_and_receipts_and_is_once(tmp_path, dws_baseline):
    store, failed, args, move, plan = failed_dws(tmp_path, dws_baseline)
    old_move = store.list_action_attempts(move.action_id)
    old_flag = store.list_action_attempts(failed.action.action_id)
    assert store.release_source_verified_dws_action(failed, apply=False, **args)
    assert store.claim_next_direct_action(claimed_at=args["requested_at"]) is None
    assert store.release_source_verified_dws_action(failed, apply=True, **args)
    assert not store.release_source_verified_dws_action(failed, apply=True, **args)
    assert store.list_action_attempts(move.action_id) == old_move
    assert store.list_action_attempts(failed.action.action_id) == old_flag
    claimed = store.claim_next_direct_action(claimed_at=args["requested_at"])
    assert claimed.action_id == failed.action.action_id
    assert claimed.action_plan_id == plan
    assert claimed.attempt_number == 2
    store.complete_direct_action_attempt(
        claimed, status="failed", provider_operation="dws_mail_action",
        provider_target=claimed.locator.stable_message_identity,
        provider_result_id="", error="dws_action_readback_mismatch:" + "d" * 64,
        finished_at="2026-10-29T11:02:01+00:00", retryable=True,
    )
    assert store.claim_next_direct_action(claimed_at="2026-10-29T12:00:00+00:00") is None
    assert store.list_action_attempts(move.action_id) == old_move
    assert len(store.list_action_attempts(claimed.action_id)) == 2


@pytest.mark.parametrize("change", ["context", "error", "parameters", "config", "move", "busy", "plan", "attempt"])
def test_source_proof_does_not_release_changed_evidence(tmp_path, dws_baseline, change):
    store, failed, args, move, _ = failed_dws(tmp_path, dws_baseline)
    if change == "context":
        args["dws_context"] = {**args["dws_context"], "sender": "other@example.com"}
    elif change == "error":
        failed = replace(failed, error="stale failure")
    elif change == "parameters":
        failed = replace(failed, parameters_json="{}")
    elif change == "config":
        args["account_configuration_snapshot"] = "stale-config"
    elif change == "attempt":
        failed = replace(failed, action=replace(failed.action, attempt_number=3))
    else:
        from app.action_entity_storage import _cas_action
        from app.email_store import DIRECT_EMAIL_ACTION_SQL
        with store._connect() as db:
            if change == "move":
                receipt = store.list_action_attempts(move.action_id)[0]
                db.execute("update action_receipts set provider_result_id=? where id=?", ("dws-readback:changed:167:" + "a" * 64, receipt["id"]))
            elif change == "busy":
                sibling = db.execute("select action_id from (" + DIRECT_EMAIL_ACTION_SQL + ") where action_plan_id=? and action_type='mark_read'", (failed.action.action_plan_id,)).fetchone()[0]
                _cas_action(db, int(sibling), expected={"status": "pending"}, changes={"status": "processing"})
            else:
                from app.email_canonical_storage import update_email_classification
                update_email_classification(db, failed.action.classification_id, {"current_action_plan_id": "changed-plan"})
    assert not store.release_source_verified_dws_action(failed, apply=True, **args)
    assert store.claim_direct_action(action_id=str(failed.action.action_id), claimed_at=args["requested_at"]) is None


@pytest.mark.parametrize("already_flagged", [True, False])
def test_operator_source_check_is_read_only_and_worker_completes_same_actions(tmp_path, dws_baseline, already_flagged):
    from app import email_dws_actions as module
    from app.email_dws_actions import DwsVerifiedMoveActionExecutor
    from tests.test_email_dws_actions import FakeDws, message
    store, failed, args, move, plan = failed_dws(tmp_path, dws_baseline)
    dws = FakeDws(important=already_flagged, tags=("107", "11"), bad_message=message(subject="JunHe test mail", conversationId="junhe-thread"))
    before = store.list_action_attempts(move.action_id)
    def provider_factory():
        return _metadata_provider()
    provider_factory.account_configuration_snapshot = args["account_configuration_snapshot"]
    preview = module.release_dws_source_verified_retry(store, account_id="dingtalk_primary", action_id=str(failed.action.action_id), dws=dws, apply=False, imap_provider_factory=provider_factory)
    assert preview["eligible"] and not preview["released"]
    result = module.release_dws_source_verified_retry(store, account_id="dingtalk_primary", action_id=str(failed.action.action_id), dws=dws, apply=True, imap_provider_factory=provider_factory)
    assert result["released"]
    assert result["source_satisfied"] is already_flagged
    assert not any(c[1:4] == ["mail", "message", "batch-update"] for c in dws.commands)
    claimed = store.claim_direct_action(action_id=str(failed.action.action_id), claimed_at="2099-01-01T00:00:00+00:00")
    assert claimed is not None and claimed.action_plan_id == plan
    outcome = DwsVerifiedMoveActionExecutor(dws, important_signals_reader=lambda: frozenset({"\\Flagged"}), mailbox="derek@stardust.ai", context=store.verified_dws_move_context_for_action(claimed)).execute(claimed)
    assert outcome.status == "done"
    store.complete_direct_action_attempt(claimed, status="done", provider_operation=outcome.provider_operation, provider_target=outcome.provider_target, provider_result_id=outcome.provider_result_id, error="", finished_at="2099-01-01T00:00:01+00:00")
    read = store.claim_next_direct_action(claimed_at="2099-01-01T00:00:02+00:00")
    assert read.action_type is EmailAction.MARK_READ
    read_result = DwsVerifiedMoveActionExecutor(dws, important_signals_reader=lambda: frozenset({"\\Flagged"}), mailbox="derek@stardust.ai", context=store.verified_dws_move_context_for_action(read)).execute(read)
    assert read_result.status == "done" and read_result.provider_operation == "dws_readback_noop"
    store.complete_direct_action_attempt(read, status="done", provider_operation=read_result.provider_operation, provider_target=read_result.provider_target, provider_result_id=read_result.provider_result_id, error="", finished_at="2099-01-01T00:00:03+00:00")
    assert set(store.direct_action_statuses_for_plan(plan).values()) == {"done"}
    assert store.list_action_attempts(move.action_id) == before
    assert len(store.list_action_attempts(claimed.action_id)) == 2


def test_explicit_dws_cli_requires_exact_action_and_account():
    from app.cli import build_parser
    parsed = build_parser().parse_args(["retry-dws-source-verified-email-action", "--account", "dingtalk_primary", "--action-id", "127062"])
    assert not parsed.apply
    assert parsed.action_id == "127062"


def _metadata_provider():
    from types import SimpleNamespace
    return SimpleNamespace(required_important_signals=lambda folder: frozenset({"\\Flagged"}), close=lambda: None)


@pytest.mark.parametrize("extra", [set(), {"$Important"}])
def test_mailbox_requirement_probe_is_read_only_and_releases_session(extra):
    from app.email_provider_actions import DeterministicEmailActionExecutor, ImapDeterministicProvider
    from tests.test_email_provider_actions import FakeWritableImapSession
    session = FakeWritableImapSession(permanent_flags={"\\Seen", "\\Flagged"} | extra)
    provider = ImapDeterministicProvider(session, account_id="dingtalk_primary")
    released = []
    executor = DeterministicEmailActionExecutor(provider, hand_over_session=released.append)
    assert executor.required_important_signals("INBOX") == {"\\Flagged"} | extra
    assert released == [provider]
    assert all(call[2] is True for call in session.calls if call[0] == "select")
    assert not any(call[0] in {"uid", "create"} for call in session.calls)


@pytest.mark.parametrize("snapshot", [None, "stale-config"])
def test_operator_rejects_unattested_or_changed_factory_before_source_access(tmp_path, dws_baseline, snapshot):
    from app.email_dws_actions import release_dws_source_verified_retry
    store, failed, _, _, _ = failed_dws(tmp_path, dws_baseline)
    def factory():
        raise AssertionError("stale provider must not be opened")
    if snapshot is not None:
        factory.account_configuration_snapshot = snapshot
    result = release_dws_source_verified_retry(store, account_id="dingtalk_primary", action_id=str(failed.action.action_id), dws=None, imap_provider_factory=factory, apply=True)
    assert not result["released"] and not result["eligible"]
