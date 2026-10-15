"""Unpublished group4D: explicit converted copies, never default bootstrap proof."""

from contextlib import closing
import sqlite3

import pytest

from tests.test_email_action_plan_direct_dao import _normalized


@pytest.fixture
def reply_pair(tmp_path, request):
    import io
    import json
    import os
    from pathlib import Path
    import subprocess
    import sys
    import tarfile
    from app.action_email_storage import migrate_email_action_schema
    from app.provider_state_schema_release import (
        apply_provider_state_migration,
        capture_provider_state_source,
    )
    from app.email_classifier_contracts import EmailAction, EmailClassificationStatus
    from app.email_store import EmailStore
    from tests.test_email_store import _classification, _task2_account_values

    old = tmp_path / "published-old"
    old.mkdir()
    archive = subprocess.check_output(
        ["git", "archive", "2773d6f4", "app"], cwd=Path(__file__).resolve().parents[1]
    )
    with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
        tar.extractall(old, filter="data")
    classifications = [
        _classification(
            status=EmailClassificationStatus.PROCESSED,
            classification_id=i,
            message_id=f"reply-{i}",
            actions=(EmailAction.AUTO_REPLY,),
            action_parameters={
                EmailAction.AUTO_REPLY: {"instruction": "Acknowledge receipt."}
            },
            thread_id=f"thread-reply-{i}",
        )
        for i in (11, 12)
    ]
    source = tmp_path / "source.sqlite3"
    import inspect

    code = (
        inspect.getsource(_reply_arguments)
        + "\n"
        + inspect.getsource(_receipt_arguments)
        + """
import json,sys
from pathlib import Path
from app.store import AutoReplyStore
from app.email_store import EmailStore
from app.email_classifier_contracts import EmailClassification
p=Path(sys.argv[1]);AutoReplyStore(p);s=EmailStore(p)
s.create_account(json.loads(sys.argv[3]))
cs=[EmailClassification.model_validate_json(value) for value in json.loads(sys.argv[2])]
for c in cs:s.persist_scan_result(c,model_text='fixture features')
s._now=lambda:'2026-10-14T09:00:00+00:00'
if sys.argv[4]=='retryable':
    args,owner=_reply_arguments(s,cs[0]);s.claim_email_reply_dispatch(**args,owner=owner)
    s.release_email_reply_dispatch_retryable(args['action_identity'],owner=owner)
    with s._connect() as db:db.execute('update email_reply_dispatch_claims set rowid=-7')
if sys.argv[4]=='two_claims':
    for index,c in enumerate(cs):
        args,owner=_reply_arguments(s,c);args['outgoing_message_id']=f'<reply-{index}@example.com>'
        s.claim_email_reply_dispatch(**args,owner=owner)
        with s._connect() as db:db.execute('update email_reply_dispatch_claims set rowid=? where action_identity=?',(-7 if index==0 else 0,args['action_identity']))
if sys.argv[4] in ('receipt','noncanonical_receipt'):
    args,owner=_reply_arguments(s,cs[0]);s.persist_email_reply_receipt(**_receipt_arguments(args))
    with s._connect() as db:
        db.execute('update email_reply_receipts set rowid=0')
        if sys.argv[4]=='noncanonical_receipt':
            value=json.loads(db.execute('select provider_receipt_json from email_reply_receipts').fetchone()[0])
            db.execute('update email_reply_receipts set provider_receipt_json=?',(json.dumps(value),))

"""
    )
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            code,
            str(source),
            json.dumps([c.model_dump_json() for c in classifications]),
            json.dumps(_task2_account_values("dingtalk-account", enabled=True)),
            getattr(request, "param", ""),
        ],
        cwd=old,
        env={**os.environ, "PYTHONPATH": str(old)},
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stderr
    target = tmp_path / "target.sqlite3"
    with (
        closing(sqlite3.connect(source)) as original,
        closing(sqlite3.connect(target)) as db,
    ):
        original.backup(db)
        db.execute("pragma foreign_keys=off")
        db.execute("begin immediate")
        provider_source = capture_provider_state_source(db)
        migrate_email_action_schema(db)
        apply_provider_state_migration(db, provider_source)
        db.commit()
    store = object.__new__(EmailStore)
    store.path = target
    store.training_run_id = None
    store._now = lambda: "2026-10-14T10:00:00+00:00"
    return store, classifications[0], None, source, old


