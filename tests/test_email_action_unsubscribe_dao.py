"""Unsubscribe DAO parity on genuinely migrated final-schema copies."""

from contextlib import closing
import sqlite3

import pytest

from app.action_storage import insert_email_action
from tests.test_email_action_storage import _cases
from tests.test_email_action_writes import _databases


def test_continuation_native_upsert_preserves_locator_created_at_and_scalar_codec():
    source, target = _databases()
    with closing(source), closing(target):
        for db in (source, target):
            for kind in ("email_action_plans", "email_unsubscribe_claims"):
                values = _cases()[kind]
                if db is source:
                    db.execute(
                        f"insert into {kind}({','.join(values)}) values({','.join('?' for _ in values)})",
                        tuple(values.values()),
                    )
                else:
                    insert_email_action(db, kind, values)

        def upsert(db, values, *, source_rowid=None):
            if db is source:
                if source_rowid is not None:
                    values = {"rowid": source_rowid, **values}
                return db.execute(
                    f"""insert into email_unsubscribe_continuations({",".join(values)})
                    values({",".join("?" for _ in values)}) on conflict(action_identity) do update set
                    effect_digest=excluded.effect_digest,observation_reference=excluded.observation_reference,
                    controls_json=excluded.controls_json,updated_at=excluded.updated_at""",
                    tuple(values.values()),
                )
            return insert_email_action(
                db,
                "email_unsubscribe_continuations",
                values,
                source_rowid=source_rowid,
                update_continuation_conflict=True,
            )

        initial = _cases()["email_unsubscribe_continuations"] | {
            "created_at": "original-created"
        }
        changed = initial | {
            "effect_digest": "later-digest",
            "observation_reference": b"opaque\x00blob",
            "controls_json": b"[]",
            "created_at": "must-not-replace",
            "updated_at": "updated",
        }
        for db in (source, target):
            assert upsert(db, initial, source_rowid=-7).rowcount == 1
            updated = upsert(db, changed, source_rowid=9)
            assert updated.rowcount == 1
            if db is target:
                assert updated.lastrowid == -7
            for invalid in (
                {"created_at": None},
                {"controls_json": "not-json"},
                {"controls_json": None},
            ):
                with pytest.raises(sqlite3.IntegrityError):
                    upsert(db, changed | invalid)
            assert (
                upsert(db, initial | {"action_identity": None}, source_rowid=0).rowcount
                == 1
            )
            assert upsert(db, changed, source_rowid=0).rowcount == 1
            with pytest.raises(sqlite3.IntegrityError):
                upsert(
                    db, initial | {"action_identity": "missing-claim"}, source_rowid=-7
                )
            with pytest.raises(sqlite3.IntegrityError):
                upsert(db, initial | {"action_identity": "missing-claim"})
        from app.action_storage import email_action_projection

        projection = email_action_projection(
            "email_unsubscribe_continuations", include_rowid=True
        )
        fields = "rowid,action_identity,effect_digest,observation_reference,controls_json,created_at,updated_at"
        assert (
            target.execute(
                "select " + fields + " from (" + projection + ") order by rowid"
            ).fetchall()
            == source.execute(
                "select "
                + fields
                + " from email_unsubscribe_continuations order by rowid"
            ).fetchall()
        )
        assert target.execute(
            "select typeof(observation_reference),typeof(controls_json) from ("
            + projection
            + ") where action_identity='claim'"
        ).fetchone() == ("blob", "blob")
        for db in (source, target):
            db.commit()
            db.execute("begin")
            upsert(db, initial | {"updated_at": "rolled-back"})
            db.rollback()
        assert (
            target.execute(
                "select " + fields + " from (" + projection + ") order by rowid"
            ).fetchall()
            == source.execute(
                "select "
                + fields
                + " from email_unsubscribe_continuations order by rowid"
            ).fetchall()
        )


@pytest.fixture
def unsubscribe_pair(tmp_path, request):
    """Genuine archived constructors seed source; only owned copy is converted."""
    import io
    import json
    import os
    from pathlib import Path
    import subprocess
    import sys
    import tarfile
    from app.store_schema_release import prepare_database_schema_release
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
    classification = _classification(
        status=EmailClassificationStatus.PROCESSED,
        classification_id=11,
        actions=(EmailAction.UNSUBSCRIBE,),
        action_parameters={},
        thread_id="thread-unsubscribe-41",
    )
    source = tmp_path / "source.sqlite3"
    import inspect

    code = (
        inspect.getsource(_authorization)
        + """
import sys,json
from pathlib import Path
from app.store import AutoReplyStore
from app.email_store import EmailStore
from app.email_classifier_contracts import EmailClassification
p=Path(sys.argv[1]);AutoReplyStore(p);s=EmailStore(p)
s.create_account(json.loads(sys.argv[3]))
c=EmailClassification.model_validate_json(sys.argv[2]);s.persist_scan_result(c,model_text='fixture features')
a,owner=_authorization(c)
s.claim_email_unsubscribe_write(**a,owner=owner)
with s._connect() as db:
    db.execute("insert into email_unsubscribe_steps(id,action_identity,effect_digest,sequence,operation,state,reference,created_at) values(700,?,?,1,'open_entry','done','seed','seed')",(a['action_identity'],a['effect_digest']))
    db.execute('delete from email_unsubscribe_steps')
    db.execute('delete from email_unsubscribe_effects')
    db.execute('delete from email_unsubscribe_claims')
payload={k:v for k,v in a.items() if k not in ('operations','effect_digest','entry_reference')}
payload.update(schema='email_agent_action.v1',lifecycle_version='email_unsubscribe_audited_v2',action_type='unsubscribe',
    unsubscribe_entries=[dict(index=0,source='header_https',digest='b'*64,reference=a['entry_reference'])],unsubscribe_authentication=None)
from app.email_task_adapter import email_conversation_id
t=AutoReplyStore(p)
task=t.ensure_reply_task(channel='email',conversation_id=email_conversation_id(a['account_id'],a['thread_identity']),conversation_title='Email',single_chat=True,
    trigger_message_id=a['action_identity'],trigger_create_time='2026-10-14T09:00:00+00:00',trigger_sender='sender@example.com',
    trigger_text='unsubscribe',trigger_message_json=json.dumps(payload),execution_generation='generation-explicit-retry')
task=t.claim_reply_task(task.id)
t.fail_reply_task(task.id,'original-browser-failure',expected_execution_generation=task.execution_generation)
if sys.argv[4] in ('audit','orphan','lineage'):
    from app.store import AgentRole
    consumer=t.claim_agent_run(task.id,task.execution_generation,role=AgentRole.CONSUMER,proposal_revision=0,turn_attempt=0,
        parent_agent_run_id=None,operation_id='',owner='consumer-owner').run
    t.complete_agent_run(consumer.id,{'outcome':'proposal'},owner='consumer-owner')
    audit=t.claim_agent_run(task.id,task.execution_generation,role=AgentRole.AUDIT,proposal_revision=0,turn_attempt=0,
        parent_agent_run_id=consumer.id,operation_id='email-unsubscribe-audit',owner='audit-owner').run
    t.fail_agent_run(audit.id,dict(code='email_unsubscribe_browser_failed',retryable=True,authorization_required=False,
        session_continuable=False),owner='audit-owner')
    s.claim_email_unsubscribe_write(**a,owner=owner)
    with s._connect() as db:
        db.execute("update email_unsubscribe_claims set status=?,phase='effect_uncertain',audit_agent_run_id=? where action_identity=?",('uncertain' if sys.argv[4] in ('audit','lineage') else 'dispatching',audit.id,a['action_identity']))
        db.execute('update email_unsubscribe_effects set audit_agent_run_id=? where action_identity=?',(audit.id,a['action_identity']))
        db.execute('update email_unsubscribe_claims set rowid=-7 where action_identity=?',(a['action_identity'],))
        if sys.argv[4]=='lineage':
            db.execute("update email_unsubscribe_claims set owner_id=?,owner_generation=1 where action_identity=?",(f'email-worker:{audit.id}',a['action_identity']))
"""
    )
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            code,
            str(source),
            classification.model_dump_json(),
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
    release = prepare_database_schema_release(Path(__file__).resolve().parents[1], target)
    try:
        release.publish()
    except subprocess.CalledProcessError as error:
        error.add_note(error.stderr or "paired child returned no stderr")
        raise
    store = EmailStore(target)
    store._now = lambda: "2026-10-14T10:00:00+00:00"
    return store, classification, None, source, old