def _reply_arguments(store, classification):
    from app.email_classifier_contracts import EmailAction
    from app.email_store import email_action_identity

    if isinstance(classification, int):
        data = store.get_classification(classification)
        classification_id, plan_id = (
            classification,
            data["action_plan"]["action_plan_id"],
        )
    else:
        classification_id, plan_id = (
            classification.classification_id,
            classification.action_plan.action_plan_id,
        )
    auth = store.get_email_reply_delivery_authorization(classification_id, plan_id)
    assert auth and auth["actions"] == ["auto_reply"]
    account = store.get_account(auth["account_id"])
    identity = email_action_identity(
        account_id=auth["account_id"],
        stable_message_identity=auth["stable_message_identity"],
        action_type=EmailAction.AUTO_REPLY,
        action_plan_version=auth["action_plan_version"],
    )
    args = dict(
        action_identity=identity,
        effect_digest="e" * 64,
        action_plan_id=auth["action_plan_id"],
        classification_id=auth["classification_id"],
        account_id=auth["account_id"],
        stable_message_identity=auth["stable_message_identity"],
        outgoing_message_id="<reply-fixture@example.com>",
        sender=account["email_address"],
        thread_identity=auth["thread_identity"],
        expected_account_updated_at=account["updated_at"],
    )
    return args, dict(
        owner_id="reply-worker", generation=1, lease_token="reply-lease-a"
    )


def _receipt_arguments(args):
    return {
        key: args[key]
        for key in (
            "action_identity",
            "effect_digest",
            "action_plan_id",
            "classification_id",
            "account_id",
            "stable_message_identity",
            "outgoing_message_id",
        )
    } | dict(
        provider_operation="sent_readback",
        provider_target="sent-message",
        provider_result_id="sent-result",
        sent_folder="Sent",
        sent_uidvalidity=42,
        sent_uid=101,
        smtp_acceptance_id="",
    )


def _old_result(pair, operation):
    import inspect
    import json
    import os
    import subprocess
    import sys

    _, classification, _, source, old = pair
    code = (
        """
import json,sys
from pathlib import Path
from app.email_store import EmailStore
from app.email_classifier_contracts import EmailClassification
s=EmailStore(Path(sys.argv[1]));s._now=lambda:'2026-10-14T10:00:00+00:00'
c=EmailClassification.model_validate_json(sys.argv[2])
"""
        + "\n".join(
            inspect.getsource(f)
            for f in (_normalized, _reply_arguments, _receipt_arguments, operation)
        )
        + f"\nprint(json.dumps(_normalized({operation.__name__}(s,c))))\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", code, str(source), classification.model_dump_json()],
        cwd=old,
        env={**os.environ, "PYTHONPATH": str(old)},
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def _claim_owner_cycle(store, classification):
    from app.email_store import EmailReplyDispatchConflict

    args, owner = _reply_arguments(store, classification)
    identity = args["action_identity"]
    errors = []
    assert store.get_email_reply_receipt(identity) is None
    assert store.get_email_reply_dispatch_claim(identity) is None
    first = store.claim_email_reply_dispatch(**args, owner=owner)
    assert first["acquired"] and first["status"] == "dispatching"
    same = store.claim_email_reply_dispatch(**args, owner=owner)
    other = store.claim_email_reply_dispatch(
        **args, owner=owner | dict(generation=2, lease_token="new")
    )
    assert (
        not same["acquired"]
        and not other["acquired"]
        and other["owner_generation"] == 1
    )
    for changed in (
        dict(effect_digest="f" * 64),
        dict(outgoing_message_id="<changed@example.com>"),
    ):
        try:
            store.claim_email_reply_dispatch(**(args | changed), owner=owner)
        except EmailReplyDispatchConflict as exc:
            errors.append(str(exc))
        else:
            raise AssertionError("immutable claim conflict was accepted")
    for mutation in (
        dict(owner_id="other"),
        dict(generation=2),
        dict(lease_token="other"),
    ):
        try:
            store.mark_email_reply_dispatch_uncertain(identity, owner=owner | mutation)
        except EmailReplyDispatchConflict as exc:
            errors.append(str(exc))
        else:
            raise AssertionError("stale owner transitioned claim")
    store.mark_email_reply_dispatch_uncertain(identity, owner=owner)
    replay = store.claim_email_reply_dispatch(
        **args, owner=owner | dict(generation=2, lease_token="new")
    )
    assert replay["status"] == "uncertain" and replay["acquired"] is False
    return (
        first,
        same,
        other,
        errors,
        replay,
        store.get_email_reply_dispatch_claim(identity),
    )


def test_claim_replay_immutable_binding_and_owner_cas_match_genuine_source(reply_pair):
    expected = _old_result(reply_pair, _claim_owner_cycle)
    assert _normalized(_claim_owner_cycle(*reply_pair[:2])) == expected
    _assert_native_facts(reply_pair)


def _assert_native_facts(pair):
    from app.action_storage import EMAIL_FIELDS, email_action_projection

    store, _, _, source, _ = pair
    with (
        closing(sqlite3.connect(source)) as old,
        closing(sqlite3.connect(store.path)) as db,
    ):
        for kind in ("email_reply_dispatch_claims", "email_reply_receipts"):
            fields = ["rowid", *EMAIL_FIELDS[kind]]
            projection = email_action_projection(kind, include_rowid=True)
            selection = ",".join(fields + [f"typeof({field})" for field in fields])
            assert (
                db.execute(
                    f"select {selection} from ({projection}) order by rowid"
                ).fetchall()
                == old.execute(
                    f"select {selection} from {kind} order by rowid"
                ).fetchall()
            ), kind


def _retry_snapshot_cycle(store, classification):
    from app.email_store import EmailReplyDispatchConflict

    args, owner = _reply_arguments(store, classification)
    identity = args["action_identity"]
    before = store.get_email_reply_dispatch_claim(identity)
    assert before["status"] == "retryable"
    account = store.get_account(args["account_id"])
    from datetime import datetime, timezone

    store._account_now = lambda: datetime(2026, 10, 15, 10, tzinfo=timezone.utc)
    store.update_account(
        args["account_id"], account | dict(imap_host="imap.changed.example")
    )
    unchanged = store.get_email_reply_dispatch_claim(identity)
    assert unchanged == before
    stale = store.claim_email_reply_dispatch(
        **args, owner=owner | dict(generation=2, lease_token="new")
    )
    assert stale is None
    refreshed, _ = _reply_arguments(store, classification)
    renewed = store.claim_email_reply_dispatch(
        **refreshed, owner=owner | dict(generation=2, lease_token="new")
    )
    assert (
        renewed["acquired"]
        and renewed["account_snapshot"]["imap_host"] == "imap.changed.example"
    )
    assert before["account_snapshot"]["imap_host"] != "imap.changed.example"
    errors = []
    try:
        store.release_email_reply_dispatch_retryable(identity, owner=owner)
    except EmailReplyDispatchConflict as exc:
        errors.append(str(exc))
    else:
        raise AssertionError("old owner released new dispatch")
    store.release_email_reply_dispatch_retryable(
        identity, owner=owner | dict(generation=2, lease_token="new")
    )
    third = store.claim_email_reply_dispatch(
        **refreshed, owner=owner | dict(generation=3, lease_token="third")
    )
    return (
        before,
        unchanged,
        stale,
        renewed,
        errors,
        third,
        store.get_email_reply_dispatch_claim(identity),
    )


@pytest.mark.parametrize("reply_pair", ["retryable"], indirect=True)
def test_retry_rebinds_snapshot_and_owner_without_changing_source_rowid(reply_pair):
    expected = _old_result(reply_pair, _retry_snapshot_cycle)
    assert _normalized(_retry_snapshot_cycle(*reply_pair[:2])) == expected
    _assert_native_facts(reply_pair)