def _claim_snapshot_cycle(store, classification):
    from app.email_store import EmailUnsubscribeClaimConflict

    authorization, owner = _authorization(classification)
    acquired = store.claim_email_unsubscribe_write(**authorization, owner=owner)
    assert acquired and acquired["acquired"]
    same = store.claim_email_unsubscribe_write(**authorization, owner=owner)
    other = store.claim_email_unsubscribe_write(
        **authorization, owner=owner | dict(generation=22, lease_token="other")
    )
    claim = store.get_email_unsubscribe_claim(authorization["action_identity"])
    snapshot = store.get_email_unsubscribe_state_snapshot(
        authorization["action_identity"]
    )
    store.advance_email_unsubscribe_phase(
        action_identity=authorization["action_identity"],
        phase="effect_uncertain",
        owner=owner,
    )
    stale = False
    try:
        store.mark_email_unsubscribe_uncertain(
            action_identity=authorization["action_identity"],
            owner=owner | dict(generation=22),
        )
    except EmailUnsubscribeClaimConflict:
        stale = True
    return acquired, same, other, claim, snapshot, stale


def _authorization(classification):
    from app.email_store import email_action_identity, email_unsubscribe_effect_digest
    from app.email_classifier_contracts import EmailAction

    plan = classification.action_plan
    authorization = dict(
        action_identity=email_action_identity(
            account_id=classification.provider_locator.account_id,
            stable_message_identity=classification.stable_message_identity,
            action_type=EmailAction.UNSUBSCRIBE,
            action_plan_version=plan.action_plan_version,
        ),
        action_plan_id=plan.action_plan_id,
        action_plan_version=plan.action_plan_version,
        classification_id=classification.classification_id,
        account_id=classification.provider_locator.account_id,
        stable_message_identity=classification.stable_message_identity,
        thread_identity="thread-unsubscribe-41",
        entry_reference="unsubscribe-entry:" + "b" * 64,
        operations=[
            dict(
                operation_reference="step-1",
                kind="open_entry",
                target_reference="entry",
            )
        ],
    )
    authorization["effect_digest"] = email_unsubscribe_effect_digest(**authorization)
    return authorization, dict(
        owner_id="email-worker", generation=21, lease_token="unsubscribe-lease-a"
    )


def _old_result(pair, operation):
    import inspect
    import json
    import os
    import subprocess
    import sys
    from tests.test_email_action_plan_direct_dao import _normalized

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
        + inspect.getsource(_normalized)
        + "\n"
        + inspect.getsource(_authorization)
        + "\n"
        + inspect.getsource(operation)
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