def _terminated_owner_cycle(store, classification):
    from app.email_store import EmailReplyDispatchConflict

    args, owner = _reply_arguments(store, classification)
    errors = []
    try:
        store.recover_terminated_email_reply_dispatch_claims(
            owner=owner,
            termination_verifier=lambda o: False,
            recovered_at="2026-10-14T11:00:00+00:00",
        )
    except EmailReplyDispatchConflict as exc:
        errors.append(str(exc))
    wrong = store.recover_terminated_email_reply_dispatch_claims(
        owner=owner | dict(generation=2),
        termination_verifier=lambda o: True,
        recovered_at="2026-10-14T11:00:00+00:00",
    )
    count = store.recover_terminated_email_reply_dispatch_claims(
        owner=owner,
        termination_verifier=lambda o: True,
        recovered_at="2026-10-14T11:00:00+00:00",
    )
    again = store.recover_terminated_email_reply_dispatch_claims(
        owner=owner,
        termination_verifier=lambda o: True,
        recovered_at="2026-10-14T11:00:00+00:00",
    )
    assert (wrong, count, again) == (0, 2, 0)
    args["outgoing_message_id"] = "<reply-0@example.com>"
    replay = store.claim_email_reply_dispatch(
        **args, owner=owner | dict(generation=2, lease_token="new")
    )
    assert replay["status"] == "uncertain" and not replay["acquired"]
    try:
        store.release_email_reply_dispatch_retryable(
            args["action_identity"], owner=owner
        )
    except EmailReplyDispatchConflict as exc:
        errors.append(str(exc))
    else:
        raise AssertionError("uncertain dispatch was replayed as retryable")
    return errors, wrong, count, again, replay


@pytest.mark.parametrize("reply_pair", ["two_claims"], indirect=True)
def test_proven_terminated_owner_recovers_only_exact_generation_and_never_replays(
    reply_pair,
):
    expected = _old_result(reply_pair, _terminated_owner_cycle)
    assert _normalized(_terminated_owner_cycle(*reply_pair[:2])) == expected
    _assert_native_facts(reply_pair)


def _receipt_cycle(store, classification):
    from app.email_store import EmailReplyReceiptConflict, EmailReplyDispatchConflict

    args, owner = _reply_arguments(store, classification)
    identity = args["action_identity"]
    errors = []
    store.claim_email_reply_dispatch(**args, owner=owner)
    receipt_args = _receipt_arguments(args)
    for bad_owner in (None, owner | dict(generation=2)):
        try:
            store.persist_email_reply_receipt(**receipt_args, claim_owner=bad_owner)
        except EmailReplyDispatchConflict as exc:
            errors.append(str(exc))
        else:
            raise AssertionError("unowned receipt completed live claim")
        assert store.get_email_reply_receipt(identity) is None
        assert store.get_email_reply_dispatch_claim(identity)["status"] == "dispatching"
    first = store.persist_email_reply_receipt(**receipt_args, claim_owner=owner)
    assert store.get_email_reply_dispatch_claim(identity)["status"] == "done"
    exact = store.persist_email_reply_receipt(**receipt_args)
    enriched = store.persist_email_reply_receipt(
        **(receipt_args | dict(smtp_acceptance_id="smtp-later"))
    )
    blank = store.persist_email_reply_receipt(**receipt_args)
    assert (
        first == exact
        and enriched == blank
        and blank["provider_receipt"]["smtp_acceptance_id"] == "smtp-later"
    )
    for changed in (
        dict(smtp_acceptance_id="smtp-conflict"),
        dict(sent_uid=102),
        dict(provider_result_id="other-result"),
        dict(effect_digest="f" * 64),
    ):
        try:
            store.persist_email_reply_receipt(**(receipt_args | changed))
        except EmailReplyReceiptConflict as exc:
            errors.append(str(exc))
        else:
            raise AssertionError("conflicting provider evidence replaced receipt")
    done = store.claim_email_reply_dispatch(
        **(args | dict(effect_digest="f" * 64)),
        owner=owner | dict(generation=2, lease_token="new"),
    )
    assert done["status"] == "done" and not done["acquired"]
    return (
        first,
        exact,
        enriched,
        blank,
        errors,
        done,
        store.get_email_reply_receipt(identity),
        store.list_email_classification_observability(11),
        store.list_email_context_receipts(
            account_id=args["account_id"],
            stable_message_identity=args["stable_message_identity"],
        ),
    )


def test_receipt_completion_replay_enrichment_conflicts_and_shared_readbacks(
    reply_pair,
):
    expected = _old_result(reply_pair, _receipt_cycle)
    assert _normalized(_receipt_cycle(*reply_pair[:2])) == expected
    _assert_native_facts(reply_pair)


def _unique_and_authorization_cycle(store, classification):
    import sqlite3

    args, owner = _reply_arguments(store, classification)
    errors = []
    for changed in (
        dict(action_identity="forged"),
        dict(expected_account_updated_at="stale"),
        dict(thread_identity="other-thread"),
        dict(sender="other@example.com"),
    ):
        assert store.claim_email_reply_dispatch(**(args | changed), owner=owner) is None
    first = store.claim_email_reply_dispatch(**args, owner=owner)
    second, _ = _reply_arguments(store, 12)
    try:
        store.claim_email_reply_dispatch(**second, owner=owner)
    except sqlite3.IntegrityError as exc:
        errors.append(exc.sqlite_errorcode)
    else:
        raise AssertionError("duplicate outgoing Message-ID was accepted")
    assert store.get_email_reply_dispatch_claim(second["action_identity"]) is None
    for statement, params in (
        (
            "update email_accounts set imap_host='changed' where account_id=?",
            (args["account_id"],),
        ),
        (
            "update email_classifications set current_action_plan_id=null where id=?",
            (11,),
        ),
        (
            "update email_messages set thread_identity='changed' where stable_message_identity=?",
            (args["stable_message_identity"],),
        ),
    ):
        try:
            with store._connect() as db:
                db.execute(statement, params)
        except sqlite3.IntegrityError as exc:
            errors.append(str(exc))
        else:
            raise AssertionError("in-flight dispatch binding changed")
    second["outgoing_message_id"] = "<second@example.com>"
    claimed_second = store.claim_email_reply_dispatch(**second, owner=owner)
    store.persist_email_reply_receipt(**_receipt_arguments(args), claim_owner=owner)
    for changed in (
        dict(action_identity="different-action"),
        dict(
            action_plan_id="missing-plan",
            outgoing_message_id="<missing@example.com>",
            action_identity="missing-action",
        ),
    ):
        try:
            store.persist_email_reply_receipt(**(_receipt_arguments(args) | changed))
        except sqlite3.IntegrityError as exc:
            errors.append(exc.sqlite_errorcode)
        else:
            raise AssertionError("native unique/FK constraint was suppressed")
    return first, claimed_second, errors


def test_outgoing_uniqueness_authorization_and_native_parent_blockers(reply_pair):
    expected = _old_result(reply_pair, _unique_and_authorization_cycle)
    assert _normalized(_unique_and_authorization_cycle(*reply_pair[:2])) == expected
    _assert_native_facts(reply_pair)


def _stored_receipt_cycle(store, classification):
    args, owner = _reply_arguments(store, classification)
    identity = args["action_identity"]
    receipt_args = _receipt_arguments(args)
    first = store.get_email_reply_receipt(identity)
    replay = store.persist_email_reply_receipt(**receipt_args)
    enriched = store.persist_email_reply_receipt(
        **(receipt_args | dict(smtp_acceptance_id="smtp-later"))
    )
    assert replay == first and enriched["created_at"] == first["created_at"]
    assert store.get_email_reply_dispatch_claim(identity) is None
    done = store.claim_email_reply_dispatch(**args, owner=owner)
    assert done["status"] == "done" and not done["acquired"]
    assert store.get_email_reply_dispatch_claim(identity) is None
    return first, replay, enriched, done