def _continuation_terminal_cycle(store, classification):
    from app.email_store import (
        email_unsubscribe_effect_digest,
        EmailUnsubscribeClaimConflict,
        EmailUnsubscribeReceiptConflict,
    )

    a, owner = _authorization(classification)
    identity = a["action_identity"]
    store.claim_email_unsubscribe_write(**a, owner=owner)
    observation = "unsubscribe-state:" + "d" * 64
    control = "unsubscribe-control:" + "c" * 64
    step = dict(
        sequence=1,
        operation="open_entry",
        state="action_required",
        reference=observation,
    )
    continuation = store.persist_email_unsubscribe_continuation(
        **a,
        owner=owner,
        controls=[dict(reference=control, kind="button", intent="confirm")],
        observation_reference=observation,
        final_step=step,
    )
    before = store.get_email_unsubscribe_state_snapshot(identity)
    waiting_events = store.list_email_classification_observability(11)
    a2 = a | dict(
        previous_effect_digest=a["effect_digest"],
        operations=a["operations"]
        + [
            dict(
                operation_reference="step-2",
                kind="click_confirmation",
                target_reference=control,
            )
        ],
    )
    a2["effect_digest"] = email_unsubscribe_effect_digest(
        **{k: v for k, v in a2.items() if k != "effect_digest"}
    )
    owner2 = owner | dict(generation=22, lease_token="lease-b")
    accepted = store.claim_email_unsubscribe_write(**a2, owner=owner2)
    assert accepted["acquired"] and accepted["executed_prefix_length"] == 1
    assert store.get_email_unsubscribe_continuation(identity) is None
    errors = []
    for changed in (dict(sequence=3, owner=owner2), dict(sequence=2, owner=owner)):
        try:
            store.append_email_unsubscribe_step(
                action_identity=identity,
                effect_digest=a2["effect_digest"],
                operation="click_confirmation",
                state="done",
                reference="receipt",
                **changed,
            )
        except EmailUnsubscribeClaimConflict as exc:
            errors.append(str(exc))
    final = dict(
        sequence=2, operation="click_confirmation", state="done", reference="receipt"
    )
    first = store.append_email_unsubscribe_step(
        action_identity=identity,
        effect_digest=a2["effect_digest"],
        owner=owner2,
        **final,
    )
    replay = store.append_email_unsubscribe_step(
        action_identity=identity,
        effect_digest=a2["effect_digest"],
        owner=owner2,
        **final,
    )
    receipt = store.persist_email_unsubscribe_terminal(
        **a2,
        outcome="done",
        receipt_id="receipt",
        evidence="terminal-page",
        claim_owner=owner2,
    )
    same = store.persist_email_unsubscribe_terminal(
        **a2, outcome="done", receipt_id="receipt", evidence="terminal-page"
    )
    try:
        store.persist_email_unsubscribe_terminal(
            **a2, outcome="done", receipt_id="different", evidence="terminal-page"
        )
    except EmailUnsubscribeReceiptConflict as exc:
        errors.append(str(exc))
    terminal = store.get_email_unsubscribe_terminal_snapshot(
        identity, a2["effect_digest"]
    )
    # Includes terminal lineage validation, snapshots and C-only list paths.
    return (
        continuation,
        before,
        waiting_events,
        accepted,
        first,
        replay,
        receipt,
        same,
        terminal,
        errors,
        store.list_email_unsubscribe_steps(identity),
        store.list_unsubscribe_states([11]),
        store.list_missing_unsubscribe_action_tasks(),
        store.list_terminal_unsubscribe_tasks_missing_receipts(),
    )


def _release_recovery_cycle(store, classification):
    from app.email_store import EmailUnsubscribeClaimConflict

    a, owner = _authorization(classification)
    identity = a["action_identity"]
    errors = []
    store.claim_email_unsubscribe_write(**a, owner=owner)
    try:
        store.release_email_unsubscribe_failed_operation(
            identity, effect_digest=a["effect_digest"], owner=owner
        )
    except EmailUnsubscribeClaimConflict as exc:
        errors.append(str(exc))
    store.advance_email_unsubscribe_phase(identity, "navigating", owner=owner)
    store.release_email_unsubscribe_failed_operation(
        identity, effect_digest=a["effect_digest"], owner=owner
    )
    assert store.get_email_unsubscribe_claim(identity) is None
    store.claim_email_unsubscribe_write(**a, owner=owner)
    try:
        store.recover_terminated_email_unsubscribe_claims(
            owner=owner,
            termination_verifier=lambda o: False,
            recovered_at="2026-10-14T11:00:00+00:00",
        )
    except EmailUnsubscribeClaimConflict as exc:
        errors.append(str(exc))
    unmatched = store.recover_terminated_email_unsubscribe_claims(
        owner=owner | dict(generation=22),
        termination_verifier=lambda o: True,
        recovered_at="2026-10-14T11:00:00+00:00",
    )
    recovered = store.recover_terminated_email_unsubscribe_claims(
        owner=owner,
        termination_verifier=lambda o: True,
        recovered_at="2026-10-14T11:00:00+00:00",
    )
    second = store.recover_terminated_email_unsubscribe_claims(
        owner=owner,
        termination_verifier=lambda o: True,
        recovered_at="2026-10-14T11:00:00+00:00",
    )
    replay = store.claim_email_unsubscribe_write(
        **a, owner=owner | dict(generation=22, lease_token="new")
    )
    assert replay["acquired"] is False and replay["status"] == "uncertain"
    return (
        errors,
        unmatched,
        recovered,
        second,
        replay,
        store.get_email_unsubscribe_state_snapshot(identity),
    )


def _assert_native_facts(pair, *, repair_timestamp=False):
    from app.action_storage import (
        EMAIL_FIELDS,
        EMAIL_ROWID_TYPES,
        email_action_projection,
    )

    store, _, _, source, _ = pair
    with (
        closing(sqlite3.connect(source)) as old,
        closing(sqlite3.connect(store.path)) as target,
    ):
        for kind in (
            "email_unsubscribe_claims",
            "email_unsubscribe_effects",
            "email_unsubscribe_continuations",
            "email_unsubscribe_steps",
            "email_unsubscribe_receipts",
            "email_unsubscribe_cancellations",
        ):
            fields = list(EMAIL_FIELDS[kind])
            if repair_timestamp and kind == "email_unsubscribe_claims":
                fields.remove("updated_at")
                for db, table in (
                    (old, kind),
                    (target, "(" + email_action_projection(kind) + ")"),
                ):
                    timestamp = db.execute(
                        f"select updated_at from {table}"
                    ).fetchone()[0]
                    from datetime import datetime, timezone

                    parsed = datetime.strptime(timestamp, "%Y-%m-%d %H:%M:%S").replace(
                        tzinfo=timezone.utc
                    )
                    assert (
                        abs((datetime.now(timezone.utc) - parsed).total_seconds()) < 120
                    )
            if kind in EMAIL_ROWID_TYPES:
                fields.insert(0, "rowid")
            projection = email_action_projection(
                kind, include_rowid=kind in EMAIL_ROWID_TYPES
            )
            order = (
                "action_identity,effect_digest"
                if kind.endswith("effects")
                else "id"
                if kind.endswith("steps")
                else "rowid"
            )
            columns = ",".join(fields)
            assert (
                target.execute(
                    f"select {columns} from ({projection}) order by {order}"
                ).fetchall()
                == old.execute(
                    f"select {columns} from {kind} order by {order}"
                ).fetchall()
            ), kind
        # Public AUTO rows come from the retired steps namespace, not actions.id.
        old_water = old.execute(
            "select seq from sqlite_sequence where name='email_unsubscribe_steps'"
        ).fetchone()[0]
        from app.action_storage import _autoincrement_high_water

        assert _autoincrement_high_water(target, "email_unsubscribe_steps") == old_water


def _orphan_release_cycle(store, classification):
    a, owner = _authorization(classification)
    identity = a["action_identity"]
    owners = store.list_orphaned_email_unsubscribe_claim_owners()
    assert len(owners) == 1 and owners[0]["owner"] == owner
    released = store.release_orphaned_email_unsubscribe_claim(identity)
    again = store.release_orphaned_email_unsubscribe_claim(identity)
    assert released and not again
    return owners, released, again, store.get_email_unsubscribe_state_snapshot(identity)


def _explicit_retry_cycle(store, classification):
    from app.email_store import EmailUnsubscribeClaimConflict

    a, owner = _authorization(classification)
    identity = a["action_identity"]
    claim = store.get_email_unsubscribe_claim(identity)
    audit_id = claim["audit_agent_run_id"]
    errors = []
    try:
        store.release_failed_email_unsubscribe_for_explicit_retry(
            identity, audit_agent_run_id=audit_id + 1
        )
    except EmailUnsubscribeClaimConflict as exc:
        errors.append(str(exc))
    store.release_failed_email_unsubscribe_for_explicit_retry(
        identity, audit_agent_run_id=audit_id
    )
    assert store.get_email_unsubscribe_claim(identity) is None
    assert store.get_email_unsubscribe_effect(identity, a["effect_digest"]) is None
    fresh = store.claim_email_unsubscribe_write(
        **a, owner=owner | dict(generation=22, lease_token="fresh-lease")
    )
    assert fresh["acquired"]
    return errors, fresh