@pytest.mark.parametrize("reply_pair", ["receipt"], indirect=True)
def test_existing_receipt_rowid_zero_enriches_without_inventing_dispatch_claim(
    reply_pair,
):
    expected = _old_result(reply_pair, _stored_receipt_cycle)
    assert _normalized(_stored_receipt_cycle(*reply_pair[:2])) == expected
    _assert_native_facts(reply_pair)


def _noncanonical_receipt_cycle(store, classification):
    from app.email_store import EmailReplyReceiptConflict

    args, _ = _reply_arguments(store, classification)
    identity = args["action_identity"]
    before = store.get_email_reply_receipt(identity)
    error = ""
    try:
        store.persist_email_reply_receipt(
            **(_receipt_arguments(args) | dict(smtp_acceptance_id="smtp-later"))
        )
    except EmailReplyReceiptConflict as exc:
        error = str(exc)
    else:
        raise AssertionError("original JSON text CAS was broadened")
    after = store.get_email_reply_receipt(identity)
    assert error == "email reply receipt changed concurrently" and before == after
    return before, error, after


@pytest.mark.parametrize("reply_pair", ["noncanonical_receipt"], indirect=True)
def test_receipt_json_cas_preserves_original_text_comparison_and_no_mutation(
    reply_pair,
):
    expected = _old_result(reply_pair, _noncanonical_receipt_cycle)
    assert _normalized(_noncanonical_receipt_cycle(*reply_pair[:2])) == expected
    _assert_native_facts(reply_pair)


def _atomic_receipt_cycle(store, classification):
    import sqlite3
    from app.email_store import EmailReplyDispatchConflict

    args, owner = _reply_arguments(store, classification)
    identity = args["action_identity"]
    receipt_args = _receipt_arguments(args)
    store.claim_email_reply_dispatch(**args, owner=owner)
    store.mark_email_reply_dispatch_uncertain(identity, owner=owner)
    with store._connect() as db:
        native = (
            db.execute(
                "select 1 from sqlite_master where type='table' and name='email_reply_dispatch_claims'"
            ).fetchone()
            is not None
        )
        if native:
            db.execute("""create trigger test_claim_done_fault before update of status on email_reply_dispatch_claims
                when new.status='done' begin select raise(abort,'claim_done_fault');end""")
        else:
            db.execute("""create trigger test_claim_done_fault before update of payload_json on actions
                when new.record_type='email_reply_dispatch_claims' and new.email_status='done'
                begin select raise(abort,'claim_done_fault');end""")
    try:
        store.persist_email_reply_receipt(**receipt_args, claim_owner=owner)
    except sqlite3.IntegrityError as exc:
        assert str(exc) == "claim_done_fault"
    else:
        raise AssertionError("claim completion fault did not fire")
    assert store.get_email_reply_receipt(identity) is None
    assert store.get_email_reply_dispatch_claim(identity)["status"] == "uncertain"
    with store._connect() as db:
        db.execute("drop trigger test_claim_done_fault")
    first = store.persist_email_reply_receipt(**receipt_args, claim_owner=owner)
    # A SQL-valid uncertain claim with an existing receipt is also reconcilable.
    # Set this synthetic current state explicitly; the real source API determines
    # whether the following enrichment/owner completion transaction is accepted.
    with store._connect() as db:
        if native:
            db.execute(
                "update email_reply_dispatch_claims set status='uncertain' where action_identity=?",
                (identity,),
            )
            db.execute(
                "update email_reply_receipts set rowid=0 where action_identity=?",
                (identity,),
            )
        else:
            from app.action_storage import email_action_projection, update_email_action

            for kind, values in (
                ("email_reply_dispatch_claims", dict(status="uncertain")),
                ("email_reply_receipts", None),
            ):
                row = db.execute(
                    "select rowid from ("
                    + email_action_projection(kind, include_rowid=True)
                    + ") where action_identity=?",
                    (identity,),
                ).fetchone()
                if values:
                    update_email_action(db, kind, row["rowid"], values)
                else:
                    update_email_action(db, kind, row["rowid"], {}, new_source_rowid=0)
    error = ""
    try:
        store.persist_email_reply_receipt(
            **(receipt_args | dict(smtp_acceptance_id="smtp-later")),
            claim_owner=owner | dict(lease_token="wrong"),
        )
    except EmailReplyDispatchConflict as exc:
        error = str(exc)
    else:
        raise AssertionError("enrichment survived mismatched owner")
    assert error == "email reply dispatch owner fence changed"
    assert store.get_email_reply_receipt(identity) == first
    assert store.get_email_reply_dispatch_claim(identity)["status"] == "uncertain"
    final = store.persist_email_reply_receipt(
        **(receipt_args | dict(smtp_acceptance_id="smtp-later")), claim_owner=owner
    )
    assert final["provider_receipt"]["smtp_acceptance_id"] == "smtp-later"
    assert store.get_email_reply_dispatch_claim(identity)["status"] == "done"
    return first, error, final, store.get_email_reply_dispatch_claim(identity)