def _cancellation_cycle(store, classification):
    from contextlib import nullcontext
    from app.email_store import EmailStore, EmailUnsubscribeClaimConflict
    from app.store import REPLY_TASK_CURRENT_SQL

    class Profile:
        def lock(self, *, timeout_seconds):
            assert timeout_seconds == 0
            return nullcontext()

    a, owner = _authorization(classification)
    identity = a["action_identity"]
    with store._connect() as db:
        task = db.execute(
            f"select * from ({REPLY_TASK_CURRENT_SQL}) where trigger_message_id=?",
            (identity,),
        ).fetchone()
    args = dict(
        task_id=task["id"],
        expected_action_identity=identity,
        expected_execution_generation=task["execution_generation"],
        expected_input_version=task["input_version"],
        reason="Keep subscription.",
        profile=Profile(),
    )
    # This method uses the public class clock for its durable decision.
    original_clock = EmailStore._now
    EmailStore._now = staticmethod(lambda: "2026-10-14T10:00:00+00:00")
    try:
        preview = store.cancel_failed_email_unsubscribe_task(**args)
        assert store.get_email_unsubscribe_cancellation(identity) is None
        saved = store.cancel_failed_email_unsubscribe_task(**args, apply=True)
        replay = store.cancel_failed_email_unsubscribe_task(
            **(args | dict(reason="different reason")), apply=True
        )
    finally:
        EmailStore._now = staticmethod(original_clock)
    errors = []
    try:
        store.claim_email_unsubscribe_write(**a, owner=owner)
    except EmailUnsubscribeClaimConflict as exc:
        errors.append(str(exc))
    assert errors == ["unsubscribe cancelled by principal"]
    return (
        preview,
        saved,
        replay,
        store.get_email_unsubscribe_cancellation(identity),
        store.get_email_unsubscribe_receipt(identity),
        store.list_missing_unsubscribe_action_tasks(),
        store.list_terminal_unsubscribe_tasks_missing_receipts(),
        errors,
    )


def _repair_journal_cycle(store, classification):
    a, owner = _authorization(classification)
    identity = a["action_identity"]
    store.claim_email_unsubscribe_write(**a, owner=owner)
    store.append_email_unsubscribe_step(
        action_identity=identity,
        effect_digest=a["effect_digest"],
        sequence=1,
        operation="open_entry",
        state="action_required",
        reference="entry",
        owner=owner,
    )
    # Reproduce the original direct-run undercoverage: the journal knows more
    # than the accepted one-entry effect. This source method intentionally repairs it.
    store.append_email_unsubscribe_step(
        action_identity=identity,
        effect_digest=a["effect_digest"],
        sequence=2,
        operation="click_confirmation",
        state="done",
        reference="control",
        owner=owner,
    )
    store.persist_email_unsubscribe_terminal(
        **a,
        outcome="done",
        receipt_id="receipt",
        evidence="terminal-page",
        claim_owner=owner,
    )
    repaired = store.repair_email_unsubscribe_effect_journal(identity)
    assert (
        repaired["repaired"]
        and repaired["effects"][0]["steps"] == 2
        and repaired["effects"][0]["claims"] == 1
        and repaired["effects"][0]["receipts"] == 1
    )
    assert store.get_email_unsubscribe_effect(identity, a["effect_digest"]) is None
    digest = repaired["effects"][0]["effect_digest"]
    again = store.repair_email_unsubscribe_effect_journal(identity)
    return (
        repaired,
        again,
        store.get_email_unsubscribe_effect(identity, digest),
        store.get_email_unsubscribe_receipt(identity),
        store.list_email_unsubscribe_steps(identity),
    )


def _retire_promote_backfill_cycle(store, classification):
    from hashlib import sha256
    from app.email_store import email_unsubscribe_effect_digest

    a, owner = _authorization(classification)
    identity = a["action_identity"]
    url = "https://mail.example.test/unsubscribe/token"
    a["entry_reference"] = "unsubscribe-entry:" + sha256(url.encode()).hexdigest()
    a["effect_digest"] = email_unsubscribe_effect_digest(
        **{k: v for k, v in a.items() if k != "effect_digest"}
    )
    absent = store.retire_email_unsubscribe_claim_for_direct_run(**a)
    store.claim_email_unsubscribe_write(**a, owner=owner)
    retired = store.retire_email_unsubscribe_claim_for_direct_run(**a)
    repeated = store.retire_email_unsubscribe_claim_for_direct_run(**a)
    first = store.persist_email_unsubscribe_terminal(
        **a, outcome="skipped_login_required", receipt_id="login", evidence="login-page"
    )
    promoted = store.persist_email_unsubscribe_terminal(
        **a,
        outcome="done",
        receipt_id="done",
        evidence="terminal-page",
        result_text="Unsubscribed.",
    )
    assert (
        first["outcome"] == "skipped_login_required" and promoted["outcome"] == "done"
    )
    invalid = False
    try:
        store.backfill_email_unsubscribe_entry_url(identity, url + "/other")
    except ValueError:
        invalid = True
    filled = store.backfill_email_unsubscribe_entry_url(identity, url)
    again = store.backfill_email_unsubscribe_entry_url(identity, url)
    blank = store.backfill_email_unsubscribe_entry_url("missing", url)
    assert invalid and filled and not again and not blank
    replay = store.claim_email_unsubscribe_write(**a, owner=owner)
    assert replay == {"acquired": False, "status": "done"}
    return (
        absent,
        retired,
        repeated,
        first,
        promoted,
        invalid,
        filled,
        again,
        blank,
        replay,
        store.get_email_unsubscribe_entry_url(11),
        store.get_email_unsubscribe_receipt(identity),
        store.list_email_context_receipts(
            account_id=a["account_id"],
            stable_message_identity=a["stable_message_identity"],
        ),
    )


def _atomic_fault_cycle(store, classification):
    import sqlite3

    a, owner = _authorization(classification)
    identity = a["action_identity"]
    store.claim_email_unsubscribe_write(**a, owner=owner)
    with store._connect() as db:
        native = (
            db.execute(
                "select 1 from sqlite_master where name='email_unsubscribe_claims' and type='table'"
            ).fetchone()
            is not None
        )
        if native:
            db.execute("""create trigger test_terminal_fault before update of status on email_unsubscribe_claims
                when new.status='done' begin select raise(abort,'terminal_fault');end""")
        else:
            db.execute("""create trigger test_terminal_fault before update of payload_json on actions
                when new.record_type='email_unsubscribe_claims' and new.email_status='done'
                begin select raise(abort,'terminal_fault');end""")
    try:
        store.persist_email_unsubscribe_terminal(
            **a,
            outcome="done",
            receipt_id="receipt",
            evidence="terminal-page",
            final_step=dict(
                sequence=1, operation="open_entry", state="done", reference="receipt"
            ),
            claim_owner=owner,
        )
    except sqlite3.IntegrityError as exc:
        assert str(exc) == "terminal_fault"
    else:
        raise AssertionError("terminal fault did not fire")
    assert store.get_email_unsubscribe_receipt(identity) is None
    assert store.list_email_unsubscribe_steps(identity) == []
    assert store.get_email_unsubscribe_claim(identity)["status"] == "dispatching"
    with store._connect() as db:
        db.execute("drop trigger test_terminal_fault")
        if native:
            db.execute(
                """insert into email_unsubscribe_claims select 'second',effect_digest,action_plan_id,
                action_plan_version,classification_id,account_id,stable_message_identity,thread_identity,
                entry_reference,operations_json,owner_id,owner_generation,lease_token,account_updated_at,
                status,phase,audit_agent_run_id,claimed_at,updated_at from email_unsubscribe_claims where action_identity=?""",
                (identity,),
            )
            db.execute("""create trigger test_recovery_fault before update of status on email_unsubscribe_claims
                when new.action_identity='second' and new.status='uncertain'
                begin select raise(abort,'recovery_fault');end""")
        else:
            from app.action_storage import insert_email_action, EMAIL_ACTION_PROJECTIONS

            row = dict(
                db.execute(
                    "select * from ("
                    + EMAIL_ACTION_PROJECTIONS["email_unsubscribe_claims"]
                    + ") where action_identity=?",
                    (identity,),
                ).fetchone()
            )
            insert_email_action(
                db, "email_unsubscribe_claims", row | dict(action_identity="second")
            )
            db.execute("""create trigger test_recovery_fault before update of payload_json on actions
                when new.record_type='email_unsubscribe_claims' and new.email_action_identity='second'
                  and new.email_status='uncertain' begin select raise(abort,'recovery_fault');end""")
    try:
        store.recover_terminated_email_unsubscribe_claims(
            owner=owner,
            termination_verifier=lambda o: True,
            recovered_at="2026-10-14T11:00:00+00:00",
        )
    except sqlite3.IntegrityError as exc:
        assert str(exc) == "recovery_fault"
    else:
        raise AssertionError("recovery fault did not fire")
    with store._connect() as db:
        db.execute("drop trigger test_recovery_fault")
    assert store.get_email_unsubscribe_claim(identity)["status"] == "dispatching"
    assert store.get_email_unsubscribe_claim("second")["status"] == "dispatching"
    return store.get_email_unsubscribe_claim(
        identity
    ), store.get_email_unsubscribe_claim("second")