def test_receipt_insert_and_enrichment_roll_back_on_claim_failure(reply_pair):
    expected = _old_result(reply_pair, _atomic_receipt_cycle)
    assert _normalized(_atomic_receipt_cycle(*reply_pair[:2])) == expected
    _assert_native_facts(reply_pair)


def _multirow_recovery_fault(store, classification):
    import sqlite3

    args, owner = _reply_arguments(store, classification)
    identity = args["action_identity"]
    before = store.get_email_reply_dispatch_claim(identity)
    second, _ = _reply_arguments(store, 12)
    with store._connect() as db:
        native = (
            db.execute(
                "select 1 from sqlite_master where type='table' and name='email_reply_dispatch_claims'"
            ).fetchone()
            is not None
        )
        if native:
            db.execute(
                """create trigger test_recovery_fault before update of status on email_reply_dispatch_claims
                when new.action_identity=? and new.status='uncertain'
                begin select raise(abort,'recovery_fault');end""".replace(
                    "?", "'" + second["action_identity"] + "'"
                )
            )
        else:
            db.execute(
                """create trigger test_recovery_fault before update of payload_json on actions
                when new.record_type='email_reply_dispatch_claims' and new.email_action_identity=? and new.email_status='uncertain'
                begin select raise(abort,'recovery_fault');end""".replace(
                    "?", "'" + second["action_identity"] + "'"
                )
            )
    try:
        store.recover_terminated_email_reply_dispatch_claims(
            owner=owner,
            termination_verifier=lambda o: True,
            recovered_at="2026-10-14T11:00:00+00:00",
        )
    except sqlite3.IntegrityError as exc:
        assert str(exc) == "recovery_fault"
    else:
        raise AssertionError("multi-row recovery fault did not fire")
    with store._connect() as db:
        db.execute("drop trigger test_recovery_fault")
    assert store.get_email_reply_dispatch_claim(identity) == before
    assert (
        store.get_email_reply_dispatch_claim(second["action_identity"])["status"]
        == "dispatching"
    )
    return before, store.get_email_reply_dispatch_claim(second["action_identity"])


@pytest.mark.parametrize("reply_pair", ["two_claims"], indirect=True)
def test_multirow_recovery_failure_preserves_both_original_claims(reply_pair):
    expected = _old_result(reply_pair, _multirow_recovery_fault)
    assert _normalized(_multirow_recovery_fault(*reply_pair[:2])) == expected
    _assert_native_facts(reply_pair)