def _competing_backfill_cycle(store, classification):
    from hashlib import sha256
    import sqlite3
    import app.email_store as module

    a, owner = _authorization(classification)
    identity = a["action_identity"]
    url = "https://mail.example.test/unsubscribe/competing"
    a["entry_reference"] = "unsubscribe-entry:" + sha256(url.encode()).hexdigest()
    a["effect_digest"] = module.email_unsubscribe_effect_digest(
        **{k: v for k, v in a.items() if k != "effect_digest"}
    )
    store.persist_email_unsubscribe_terminal(
        **a, outcome="done", receipt_id="receipt", evidence="terminal-page"
    )
    original = module._validate_unsubscribe_entry_url
    raced = []

    def competing(value, *, entry_reference):
        validated = original(value, entry_reference=entry_reference)
        with sqlite3.connect(store.path, timeout=1) as db:
            db.row_factory = sqlite3.Row
            if db.execute(
                "select 1 from sqlite_master where name='email_unsubscribe_receipts' and type='table'"
            ).fetchone():
                db.execute(
                    "update email_unsubscribe_receipts set entry_url=? where action_identity=?",
                    (url, identity),
                )
            else:
                from app.action_storage import (
                    email_action_projection,
                    update_email_action,
                )

                db.execute("begin immediate")
                row = db.execute(
                    "select rowid from ("
                    + email_action_projection(
                        "email_unsubscribe_receipts", include_rowid=True
                    )
                    + ") where action_identity=?",
                    (identity,),
                ).fetchone()
                update_email_action(
                    db, "email_unsubscribe_receipts", row["rowid"], dict(entry_url=url)
                )
        raced.append(True)
        return validated

    module._validate_unsubscribe_entry_url = competing
    try:
        filled = store.backfill_email_unsubscribe_entry_url(identity, url)
    finally:
        module._validate_unsubscribe_entry_url = original
    assert raced == [True] and filled is False
    return filled, store.get_email_unsubscribe_receipt(identity)


def _audited_lineage_cycle(store, classification):
    from app.email_store import EmailPersistenceCorruption
    from app.store import REPLY_TASK_CURRENT_SQL

    a, _ = _authorization(classification)
    identity = a["action_identity"]
    with store._connect() as db:
        task = db.execute(
            f"select * from ({REPLY_TASK_CURRENT_SQL}) where trigger_message_id=?",
            (identity,),
        ).fetchone()
    claim = store.get_email_unsubscribe_claim(identity)
    store.persist_email_unsubscribe_terminal(
        **a,
        outcome="done",
        receipt_id="receipt",
        evidence="terminal-page",
        final_step=dict(
            sequence=1, operation="open_entry", state="done", reference="receipt"
        ),
    )
    snapshot = store.get_email_unsubscribe_terminal_snapshot(
        identity,
        a["effect_digest"],
        task_id=task["id"],
        task_execution_generation=task["execution_generation"],
    )
    events = store.list_email_classification_observability(11)
    event = next(event for event in events if event["kind"] == "unsubscribe")
    assert len(event["consumer_run_ids"]) == len(event["audit_run_ids"]) == 1
    assert event["audit_run_ids"] == [claim["audit_agent_run_id"]]
    # A later task generation does not transfer the earned Audit receipt.
    with store._connect() as db:
        db.execute(
            "update tasks set execution_generation='later-generation' where record_type='reply_tasks' and id=?",
            (task["id"],),
        )
    later = store.get_email_unsubscribe_terminal_snapshot(
        identity,
        a["effect_digest"],
        task_id=task["id"],
        task_execution_generation="later-generation",
    )
    stale = False
    try:
        store.get_email_unsubscribe_terminal_snapshot(
            identity,
            a["effect_digest"],
            task_id=task["id"],
            task_execution_generation=task["execution_generation"],
        )
    except EmailPersistenceCorruption:
        stale = True
    assert stale
    return (
        snapshot,
        events,
        later,
        stale,
        store.list_email_classification_observability(11),
        store.list_email_context_receipts(
            account_id=a["account_id"],
            stable_message_identity=a["stable_message_identity"],
        ),
    )