def test_actual_reply_claim_lookup_uses_existing_typed_index_with_large_volume(
    reply_pair,
):
    from app.action_storage import EMAIL_ACTION_PROJECTIONS, insert_email_action

    store, classification, *_ = reply_pair
    args, owner = _reply_arguments(store, classification)
    expected = _old_result(reply_pair, _claim_owner_cycle)[1]
    store.claim_email_reply_dispatch(**args, owner=owner)
    original = store._open_connection

    def measure():
        steps = 0
        traced = []

        def count():
            nonlocal steps
            steps += 1
            return 0

        def instrument():
            db = original()
            db.set_progress_handler(count, 1)
            db.set_trace_callback(traced.append)
            return db

        store._open_connection = instrument
        try:
            result = store.claim_email_reply_dispatch(**args, owner=owner)
        finally:
            store._open_connection = original
        return _normalized(result), steps, traced

    small, small_steps, _ = measure()
    assert small == expected
    with store._connect() as db:
        claim = dict(
            db.execute(
                "select * from ("
                + EMAIL_ACTION_PROJECTIONS["email_reply_dispatch_claims"]
                + ") where action_identity=?",
                (args["action_identity"],),
            ).fetchone()
        )
        db.execute("begin immediate")
        for index in range(600):
            insert_email_action(
                db,
                "email_reply_dispatch_claims",
                claim
                | dict(
                    action_identity=f"unrelated-reply-{index}",
                    outgoing_message_id=f"<unrelated-{index}@example.com>",
                ),
            )
        for index in range(3000):
            insert_email_action(
                db,
                "email_unsubscribe_cancellations",
                dict(
                    action_identity=f"unrelated-cancellation-{index}",
                    decision_json="{}",
                ),
            )
    large, large_steps, traced = measure()
    assert large == small
    assert large_steps <= small_steps + 200, (small_steps, large_steps)
    with store._connect() as db:
        queries = [
            sql
            for sql in traced
            if sql.lstrip().lower().startswith("select")
            and "record_type='email_reply_dispatch_claims'" in sql
        ]
        assert queries
        plans = [
            row[3] for sql in queries for row in db.execute("explain query plan " + sql)
        ]
        assert any(
            "SEARCH a USING INDEX idx_actions_email_email_reply_dispatch_claims_2"
            in plan
            for plan in plans
        ), plans
        assert not any("SCAN " in plan for plan in plans), plans
    print(
        f"reply claim VM steps {small_steps} -> {large_steps}; 600 same-family plus 3000 unrelated rows"
    )


def _nullable_public_key_cycle(store, classification):
    args, owner = _reply_arguments(store, classification)
    identity = args["action_identity"]
    store.claim_email_reply_dispatch(**args, owner=owner)
    store.persist_email_reply_receipt(**_receipt_arguments(args), claim_owner=owner)
    # Preserve the native TEXT PRIMARY KEY allowance; SQL NULL is not a
    # physical actions identity and the original equality lookup finds no row.
    with store._connect() as db:
        native = (
            db.execute(
                "select 1 from sqlite_master where type='table' and name='email_reply_receipts'"
            ).fetchone()
            is not None
        )
        for kind in ("email_reply_dispatch_claims", "email_reply_receipts"):
            if native:
                db.execute(
                    f"update {kind} set action_identity=null where action_identity=?",
                    (identity,),
                )
            else:
                from app.action_storage import (
                    email_action_projection,
                    update_email_action,
                )

                row = db.execute(
                    "select rowid from ("
                    + email_action_projection(kind, include_rowid=True)
                    + ") where action_identity=?",
                    (identity,),
                ).fetchone()
                update_email_action(db, kind, row["rowid"], dict(action_identity=None))
    assert store.get_email_reply_receipt(None) is None
    assert store.get_email_reply_dispatch_claim(None) is None
    errors = []
    for operation in (
        lambda: store.claim_email_reply_dispatch(
            **(args | dict(action_identity=None)), owner=owner
        ),
        lambda: store.persist_email_reply_receipt(
            **(_receipt_arguments(args) | dict(action_identity=None))
        ),
    ):
        try:
            operation()
        except ValueError as exc:
            errors.append(str(exc))
        else:
            raise AssertionError("application accepted a NULL action identity")
    assert len(errors) == 2
    return (
        errors,
        store.get_email_reply_receipt(identity),
        store.get_email_reply_dispatch_claim(identity),
    )


def test_native_nullable_public_keys_remain_distinct_from_strict_application_inputs(
    reply_pair,
):
    expected = _old_result(reply_pair, _nullable_public_key_cycle)
    assert _normalized(_nullable_public_key_cycle(*reply_pair[:2])) == expected
    _assert_native_facts(reply_pair)
